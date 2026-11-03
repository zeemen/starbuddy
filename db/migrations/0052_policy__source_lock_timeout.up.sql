-- 0052 policy · 来源清单的锁等待上限（specs/policy-decisions-v0.9.1/spec.md“PR4”；lessons“PR3 / 已知风险”第 1 条）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。请以单事务执行（psql -1）。
-- 只改 policy 的三个来源清单函数（属主仍是 authz，授权不变），归入 0048 已登记的“来源清单”例外（函数读写 platform 的权限表与操作记录，ADR 02）。
-- 依赖 0048（retire_source_domain 现行版本）、0050（add_source_domain、lock_active_source_host 现行版本）。
-- 背景：0050 起来源文件守卫经 policy.lock_active_source_host 对清单行 FOR SHARE，写入方 kge 开着事务不提交时，
-- 下线（以及重新启用）对同一行的 FOR UPDATE 会一直等，审核台请求挂住。
-- 要点：
--   1. policy.add_source_domain、policy.retire_source_domain 的锁等待上限：每次等锁最多 5 秒；持续有登记时总等待可能更久
--      （lock_timeout 按每次取锁计；审核台后端另有 10 秒客户端超时兜底）。函数声明 SET lock_timeout = '5s'
--      （等同于函数内 SET LOCAL，但只在函数执行期间生效、返回后恢复调用方原值，不影响同一事务里之后的语句）。
--      等锁超时以 SQLSTATE 55P03（lock_not_available）报固定说明“官方来源清单的这一行正被占用，请稍后重试”，
--      审核台后端映射为 409 BUSY（services/learning/review_console/store.py）。超时时整笔回滚：清单与操作记录都不变；
--   2. 持锁方的空闲事务上限 60 秒：policy.lock_active_source_host（只授 kge，来源文件守卫唯一的取锁入口）取到共享锁后，
--      把本事务的 idle_in_transaction_session_timeout 设为 60 秒（set_config 的事务级设置，事务结束即恢复；
--      调用方已设了更短的非零值时不放宽）。kge 拿着清单行的锁空闲超过 60 秒，服务端断开该会话、锁随之释放。
--      偏差（对 spec“kge 写入角色 ALTER ROLE … SET”）：starbuddy_kge 是 NOLOGIN 组角色（0011 的角色断言要求如此），
--      Postgres 的角色级参数只在以该角色登录时生效，SET ROLE 与“登录角色继承组角色”都不读组角色的设置，
--      ALTER ROLE starbuddy_kge SET … 对任何 kge 连接都不起作用；登录角色名由部署决定、不在迁移里。所以改为在取锁处按事务设置，
--      与登录角色无关。部署时仍建议 DBA 对 kge 的登录角色另设 ALTER ROLE <kge 登录角色> SET idle_in_transaction_session_timeout = '60s'
--      （同 0020 对签字登录角色日志参数的做法），覆盖不经取锁函数的长事务；
--   3. 局限：空闲上限只管“事务空闲”；kge 在事务里持续执行语句（不空闲）时仍可长时间持锁，此时由第 1 条的锁等待上限（每次等锁最多 5 秒；
--      持续有登记时总等待可能更久）与审核台的客户端超时保证下线不挂起，只是失败（409，稍后重试）。空闲上限只是防程序出错的安全网：
--      kge 可在自己的事务里 SET LOCAL 撤掉它。

-- ---------- 1. 新增（或重新启用）：0050 原文，加锁等待上限与超时说明 ----------
CREATE OR REPLACE FUNCTION policy.add_source_domain(p_host text, p_note text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET lock_timeout = '5s'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_host text;
  v_note text := btrim(p_note);
  v_status text;
  v_action text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.staff_permission p WHERE p.staff_id = v_staff AND p.permission = 'policy_source_admin') THEN
    RAISE EXCEPTION '没有维护官方来源清单的权限（policy_source_admin）' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_host := policy.normalize_source_host(p_host);
  IF v_host IS NULL THEN
    RAISE EXCEPTION '主机名不合格：只收域名（至少两段），不收 IP、通配符、端口、路径、空值或只有公共后缀的主机' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF v_note IS NULL OR char_length(v_note) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION '说明（发布机关）须为 1–64 个字' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF policy.has_hidden_chars(v_note) THEN
    RAISE EXCEPTION '说明（发布机关）不能含控制字符、格式字符或行段分隔符' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  BEGIN
    SELECT d.status INTO v_status FROM policy.source_domain d WHERE d.host = v_host FOR UPDATE;
    IF v_status = 'active' THEN
      RAISE EXCEPTION '主机名 % 已在官方来源清单里', v_host USING ERRCODE = 'unique_violation';
    ELSIF v_status = 'retired' THEN
      UPDATE policy.source_domain SET status = 'active', issuer = v_note WHERE host = v_host;
      v_action := 'reactivate';
    ELSE
      INSERT INTO policy.source_domain (host, issuer, added_by) VALUES (v_host, v_note, 'review-console');
      v_action := 'add';
    END IF;
  EXCEPTION WHEN lock_not_available THEN
    -- 锁等待超过 5 秒（0052）：清单行正被来源文件登记持有共享锁，或另一笔新增 / 下线未提交
    RAISE EXCEPTION '官方来源清单的这一行正被占用，请稍后重试' USING ERRCODE = 'lock_not_available';
  END;
  INSERT INTO platform.policy_source_domain_change (host, action, note, actor_account_id, changed_at)
  VALUES (v_host, v_action, v_note, v_staff, clock_timestamp());
  RETURN v_host;
