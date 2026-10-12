-- 0054 月度计划快照（specs/family-goals-monthly-plan/step2-spec.md，PLT-14 第 2 步）：权益（weekly_report 含 monthly）、存储、写入守卫
-- （逐项核对引用的判断；上月点亮按 claim_history 与上月快照）、同一学生同一月份只一份（已有快照先返回，再查权益）、只追加、
-- 按受众的视图（学生只读学习计划；家长读三段、分差只给本人当前版诉求对应的项、5 分一档的区间）、洞察服务的输入与重放函数
-- （只读当前有效绑定家长的当前版诉求，不读补充说明）、个人层表清单。全部合成数据（规则 8）；自建两户，不依赖前面测试对 S1、S2 的改动。
-- 用 79m 的 t.m_as(角色, 账号, SQL)。整段在一个事务里，结束回滚（快照表对 BFF 没有表权限，不进 80 的可见性矩阵）。
\set G1 '''9a540000-0000-4000-8000-000000000001'''
\set G2 '''9a540000-0000-4000-8000-000000000002'''
\set GR '''9a540000-0000-4000-8000-000000000003'''
\set GO '''9a540000-0000-4000-8000-000000000004'''
\set AX '''9a540000-0000-4000-8000-000000000011'''
\set AY '''9a540000-0000-4000-8000-000000000012'''
\set AZ '''9a540000-0000-4000-8000-000000000013'''
\set H1 '''9a540000-0000-4000-8000-0000000000a1'''
\set H2 '''9a540000-0000-4000-8000-0000000000a2'''
\set X '''9a540000-0000-4000-8000-000000000c01'''
\set Y '''9a540000-0000-4000-8000-000000000c02'''
\set Z '''9a540000-0000-4000-8000-000000000c03'''
\set REC '''9a540000-0000-4000-8000-0000000000e1'''
\set RECOLD '''9a540000-0000-4000-8000-0000000000e2'''
\set GG1 '''9a540000-0000-4000-8000-0000000000f1'''
\set GG1OLD '''9a540000-0000-4000-8000-0000000000f0'''
\set GG2 '''9a540000-0000-4000-8000-0000000000f2'''
\set GGR '''9a540000-0000-4000-8000-0000000000f3'''
\set K1 '''9a540000-0000-4000-8000-00000000d001'''
\set K2 '''9a540000-0000-4000-8000-00000000d002'''
\set K3 '''9a540000-0000-4000-8000-00000000d003'''
\set K4 '''9a540000-0000-4000-8000-00000000d004'''
\set K5 '''9a540000-0000-4000-8000-00000000d005'''
\set K6 '''9a540000-0000-4000-8000-00000000d006'''
\set K7 '''9a540000-0000-4000-8000-00000000d007'''
\set K8 '''9a540000-0000-4000-8000-00000000d008'''
\set K9 '''9a540000-0000-4000-8000-00000000d009'''
\set K10 '''9a540000-0000-4000-8000-00000000d010'''
\set KE '''9a540000-0000-4000-8000-00000000d0e1'''
\set KEH '''9a540000-0000-4000-8000-00000000d0e2'''
\set KEO '''9a540000-0000-4000-8000-00000000d0e3'''

BEGIN;
-- ================= 夹具 =================
-- 户一：家长 G1（创建者）、G2（第二位家长）、GR（已解除绑定）；学生 X（专业档）、Y（基础档）。户二：家长 GO、学生 Z（没有订阅）
INSERT INTO platform.account (id, kind) VALUES (:G1, 'guardian'), (:G2, 'guardian'), (:GR, 'guardian'), (:GO, 'guardian'),
  (:AX, 'student'), (:AY, 'student'), (:AZ, 'student');
INSERT INTO platform.household (id, created_by_account) VALUES (:H1, :G1), (:H2, :GO);
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  (:X, :AX, :H1, 8, 'nanjing', 'bubian-2024', '2013-02-01'), (:Y, :AY, :H1, 8, 'nanjing', 'bubian-2024', '2013-03-01'),
  (:Z, :AZ, :H2, 8, 'nanjing', 'bubian-2024', '2013-04-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:G1, :X, 'household_creator'), (:G1, :Y, 'household_creator'), (:G2, :X, 'invite'), (:GR, :X, 'invite'), (:GO, :Z, 'household_creator');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :GR;
-- 已发布的测试配置：照抄草稿 v12-pilot@1（weekly_report：基础 basic、Plus full、Pro 与 Max full_and_monthly）
INSERT INTO platform.plan_config (version, note_code) VALUES ('monthly-dbtest@1', 'monthly_plan_dbtest');
INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
  SELECT 'monthly-dbtest@1', tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1';
INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
  SELECT 'monthly-dbtest@1', tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'v12-pilot@1';
UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = 'monthly-dbtest@1';
INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end) VALUES
  (:H1, :X, 'pro', 'monthly-dbtest@1', 'pilot_virtual', platform.meter_now() - interval '1 day', platform.meter_now() + interval '29 days'),
  (:H1, :Y, 'basic', 'monthly-dbtest@1', 'pilot_virtual', platform.meter_now() - interval '1 day', platform.meter_now() + interval '29 days');
