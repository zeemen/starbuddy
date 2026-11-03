-- 0034 考试安排、考前预测冻结与对账卡（specs/sie-03-dispute-reconcile/spec.md 验收第 2 条的库层部分）。全部合成数据。
-- 整个文件在一个事务里，结束回滚。用 79m 的 t.m_as(角色, 账号, SQL)。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set R1 '''d1f10000-0000-4000-8000-000000000f01'''
\set R2 '''d1f10000-0000-4000-8000-000000000f02'''
\set R3 '''d1f10000-0000-4000-8000-000000000f03'''
\set R4 '''d1f10000-0000-4000-8000-000000000f04'''
\set E1 '''d1e10000-0000-4000-8000-000000000e01'''
\set E2 '''d1e10000-0000-4000-8000-000000000e02'''
\set E3 '''d1e10000-0000-4000-8000-000000000e03'''
BEGIN;
SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS today \gset
CREATE FUNCTION t.o_plan(p_acct uuid, p_student uuid, p_date date, p_full numeric, p_nodes text[], p_type text DEFAULT 'midterm') RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT insight.submit_exam_plan(%L, ''aya'', %L, %L, %L, %L)::text',
                                                  p_student, p_type, p_date, p_full, p_nodes))::uuid $$;
CREATE FUNCTION t.o_score(p_acct uuid, p_student uuid, p_plan uuid, p_raw numeric) RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, format('SELECT insight.submit_exam_score(%L, %L, %L)::text', p_student, p_plan, p_raw))::uuid $$;
CREATE FUNCTION t.o_predict(p_student uuid, p_exam uuid, p_low numeric, p_high numeric, p_claim uuid DEFAULT 'd1e10000-0000-4000-8000-000000000e01')
  RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format(
    'INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low, interval_high, basis, evidence_ids, algorithm_version)
     VALUES (%L, %L, %L, %L, %L, ''{"nodes": 2}'', ''{e1000000-0000-4000-8000-000000000001}'', ''sie-predict@0.1.0'') RETURNING id::text',
    p_student, p_exam, p_claim, p_low, p_high))::uuid $$;
-- 判断：考试类（预测对应的判断）与对账句引用的节点类
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.reconcile.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：对账句', 'approved', 'test-reviewer', now()),
  ('test.predict.exam', 1, 'exam', '{agent,student,guardian}', '60 days', '合成模板：考试区间', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:R1, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.reconcile.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:R2, :S2, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.reconcile.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:R3, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.AXIS', 'test.reconcile.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student}', now() + interval '30 days'),
  (:R4, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.ROOT', 'test.reconcile.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:E1, :S1, 'aya', 'exam', 'midterm', 'test.predict.exam', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:E2, :S1, 'aya', 'exam', 'final', 'test.predict.exam', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:E3, :S2, 'aya', 'exam', 'midterm', 'test.predict.exam', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days');
RESET ROLE;
SELECT t.ok(insight.exam_freeze_at('2026-10-01') = '2026-09-30 16:00:00+00'::timestamptz, '冻结时刻是考试当天零点（上海时区，UTC+8）');

-- ================= 一、考试安排（exam_record 的 raw_score 为空） =================
SELECT t.o_plan(:aS1, :S1, (:'today'::date + 10), 100, ARRAY['MATH.FUNC.QUAD.VERTEX_FORM', 'MATH.FUNC.QUAD.GRAPH']) AS p1 \gset
SELECT t.ok((SELECT raw_score IS NULL AND full_score = 100 AND cardinality(scope_node_ids) = 2 AND entered_by_role = 'self'
                    AND source_type = 'self' AND entered_by_account_id = :aS1::uuid AND plan_record_id IS NULL FROM insight.exam_record WHERE id = :'p1'),
            '学生本人录入考试安排：范围节点、日期、满分、录入人；没有成绩');
SELECT t.ok(t.o_plan(:gA, :S1, (:'today'::date + 10), 100, ARRAY['MATH.FUNC.QUAD.GRAPH']) = :'p1'::uuid,
            '同一场考试（学科、类型、日期）家长再录一次：复用已有安排，每场考试只有一条安排');
SELECT t.o_plan(:gA, :S1, (:'today'::date + 20), 150, ARRAY['MATH.FUNC.QUAD.VERTEX_FORM'], 'final') AS p2 \gset
SELECT t.ok((SELECT entered_by_role = 'guardian' AND full_score = 150 FROM insight.exam_record WHERE id = :'p2'),
            '已绑定家长也能录入考试安排；满分不再限于 100 / 120（1–300，政策库校验在洞察服务）');
SELECT t.o_plan(:aS1, :S1, :'today'::date, 100, ARRAY['MATH.FUNC.QUAD.GRAPH'], 'monthly') AS p_today \gset
SELECT t.ok(:'p_today' IS NOT NULL, '当天的考试也能录入安排');
SELECT t.throws_like(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :gB, :S1, :'today'::date + 3),
                     '42501', 'not allowed', '另一户的家长不能给这个学生录考试安排');
