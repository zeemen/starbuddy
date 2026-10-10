-- 家长诉求（specs/family-goals-monthly-plan 第 1 步，PLT-14；迁移 0053）的合成种子：只给开发库与一次性测试库用，不由迁移执行，
-- 不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 family-on 项目：e2e/family-goals.spec.ts）。
-- 前提：已执行全部迁移（含 0053）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/family-goals-synthetic.sql
--
-- 内容（全部合成）：
--   - 家庭 HG2：家长 gG（合成号码 +99900000110），孩子 G9（初三，在读数学、语文）、G8（初二，在读数学）；隐私政策与监护人同意（合成文本版本）；
--   - 招录库：一个南京编码的合成来源主机名、一份已发布的合成来源文件、两所已复核发布的合成学校（在办）。
--     学校与来源文件的发布本该经两名 staff 签字（0041）；这里只为端到端的“可选学校”名单，以迁移执行者身份关闭触发器直写
--     （签字发布流程由 db/tests/77、79v、79zb 覆盖）。名称都是明显的合成名，不是任何真实学校。
--   不写任何诉求：家长在页面上自己填。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF to_regclass('insight.guardian_goal') IS NULL THEN
    RAISE EXCEPTION '先执行迁移 0053（insight.guardian_goal）';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '57530000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '家长诉求合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO platform.account (id, kind) VALUES
  ('57530000-0000-4000-8000-0000000000a1', 'guardian'),
  ('57530000-0000-4000-8000-0000000000b9', 'student'),
  ('57530000-0000-4000-8000-0000000000b8', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('57530000-0000-4000-8000-0000000000d1', '57530000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('57530000-0000-4000-8000-0000000000c9', '57530000-0000-4000-8000-0000000000b9', '57530000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'bubian-2024', '2012-02-01'),
  ('57530000-0000-4000-8000-0000000000c8', '57530000-0000-4000-8000-0000000000b8', '57530000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'bubian-2024', '2013-02-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '57530000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['57530000-0000-4000-8000-0000000000c9', '57530000-0000-4000-8000-0000000000c8']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('57530000-0000-4000-8000-0000000000a1', '+99900000110', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '57530000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['57530000-0000-4000-8000-0000000000c9', '57530000-0000-4000-8000-0000000000c8']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('57530000-0000-4000-8000-0000000000c9', 'aya', 'active'),
  ('57530000-0000-4000-8000-0000000000c9', 'wenli', 'active'),
  ('57530000-0000-4000-8000-0000000000c8', 'aya', 'active');

-- 招录库（只在本事务里关闭触发器；见文件头说明）
SET LOCAL session_replication_role = replica;
INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.nanjing-e2e.example', '合成教育局（端到端）', 'synthetic-seed');
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on,
                                   status, reviewers, reviewed_at)
VALUES ('57530000-0000-4000-8000-0000000005d1', 'nanjing', '合成教育局（端到端）', '合成〔2026〕E2E号', '合成学校名单（非真实数据）', '2026-05-01',
        'https://jyj.nanjing-e2e.example/doc/e2e', 2026, '2026-09-01', 'published', '{syn-seed-a,syn-seed-b}', now());
INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id, review_status, reviewers, reviewed_at) VALUES
  ('57530000-0000-4000-8000-0000000005c1', 'nanjing', 'synth-e2e-hs-1', '合成高中甲（端到端，非真实）', 'level_a', '57530000-0000-4000-8000-0000000005d1',
   'published', '{syn-seed-a,syn-seed-b}', now()),
  ('57530000-0000-4000-8000-0000000005c2', 'nanjing', 'synth-e2e-hs-2', '合成高中乙（端到端，非真实）', 'level_a', '57530000-0000-4000-8000-0000000005d1',
   'published', '{syn-seed-a,syn-seed-b}', now());
SET LOCAL session_replication_role = origin;

COMMIT;