-- 诉求：G1 第 1 版（已替换）与第 2 版（当前），G2 当前版，GR（已解除绑定）当前版；补充说明只回显给作者，不得进入任何输入
INSERT INTO insight.guardian_goal (id, student_id, guardian_account_id, version, focus_subjects, target_scores, note, superseded_at) VALUES
  (:GG1OLD, :X, :G1, 1, '{wenli}', '[{"subject": "aya", "full_score": 120, "target_score": 100}]', '合成说明：旧版', now()),
  (:GG1, :X, :G1, 2, '{aya}', '[{"subject": "aya", "full_score": 120, "target_score": 105}]', '合成说明：当前版', NULL),
  (:GG2, :X, :G2, 1, '{}', '[{"subject": "aya", "full_score": 120, "target_score": 110}]', NULL, NULL),
  (:GGR, :X, :GR, 1, '{wenli}', '[{"subject": "aya", "full_score": 120, "target_score": 95}]', NULL, NULL);
-- 成绩：X 数学当前版 92.5 / 120，另有一条已替换的旧版
INSERT INTO insight.exam_record (id, student_id, subject, exam_type, exam_date, full_score, raw_score, entered_by_role, entered_by_account_id,
                                 source_type, superseded_at) VALUES
  (:RECOLD, :X, 'aya', 'midterm', '2026-09-20', 120, 90, 'self', :AX, 'self', now()),
  (:REC, :X, 'aya', 'midterm', '2026-09-20', 120, 92.5, 'self', :AX, 'self', NULL);
