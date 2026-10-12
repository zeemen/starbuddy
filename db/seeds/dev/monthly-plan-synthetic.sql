-- 月度计划（specs/family-goals-monthly-plan 第 3 步，PLT-14；快照表与视图为迁移 0054 草稿）的合成种子：只给开发库与一次性测试库用，
-- 不由迁移执行，不能用于 pilot 或生产（CLAUDE.md 规则 8、9）。apps/web/scripts/e2e.sh 在一次性测试库里执行它（端到端 family-on 项目：
-- e2e/monthly-plan.spec.ts；真实洞察服务按这些输入生成快照）。
-- 前提：已执行全部迁移（含 0053、0054）、db/seeds/dev/shell-synthetic.sql（发布额度配置 dev-shell-plan@1）与
--       db/seeds/dev/star-map-v1-synthetic.sql（发布合成数学地图 0.9.0，本种子的掌握行引用它的节点）。
-- 用法（以迁移执行者身份；只能执行一次；文件自带事务）：
--   PGOPTIONS='-c starbuddy.allow_synthetic_seed=on' psql -X -v ON_ERROR_STOP=1 -f db/seeds/dev/monthly-plan-synthetic.sql
--
-- 内容（全部合成：没有姓名、学校；号码用未分配的国家码 +999；文字是明显占位）：
--   - 家庭 HM：家长 gM（+99900000111），孩子 M1 八年级（专业档 Pro，dev-shell-plan@1 的 weekly_report 含 monthly → 有月度计划权益）、
--     M2 八年级（基础档，没有月度计划权益）；两人在读数学，都有合成同意；
--   - 判断模板（合成占位，以迁移执行者身份模拟人工登记）：syn.math.node_mastery@1、syn.math.node_transfer@1（与洞察服务合成判断策略
--     config/claims.synthetic.json 同键同版本）、syn.exam.reconcile@1（考试判断）；e2e.sh 给 family-on 的 BFF 一份把它们列进对外白名单的
--     显示规则（只在这个一次性环境；正式的 config/shell.json 仍是空表，必停项）；
--   - M1 的节点键掌握行（以学习服务身份写，认知层次 understand，引用 star-map-v1 合成地图的节点）与对应的掌握判断（以洞察服务身份写，
--     值与洞察服务生成的同形：{level, gate}；对学生与家长都可见、来源 inferred）：
--       N041 execute、N042 understand、N043 stable → 三个“待点亮”的优先知识点；N044 transfer → 已进入迁移，不是候选；
--     一条开着的复测任务（N042，已到期 → 按月初计入本月复测）；
--   - M1 最近一次数学期中成绩 92.5 / 120（学生自录）与引用它的考试判断（对家长可见），以及 gM 的诉求（关注数学，数学目标 105 / 120）：
--     家长视图读时现算的分差 12.5 → 10–15 一档；
--   - 不写任何月度计划快照：家长打开“月度计划”页时 BFF 请洞察服务生成（同月只生成一次）。
--   evidence_ids 是随机生成、库里不存在的证据 ID（mastery_estimate 与 insight.claim 都只要求至少一个、不设外键），只为让判断成立，
--   不代表任何作答，不能当作掌握或证据链的样例。
BEGIN;

DO $$
BEGIN
  IF coalesce(current_setting('starbuddy.allow_synthetic_seed', true), '') <> 'on' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与一次性测试库：执行方须显式设置 starbuddy.allow_synthetic_seed=on';
  END IF;
  IF current_database() !~ '^starbuddy_(dev|test)(_[a-z0-9_]+)?$' THEN
    RAISE EXCEPTION '合成种子只能用于开发库与测试库（库名 starbuddy_dev* 或 starbuddy_test*），当前库：%', current_database();
  END IF;
  IF to_regclass('insight.monthly_plan') IS NULL OR to_regclass('insight.guardian_goal') IS NULL THEN
    RAISE EXCEPTION '先执行迁移 0053、0054（insight.guardian_goal、insight.monthly_plan）';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM platform.plan_config WHERE version = 'dev-shell-plan@1' AND status = 'published') THEN
    RAISE EXCEPTION '先执行 db/seeds/dev/shell-synthetic.sql（发布额度配置 dev-shell-plan@1）';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM kge.knowledge_node WHERE map_version_id = '5e000000-0000-4000-8000-0000000000e1' AND id = 'MATH.SV1B.T1.N044') THEN
    RAISE EXCEPTION '先执行 db/seeds/dev/star-map-v1-synthetic.sql（合成数学地图 0.9.0）';
  END IF;
  IF EXISTS (SELECT 1 FROM platform.household WHERE id = '57540000-0000-4000-8000-0000000000d1') THEN
    RAISE EXCEPTION '月度计划合成种子已经执行过（只能执行一次）';
  END IF;
