-- SIE-02b 第二步 PR 2 首份报告开发演示页的合成种子：只给开发库与一次性测试库用，不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 report-on 项目：e2e/first-report.spec.ts）。
-- 前提：已执行全部迁移（含 0010）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/first-report-synthetic.sql
--
-- 内容（全部合成，文本是明显占位，不是真实文案）：
--   - 判断模板 syn.report.node_mastery@1、syn.report.node_transfer@1（已审核，合成占位；以迁移执行者身份模拟人工登记）；
--   - 家庭 HR：家长 gR（合成号码 +99900000108），孩子 R1、R2（兄妹，九年级），隐私政策与监护人同意（合成文本版本）；
--   - R1 的四条知识节点判断（对学生、家长可见；来源 objective）：掌握·成立、掌握·假设、迁移·成立、掌握·待验证（先成立再转待验证），
--     以洞察服务身份写入（0010 的守卫照常核对）；R2 没有判断（演示页显示“还没有报告”）。
-- 端到端用一个合成洞察服务桩（apps/web/scripts/fake-insight.ts）按这四条判断返回契约形状的报告与解释；第四个 BFF 的显示规则
-- 把两个合成模板列进对外白名单（scripts/e2e.sh 生成，只在这个一次性环境）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '5c000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '首份报告合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('syn.report.node_mastery', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜知识掌握', 'approved',
        'synthetic-seed', now()),
       ('syn.report.node_transfer', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜知识迁移', 'approved',
        'synthetic-seed', now());

INSERT INTO platform.account (id, kind) VALUES
  ('5c000000-0000-4000-8000-0000000000a1', 'guardian'),
  ('5c000000-0000-4000-8000-0000000000b1', 'student'),
  ('5c000000-0000-4000-8000-0000000000b2', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('5c000000-0000-4000-8000-0000000000d1', '5c000000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version) VALUES
  ('5c000000-0000-4000-8000-0000000000c1', '5c000000-0000-4000-8000-0000000000b1', '5c000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024'),
  ('5c000000-0000-4000-8000-0000000000c2', '5c000000-0000-4000-8000-0000000000b2', '5c000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '5c000000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['5c000000-0000-4000-8000-0000000000c1', '5c000000-0000-4000-8000-0000000000c2']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('5c000000-0000-4000-8000-0000000000a1', '+99900000108', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '5c000000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['5c000000-0000-4000-8000-0000000000c1', '5c000000-0000-4000-8000-0000000000c2']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);

SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  ('5c000000-0000-4000-8000-000000000201', '5c000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.SYN.R1.N1',
   'syn.report.node_mastery', 1, '{}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5c000000-0000-4000-8000-000000000202', '5c000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.SYN.R1.N2',
   'syn.report.node_mastery', 1, '{}', 0.5, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5c000000-0000-4000-8000-000000000203', '5c000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.SYN.R1.N1',
   'syn.report.node_transfer', 1, '{}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('5c000000-0000-4000-8000-000000000204', '5c000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'MATH.SYN.R1.N3',
   'syn.report.node_mastery', 1, '{}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days');
UPDATE insight.claim SET status = 'to_verify', change_reason = 'expired' WHERE id = '5c000000-0000-4000-8000-000000000204';
RESET ROLE;

COMMIT;