-- 复测任务（学习服务写入；这里以复制模式直接写，只为造状态）：本月到期 1 条、逾期 1 条、上月完成 1 条、下月到期 1 条
SET LOCAL session_replication_role = replica;
INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at, status, closed_at, closing_attempt_id) VALUES
  (:X, 'aya', 'MATH.SYN.MPDB.A', 'apply', 'decay', date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' + interval '5 days',
   'open', NULL, NULL),
  (:X, 'aya', 'MATH.SYN.MPDB.B', 'apply', 'decay', date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' - interval '3 days',
   'open', NULL, NULL),
  (:X, 'aya', 'MATH.SYN.MPDB.C', 'know', 'hint_followup', date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' - interval '12 days',
   'failed', date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' - interval '10 days', gen_random_uuid()),
  (:X, 'aya', 'MATH.SYN.MPDB.D', 'apply', 'decay', date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' + interval '40 days',
   'open', NULL, NULL);
SET LOCAL session_replication_role = origin;
-- 判断：模板（合成占位，模拟人工登记）；洞察服务写节点与考试判断
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.monthly.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：月度计划节点', 'approved', 'test-reviewer', now()),
  ('test.monthly.exam', 1, 'exam', '{agent,student,guardian}', '60 days', '合成模板：月度计划考试', 'approved', 'test-reviewer', now());
SELECT t.m_as('starbuddy_insight', NULL, format($f$
  INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                             status, change_reason, visibility, expires_at) VALUES
    (%1$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.A', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%2$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.B', 'test.monthly.node', 1, '{"level": "apply", "gate": "stable"}', 0.6, 'objective', 'hypothesis', 'created', '{agent,student}', now() + interval '30 days'),
    (%4$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.D', 'test.monthly.node', 1, '{"level": "apply", "gate": "transfer"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%5$L, %10$L, 'aya', 'node', 'MATH.SYN.MPDB.A', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%6$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.E', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%8$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.G', 'test.monthly.node', 1, '{"level": "apply", "gate": "understand"}', 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%16$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.H', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'objective', 'hypothesis', 'created', '{agent,guardian}', now() + interval '30 days'),
    (%17$L, %9$L, 'aya', 'node', 'MATH.SYN.MPDB.J', 'test.monthly.node', 1, '{"level": "apply", "gate": "transfer"}', 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%11$L, %9$L, 'aya', 'exam', %14$L, 'test.monthly.exam', 1, '{"position": "below"}', 1, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
    (%12$L, %9$L, 'aya', 'exam', %14$L, 'test.monthly.exam', 1, '{"position": "below"}', 1, 'objective', 'hypothesis', 'created', '{agent,student}', now() + interval '30 days'),
    (%13$L, %9$L, 'aya', 'exam', %15$L, 'test.monthly.exam', 1, '{"position": "below"}', 1, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days')
  RETURNING 'ok'$f$, :K1, :K2, NULL, :K4, :K5, :K6, NULL, :K8, :X, :Y, :KE, :KEH, :KEO, :REC, :RECOLD, :K9, :K10));
SELECT t.m_as('starbuddy_insight', NULL, format($f$UPDATE insight.claim SET status = 'to_verify', change_reason = 'exam_deviation' WHERE id = %L RETURNING 'ok'$f$, :K6));
-- 家长观察来源、已过期的判断：以复制模式直接写（只为造出这些状态）；
-- K4（节点 D）按历史在上个月进入“迁移”，K10（节点 J）在本月才进入：改历史的时间（只为造出“过去”的状态）
SET LOCAL session_replication_role = replica;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence, source_type,
                           status, change_reason, visibility, expires_at, created_at, updated_at, evidence_ids) VALUES
  (:K3, :X, 'aya', 'node', 'MATH.SYN.MPDB.C', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'guardian', 'established', 'created',
   '{agent,student,guardian}', now() + interval '30 days', now(), now(), '{}'),
  (:K7, :X, 'aya', 'node', 'MATH.SYN.MPDB.F', 'test.monthly.node', 1, '{"level": "apply", "gate": "execute"}', 0.6, 'objective', 'established', 'created',
   '{agent,student,guardian}', now() - interval '1 hour', now() - interval '2 days', now() - interval '2 days', '{}');
UPDATE insight.claim_history SET at = (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') - interval '10 days') AT TIME ZONE 'Asia/Shanghai'
 WHERE claim_id = :K4;
SET LOCAL session_replication_role = origin;

-- 合成的月度计划（与 insight/monthly.py 的输出同形状）
CREATE FUNCTION t.mp_month() RETURNS date LANGUAGE sql STABLE AS $$ SELECT date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date $$;
CREATE FUNCTION t.mp_node(p_pos integer, p_node text, p_claim uuid, p_level text DEFAULT 'apply', p_version integer DEFAULT 1) RETURNS jsonb
  LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('position', p_pos, 'subject', 'aya', 'node_id', p_node, 'cognitive_level', p_level, 'claim_id', p_claim,
                            'template_key', 'test.monthly.node', 'template_version', p_version, 'copy_key', 'monthly_plan.student.priority_node') $$;
CREATE FUNCTION t.mp_gap(p_goal uuid, p_claim uuid, p_rec uuid) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('subject', 'aya', 'goal_id', p_goal, 'exam_record_id', p_rec, 'claim_id', p_claim,
                            'template_key', 'test.monthly.exam', 'template_version', 1, 'copy_key', 'monthly_plan.review.score_gap') $$;
CREATE FUNCTION t.mp_lit(p_node text, p_claim uuid) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('subject', 'aya', 'node_id', p_node, 'claim_id', p_claim, 'template_key', 'test.monthly.node', 'template_version', 1,
                            'copy_key', 'monthly_plan.review.lit') $$;
CREATE FUNCTION t.mp_last(p_lit jsonb, p_month date DEFAULT (t.mp_month() - interval '1 month')::date, p_planned integer DEFAULT 2) RETURNS jsonb
  LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('month', p_month, 'planned_nodes', p_planned, 'lit', p_lit, 'copy_key', 'monthly_plan.review.last_month') $$;
-- 输入引用：缺省把所引判断都登记进去（p_claims 给了就用它）
CREATE FUNCTION t.mp_refs(p_nodes jsonb, p_review jsonb, p_claims text[] DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'as_of', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'claims', (SELECT coalesce(jsonb_agg(jsonb_build_array(c, '00000000-0000-4000-8000-0000000000aa') ORDER BY c), '[]'::jsonb)
                 FROM (SELECT DISTINCT c FROM unnest(coalesce(p_claims, ARRAY(
                         SELECT e ->> 'claim_id' FROM jsonb_array_elements(p_nodes || (p_review -> 'score_gaps')
                                                                            || coalesce(p_review #> '{last_month,lit}', '[]')) e))) c) x),
    'mastery', '[]'::jsonb, 'exam_records', '[]'::jsonb, 'exam_plans', '[]'::jsonb, 'goals', '[]'::jsonb, 'retest_tasks', '[]'::jsonb,
    'prev_plan_id', NULL::text,
    'fingerprints', jsonb_build_object('claims_policy', repeat('1', 16), 'config', repeat('2', 32), 'templates', repeat('3', 32),
                                       'today', repeat('4', 32))) $$;
CREATE FUNCTION t.mp_plan(p_nodes jsonb DEFAULT '[]', p_review jsonb DEFAULT '{"last_month": null, "score_gaps": []}',
                          p_notes jsonb DEFAULT '[{"key": "weekly_rhythm_support", "copy_key": "monthly_plan.guardian.weekly_rhythm_support", "vars": {"sessions_per_week": 5, "minutes_per_session": 30}}]',
                          p_retest_day date DEFAULT t.mp_month() + 5, p_claims text[] DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'student_plan', jsonb_build_object(
      'priority_nodes', p_nodes,
      'weekly_rhythm', '{"sessions_per_week": 5, "minutes_per_session": 30, "copy_key": "monthly_plan.student.weekly_rhythm"}'::jsonb,
      'retests', jsonb_build_object('count', 1, 'dates', jsonb_build_array(p_retest_day), 'by_subject', '[{"subject": "aya", "count": 1}]'::jsonb,
                                    'copy_key', 'monthly_plan.student.retests'),
      'mini_reviews', jsonb_build_array(jsonb_build_object('subject', 'aya', 'date', t.mp_month() + 23, 'copy_key', 'monthly_plan.student.mini_review'))),
    'guardian_notes', p_notes, 'review', p_review, 'plan_basis', '{"items": []}'::jsonb, 'input_refs', t.mp_refs(p_nodes, p_review, p_claims),
    'algorithm_version', 'sie-monthly@0.1.0', 'config_version', 'syn-0.1.0_syn-0.1.0', 'template_version', 'syn-tpl-0.1.0',
    'policy_weights', 'default', 'inputs_digest', repeat('a', 64)) $$;
CREATE FUNCTION t.mp_save(p_student uuid, p_plan jsonb, p_month date DEFAULT t.mp_month()) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format('SELECT insight.save_monthly_plan(%L, %L, %L)::text', p_student, p_month, p_plan))::jsonb $$;
CREATE FUNCTION t.mp_save_sql(p_student uuid, p_plan jsonb, p_month date DEFAULT t.mp_month()) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT format('SELECT t.mp_save(%L, %L, %L)', p_student, p_plan, p_month) $$;
CREATE FUNCTION t.mp_gview(p_acct uuid, p_student uuid) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT insight.monthly_plan_guardian_view(%L, %L)::text', p_student, t.mp_month()))::jsonb $$;
CREATE FUNCTION t.mp_sview(p_acct uuid, p_student uuid) RETURNS jsonb LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT insight.monthly_plan_student_view(%L, %L)::text', p_student, t.mp_month()))::jsonb $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA t TO PUBLIC;
-- 上月快照（计划里有节点 D 与 A）：只收本月的守卫挡着，以复制模式直接写（只为造出“过去”的状态）
SET LOCAL session_replication_role = replica;
INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version, config_version,
                                  template_version, policy_weights, inputs_digest)
SELECT :X, (t.mp_month() - interval '1 month')::date, p -> 'student_plan', p -> 'guardian_notes', p -> 'review', p -> 'plan_basis', p -> 'input_refs',
       'sie-monthly@0.1.0', 'syn', 'syn', 'default', repeat('b', 64)
  FROM t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.D', :K4), t.mp_node(2, 'MATH.SYN.MPDB.A', :K1)),
                 p_retest_day => (t.mp_month() - interval '1 month')::date + 5) p;
UPDATE insight.monthly_plan SET student_plan = jsonb_set(student_plan, '{mini_reviews}', '[]') WHERE student_id = :X;
SET LOCAL session_replication_role = origin;

-- ================= 一、权限与清单 =================
SELECT t.ok((SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = 'insight.monthly_plan'::regclass)
            AND NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'insight' AND tablename = 'monthly_plan'),
            '月度计划表 ENABLE + FORCE RLS 且没有任何策略');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review',
                                                   'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) r
                         WHERE has_any_column_privilege(r, 'insight.monthly_plan', 'SELECT') OR has_any_column_privilege(r, 'insight.monthly_plan', 'INSERT')
                            OR has_any_column_privilege(r, 'insight.monthly_plan', 'UPDATE') OR has_table_privilege(r, 'insight.monthly_plan', 'DELETE')
                            OR has_table_privilege(r, 'insight.monthly_plan', 'TRUNCATE'))
            AND has_table_privilege('starbuddy_eraser', 'insight.monthly_plan', 'DELETE')
            AND NOT has_column_privilege('starbuddy_eraser', 'insight.monthly_plan', 'student_plan', 'SELECT'),
            '应用角色（含洞察服务与 BFF）对月度计划表都没有表权限；删除执行身份只删、只读学生键');
SELECT t.ok((SELECT array_agg(r ORDER BY r) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review',
                                                              'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_report']) r
              WHERE has_function_privilege(r, 'insight.save_monthly_plan(uuid, date, jsonb)', 'EXECUTE')
                 OR has_function_privilege(r, 'insight.monthly_plan_facts(uuid, date)', 'EXECUTE')
                 OR has_function_privilege(r, 'insight.monthly_plan_read(uuid, date)', 'EXECUTE')
                 OR has_function_privilege(r, 'insight.monthly_plan_replay_inputs(uuid, date)', 'EXECUTE')) = '{starbuddy_insight}'
            AND (SELECT array_agg(r ORDER BY r) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review',
                                                                  'starbuddy_scheduler', 'starbuddy_kge', 'starbuddy_report']) r
                  WHERE has_function_privilege(r, 'insight.monthly_plan_guardian_view(uuid, date)', 'EXECUTE')
                     OR has_function_privilege(r, 'insight.monthly_plan_student_view(uuid, date)', 'EXECUTE')) = '{starbuddy_bff}'
            AND NOT has_function_privilege('starbuddy_insight', 'insight.monthly_plan_entitled(uuid)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_bff', 'insight.monthly_plan_entitled(uuid)', 'EXECUTE'),
            '写入、输入、读快照与重放四个函数只授洞察服务；两个视图函数只授 BFF；权益判断只给函数属主');
SELECT t.ok(EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'monthly_plan'
                     AND strategy = 'delete' AND erase_scope = 'reports' AND export_topic IS NULL)
            AND t.p_unregistered() = '',
            '月度计划表登记进个人层表清单：随删除请求删除（reports），不进导出包');