SELECT t.throws_like(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :gC, :S1, :'today'::date + 3),
                     '42501', 'not allowed', '没有绑定的家长不能录考试安排');
SELECT t.throws_like(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :aS2, gen_random_uuid(), :'today'::date + 3),
                     '42501', 'not allowed', '不存在的学生与无权同一个错');
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, %s, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :aS1, :S1, :'today'::date + 3, v), '22023',
                format('满分 %s 不合要求（只收 1–300 的整数）', v))
  FROM unnest(ARRAY['0', '301', '99.5']) v;
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :aS1, :S1, :'today'::date - 1),
                '22023', '考试日期不能早于今天（上海时区）');
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH''])', :aS1, :S1, :'today'::date + 400),
                '22023', '考试日期最多一年后');
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, 100, %L)', :aS1, :S1, :'today'::date + 3, '{}'), '22023', '考试安排必须带范围节点');
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''not a node''])', :aS1, :S1, :'today'::date + 3), '22023', '范围节点 ID 格式');
SELECT t.throws(format('SELECT t.o_plan(%L, %L, %L::date, 100, ARRAY[''MATH.FUNC.QUAD.GRAPH'', ''MATH.FUNC.QUAD.GRAPH''])', :aS1, :S1, :'today'::date + 3),
                '22023', '范围节点不能重复');
SELECT t.throws($$INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, entered_by_role,
                                                   entered_by_account_id, source_type)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'midterm', '2026-12-01', 100, NULL, 'self',
                          'a1000000-0000-4000-8000-0000000000a1', 'self')$$,
                '23514', '没有成绩的考试记录必须带范围节点（约束）');
SELECT t.throws(format($$INSERT INTO insight.exam_record (student_id, subject, exam_type, exam_date, full_score, raw_score, scope_node_ids,
                                                          entered_by_role, entered_by_account_id, source_type, plan_record_id)
                         VALUES (%L, 'aya', 'midterm', '2026-12-02', 100, NULL, '{MATH.FUNC.QUAD.GRAPH}', 'self', %L, 'self', %L)$$, :S1, :aS1, :'p1'),
                '23514', '考试安排本身不能挂别的安排（只有成绩行挂 plan_record_id）');
SELECT t.ok(t.m_as('starbuddy_bff', :aS1, format('SELECT insight.onboarding_view(%L)::text', :S1))::jsonb -> 'exams'
            @? '$[*] ? (!exists(@.raw_score))' IS NOT TRUE,
            '建档读取的成绩列表不含考试安排（raw_score 为空的不列）');
