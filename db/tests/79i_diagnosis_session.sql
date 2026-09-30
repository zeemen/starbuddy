-- 0027 数学自适应诊断的会话与步骤（specs/sie-02a-math-diagnosis/spec.md 验收第 4 条）。全部合成数据。
-- 用 79d 的 t.ob(账号, SQL) 以 BFF 身份按账号读；学生 c1（账号 a1，家庭 A，家长 gA）、c2（账号 b1，家庭 B）。
-- 整个文件在一个事务里，结束回滚。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set sf '''5f000000-0000-4000-8000-00000000005f'''
\set d1 '''d1a00000-0000-4000-8000-0000000000d1'''
\set d2 '''d2a00000-0000-4000-8000-0000000000d2'''
\set gd1 '''9d100000-0000-4000-8000-0000000009d1'''
\set gd2 '''9d200000-0000-4000-8000-0000000009d2'''
\set st1 '''5d100000-0000-4000-8000-00000000d5a1'''
\set st2 '''5d200000-0000-4000-8000-00000000d5a2'''
\set st3 '''5d300000-0000-4000-8000-00000000d5a3'''
BEGIN;

CREATE FUNCTION t.as_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.as_role(text, text) TO PUBLIC;

-- 合成作答（判分要指向真实作答）
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, request_idempotency_key) VALUES
  ('a8d00000-0000-4000-8000-0000000000d1', '2026-10-10 09:00:00+00', :S1, 'aya', 'synthetic-0301-07', 'e1000000-0000-4000-8000-000000000001', 'synthetic-diag-0001'),
  ('a8d00000-0000-4000-8000-0000000000d2', '2026-10-10 09:00:00+00', :S2, 'aya', 'synthetic-0301-08', 'e2000000-0000-4000-8000-000000000002', 'synthetic-diag-0002'),
  ('a8d00000-0000-4000-8000-0000000000d3', '2026-10-10 09:05:00+00', :S1, 'aya', 'synthetic-0301-08', 'e1000000-0000-4000-8000-000000000001', 'synthetic-diag-0003');

-- S1 的一条 0301-08 学生原文（复制 e1 改题号），用作过程原文证据
INSERT INTO platform.evidence_event
SELECT (jsonb_populate_record(NULL::platform.evidence_event,
          to_jsonb(e) || '{"evidence_id": "e1d80000-0000-4000-8000-0000000000e8", "item_id": "synthetic-0301-08"}'::jsonb)).*
  FROM platform.evidence_event e WHERE e.evidence_id = 'e1000000-0000-4000-8000-000000000001';
INSERT INTO platform.evidence_event
SELECT (jsonb_populate_record(NULL::platform.evidence_event,
          to_jsonb(e) || '{"evidence_id": "e2d80000-0000-4000-8000-0000000000e8", "item_id": "synthetic-0301-08"}'::jsonb)).*
  FROM platform.evidence_event e WHERE e.evidence_id = 'e2000000-0000-4000-8000-000000000002';

-- ================= 一、学习服务建会话、写步骤 =================
SET ROLE starbuddy_learning;
INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
VALUES (:d1, :S1, 'aya', 'aya-diag-trace@0.1.0', '0.2.0', 'e5000000-0000-4000-8000-0000000000e1', 'e6000000-0000-4000-8000-0000000000e1');
SELECT t.ok((SELECT status = 'active' AND time_budget_sec = 2100 AND item_limit = 20 FROM platform.diagnosis_session WHERE id = :d1),
            '会话默认进行中，上限 35 分钟、20 题');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
                         VALUES (%L, 'aya', 'aya-diag-trace@0.1.0', '0.2.0', gen_random_uuid(), gen_random_uuid())$$, :S1),
                '23505', '同一学生同一学科只能有一个未结束的会话');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
                         VALUES (%L, 'zhixing', 'x@0.1.0', '0.2.0', gen_random_uuid(), 'e6000000-0000-4000-8000-0000000000e1')$$, :S1),
                '23505', '幂等键在同一学生内唯一');
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES (:gd1, :S1, 'aya', 'a8d00000-0000-4000-8000-0000000000d1', '2026-10-10 09:00:00+00', 'incorrect', 'aya-symbolic-ts@0.1.0', 0, 1, true, 0.95,
        'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, '{}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()),
       (:gd2, :S2, 'aya', 'a8d00000-0000-4000-8000-0000000000d2', '2026-10-10 09:00:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 0.95,
        'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, '{}', 'formal', '{e2000000-0000-4000-8000-000000000002}', gen_random_uuid());
INSERT INTO platform.diagnosis_step (id, session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
VALUES (:st1, :d1, 1, 'adaptive', 'MATH.FUNC.QUAD.VERTEX_FORM', 'synthetic-0301-07', 'target', gen_random_uuid());
UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = :gd1, answered_at = now(), elapsed_sec = 90 WHERE id = :st1;
SELECT t.ok((SELECT status FROM platform.diagnosis_step WHERE id = :st1) = 'answered', '作答后步骤关联判分');
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET elapsed_sec = 5 WHERE id = %L$$, :st1), '22023', '已结束的步骤不能再改');
INSERT INTO platform.diagnosis_step (id, session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
VALUES (:st2, :d1, 2, 'adaptive', 'MATH.FUNC.QUAD.GRAPH', 'synthetic-0301-08', 'prereq_trace', gen_random_uuid());
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = %L, answered_at = now() WHERE id = %L$$, :gd2, :st2),
                '22023', '步骤不能关联别的学生的判分（同一道题）');