-- ================= 二、权益：不另设开关，weekly_report 的取值含 monthly 即有 =================
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.plan_feature WHERE feature_code NOT IN ('max_active_subjects', 'lesson_mode', 'weekly_report',
                                                                                        'appeal_priority', 'core_subjects', 'elective_slots',
                                                                                        'daily_minutes_cap')),
            '0054 没有新增权益代码（不另设套餐开关）');
SELECT t.ok(insight.monthly_plan_entitled(:X) AND NOT insight.monthly_plan_entitled(:Y) AND NOT insight.monthly_plan_entitled(:Z)
            AND NOT insight.monthly_plan_entitled(gen_random_uuid()),
            '权益按当前主订阅取：专业档（full_and_monthly）有，基础档（basic）与没有订阅的没有');
SELECT t.ok((SELECT string_agg(tier::text, ',' ORDER BY tier) FROM platform.plan_feature
              WHERE feature_code = 'weekly_report' AND position('monthly' IN value_code) > 0 AND config_version IN ('v12-pilot@1', 'ch8-hypothesis@1'))
              = 'pro,pro,max',
            '两份种子配置里含 monthly 的档：v12 的 Pro、Max 与 ch8 的 Pro（ch8 的 Max 是 cross_subject_deep，不含 monthly，见报告待人确认）');