SELECT t.ok(has_function_privilege('starbuddy_bff', 'insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[])', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_insight', 'insight.submit_exam_plan(uuid, platform.subject, text, date, numeric, text[])', 'EXECUTE')
            AND has_function_privilege('starbuddy_bff', 'insight.submit_exam_score(uuid, uuid, numeric)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_learning', 'insight.submit_exam_score(uuid, uuid, numeric)', 'EXECUTE'),
            '录入考试安排与成绩只授 BFF');

-- ================= 二、预测：只由洞察服务写，考试当天零点起不能新建与修改 =================
SELECT t.o_predict(:S1, :'p1', 70, 85) AS f1 \gset
SELECT t.ok((SELECT level = 0.80 AND source_type = 'inferred' AND frozen_at IS NULL AND algorithm_version = 'sie-predict@0.1.0' AND claim_id = :E1::uuid
               FROM insight.exam_prediction WHERE id = :'f1'),
            '洞察服务写预测：80% 区间，来源为推断，带算法版本、证据 ID 与对应的考试类判断');
SELECT t.throws(format('SELECT t.o_predict(%L, %L, 70, 85, %L)', :S1, :'p1', :E2), '23505', '一场考试只有一条预测');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$, format(
  'INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low, interval_high, level, basis, algorithm_version) VALUES (%L, %L, %L, 90, 100, 0.9, ''{}'', ''x@0.1.0'') RETURNING id::text',
  :S1, :'p2', :E2)), '23514', '区间级别固定 0.80');
SELECT t.throws_like(format('SELECT t.o_predict(%L, %L, 90, 155, %L)', :S1, :'p2', :E2), '23514', '满分', '预测区间不能超出满分');
SELECT t.throws(format('SELECT t.o_predict(%L, %L, 90, 80, %L)', :S1, :'p2', :E2), '23514', '区间下界不能大于上界');
SELECT t.throws_like(format('SELECT t.o_predict(%L, %L, 70, 85, %L)', :S2, :'p2', :E3), '23514', '当前的考试安排', '预测不能挂到别的学生的考试上');
SELECT t.throws_like(format('SELECT t.o_predict(%L, %L, 70, 85, %L)', :S1, :'p2', :E3), '23514', '考试类判断', '预测对应的判断必须是该学生的');
SELECT t.throws_like(format('SELECT t.o_predict(%L, %L, 70, 85, %L)', :S1, :'p2', :R1), '23514', '考试类判断', '预测对应的判断必须是考试类');
SELECT t.throws_like(format('SELECT t.o_predict(%L, %L, 70, 85, %L)', :S1, :'p_today', :E2), '23514', '零点起', '考试当天零点起不能再新建预测');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format(
  'INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low, interval_high, basis, algorithm_version, frozen_at) VALUES (%L, %L, %L, 1, 2, ''{}'', ''x@0.1.0'', now()) RETURNING id::text', :S1, :'p2', :E2)),
                '42501', '洞察服务不能自己写冻结时间（没有列权限）');
SELECT t.m_as('starbuddy_insight', NULL, format('UPDATE insight.exam_prediction SET interval_low = 72, interval_high = 88 WHERE id = %L RETURNING id::text', :'f1'));
SELECT t.ok((SELECT interval_low = 72 AND interval_high = 88 FROM insight.exam_prediction WHERE id = :'f1'), '冻结前可以修改预测');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('UPDATE insight.exam_prediction SET frozen_at = now() WHERE id = %L RETURNING id::text', :'f1')),
                '42501', '洞察服务不能冻结（只由定时任务）');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('DELETE FROM insight.exam_prediction WHERE id = %L RETURNING id::text', :'f1')),
                '42501', '洞察服务不能删预测');
SELECT t.throws_like(format('UPDATE insight.exam_prediction SET frozen_at = now() WHERE id = %L', :'f1'), '23514', '不早于考试当天零点',
                     '考试前不能冻结（守卫，超级用户也不行）');

-- 把 p1 的考试日期改到昨天（以超级用户绕过 0022 的只追加守卫，模拟时间过去）
ALTER TABLE insight.exam_record DISABLE TRIGGER guard_fact_update;
UPDATE insight.exam_record SET exam_date = :'today'::date - 1 WHERE id = :'p1';
ALTER TABLE insight.exam_record ENABLE TRIGGER guard_fact_update;
SELECT t.throws_like(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('UPDATE insight.exam_prediction SET interval_low = 60 WHERE id = %L RETURNING id::text', :'f1')),
                     '23514', '零点起', '考试当天零点起，未冻结的预测也不能再改');