-- 判分判的必须是这一步出的题（安全审查 S4）：gd1 判的是 0301-07，st2 出的是 0301-08
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = %L, answered_at = now() WHERE id = %L$$, :gd1, :st2),
                '22023', '步骤不能关联判的是别的题的判分');
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET work_evidence_id = 'e1d80000-0000-4000-8000-0000000000e8' WHERE id = %L$$, :st2),
                '23514', '自适应题不能挂过程原文');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
                         VALUES (%L, 3, 'adaptive', 'MATH.FUNC.QUAD.AXIS', NULL, 'target', gen_random_uuid())$$, :d1),
                '23514', '有题的步骤必须带题目');
INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, status, idempotency_key)
VALUES (:d1, 3, 'adaptive', 'MATH.FUNC.QUAD.AXIS', NULL, 'no_item', 'no_item', gen_random_uuid());
SELECT t.ok(true, '无题可测记为 no_item，不带题目');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
                         VALUES (%L, 4, 'process', 'MATH.FUNC.QUAD.AXIS', 'synthetic-0301-07', 'target', gen_random_uuid())$$, :d1),
                '23514', '过程题只能以 process_near_gap 选出');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
                         VALUES (%L, 4, 'adaptive', 'math.bad', 'synthetic-0301-07', 'target', gen_random_uuid())$$, :d1),
                '23514', '节点 ID 必须合规');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
                         VALUES (%L, 1, 'adaptive', 'MATH.FUNC.QUAD.AXIS', 'synthetic-0301-07', 'target', gen_random_uuid())$$, :d1),
                '23505', '步骤序号在会话内唯一');

-- 会话：用时与题数只增不减；配置不能改；结束后不能再改、不能再加步骤
UPDATE platform.diagnosis_session SET elapsed_sec = 90, item_count = 1 WHERE id = :d1;
SELECT t.throws(format($$UPDATE platform.diagnosis_session SET elapsed_sec = 10 WHERE id = %L$$, :d1), '22023', '用时不能倒退');
SELECT t.throws(format($$UPDATE platform.diagnosis_session SET resumed_at = resumed_at - interval '1 hour' WHERE id = %L$$, :d1), '22023', '续做时间不能倒退');
SELECT t.throws(format($$UPDATE platform.diagnosis_session SET item_count = 21 WHERE id = %L$$, :d1), '23514', '题数不能超过上限');
SELECT t.throws(format($$UPDATE platform.diagnosis_session SET time_budget_sec = 7200 WHERE id = %L$$, :d1), '42501', '学习服务不能改时长上限（列权限）');
UPDATE platform.diagnosis_session SET status = 'paused' WHERE id = :d1;
UPDATE platform.diagnosis_session SET status = 'active' WHERE id = :d1;
SELECT t.ok((SELECT status FROM platform.diagnosis_session WHERE id = :d1) = 'active', '可暂停后续做');
SELECT t.throws(format($$DELETE FROM platform.diagnosis_step WHERE id = %L$$, :st1), '42501', '学习服务不能删步骤');
SELECT t.throws(format($$DELETE FROM platform.diagnosis_session WHERE id = %L$$, :d1), '42501', '学习服务不能删会话');

-- ================= 二、过程观察进复核队列 =================
INSERT INTO platform.diagnosis_step (id, session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
VALUES (:st3, :d1, 4, 'process', 'MATH.FUNC.QUAD.GRAPH', 'synthetic-0301-08', 'process_near_gap', gen_random_uuid());
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{process_observation}')$$, :S1, :st3),
                '42501', '未作答的过程题不能进队');
