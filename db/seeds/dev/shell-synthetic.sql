-- 统一壳 H4（壳页面与读路由）的合成种子：只给开发库与一次性测试库用，不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。
-- apps/web/scripts/e2e.sh 在一次性测试库里执行它（审查后从 services/bff/scripts/ 挪到这里，请人审）。
-- 前提：已执行全部迁移、阿鸦内容登记（adapters/aya/scripts/register-content.ts 的 SQL）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/syn-publish-content.sql -f db/seeds/dev/shell-synthetic.sql
--
-- 内容（全部合成：没有姓名、学校；号码用未分配的国家码 +999）：
--   - 额度配置 dev-shell-plan@1：照抄迁移 0015 的草稿 v12-pilot@1（基本档核心任选 1 科、选修 0；专业档核心全开、选修 4），只在开发、测试库发布；
--   - 数学、语文各一份合成的已发布课程版本（先登记草稿，再以合成 staff 经 4-G 发布闸门的签字函数发布，迁移 0039）；
--   - 家庭 HG：家长 gH（+99900000106），孩子 GT 八年级（试用中）、GR 九年级（留存态：试用已结束）、GP 九年级（试点、基本档，在读数学）；
--     三个孩子都有隐私政策与监护人同意（合成文本版本 synthetic-0，待人确认 #13）。
-- 订阅直接写行（不经 meter_open_subscription：试用一次性领取、试点名单都绑在家庭与号码上，合成家庭不走那条路），额度经 meter_grant_period 发放。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF to_regprocedure('pg_temp.syn_publish_content(uuid)') IS NULL THEN
    RAISE EXCEPTION '先在同一会话里执行 db/seeds/dev/syn-publish-content.sql（定义合成发布函数）';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '5d000000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '统一壳合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

-- 额度配置（新规则），只在开发、测试库发布
INSERT INTO platform.plan_config (version, note_code) VALUES ('dev-shell-plan@1', 'dev_synthetic_seed');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
SELECT 'dev-shell-plan@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
SELECT 'dev-shell-plan@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'v12-pilot@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'dev-shell-plan@1';

-- 合成的已发布课程版本（没有已发布版本的学科一科也选不了）。4-G 迁移 0039 起：先登记草稿，再以合成 staff 经签字函数发布；
-- 数学有知识地图骨架，须指向已发布的地图版本：库里没有就建空的合成占位地图（两名合成 staff 签字发布），内容版本发布后即撤下占位地图
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version) VALUES
  ('5d000000-0000-4000-8000-0000000000e1', 'nanjing', 2027, 9, 'syn-shell', 'aya', '0.0.1'),
  ('5d000000-0000-4000-8000-0000000000e2', 'nanjing', 2027, 9, 'syn-shell', 'wenli', '0.0.1');
-- 函数由 db/seeds/dev/syn-publish-content.sql 定义（同一会话里先执行）；副作用：数学、语文在该库里“已上线”
SELECT pg_temp.syn_publish_content(v)
  FROM unnest(ARRAY['5d000000-0000-4000-8000-0000000000e1', '5d000000-0000-4000-8000-0000000000e2']::uuid[]) v;

-- 家庭、账号、档案、绑定
INSERT INTO platform.account (id, kind) VALUES
  ('5d000000-0000-4000-8000-0000000000a1', 'guardian'),
  ('5d000000-0000-4000-8000-0000000000b1', 'student'),
  ('5d000000-0000-4000-8000-0000000000b2', 'student'),
  ('5d000000-0000-4000-8000-0000000000b3', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('5d000000-0000-4000-8000-0000000000d1', '5d000000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version) VALUES
  ('5d000000-0000-4000-8000-0000000000c1', '5d000000-0000-4000-8000-0000000000b1', '5d000000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'suke-2024'),
  ('5d000000-0000-4000-8000-0000000000c2', '5d000000-0000-4000-8000-0000000000b2', '5d000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024'),
  ('5d000000-0000-4000-8000-0000000000c3', '5d000000-0000-4000-8000-0000000000b3', '5d000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'suke-2024');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '5d000000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['5d000000-0000-4000-8000-0000000000c1', '5d000000-0000-4000-8000-0000000000c2', '5d000000-0000-4000-8000-0000000000c3']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('5d000000-0000-4000-8000-0000000000a1', '+99900000106', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '5d000000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['5d000000-0000-4000-8000-0000000000c1', '5d000000-0000-4000-8000-0000000000c2', '5d000000-0000-4000-8000-0000000000c3']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);

-- 订阅：GT 试用（基本档，14 天），GP 试点（基本档，30 天），GR 试用已结束（留存态）
DO $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_id uuid;
BEGIN
  INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
  VALUES ('5d000000-0000-4000-8000-0000000000d1', '5d000000-0000-4000-8000-0000000000c1', 'basic', 'dev-shell-plan@1', 'trial',
          v_now - interval '1 day', v_now + interval '13 days') RETURNING id INTO v_id;
  PERFORM platform.meter_grant_period(v_id);
  INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
  VALUES ('5d000000-0000-4000-8000-0000000000d1', '5d000000-0000-4000-8000-0000000000c3', 'basic', 'dev-shell-plan@1', 'pilot_virtual',
          v_now - interval '1 day', v_now + interval '29 days') RETURNING id INTO v_id;
  PERFORM platform.meter_grant_period(v_id);
  INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
  VALUES ('5d000000-0000-4000-8000-0000000000d1', '5d000000-0000-4000-8000-0000000000c2', 'basic', 'dev-shell-plan@1', 'trial',
          v_now - interval '20 days', v_now - interval '6 days') RETURNING id INTO v_id;
  UPDATE platform.subscription SET status = 'ended' WHERE id = v_id;
END $$;

INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('5d000000-0000-4000-8000-0000000000c3', 'aya', 'active');

COMMIT;