-- ================= 三、洞察服务的输入：只读当前有效绑定家长的当前版诉求，不读补充说明 =================
SELECT t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_facts(%L, %L)::text', :X, t.mp_month())) AS facts \gset
SELECT t.ok((:'facts'::jsonb ->> 'entitled')::boolean
            AND (SELECT array_agg(g ->> 'goal_id' ORDER BY g ->> 'goal_id') FROM jsonb_array_elements(:'facts'::jsonb -> 'goals') g)
                = ARRAY[:GG1, :GG2]
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'facts'::jsonb -> 'goals') g
                             WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(g) k) <> ARRAY['focus_subjects', 'goal_id', 'target_scores'])
            AND position('合成说明' IN :'facts') = 0 AND position(:G1 IN :'facts') = 0,
            '输入只含有效绑定家长（G1、G2）的当前版诉求：已解除绑定的 GR 与 G1 的旧版不在，不给补充说明与账号');
SELECT t.ok((SELECT array_agg(jsonb_build_array(r ->> 'node_id', r ->> 'date')) FROM jsonb_array_elements(:'facts'::jsonb -> 'retests_due') r)
              = ARRAY[jsonb_build_array('MATH.SYN.MPDB.B', t.mp_month()), jsonb_build_array('MATH.SYN.MPDB.A', t.mp_month() + 5)]
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'facts'::jsonb -> 'retests_due') r WHERE NOT r ? 'task_id')
            AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'facts'::jsonb) k) = ARRAY['entitled', 'goals', 'retests_due'],
            '输入的复测：本月到期的与逾期的（按月初计，带任务 ID），下月到期的不算；不给上月完成数等不引用判断的计数');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_facts(%L, %L)::text', :Y, t.mp_month()))::jsonb
              = '{"entitled": false}'::jsonb,
            '基础档学生的输入只有“不可用”，不给诉求与复测');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('SELECT insight.monthly_plan_facts(%L, %L)::text', :X, t.mp_month() + 1)),
                '22023', '月份必须是某月第一天');

-- ================= 四、写入守卫：每项引用都要核对 =================
SELECT t.throws_like(t.mp_save_sql(:Y, t.mp_plan()), '55000', 'PLAN_NOT_ENTITLED', '基础档学生：写入函数以 PLAN_NOT_ENTITLED 拒绝，不写行');
SELECT t.throws(t.mp_save_sql(:Z, t.mp_plan()), '55000', '没有订阅的学生同样拒绝');
SELECT t.throws(t.mp_save_sql(gen_random_uuid(), t.mp_plan()), '42501', '不存在的学生 42501');
SELECT t.throws(t.mp_save_sql(:X, t.mp_plan() - 'inputs_digest'), '22023', '载荷少键 22023');
SELECT t.throws(t.mp_save_sql(:X, t.mp_plan() || '{"note": "x"}'), '22023', '载荷多键（例如家长补充说明）22023');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(), (t.mp_month() - interval '2 month')::date), '23514', '只收本月', '只收本月（上海时区）');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_retest_day => t.mp_month() + 40)), '23514', '日期不在本月', '复测日期不在本月');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.A', :K5)))), '23514', '待点亮',
                     '优先知识点不能引用别的学生的判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.B', :K2)))), '23514', '待点亮',
                     '优先知识点不能引用家长看不到的判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.H', :K9)))), '23514', '待点亮',
                     '优先知识点不能引用学生看不到的判断（学生读得到这一段）');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.C', :K3)))), '23514', '待点亮',
                     '优先知识点不能引用家长观察来源的判断（学生读得到这一段）');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.D', :K4)))), '23514', '待点亮',
                     '优先知识点不能引用已进入“迁移”的判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.E', :K6)))), '23514', '待点亮',
                     '优先知识点不能引用待验证的判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.F', :K7)))), '23514', '待点亮',
                     '优先知识点不能引用已过期的判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.Z', :K1)))), '23514', '待点亮',
                     '优先知识点的节点必须是判断的对象');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.A', :K1, 'know')))), '23514', '待点亮',
                     '优先知识点的层次必须与判断一致');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.A', :K1, 'apply', 2)))), '23514', '待点亮',
                     '模板版本必须与判断一致');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(2, 'MATH.SYN.MPDB.A', :K1)))), '23514', '形状',
                     '先后（position）必须从 1 连续');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.A', :K1) || '{"focus_factor": 1.5}'))), '23514', '形状',
                     '学生段的优先知识点不能带系数等多余字段');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.A', :K1)), p_claims => ARRAY[]::text[])),
                     '23514', '输入引用', '所引判断必须都在输入引用里（重放能找回同一组判断）');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_notes => '[{"key": "x_note", "copy_key": "monthly_plan.guardian.x_note", "vars": {"text": "合成文字"}}]')),
                     '23514', '形状', '注意事项的变量只收计划事实（小整数或真假），不收文字');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', NULL, 'score_gaps',
                       jsonb_build_array(t.mp_gap(:GGR, :KE, :REC))))), '23514', '分差', '分差不能用已解除绑定家长的诉求');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', NULL, 'score_gaps',
                       jsonb_build_array(t.mp_gap(:GG1OLD, :KE, :REC))))), '23514', '分差', '分差不能用已替换的旧版诉求');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', NULL, 'score_gaps',
                       jsonb_build_array(t.mp_gap(:GG1, :KEH, :REC))))), '23514', '分差', '分差不能引用家长看不到的考试判断');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', NULL, 'score_gaps',
                       jsonb_build_array(t.mp_gap(:GG1, :KEO, :RECOLD))))), '23514', '分差', '分差不能用已替换的成绩记录');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', NULL, 'score_gaps',
                       jsonb_build_array(t.mp_gap(:GG1, :KE, :REC) || '{"gap": 12.5}')))), '23514', '形状', '分差不存成绩、目标分与分差的值');