RESET ROLE;
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES ('9d300000-0000-4000-8000-0000000009d3', :S1, 'aya', 'a8d00000-0000-4000-8000-0000000000d3', '2026-10-10 09:05:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 0.95,
        'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, '{}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid());
SET ROLE starbuddy_learning;
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET work_evidence_id = 'e1000000-0000-4000-8000-000000000001' WHERE id = %L$$, :st3),
                '22023', '过程原文证据必须是本步题目的学生原文（e1 是 0301-07 的，st3 出的是 0301-08）');
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET work_evidence_id = 'e2d80000-0000-4000-8000-0000000000e8' WHERE id = %L$$, :st3),
                '22023', '过程原文证据不能是别的学生的');
UPDATE platform.diagnosis_step SET work_evidence_id = 'e1d80000-0000-4000-8000-0000000000e8' WHERE id = :st3;
SELECT t.ok((SELECT work_evidence_id FROM platform.diagnosis_step WHERE id = :st3) = 'e1d80000-0000-4000-8000-0000000000e8', '过程题记下本人本题的原文证据引用');
UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = '9d300000-0000-4000-8000-0000000009d3', answered_at = now() WHERE id = :st3;
INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
VALUES ('aya', :S1, 'diagnosis_step', :st3, '{process_observation}');
SELECT t.ok((SELECT count(*) FROM platform.review_task WHERE source_kind = 'diagnosis_step' AND source_id = :st3) = 1, '已作答的过程题以过程观察进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{process_observation}')$$, :S1, :st1),
                '42501', '自适应题不能以过程观察进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{process_observation}')$$, :S2, :st3),
                '42501', '学生与步骤不一致不能进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'grade_result', %L, '{process_observation}')$$, :S1, :gd1),
                '42501', '学习服务不能让判分以过程观察进队（策略）');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{spot_check}')$$, :S1, :st1),
                '42501', '学习服务不能让诊断步骤以别的原因进队（策略）');
RESET ROLE;
-- 约束层（策略之外的第二道）：即使绕过策略，库也拒绝
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'grade_result', %L, '{process_observation}')$$, :S1, :gd1),
                '23514', '约束：判分不能以过程观察进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{spot_check}')$$, :S1, :st1),
                '23514', '约束：诊断步骤只能以过程观察进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'diagnosis_step', %L, '{process_observation,spot_check}')$$, :S1, :st1),
                '23514', '约束：过程观察不能和别的原因混在一起');
-- 审核台详情给出步骤引用与判分的证据 ID，不含选题理由
SELECT id AS rt FROM platform.review_task WHERE source_id = :st3 \gset
SELECT t.as_staff(:sf, format('SELECT platform.take_review_task(%L)::text', :'rt')) AS taken \gset
SELECT t.ok(t.as_staff(:sf, format('SELECT platform.review_task_detail(%L) -> ''source'' ->> ''work_evidence_id''', :'rt')) = 'e1d80000-0000-4000-8000-0000000000e8',
            '审核台详情给出过程原文证据的引用（契约审查 B1）');
SELECT t.ok(t.as_staff(:sf, format('SELECT platform.review_task_detail(%L) -> ''source'' ->> ''kind''', :'rt')) = 'process'
            AND t.as_staff(:sf, format('SELECT ((platform.review_task_detail(%L) -> ''source'') ? ''reason_code'')::text', :'rt')) = 'false',
            '审核台详情给出过程题的节点、题目与判分引用，不给选题理由');
SELECT t.throws(format('SELECT t.as_staff(%L, format(''SELECT platform.resolve_review_task(%%L, ''''upheld'''')::text'', %L))', :sf, :'rt'),
                '22023', '过程观察不能用判分的结案码');
SELECT t.as_staff(:sf, format('SELECT platform.resolve_review_task(%L, ''process_noted'')::text', :'rt')) AS resolved \gset
SELECT t.ok((SELECT status FROM platform.review_task WHERE id = :'rt') = 'resolved', '过程观察以“已看过程并记录”结案');

-- ================= 三、可见性：学生本人只看进度列；家长、其他学生看不到 =================
SELECT t.ok(t.ob(:aS1, format($$SELECT status || ':' || item_count FROM platform.diagnosis_session WHERE id = %L$$, :d1)) = 'active:1',
            '学生本人能读自己会话的进度');
SELECT t.ok(t.ob(:aS1, format($$SELECT count(*)::text FROM platform.diagnosis_step WHERE session_id = %L$$, :d1)) = '4',
            '学生本人能读自己会话的步骤（题目与状态）');