END $$;
COMMENT ON FUNCTION policy.add_source_domain(text, text) IS
  '新增或重新启用官方来源主机名（0048，0050 加输入核对，0052 加锁等待上限）：active staff 且有 policy_source_admin；主机名规范化（含 xn-- 往返解码）；'
  '说明不能含 Cc、Cf、Zl、Zp；等清单行锁超过 5 秒报 55P03“稍后重试”；操作记录写 platform.policy_source_domain_change';

-- ---------- 2. 下线：0048 原文，加锁等待上限与超时说明 ----------
CREATE OR REPLACE FUNCTION policy.retire_source_domain(p_host text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp SET lock_timeout = '5s'
  AS $$
DECLARE
  v_staff uuid := platform.review_current_staff();
  v_host text;
  v_status text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.staff_permission p WHERE p.staff_id = v_staff AND p.permission = 'policy_source_admin') THEN
    RAISE EXCEPTION '没有维护官方来源清单的权限（policy_source_admin）' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_host := policy.normalize_source_host(p_host);
  IF v_host IS NULL THEN
    RAISE EXCEPTION '主机名不合格：只收域名（至少两段），不收 IP、通配符、端口、路径、空值或只有公共后缀的主机' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  BEGIN
    SELECT d.status INTO v_status FROM policy.source_domain d WHERE d.host = v_host FOR UPDATE;
  EXCEPTION WHEN lock_not_available THEN
    -- 锁等待超过 5 秒（0052）：清单行正被来源文件登记持有共享锁（kge 事务未提交），或另一笔新增 / 下线未提交
    RAISE EXCEPTION '官方来源清单的这一行正被占用，请稍后重试' USING ERRCODE = 'lock_not_available';
  END;
  IF v_status IS NULL THEN
    RAISE EXCEPTION '官方来源清单里没有主机名 %', v_host USING ERRCODE = 'no_data_found';
  ELSIF v_status = 'retired' THEN
    RAISE EXCEPTION '主机名 % 已下线', v_host USING ERRCODE = 'check_violation';
  END IF;
  -- 只下线，不删行（已被来源文件引用的主机名必须留在清单里）
  UPDATE policy.source_domain SET status = 'retired' WHERE host = v_host;
  INSERT INTO platform.policy_source_domain_change (host, action, note, actor_account_id, changed_at)
  VALUES (v_host, 'retire', NULL, v_staff, clock_timestamp());
  RETURN v_host;
END $$;
COMMENT ON FUNCTION policy.retire_source_domain(text) IS
  '下线官方来源主机名（0048，0052 加锁等待上限）：active staff 且有 policy_source_admin；只改状态不删行；'
  '等清单行锁超过 5 秒报 55P03“稍后重试”；操作记录写 platform.policy_source_domain_change';

-- ---------- 3. 取锁方的空闲事务上限：0050 原文，取到锁后把本事务的空闲上限设为 60 秒 ----------
CREATE OR REPLACE FUNCTION policy.lock_active_source_host(p_host text) RETURNS boolean
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
  AS $$
DECLARE v_idle bigint;
BEGIN
  PERFORM 1 FROM policy.source_domain d WHERE d.host = p_host AND d.status = 'active' FOR SHARE;
  IF FOUND THEN
    -- 持锁的事务空闲超过 60 秒即被服务端断开、锁随之释放（0052）；事务级设置，不放宽调用方已设的更短上限
    SELECT s.setting::bigint INTO v_idle FROM pg_catalog.pg_settings s WHERE s.name = 'idle_in_transaction_session_timeout';
    IF v_idle = 0 OR v_idle > 60000 THEN
      PERFORM set_config('idle_in_transaction_session_timeout', '60s', true);
    END IF;
    RETURN true;
  END IF;
  RETURN false;
END $$;
COMMENT ON FUNCTION policy.lock_active_source_host(text) IS
  '来源文件守卫用（0050）：主机名在官方来源清单里且 active 时对该行加共享锁并返回 true（与并发的下线串行）；只授写入方 kge。'
  '0052 起取到锁后把本事务的 idle_in_transaction_session_timeout 设为 60 秒（不放宽更短的设置），持锁空闲过久的会话被断开';
