-- 0048 platform · 官方来源清单由管理员维护（specs/policy-decisions-v0.9.1/spec.md 第 4 节，PR1；用户结论 4，spec 待人确认 #3 默认）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、RLS 与权限）。请以单事务执行（psql -1）。
-- 依赖 0013（policy.source_domain、policy.url_host）、0018（只追加函数 platform.reject_signoff_change）、0020（staff 账号）、
-- 0023（platform.review_current_staff）、0035（个人层表清单）、0041（属主 authz 读 source_domain）。
-- 要点：
--   1. staff 权限表 platform.staff_permission（staff_id、permission、granted_by、granted_at）：首个权限 policy_source_admin；
--      只由迁移或 DBA 授予（应用角色没有写权限）；审核台签字身份 starbuddy_review 只读自己的权限（审核台据此给无权限的 staff 403，PR2）；
--      库层守卫只认 staff 账号；granted_by 写授予人（DBA 或迁移的说明），不是账号引用；
--   2. policy.add_source_domain(主机名, 说明)、policy.retire_source_domain(主机名)：SECURITY DEFINER，属主 authz，只授 starbuddy_review
--      （审核台的签字连接，带当前 staff 账号；照 0041 的签字函数）；要求当前账号是 active staff 且有 policy_source_admin；
--      - 主机名规范化：去首尾空白、转小写、去掉一个结尾的点；只收域名（至少两段，每段字母数字与连字符，末段以字母开头），
--        IP、通配符、端口、路径、空值一律拒绝；只有公共后缀的主机名（gov.cn、com.cn、edu.cn 等，单段一律）也拒绝：
--        登记 gov.cn 等于放行它下面所有主机（policy.normalize_source_host）；
--      - 新增：已在清单（active）的拒绝；已下线的重新启用；说明写进清单行的 issuer（发布机关）；
--      - 下线：只改状态，不删行。清单行一律不能删（应用角色与函数都不删）：已被来源文件引用的主机名只能下线，
--        挂在它下面的来源文件仍能更新核对日期或撤下（guard_source_document）；没被引用的同样只下线，留作历史；
--   3. 操作记录写 platform.policy_source_domain_change（主机名、动作、说明、操作者账号、时间；只追加，只有属主 authz 能读写）。
--      账号引用不进 policy schema（规则 13；攻击面公共层断言与金丝雀）：清单行的 added_by 固定写 'review-console'，操作者只记在 platform。
-- 跨 schema（ADR 02 例外）：两个函数要读 platform.staff_permission、写 platform.policy_source_domain_change，同时写 policy.source_domain；
-- 账号引用只能在 platform，清单只能在 policy，函数必须同时依赖两边，拆成两个迁移会有一个迁移里函数引用尚不存在的表。

-- ---------- 1. staff 权限 ----------
CREATE TABLE platform.staff_permission (
  staff_id    uuid NOT NULL REFERENCES platform.account (id),
  permission  text NOT NULL CHECK (permission IN ('policy_source_admin')),
  granted_by  text NOT NULL CHECK (char_length(granted_by) BETWEEN 1 AND 64),   -- 授予人（DBA 或迁移），不是账号引用
  granted_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (staff_id, permission)
);
COMMENT ON TABLE platform.staff_permission IS
  'staff 的附加权限（0048）：只由迁移或 DBA 授予与收回；审核台签字身份只读自己的权限。首个权限 policy_source_admin（维护官方来源清单）';
CREATE FUNCTION platform.guard_staff_permission() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id = NEW.staff_id AND a.kind = 'staff') THEN
    RAISE EXCEPTION '只有 staff 账号能有 staff 权限' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION platform.guard_staff_permission() FROM PUBLIC;
CREATE TRIGGER guard_staff_permission BEFORE INSERT OR UPDATE ON platform.staff_permission
  FOR EACH ROW EXECUTE FUNCTION platform.guard_staff_permission();
ALTER TABLE platform.staff_permission ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.staff_permission FORCE ROW LEVEL SECURITY;
REVOKE ALL ON platform.staff_permission FROM PUBLIC;
GRANT SELECT ON platform.staff_permission TO starbuddy_review, starbuddy_authz;
CREATE POLICY staff_permission_self_read ON platform.staff_permission FOR SELECT TO starbuddy_review
  USING (staff_id = platform.current_account_id());