-- K1（节点 A，在上月计划里）的历史挪到上个月：只剩“门槛不是迁移”这一条拦着
SAVEPOINT lit_gate;
SET LOCAL session_replication_role = replica;
UPDATE insight.claim_history SET at = (date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') - interval '10 days') AT TIME ZONE 'Asia/Shanghai'
 WHERE claim_id = :K1;
SET LOCAL session_replication_role = origin;
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last(jsonb_build_array(t.mp_lit('MATH.SYN.MPDB.A', :K1))),
                       'score_gaps', '[]'::jsonb))), '23514', '迁移', '上月点亮必须引用已进入“迁移”的判断');
ROLLBACK TO SAVEPOINT lit_gate;
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last(jsonb_build_array(t.mp_lit('MATH.SYN.MPDB.J', :K10))),
                       'score_gaps', '[]'::jsonb))), '23514', '上月快照的计划', '上月点亮的节点必须在上月快照的计划里');
-- 把上月计划的第 2 个节点换成 J：只剩“本月才进入迁移”这一条拦着
SAVEPOINT plan_j;
SET LOCAL session_replication_role = replica;
UPDATE insight.monthly_plan SET student_plan = jsonb_set(student_plan, '{priority_nodes,1,node_id}', '"MATH.SYN.MPDB.J"')
 WHERE student_id = :X AND month = (t.mp_month() - interval '1 month')::date;
SET LOCAL session_replication_role = origin;
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last(jsonb_build_array(t.mp_lit('MATH.SYN.MPDB.J', :K10))),
                       'score_gaps', '[]'::jsonb))), '23514', '迁移', '上月点亮按 claim_history 判断：本月才进入“迁移”的不算上月');
ROLLBACK TO SAVEPOINT plan_j;
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last('[]', p_planned => 3), 'score_gaps', '[]'::jsonb))),
                     '23514', '上月快照', '上月计划节点数必须与上月快照一致');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last('[]', t.mp_month()), 'score_gaps', '[]'::jsonb))),
                     '23514', '日期', '上月回顾的月份必须是上个月');
SELECT t.throws_like(t.mp_save_sql(:X, t.mp_plan(p_review => jsonb_build_object('last_month', t.mp_last('[]') || '{"retests_done": 1}', 'score_gaps', '[]'::jsonb))),
                     '23514', '形状', '上月回顾不收不引用判断的关于学生的计数');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.monthly_plan WHERE student_id IN (:X, :Y, :Z) AND month = t.mp_month()), '被拒的写入一行都没有留下');
-- 合格的一份：两个优先知识点、上月点亮一项（按历史在上月进入迁移、在上月计划里）、两位家长各一项分差引用
SELECT t.mp_save(:X, t.mp_plan(jsonb_build_array(t.mp_node(1, 'MATH.SYN.MPDB.G', :K8, 'apply'), t.mp_node(2, 'MATH.SYN.MPDB.A', :K1)),
                               jsonb_build_object('last_month', t.mp_last(jsonb_build_array(t.mp_lit('MATH.SYN.MPDB.D', :K4))),
                                                  'score_gaps', jsonb_build_array(t.mp_gap(:GG1, :KE, :REC), t.mp_gap(:GG2, :KE, :REC))))) AS saved \gset
SELECT t.ok((:'saved'::jsonb ->> 'created')::boolean AND (SELECT count(*) FROM insight.monthly_plan WHERE student_id = :X AND month = t.mp_month()) = 1
            AND (SELECT generated_at = now() FROM insight.monthly_plan WHERE student_id = :X AND month = t.mp_month()),
            '合格的快照写入成功（生成时间由库写）');