END $$;

-- ---------- 判断模板（合成占位，以迁移执行者身份模拟人工登记；与洞察服务合成判断策略同键同版本，已有就不动） ----------
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('syn.math.node_mastery', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】{node_id}｜{level}｜{gate}', 'approved',
        'synthetic-seed', now()),
       ('syn.math.node_transfer', 1, 'node', '{agent,student,guardian}', '60 days', '【占位模板·未审核】迁移｜{node_id}｜{level}', 'approved',
        'synthetic-seed', now()),
       ('syn.exam.reconcile', 1, 'exam', '{agent,student,guardian}', '60 days', '【占位模板·未审核】对账｜{position}', 'approved',
        'synthetic-seed', now())
ON CONFLICT DO NOTHING;

-- ---------- 家庭 HM ----------
INSERT INTO platform.account (id, kind) VALUES
  ('57540000-0000-4000-8000-0000000000a1', 'guardian'),
  ('57540000-0000-4000-8000-0000000000b1', 'student'),
  ('57540000-0000-4000-8000-0000000000b2', 'student');
INSERT INTO platform.household (id, created_by_account) VALUES
  ('57540000-0000-4000-8000-0000000000d1', '57540000-0000-4000-8000-0000000000a1');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('57540000-0000-4000-8000-0000000000c1', '57540000-0000-4000-8000-0000000000b1', '57540000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'bubian-2024', '2013-02-01'),
  ('57540000-0000-4000-8000-0000000000c2', '57540000-0000-4000-8000-0000000000b2', '57540000-0000-4000-8000-0000000000d1', 8, 'nanjing', 'bubian-2024', '2013-03-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
SELECT '57540000-0000-4000-8000-0000000000a1', s, 'household_creator'
  FROM unnest(ARRAY['57540000-0000-4000-8000-0000000000c1', '57540000-0000-4000-8000-0000000000c2']::uuid[]) s;
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('57540000-0000-4000-8000-0000000000a1', '+99900000111', now());
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
SELECT s, '57540000-0000-4000-8000-0000000000a1', t, d, 'granted'
  FROM unnest(ARRAY['57540000-0000-4000-8000-0000000000c1', '57540000-0000-4000-8000-0000000000c2']::uuid[]) s,
       (VALUES ('privacy_policy', 'privacy-policy@synthetic-0'), ('guardian_consent_under14', 'guardian-consent@synthetic-0')) c (t, d);
INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES
  ('57540000-0000-4000-8000-0000000000c1', 'aya', 'active'),
  ('57540000-0000-4000-8000-0000000000c2', 'aya', 'active');

-- 订阅（直接写行，做法同 shell-synthetic.sql）：M1 专业档（有月度计划权益），M2 基础档（没有）
DO $$
DECLARE
  v_now timestamptz := platform.meter_now();
  v_id uuid;
BEGIN
  INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
  VALUES ('57540000-0000-4000-8000-0000000000d1', '57540000-0000-4000-8000-0000000000c1', 'pro', 'dev-shell-plan@1', 'pilot_virtual',
          v_now - interval '1 day', v_now + interval '29 days') RETURNING id INTO v_id;
  PERFORM platform.meter_grant_period(v_id);
  INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
  VALUES ('57540000-0000-4000-8000-0000000000d1', '57540000-0000-4000-8000-0000000000c2', 'basic', 'dev-shell-plan@1', 'pilot_virtual',
          v_now - interval '1 day', v_now + interval '29 days') RETURNING id INTO v_id;
  PERFORM platform.meter_grant_period(v_id);
END $$;

-- ---------- M1 的节点键掌握行与复测任务（以学习服务身份写；ID 固定，判断按它引用依据） ----------
SET LOCAL ROLE starbuddy_learning;
INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
                                       algorithm_version, evidence_ids)
SELECT ('57540000-0000-4000-8000-0000000001' || lpad(n::text, 2, '0'))::uuid, '57540000-0000-4000-8000-0000000000c1', 'aya',
       'MATH.SV1B.T1.N' || lpad(n::text, 3, '0'), 'understand', '5e000000-0000-4000-8000-0000000000e1', s::platform.mastery_state,
       0.5, 0.6, 'syn-mastery@0.1.0', ARRAY[gen_random_uuid()]
  FROM (VALUES (41, 'execute'), (42, 'understand'), (43, 'stable'), (44, 'transfer')) v (n, s);
INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at)
VALUES ('57540000-0000-4000-8000-0000000000c1', 'aya', 'MATH.SV1B.T1.N042', 'understand', 'decay', now() - interval '1 hour');
RESET ROLE;

