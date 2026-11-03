-- 数学自适应诊断演示页的合成种子（specs/sie-02a-math-diagnosis/spec.md 设计要点 5）。只给开发库与一次性测试库用，不由迁移执行，
-- 不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。全部是合成数据：没有姓名、学校、手机号。
-- 只进一次性开发 / 测试库（库名 starbuddy_dev* / starbuddy_test*，用完即删的实例）：它会发布一份开发用额度配置、开通试点会员，
-- 不要在长期共用的开发库里执行（契约审查建议 5）。
-- 借号做法是临时的：诊断的合成题面借用阿鸦内容包里已登记的题号（只作验证器判分的槽位，节点在 MATH.SYN.* 合成命名空间）。
-- 插件 API present_item 签字、真实题库接入后，删掉借号的合成题面与本种子里对阿鸦题目登记的前置检查（lessons 待人确认 #1）。
--
-- 前提：已执行全部迁移与阿鸦内容登记（node adapters/aya/scripts/register-content.ts 的 SQL）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务，不要再加 -1）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/diagnosis-synthetic.sql
--
-- 内容：
--   家庭 HD（合成）：家长 gD，九年级学生 D1（数学诊断的试点目标只有九年级二次函数主题）。家庭进试点名单，家长经函数开通试点会员
--   （与生产路径相同），D1 因此有完整诊断的额度。
--   开发库里没有已发布的额度配置时，把迁移 0015 的 v12-pilot@1 复制成 dev-synthetic-plan@1 并发布（只在开发、测试库）。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '5a000000-0000-4000-8000-0000000000d5') THEN
    RAISE EXCEPTION '诊断合成种子已经执行过（只能执行一次）';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.item WHERE subject = 'aya' AND item_id = 'Q-FUN-U01-T-002') THEN
    RAISE EXCEPTION '没有登记阿鸦题目：先执行 adapters/aya/scripts/register-content.ts 生成的 SQL';
  END IF;
END $$;

-- 额度配置：开发、测试库里没有已发布的配置时，复制 v12-pilot@1 发布一份（生产配置由人按规格书发布，不走这里）
INSERT INTO platform.plan_config (version, note_code)
SELECT 'dev-synthetic-plan@1', 'dev_synthetic_seed' WHERE NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE status = 'published');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'dev-synthetic-plan@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance
 WHERE config_version = 'v12-pilot@1' AND EXISTS (SELECT 1 FROM platform.plan_config WHERE version = 'dev-synthetic-plan@1' AND status <> 'published');
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'dev-synthetic-plan@1', tier, feature_code, value_code FROM platform.plan_feature
 WHERE config_version = 'v12-pilot@1' AND EXISTS (SELECT 1 FROM platform.plan_config WHERE version = 'dev-synthetic-plan@1' AND status <> 'published');
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'dev-synthetic-plan@1' AND status <> 'published';

-- 账号、家庭、学生、绑定、选科
INSERT INTO platform.account (id, kind) VALUES
  ('5a000000-0000-4000-8000-0000000000a5', 'guardian'),   -- gD：HD 的家长
  ('5a000000-0000-4000-8000-0000000000b5', 'student');    -- D1 的账号
INSERT INTO platform.household (id, created_by_account) VALUES
  ('5a000000-0000-4000-8000-0000000000d5', '5a000000-0000-4000-8000-0000000000a5');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version) VALUES
  ('5a000000-0000-4000-8000-0000000000c5', '5a000000-0000-4000-8000-0000000000b5', '5a000000-0000-4000-8000-0000000000d5', 9, 'nanjing', 'suke-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('5a000000-0000-4000-8000-0000000000a5', '5a000000-0000-4000-8000-0000000000c5', 'household_creator');
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('5a000000-0000-4000-8000-0000000000c5', 'aya', 'active');
INSERT INTO platform.pilot_household (household_id, cohort_code) VALUES ('5a000000-0000-4000-8000-0000000000d5', 'dev_synthetic');

-- 家长经函数开通试点会员（与生产路径相同：只认已绑定的家长）
SELECT set_config('app.account_id', '5a000000-0000-4000-8000-0000000000a5', true);
DO $$
DECLARE r jsonb := platform.meter_open_subscription('5a000000-0000-4000-8000-0000000000c5', 'pilot_virtual');
BEGIN
  IF r ->> 'outcome' <> 'opened' THEN
    RAISE EXCEPTION '合成试点会员没有开通：%', r;
  END IF;
END $$;
SELECT set_config('app.account_id', '', true);

COMMIT;