SELECT t.mp_save(:X, t.mp_plan()) AS again \gset
SELECT t.ok((:'again'::jsonb ->> 'created')::boolean IS FALSE AND (:'again'::jsonb ->> 'id') = (:'saved'::jsonb ->> 'id')
            AND (SELECT jsonb_array_length(student_plan -> 'priority_nodes') FROM insight.monthly_plan WHERE student_id = :X AND month = t.mp_month()) = 2,
            '同一学生同一月份第二次写入返回原行，不写新行、不改原行');
-- 已有快照先返回，再查权益：订阅降为基础档后再写，仍返回原行（不报没有权益）
UPDATE platform.subscription SET tier = 'basic' WHERE student_id = :X;
SELECT t.ok((t.mp_save(:X, t.mp_plan()) ->> 'created')::boolean IS FALSE, '已有快照先返回，再查权益（降档后再写仍返回原行）');
UPDATE platform.subscription SET tier = 'pro' WHERE student_id = :X;
SELECT t.throws(format($$INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version,
                          config_version, template_version, policy_weights, inputs_digest)
                        SELECT student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version, config_version,
                               template_version, policy_weights, inputs_digest FROM insight.monthly_plan WHERE student_id = %L AND month = %L$$,
                       :X, t.mp_month()), '23505', '表约束兜底：同一学生同一月份只有一份');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_read(%L, %L)::text', :X, t.mp_month()))::jsonb ->> 'id'
              = :'saved'::jsonb ->> 'id'
            AND t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_read(%L, %L)::text', :Y, t.mp_month())) IS NULL,
            '洞察服务经读函数取回快照；没有的返回 NULL');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_replay_inputs(%L, %L)::text', :X, t.mp_month()))::jsonb
              = '{"goals": [], "retests_due": []}'::jsonb
            AND t.m_as('starbuddy_insight', NULL, format('SELECT insight.monthly_plan_replay_inputs(%L, %L)::text', :Y, t.mp_month())) IS NULL,
            '重放函数只按快照里登记的诉求版本与复测任务取回（这份快照没登记，取回为空）');

-- ================= 五、只追加 =================
SELECT t.throws_like(format($$UPDATE insight.monthly_plan SET guardian_notes = '[]' WHERE student_id = %L$$, :X), '42501', '只追加',
                     '快照不能修改（守卫，超级用户也一样）');
SELECT t.throws_like(format($$DELETE FROM insight.monthly_plan WHERE student_id = %L$$, :X), '42501', '只随删除请求', '快照只随删除请求删除');
SELECT t.throws_like('TRUNCATE insight.monthly_plan', '42501', '不能清空', '快照不能清空');

-- ================= 六、按受众读 =================
SELECT t.mp_gview(:G1, :X) AS gv1 \gset
SELECT t.mp_gview(:G2, :X) AS gv2 \gset
SELECT t.mp_sview(:AX, :X) AS sv \gset
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'gv1'::jsonb) k)
              = ARRAY['config_version', 'generated_at', 'guardian_notes', 'month', 'review', 'student_plan', 'template_version']
            AND jsonb_array_length(:'gv1'::jsonb #> '{student_plan,priority_nodes}') = 2
            AND jsonb_array_length(:'gv1'::jsonb #> '{review,last_month,lit}') = 1
            AND position('plan_basis' IN :'gv1') = 0 AND position('input_refs' IN :'gv1') = 0,
            '家长视图：学习计划、家长注意事项与回顾三段；不返回排序依据与输入引用');
SELECT t.ok((:'gv1'::jsonb #> '{review,score_gaps}') = jsonb_build_array(jsonb_build_object(
              'subject', 'aya', 'exam_type', 'midterm', 'exam_date', '2026-09-20', 'gap_band', '{"reached": false, "low": 10, "high": 15}'::jsonb,
              'claim_id', :KE, 'template_key', 'test.monthly.exam', 'template_version', 1, 'copy_key', 'monthly_plan.review.score_gap'))
            AND (:'gv2'::jsonb #> '{review,score_gaps,0,gap_band}') = '{"reached": false, "low": 15, "high": 20}'::jsonb
            AND jsonb_array_length(:'gv2'::jsonb #> '{review,score_gaps}') = 1
            AND position('goal_id' IN :'gv1') = 0 AND position('92.5' IN :'gv1') = 0 AND position('105' IN :'gv1') = 0,
            '分差读时现算、5 分一档的区间，只给本人诉求对应的项（两位家长互相看不到对方的目标）；不给成绩、目标分与诉求 ID');
SELECT t.ok(insight.monthly_gap_band(0) = '{"reached": true}' AND insight.monthly_gap_band(-3) = '{"reached": true}'
            AND insight.monthly_gap_band(5) = '{"reached": false, "low": 0, "high": 5}' AND insight.monthly_gap_band(0.5) = '{"reached": false, "low": 0, "high": 5}'
            AND insight.monthly_gap_band(5.5) = '{"reached": false, "low": 5, "high": 10}',
            '分差区间：(0, 5] → 0–5，(5, 10] → 5–10；不高于 0 记为已达到');
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'sv'::jsonb) k) = ARRAY['generated_at', 'month', 'student_plan']
            AND (:'sv'::jsonb -> 'student_plan') = (:'gv1'::jsonb -> 'student_plan')
            AND position('guardian' IN :'sv') = 0 AND position('score' IN :'sv') = 0 AND position('"review"' IN :'sv') = 0,
            '学生视图只有学习计划：没有家长注意事项、回顾、分差与诉求');
