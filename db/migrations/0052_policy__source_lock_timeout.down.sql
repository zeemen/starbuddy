-- 0052 回滚：来源清单三个函数回到 0050（add_source_domain、lock_active_source_host）与 0048（retire_source_domain）原文，
-- 去掉 5 秒锁等待上限与持锁事务的 60 秒空闲上限。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。没有数据前提：0052 不加表、列与数据，授权与属主不变。
-- CREATE OR REPLACE 按新语句重设函数的全部 SET 子句（属主与授权不变），0052 加的 SET lock_timeout 随之去掉（run.sh 回滚检查核对）。
-- 回滚说明：回滚后 kge 开着事务不提交时，下线与重新启用会一直等锁（PR3 记录的已知风险重新出现）；
-- 审核台对 55P03 的 409 映射保留无害（不会被触发）。0052 没有执行 ALTER ROLE，回滚不改任何角色设置。

-- ---------- 3. 取锁函数（0050 原文） ----------
CREATE OR REPLACE FUNCTION policy.lock_active_source_host(p_host text) RETURNS boolean
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
  AS $$
BEGIN
  PERFORM 1 FROM policy.source_domain d WHERE d.host = p_host AND d.status = 'active' FOR SHARE;
  RETURN FOUND;
END $$;
COMMENT ON FUNCTION policy.lock_active_source_host(text) IS
  '来源文件守卫用（0050）：主机名在官方来源清单里且 active 时对该行加共享锁并返回 true（与并发的下线串行）；只授写入方 kge';

-- ---------- 2. 下线（0048 原文） ----------
CREATE OR REPLACE FUNCTION policy.retire_source_domain(p_host text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
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
  SELECT d.status INTO v_status FROM policy.source_domain d WHERE d.host = v_host FOR UPDATE;
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
  '下线官方来源主机名（0048）：active staff 且有 policy_source_admin；只改状态不删行；操作记录写 platform.policy_source_domain_change';

-- ---------- 1. 新增（0050 原文） ----------
CREATE OR REPLACE FUNCTION policy.add_source_domain(p_host text, p_note text) RETURNS text
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, policy, pg_temp
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
  INSERT INTO platform.policy_source_domain_change (host, action, note, actor_account_id, changed_at)
  VALUES (v_host, v_action, v_note, v_staff, clock_timestamp());
  RETURN v_host;
END $$;
COMMENT ON FUNCTION policy.add_source_domain(text, text) IS
  '新增或重新启用官方来源主机名（0048，0050 加输入核对）：active staff 且有 policy_source_admin；主机名规范化（含 xn-- 往返解码）；'
  '说明不能含 Cc、Cf、Zl、Zp；操作记录写 platform.policy_source_domain_change';
