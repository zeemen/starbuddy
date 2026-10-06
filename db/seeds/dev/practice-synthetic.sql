-- 错题再练学生端页面（specs/practice-loop-stage1 PR 4）的合成种子：只给开发库与一次性测试库用，不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 practice-on 项目：e2e/practice.spec.ts；开关关的核对 e2e/practice-off.spec.ts）。
-- 前提：已执行全部迁移；在 db/seeds/dev/shell-synthetic.sql 之后执行（复用它发布的合成套餐配置 dev-shell-plan@1）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/practice-synthetic.sql
--
-- 内容（全部合成）：
--   - 家庭 HP：家长 gP（合成号码 +99900000109），孩子 P1、P2、P3（九年级），隐私政策与监护人同意（合成文本版本）；
--   - P1：试用中（题库自练不限次），走完错 → 取题 → 作答 → 闭环；
--   - P2：试用中，家长设的宵禁 09:00–06:00（覆盖缺省的 22:00–06:00；端到端的计量时钟从北京时间 10:00 起走，所以一直在宵禁里），
--     取题与作答 402 curfew；
--   - P3：没有任何订阅（meter_state = none），作答 402 no_entitlement。
-- 再练条目、题面与判分来自合成学习服务桩（apps/web/scripts/fake-learning-practice.ts），本种子不写任何学习记录（作答、证据、掌握）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE version = 'dev-shell-plan@1' AND status = 'published') THEN
    RAISE EXCEPTION '先执行 db/seeds/dev/shell-synthetic.sql（合成套餐配置 dev-shell-plan@1）';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '59000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '错题再练合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

INSERT INTO platform.account (id, kind) VALUES
  ('59000000-0000-4000-8000-0000000000a1', 'guardian'),
  ('59000000-0000-4000-8000-0000000000b1', 'student'),
  ('59000000-0000-4000-8000-0000000000b2', 'student'),
  ('59000000-0000-4000-8000-0000000000b3', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('59000000-0000-4000-8000-0000000000d1', '59000000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version) VALUES
  ('59000000-0000-4000-8000-0000000000c1', '59000000-0000-4000-8000-0000000000b1', '59000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024'),
  ('59000000-0000-4000-8000-0000000000c2', '59000000-0000-4000-8000-0000000000b2', '59000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024'),
  ('59000000-0000-4000-8000-0000000000c3', '59000000-0000-4000-8000-0000000000b3', '59000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '59000000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['59000000-0000-4000-8000-0000000000c1', '59000000-0000-4000-8000-0000000000c2',
                    '59000000-0000-4000-8000-0000000000c3']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('59000000-0000-4000-8000-0000000000a1', '+99900000109', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '59000000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['59000000-0000-4000-8000-0000000000c1', '59000000-0000-4000-8000-0000000000c2',
                    '59000000-0000-4000-8000-0000000000c3']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);

DO $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_id uuid;
  v_s uuid;
BEGIN
  FOREACH v_s IN ARRAY ARRAY['59000000-0000-4000-8000-0000000000c1', '59000000-0000-4000-8000-0000000000c2']::uuid[] LOOP
    INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
    VALUES ('59000000-0000-4000-8000-0000000000d1', v_s, 'basic', 'dev-shell-plan@1', 'trial', v_now - interval '1 day', v_now + interval '13 days')
    RETURNING id INTO v_id;
    PERFORM platform.meter_grant_period(v_id);
  END LOOP;
END $$;

-- P2 的宵禁：直接写表（以迁移执行者身份，模拟家长经 platform.set_time_guard 设定；起止覆盖缺省 22:00–06:00，与该函数的规则一致）
INSERT INTO platform.student_time_guard (student_id, daily_cap_minutes, curfew_start, curfew_end, updated_at, updated_by)
VALUES ('59000000-0000-4000-8000-0000000000c2', NULL, time '09:00', time '06:00', now(), '59000000-0000-4000-8000-0000000000a1');

INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('59000000-0000-4000-8000-0000000000c1', 'aya', 'active'),
  ('59000000-0000-4000-8000-0000000000c2', 'aya', 'active'),
  ('59000000-0000-4000-8000-0000000000c3', 'aya', 'active');

COMMIT;