SELECT t.ok(t.mp_gview(:AX, :X) IS NULL AND t.mp_sview(:G1, :X) IS NULL AND t.mp_gview(:GR, :X) IS NULL AND t.mp_sview(:GR, :X) IS NULL
            AND t.mp_gview(:GO, :X) IS NULL AND t.mp_sview(:AY, :X) IS NULL AND t.mp_gview(:G1, gen_random_uuid()) IS NULL
            AND t.mp_gview(NULL, :X) IS NULL AND t.mp_sview(NULL, :X) IS NULL,
            '学生读不到家长视图、家长读不到学生视图；已解除绑定、别家、兄弟姐妹、未登录、不存在一律 NULL');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_bff', %L, 'SELECT count(*)::text FROM insight.monthly_plan')$$, :G1), '42501',
                'BFF 以家长身份也不能直读快照表（只经视图函数）');
-- 学生视图读时再核来源：快照里混进一条家长观察来源判断的项（以复制模式直接改，只为造出这种状态）——学生视图不给，家长视图给
SAVEPOINT guardian_source;
SET LOCAL session_replication_role = replica;
UPDATE insight.monthly_plan SET student_plan = jsonb_set(student_plan, '{priority_nodes}',
         (student_plan -> 'priority_nodes') || jsonb_build_array(t.mp_node(3, 'MATH.SYN.MPDB.C', :K3)))
 WHERE student_id = :X AND month = t.mp_month();
SET LOCAL session_replication_role = origin;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM jsonb_array_elements(t.mp_sview(:AX, :X) #> '{student_plan,priority_nodes}') n WHERE n ->> 'claim_id' = :K3)
            AND EXISTS (SELECT 1 FROM jsonb_array_elements(t.mp_gview(:G1, :X) #> '{student_plan,priority_nodes}') n WHERE n ->> 'claim_id' = :K3),
            '学生视图读时不给引用家长观察来源判断的项（家长视图给）');
ROLLBACK TO SAVEPOINT guardian_source;
-- 分差引用不是当前版：诉求换了新版、成绩记录被替换，视图读时不再给这一项
SAVEPOINT not_current;
UPDATE insight.guardian_goal SET superseded_at = now() WHERE id = :GG1;
SELECT t.ok(jsonb_array_length(t.mp_gview(:G1, :X) #> '{review,score_gaps}') = 0 AND jsonb_array_length(t.mp_gview(:G2, :X) #> '{review,score_gaps}') = 1,
            '诉求换了新版：旧版对应的分差不再给（另一位家长的不受影响）');
ROLLBACK TO SAVEPOINT not_current;
SAVEPOINT not_current;
UPDATE insight.exam_record SET superseded_at = now() WHERE id = :REC;
SELECT t.ok(jsonb_array_length(t.mp_gview(:G1, :X) #> '{review,score_gaps}') = 0 AND jsonb_array_length(t.mp_gview(:G2, :X) #> '{review,score_gaps}') = 0,
            '成绩记录被替换：分差不再给');
ROLLBACK TO SAVEPOINT not_current;
-- 引用的判断事后被撤回：视图读时不再给这一项（快照本身不改）
SELECT t.m_as('starbuddy_insight', NULL, format($f$UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = %L RETURNING 'ok'$f$, :K1));
SELECT t.m_as('starbuddy_insight', NULL, format($f$UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = %L RETURNING 'ok'$f$, :KE));
SELECT t.ok((SELECT array_agg(n ->> 'node_id') FROM jsonb_array_elements(t.mp_gview(:G1, :X) #> '{student_plan,priority_nodes}') n) = ARRAY['MATH.SYN.MPDB.G']
            AND (SELECT array_agg(n ->> 'node_id') FROM jsonb_array_elements(t.mp_sview(:AX, :X) #> '{student_plan,priority_nodes}') n) = ARRAY['MATH.SYN.MPDB.G']
            AND jsonb_array_length(t.mp_gview(:G1, :X) #> '{review,score_gaps}') = 0
            AND (SELECT jsonb_array_length(student_plan -> 'priority_nodes') FROM insight.monthly_plan WHERE student_id = :X AND month = t.mp_month()) = 2,
            '判断被撤回后：两个视图都不再给引用它的项，快照不改');

-- ================= 七、删除只随删除请求 =================
SAVEPOINT erase;
SET LOCAL ROLE starbuddy_eraser;
SELECT set_config('starbuddy.erasure_student', :Y, true);
SELECT t.throws_like(format($$DELETE FROM insight.monthly_plan WHERE student_id = %L$$, :X), '42501', '只随删除请求',
                     '删除执行身份也只能删正在删除的那个学生的快照');
SELECT set_config('starbuddy.erasure_student', :X, true);
DELETE FROM insight.monthly_plan WHERE student_id = :X;
RESET ROLE;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.monthly_plan WHERE student_id = :X), '删除请求执行中（eraser + 本事务学生）能删该学生的快照');
ROLLBACK TO SAVEPOINT erase;
ROLLBACK;