-- ================= 三、冻结（定时任务） =================
SELECT t.throws(format('SELECT t.m_as(%L, NULL, ''SELECT insight.freeze_due_predictions(now())::text'')', r), '42501', format('%s 不能调用冻结函数', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_insight', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_eraser']) r;
SELECT t.throws('SELECT t.m_as(''starbuddy_scheduler'', NULL, ''SELECT insight.freeze_due_predictions(now() + interval ''''1 hour'''')::text'')',
                '22023', '冻结时间不能是将来（不能提前冻结）');
SELECT t.throws('SELECT t.m_as(''starbuddy_scheduler'', NULL, ''SELECT insight.freeze_due_predictions(now() - interval ''''2 days'''')::text'')',
                '22023', '冻结时间最多往前 1 天');
SELECT t.o_predict(:S1, :'p2', 90, 110, :E2) AS f2 \gset
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT insight.freeze_due_predictions(now())::text')::int = 1, '定时任务冻结到期的预测（1 条）');
SELECT t.ok((SELECT frozen_at IS NOT NULL FROM insight.exam_prediction WHERE id = :'f1')
            AND (SELECT frozen_at IS NULL FROM insight.exam_prediction WHERE id = :'f2'),
            '只冻结考试当天零点已过的预测，将来的不动');
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT insight.freeze_due_predictions(now())::text')::int = 0, '再跑一次不重复冻结');
SELECT t.throws_like(format('UPDATE insight.exam_prediction SET interval_high = 99 WHERE id = %L', :'f1'), '42501', '已冻结',
                     '冻结后不可改（超级用户也不行）');
SELECT t.throws_like(format('UPDATE insight.exam_prediction SET frozen_at = NULL WHERE id = %L', :'f1'), '42501', '已冻结', '冻结后不能解冻');
SELECT t.throws_like(format('DELETE FROM insight.exam_prediction WHERE id = %L', :'f1'), '42501', '只随删除请求删除', '冻结后不能删除');
SELECT set_config('starbuddy.erasure_student', :S1, true);
SELECT t.throws_like(format('DELETE FROM insight.exam_prediction WHERE id = %L', :'f1'), '42501', '只随删除请求删除',
                     '设了删除变量但不是删除执行身份，仍不能删冻结的预测');
SELECT set_config('starbuddy.erasure_student', '', true);
SELECT t.throws('TRUNCATE insight.exam_prediction CASCADE', '42501', '预测不能清空');

-- ================= 四、成绩与对账卡 =================
SELECT t.throws_like(format('SELECT t.o_score(%L, %L, %L, 95)', :gB, :S1, :'p1'), '42501', 'not allowed', '另一户的家长不能录这个学生的成绩');
SELECT t.throws(format('SELECT t.o_score(%L, %L, %L, 101)', :aS1, :S1, :'p1'), '22023', '成绩不能超过满分');
SELECT t.throws(format('SELECT t.o_score(%L, %L, %L, 95)', :aS1, :S1, gen_random_uuid()), '22023', '考试安排必须存在且属于该学生');
SELECT t.throws(format('SELECT t.o_score(%L, %L, %L, 95)', :aS2, :S2, :'p1'), '22023', '不能拿别的学生的考试安排录自己的成绩');
SELECT t.o_score(:aS1, :S1, :'p1', 95) AS s1 \gset
SELECT t.ok((SELECT plan_record_id = :'p1'::uuid AND raw_score = 95 AND subject = 'aya' AND full_score = 100 FROM insight.exam_record WHERE id = :'s1'),
            '成绩按考试安排录入：挂 plan_record_id，学科、满分取自安排');
SELECT t.m_as('starbuddy_bff', :aS1, format($$SELECT insight.submit_exam_record(%L, 'aya', 'midterm', %L, 100, 80, NULL, NULL, NULL)::text$$,
                                            :S1, :'today'::date - 1))::uuid AS s_unlinked \gset