-- ---------- M1 的掌握判断（以洞察服务身份写；0010 的守卫核对模板、可见范围、有效期与依据归属） ----------
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                           status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids)
SELECT ('57540000-0000-4000-8000-0000000002' || lpad(n::text, 2, '0'))::uuid, '57540000-0000-4000-8000-0000000000c1', 'aya', 'node',
       'MATH.SV1B.T1.N' || lpad(n::text, 3, '0'), k, 1, jsonb_build_object('level', 'understand', 'gate', g), 0.6, 'inferred',
       'established', 'created', '{agent,student,guardian}', now() + interval '30 days', 'mastery_estimate',
       ('57540000-0000-4000-8000-0000000001' || lpad(n::text, 2, '0'))::uuid, 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()]
  FROM (VALUES (41, 'syn.math.node_mastery', 'execute'), (42, 'syn.math.node_mastery', 'understand'), (43, 'syn.math.node_mastery', 'stable'),
               (44, 'syn.math.node_transfer', 'transfer')) v (n, k, g);
RESET ROLE;

-- ---------- 最近一次成绩、考试判断与家长诉求（分差 12.5 → 10–15 一档） ----------
INSERT INTO insight.exam_record (id, student_id, subject, exam_type, exam_date, full_score, raw_score, entered_by_role, entered_by_account_id, source_type)
VALUES ('57540000-0000-4000-8000-0000000003e1', '57540000-0000-4000-8000-0000000000c1', 'aya', 'midterm',
        (now() AT TIME ZONE 'Asia/Shanghai')::date - 3, 120, 92.5, 'self', '57540000-0000-4000-8000-0000000000b1', 'self');
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, evidence_ids,
                           source_type, model_version, status, change_reason, visibility, expires_at)
VALUES ('57540000-0000-4000-8000-0000000003c1', '57540000-0000-4000-8000-0000000000c1', 'aya', 'exam', '57540000-0000-4000-8000-0000000003e1',
        'syn.exam.reconcile', 1, '{"position": "below"}', 1, ARRAY[gen_random_uuid()], 'inferred', 'algo:sie-reconcile@0.1.0', 'hypothesis',
        'created', '{agent,student,guardian}', now() + interval '20 days');
INSERT INTO insight.guardian_goal (id, student_id, guardian_account_id, version, focus_subjects, target_scores, note)
VALUES ('57540000-0000-4000-8000-0000000003f1', '57540000-0000-4000-8000-0000000000c1', '57540000-0000-4000-8000-0000000000a1', 1, '{aya}',
        '[{"subject": "aya", "full_score": 120, "target_score": 105}]', '合成说明：只回显给作者');

COMMIT;