-- ---------- 2. 来源清单操作记录（只追加） ----------
CREATE TABLE platform.policy_source_domain_change (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  host              text NOT NULL CHECK (host ~ '^[a-z0-9][a-z0-9.\-]{2,252}$'),
  action            text NOT NULL CHECK (action IN ('add', 'reactivate', 'retire')),
  note              text CHECK (char_length(note) BETWEEN 1 AND 64),
  actor_account_id  uuid NOT NULL REFERENCES platform.account (id),
  changed_at        timestamptz NOT NULL,
  CONSTRAINT policy_source_domain_change_note CHECK ((action = 'retire') = (note IS NULL))
);
CREATE INDEX policy_source_domain_change_host_idx ON platform.policy_source_domain_change (host, changed_at);
COMMENT ON TABLE platform.policy_source_domain_change IS
  '官方来源清单（policy.source_domain）的操作记录（0048）：只由 policy.add_source_domain / retire_source_domain 写入；只追加；'
  '操作者是 staff 账号，所以记在 platform 而不是 policy（规则 13）';
CREATE TRIGGER reject_source_domain_change_change BEFORE UPDATE OR DELETE ON platform.policy_source_domain_change
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_source_domain_change_truncate BEFORE TRUNCATE ON platform.policy_source_domain_change
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();
ALTER TABLE platform.policy_source_domain_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.policy_source_domain_change FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的函数属主 authz 能读写
REVOKE ALL ON platform.policy_source_domain_change FROM PUBLIC;
GRANT SELECT, INSERT ON platform.policy_source_domain_change TO starbuddy_authz;

-- 个人层表清单（0035）：两张表都只含教研账号，按豁免登记（同 policy_signoff、release_record）
INSERT INTO platform.personal_table_registry (nspname, relname, key_path, student_predicate, strategy, exempt_reason) VALUES
  ('platform', 'staff_permission', 'staff_id（只有教研账号）',
   'staff_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   'staff 的附加权限，库层守卫只认 staff 账号'),
  ('platform', 'policy_source_domain_change', 'actor_account_id（只有教研账号）',
   'actor_account_id = (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = $1)', 'exempt',
   '官方来源清单的操作记录，操作者只含教研账号');

-- ---------- 3. 主机名规范化 ----------
-- 公共后缀（单段的已被“至少两段”拒绝）：登记它们等于放行其下所有主机。只列考情来源可能碰到的常见后缀，不是完整的公共后缀表
CREATE FUNCTION policy.normalize_source_host(p_host text) RETURNS text
  LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT h FROM (SELECT regexp_replace(lower(btrim(p_host)), '\.$', '') AS h) x
         WHERE char_length(h) BETWEEN 4 AND 253
           AND h ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'
           AND h <> ALL (ARRAY['gov.cn', 'com.cn', 'edu.cn', 'org.cn', 'net.cn', 'ac.cn', 'mil.cn', 'js.cn', 'sh.cn', 'bj.cn',
                               'com.hk', 'edu.hk', 'gov.hk', 'org.hk', 'net.hk', 'co.uk', 'ac.uk', 'gov.uk', 'org.uk',
                               'com.tw', 'edu.tw', 'gov.tw', 'org.tw']) $$;
REVOKE EXECUTE ON FUNCTION policy.normalize_source_host(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.normalize_source_host(text) TO starbuddy_authz;
COMMENT ON FUNCTION policy.normalize_source_host(text) IS
  '官方来源主机名规范化（0048）：去首尾空白、小写、去一个结尾的点；只收至少两段的域名，末段以字母开头；'
  'IP、通配符、端口、路径、空值、只有公共后缀（gov.cn、com.cn 等）的返回 NULL';

-- ---------- 4. 新增与下线 ----------
GRANT INSERT, UPDATE (status, issuer) ON policy.source_domain TO starbuddy_authz;

CREATE FUNCTION policy.add_source_domain(p_host text, p_note text) RETURNS text
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

CREATE FUNCTION policy.retire_source_domain(p_host text) RETURNS text
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

ALTER FUNCTION policy.add_source_domain(text, text) OWNER TO starbuddy_authz;
ALTER FUNCTION policy.retire_source_domain(text) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION policy.add_source_domain(text, text), policy.retire_source_domain(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policy.add_source_domain(text, text), policy.retire_source_domain(text) TO starbuddy_review;
COMMENT ON FUNCTION policy.add_source_domain(text, text) IS
  '新增或重新启用官方来源主机名（0048）：active staff 且有 policy_source_admin；主机名规范化；操作记录写 platform.policy_source_domain_change';
COMMENT ON FUNCTION policy.retire_source_domain(text) IS
  '下线官方来源主机名（0048）：active staff 且有 policy_source_admin；只改状态不删行；操作记录写 platform.policy_source_domain_change';