CREATE FUNCTION t.o_card(p_pred uuid, p_exam uuid, p_in boolean, p_dirs text[], p_sentences jsonb,
                         p_audience text[] DEFAULT '{student,guardian}', p_student uuid DEFAULT 'c1000000-0000-4000-8000-0000000000c1')
  RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format(
    'INSERT INTO insight.exam_reconciliation (student_id, prediction_id, exam_record_id, in_interval, deviation_directions, deviation_nodes,
                                              adjustments, audience, sentences, evidence_ids, algorithm_version)
     VALUES (%L, %L, %L, %L, %L, ''{MATH.FUNC.QUAD.GRAPH}'', ''{claims_to_verify}'', %L, %L, ''{e1000000-0000-4000-8000-000000000001}'', ''sie-reconcile@0.1.0'') RETURNING id::text',
    p_student, p_pred, p_exam, p_in, p_dirs, p_audience, p_sentences))::uuid $$;
\set good_sentence '''[{"claim_id": "d1f10000-0000-4000-8000-000000000f01", "template_key": "test.reconcile.node", "direction": "calculation_slip"}]'''
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{calculation_slip}'', %L)', :'f2', :'s1', :good_sentence), '23514', '已冻结的预测',
                     '只能对已冻结的预测对账');
SELECT t.throws(format('SELECT t.o_card(%L, %L, true, ''{}'', %L)', :'f1', :'s1', :good_sentence), '23514',
                '区间内外由库按成绩计算（95 不在 72–88 内），调用方说“区间内”也没用，区间外至少一个偏差方向');
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, %L, %L)', :'f1', :'s1', d, :good_sentence), '23514', format('偏差方向 %s 被拒绝', d))
  FROM unnest(ARRAY['{careless}', '{bad_luck}', '{question_type_transfer}', '{time_shortage,time_shortage}']) d;
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                       '[{"template_key": "test.reconcile.node"}]'), '23514', '每句必须带判断 ID');
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                       '[{"claim_id": "d1f10000-0000-4000-8000-000000000f01", "template_key": "test.reconcile.node", "text": "自由文本"}]'),
                '23514', '每句不收自由文本（只存判断 ID 与模板键）');
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                       '[{"claim_id": "d1f10000-0000-4000-8000-000000000f01", "template_key": "test.reconcile.node", "direction": "time_shortage_x"}]'),
                '23514', '每句的偏差方向也只收四个值');
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1', '[]'), '23514', '对账卡至少一句');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                            '[{"claim_id": "d1f10000-0000-4000-8000-000000000f02", "template_key": "test.reconcile.node"}]'),
                     '23514', '对读者可见', '每句不能引用别的学生的判断');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                            '[{"claim_id": "d1f10000-0000-4000-8000-000000000f01", "template_key": "test.other.node"}]'),
                     '23514', '对读者可见', '句子的模板键必须是所引判断的模板');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                            '[{"claim_id": "d1f10000-0000-4000-8000-000000000f03", "template_key": "test.reconcile.node"}]'),
                     '23514', '对读者可见', '家长版的卡不能引用只给学生看的判断');
SAVEPOINT student_card;
SELECT t.ok(t.o_card(:'f1', :'s1', false, '{time_shortage}', '[{"claim_id": "d1f10000-0000-4000-8000-000000000f03", "template_key": "test.reconcile.node"}]',
                     '{student}') IS NOT NULL, '学生版的卡可以引用只给学生看的判断');
ROLLBACK TO SAVEPOINT student_card;
UPDATE insight.claim_template SET status = 'retired' WHERE template_key = 'test.reconcile.node';
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1', :good_sentence), '23514', '对读者可见',
                     '所引判断的模板已退役：不能进对账卡');
