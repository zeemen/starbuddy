-- 0018 platform · 审核台签字人（specs/release-gate-sprint4/spec.md 第 3 节；冲刺 1 PR 1）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。依赖 0011；0019（kge 签字函数）依赖本迁移。
-- 签字人是 staff 账号，属于平台个人层（platform.account）；公共知识层 kge.* 不能存任何账号引用（规则 13，
-- db/tests/80_attack_surface.sql 断言），所以签字人记在这里，按审核记录 / 变更单的 ID 引用 kge。
-- 只追加：签字写入后不能改、不能删；只有签字函数的属主 starbuddy_authz 能写（BYPASSRLS）。其他应用角色没有任何权限。

CREATE TABLE platform.content_signoff (
  content_review_id  uuid PRIMARY KEY REFERENCES kge.content_review (id),
  account_id         uuid NOT NULL REFERENCES platform.account (id),
  signed_at          timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE platform.content_signoff IS '审核记录（kge.content_review）的签字人：只由 kge.sign_review / kge.retire_map_version 写入（0019）';

CREATE TABLE platform.map_change_signoff (
  map_change_id  uuid NOT NULL REFERENCES kge.map_change (id),
  account_id     uuid NOT NULL REFERENCES platform.account (id),
  decision       text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  digest         text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),  -- 签字时变更单内容的摘要：之后内容改了，这个签字不再计数
  signed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (map_change_id, account_id, digest)  -- 同一账号对同一变更单的同一内容只能签一次；内容改过后可以对新内容再签
);
COMMENT ON TABLE platform.map_change_signoff IS '变更单（kge.map_change）的签字人：只由 kge.sign_map_change 写入（0019）；两名不同 staff 批准才转为 approved';

CREATE FUNCTION platform.reject_signoff_change() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE EXECUTE ON FUNCTION platform.reject_signoff_change() FROM PUBLIC;
CREATE TRIGGER reject_signoff_change BEFORE UPDATE OR DELETE ON platform.content_signoff
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_signoff_truncate BEFORE TRUNCATE ON platform.content_signoff
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_signoff_change BEFORE UPDATE OR DELETE ON platform.map_change_signoff
  FOR EACH ROW EXECUTE FUNCTION platform.reject_signoff_change();
CREATE TRIGGER reject_signoff_truncate BEFORE TRUNCATE ON platform.map_change_signoff
  FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_signoff_change();

GRANT SELECT, INSERT ON platform.content_signoff, platform.map_change_signoff TO starbuddy_authz;
ALTER TABLE platform.content_signoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.content_signoff FORCE ROW LEVEL SECURITY;
ALTER TABLE platform.map_change_signoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.map_change_signoff FORCE ROW LEVEL SECURITY;
-- 没有任何策略：只有 BYPASSRLS 的 starbuddy_authz（签字函数属主）能读写