SELECT t.throws(format($$SELECT t.ob(%L, 'SELECT reason_code FROM platform.diagnosis_step LIMIT 1')$$, :aS1), '42501', '学生读不到选题理由');
SELECT t.throws(format($$SELECT t.ob(%L, 'SELECT algorithm_version FROM platform.diagnosis_session LIMIT 1')$$, :aS1), '42501', '学生读不到算法版本');
SELECT t.throws(format($$SELECT t.ob(%L, 'SELECT grade_result_id::text FROM platform.diagnosis_step LIMIT 1')$$, :aS1), '42501', '学生经这张表读不到判分引用');
SELECT t.throws(format($$SELECT t.ob(%L, 'SELECT work_evidence_id::text FROM platform.diagnosis_step LIMIT 1')$$, :aS1), '42501', '学生经这张表读不到过程原文引用');
SELECT t.ok(t.ob(:gA, 'SELECT count(*)::text FROM platform.diagnosis_session') = '0', '家长读不到孩子的诊断会话');
SELECT t.ok(t.ob(:gA, 'SELECT count(*)::text FROM platform.diagnosis_step') = '0', '家长读不到孩子的诊断步骤');
SELECT t.ok(t.ob(:aS2, format($$SELECT count(*)::text FROM platform.diagnosis_session WHERE student_id = %L$$, :S1)) = '0'
            AND t.ob(:aS2, format($$SELECT count(*)::text FROM platform.diagnosis_step WHERE session_id = %L$$, :d1)) = '0', '其他学生读不到');
SELECT t.ok(t.ob(NULL, 'SELECT count(*)::text FROM platform.diagnosis_session') = '0', '未登录读不到');
SELECT t.throws(format($$SELECT t.ob(%L, format('INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key) VALUES (%%L, ''aya'', ''x@0.1.0'', ''0.2.0'', gen_random_uuid(), gen_random_uuid())', %L))$$, :aS1, :S1),
                '42501', 'BFF 不能建会话（只有学习服务能写）');
SELECT t.throws(format($$SELECT t.ob(%L, format('UPDATE platform.diagnosis_session SET status = ''finished'' WHERE id = %%L', %L))$$, :aS1, :d1),
                '42501', 'BFF 不能改会话');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', 'SELECT count(*)::text FROM platform.diagnosis_session')$$, '42501', '洞察服务读不到会话');
SELECT t.throws($$SELECT t.as_role('starbuddy_review', 'SELECT count(*)::text FROM platform.diagnosis_step')$$, '42501', '审核台不能直接读步骤表');

-- ================= 四、结束与级联删除 =================
SET ROLE starbuddy_learning;
UPDATE platform.diagnosis_session SET status = 'finished', finished_at = now() WHERE id = :d1;
SELECT t.throws(format($$UPDATE platform.diagnosis_session SET status = 'active', finished_at = NULL WHERE id = %L$$, :d1), '22023', '结束的会话不能再打开');
SELECT t.throws(format($$UPDATE platform.diagnosis_step SET status = 'skipped' WHERE session_id = %L AND status = 'presented'$$, :d1),
                '22023', '会话结束后，未作答的步骤也不能再改');
SELECT t.throws(format($$INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, idempotency_key)
                         VALUES (%L, 9, 'adaptive', 'MATH.FUNC.QUAD.AXIS', 'synthetic-0301-07', 'target', gen_random_uuid())$$, :d1),
                '22023', '结束的会话不能再加步骤');
INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
VALUES (:d2, :S1, 'aya', 'aya-diag-trace@0.1.0', '0.2.0', 'e5000000-0000-4000-8000-0000000000e2', 'e6000000-0000-4000-8000-0000000000e2');
SELECT t.ok(true, '上一个会话结束后可以开新会话');
RESET ROLE;
SAVEPOINT cascade_check;
DELETE FROM platform.review_task WHERE student_id = :S1;
DELETE FROM platform.diagnosis_step WHERE grade_result_id IS NOT NULL AND session_id IN (SELECT id FROM platform.diagnosis_session WHERE student_id = :S1);
SELECT t.ok(true, '（级联检查前清掉引用判分的步骤）');
ROLLBACK TO SAVEPOINT cascade_check;
SELECT t.ok((SELECT confdeltype FROM pg_constraint WHERE conrelid = 'platform.diagnosis_session'::regclass AND contype = 'f'
               AND confrelid = 'platform.student_profile'::regclass) = 'c'
            AND (SELECT confdeltype FROM pg_constraint WHERE conrelid = 'platform.diagnosis_step'::regclass AND contype = 'f'
               AND confrelid = 'platform.diagnosis_session'::regclass) = 'c',
            '删除学生档案时会话与步骤级联删除');

ROLLBACK;