UPDATE insight.claim_template SET status = 'approved' WHERE template_key = 'test.reconcile.node';
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET created_at = now() - interval '2 days', expires_at = now() - interval '1 minute' WHERE id = :R4;
ALTER TABLE insight.claim ENABLE TRIGGER USER;
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1',
                            '[{"claim_id": "d1f10000-0000-4000-8000-000000000f04", "template_key": "test.reconcile.node"}]'),
                     '23514', '对读者可见', '所引判断已过期：不能进对账卡');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'p1', :good_sentence), '23514', '按这场考试安排',
                     '对账要用有成绩的记录，不能用考试安排本身');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s_unlinked', :good_sentence), '23514', '按这场考试安排',
                     '对账按 plan_record_id 匹配：没挂这场安排的成绩不能用（即使学科、日期相同）');
SELECT t.throws_like(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L, ''{student}'', %L)', :'f1', :'s1', :good_sentence, :S2), '23514', '已冻结的预测',
                     '不能以别的学生名义对账');
SELECT t.o_card(:'f1', :'s1', true, '{calculation_slip,item_type_transfer}', :good_sentence) AS c1 \gset
SELECT t.ok((SELECT NOT in_interval AND deviation_directions = '{calculation_slip,item_type_transfer}' AND source_type = 'inferred'
                    AND algorithm_version = 'sie-reconcile@0.1.0' AND adjustments = '{claims_to_verify}' AND deviation_nodes = '{MATH.FUNC.QUAD.GRAPH}'
               FROM insight.exam_reconciliation WHERE id = :'c1'),
            '对账卡落库：区间外（库算），偏差方向为契约枚举（可多个），带偏差节点、调整、来源与算法版本');
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f1', :'s1', :good_sentence), '23505', '一条预测只有一张对账卡');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('UPDATE insight.exam_reconciliation SET deviation_directions = ''{time_shortage}'' WHERE id = %L RETURNING id::text', :'c1')),
                '42501', '洞察服务不能改对账卡（只追加）');
SELECT t.throws_like(format('UPDATE insight.exam_reconciliation SET deviation_directions = ''{time_shortage}'' WHERE id = %L', :'c1'), '42501', '只追加',
                     '超级用户也不能改对账卡');
SELECT t.throws_like(format('DELETE FROM insight.exam_reconciliation WHERE id = %L', :'c1'), '42501', '只随删除请求删除', '对账卡不能单独删除');
SELECT t.throws('TRUNCATE insight.exam_reconciliation', '42501', '对账卡不能清空');
-- 区间内：不归因
ALTER TABLE insight.exam_record DISABLE TRIGGER guard_fact_update;
UPDATE insight.exam_record SET exam_date = :'today'::date - 1 WHERE id = :'p2';
ALTER TABLE insight.exam_record ENABLE TRIGGER guard_fact_update;
SELECT t.m_as('starbuddy_scheduler', NULL, 'SELECT insight.freeze_due_predictions(now())::text');
SELECT t.o_score(:gA, :S1, :'p2', 100) AS s2 \gset
SELECT t.throws(format('SELECT t.o_card(%L, %L, false, ''{time_shortage}'', %L)', :'f2', :'s2', :good_sentence), '23514', '区间内不归因（给了方向就拒绝）');
SELECT t.o_card(:'f2', :'s2', false, '{}', '[{"claim_id": "d1f10000-0000-4000-8000-000000000f01", "template_key": "test.reconcile.node"}]') AS c2 \gset
SELECT t.ok((SELECT in_interval AND deviation_directions = '{}' FROM insight.exam_reconciliation WHERE id = :'c2'), '区间内：库算出 in_interval，偏差方向为空');

-- ================= 五、可见：BFF、学科服务都读不到预测与对账卡（经洞察服务） =================
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM insight.exam_prediction'')', :aS1), '42501', 'BFF 读不到预测表');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM insight.exam_reconciliation'')', :gA), '42501', 'BFF 读不到对账卡表');
SELECT t.throws('SELECT t.m_as(''starbuddy_learning'', NULL, ''SELECT count(*)::text FROM insight.exam_prediction'')', '42501', '学科服务读不到预测（不作计算输入）');
ROLLBACK;
