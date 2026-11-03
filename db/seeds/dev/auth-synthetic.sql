-- 统一壳 H1（家庭登录与学生模式）的合成种子：只给开发库与一次性测试库用，不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- 前提：已执行全部迁移与 db/seeds/dev/star-map-synthetic.sql（家庭 HS：家长 gS，孩子 S1 八年级、S2 七年级）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/auth-synthetic.sql
-- 内容：
--   - gS 的登录号码 +99900000101（未分配的国家码 +999，不会是真实号码；短信捕获桩也只收 +999）；
--     gO +99900000102、gD +99900000105（H2 起端到端全程真实会话）；
--   - S1 已有隐私政策与监护人同意（合成文本版本 synthetic-0，只在 dev / test / staging 可用，待人确认 #13）；
--   - S2 没有任何同意：打开学生模式应提示先完成同意。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.household WHERE id = '5a000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '先执行 db/seeds/dev/star-map-synthetic.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.account_contact WHERE account_id = '5a000000-0000-4000-8000-0000000000a1') THEN
    RAISE EXCEPTION '登录合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('5a000000-0000-4000-8000-0000000000a1', '+99900000101', now());
-- 4-H H2（开发身份桩删除后，星图、建档、演示页的端到端都走真实会话）：无关家庭的家长 gO，
-- 以及数学诊断演示的家长 gD（db/seeds/dev/diagnosis-synthetic.sql 执行过时才有这个账号）
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at)
SELECT a.id, p.phone, now()
  FROM (VALUES ('5a000000-0000-4000-8000-0000000000a2'::uuid, '+99900000102'),
               ('5a000000-0000-4000-8000-0000000000a5'::uuid, '+99900000105')) p (id, phone)
  JOIN platform.account a ON a.id = p.id;
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision) VALUES
  ('5a000000-0000-4000-8000-0000000000c1', '5a000000-0000-4000-8000-0000000000a1', 'privacy_policy', 'privacy-policy@synthetic-0', 'granted'),
  ('5a000000-0000-4000-8000-0000000000c1', '5a000000-0000-4000-8000-0000000000a1', 'guardian_consent_under14', 'guardian-consent@synthetic-0', 'granted');

COMMIT;
