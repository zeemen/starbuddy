-- 0023 统一复核队列（specs/review-queue-sprint2/spec.md 验收）：进队、领取、放回、结案白名单、审核台读函数、抽检、画像异议。
-- 全部合成数据；整个文件在一个事务里，结束回滚，不影响 80 的可见性矩阵。
-- staff：5f（在用）、5e（在用，76 建）、5d（已停用，79b 建）；学生 c1（家庭 A）、c2（家庭 B）。
\set sf '''5f000000-0000-4000-8000-00000000005f'''
\set se '''5e000000-0000-4000-8000-00000000005e'''
\set sdis '''5d000000-0000-4000-8000-00000000005d'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set g1 '''9b100000-0000-4000-8000-0000000009b1'''
\set g2 '''9b200000-0000-4000-8000-0000000009b2'''
BEGIN;

-- 以 staff 身份在 starbuddy_review 下执行一句，返回文本（t.as_staff 在 76 定义）
CREATE FUNCTION t.rq(p_staff uuid, p_sql text) RETURNS text LANGUAGE sql AS $$ SELECT t.as_staff(p_staff, p_sql) $$;
-- 以某个应用角色执行一句（不设账号）
CREATE FUNCTION t.as_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.rq(uuid, text), t.as_role(text, text) TO PUBLIC;

-- ================= 进队：学习服务写判分时同一事务进队 =================
SET ROLE starbuddy_learning;
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES (:g1, :S1, 'zhixing', 'a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'partial', 'zhixing-synthetic@0.1.0', 2, 4, false, 0.4,
        'rubric:zhixing-synthetic@0.1.0;prompt:zhixing-m3-grade@0.2.0', 'model:fake-a/fake-reasoner@2026-09-01', true,
        '{subjective_dimension}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()),
       (:g2, :S2, 'aya', 'a9000000-0000-4000-8000-0000000000a9', '2026-10-09 11:30:00+00', 'partial', 'aya-symbolic-ts@0.1.0', 1, 2, true, 0.9,
        'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', true, '{student_appeal}', 'formal',
        '{e2000000-0000-4000-8000-000000000002}', gen_random_uuid());
INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids)
VALUES (:g1, 'p1', 2, 2, 'rubric_model', true, NULL, 0.4, '{e1000000-0000-4000-8000-000000000001}'),
       (:g1, 'p2', 0, 2, 'rubric_model', true, NULL, 0.4, '{e1000000-0000-4000-8000-000000000001}');
INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
VALUES ('zhixing', :S1, 'grade_result', :g1, '{subjective_dimension}'), ('aya', :S2, 'grade_result', :g2, '{student_appeal}');
SELECT t.ok((SELECT count(*) FROM platform.review_task WHERE source_id IN (:g1, :g2) AND status = 'open') = 2,
            '需要复核的判分与申诉由学习服务进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('zhixing', %L, 'grade_result', %L, '{subjective_dimension}')$$, :S1, :g1),
                '23505', '同一判分重放不重复进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'grade_result', gen_random_uuid(), '{low_confidence}')$$, :S1),
                '42501', '学习服务不能为不存在的判分进队');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('zhixing', %L, 'grade_result', %L, '{low_confidence}')$$, :S2, :g1),
                '42501', '进队的学生必须与判分一致');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'grade_result', %L, '{low_confidence}')$$, :S1, :g1),
                '42501', '进队的学科必须与判分一致');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'skill_observation', gen_random_uuid(), '{spot_check}')$$, :S1),
                '42501', '学习服务不能放抽检任务');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'claim', 'c1a10000-0000-4000-8000-0000000000c1', '{profile_dispute}')$$, :S1),
                '42501', '学习服务不能放画像异议');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons, status, assigned_to, assigned_at)
                         VALUES ('aya', %L, 'skill_observation', gen_random_uuid(), '{low_confidence}', 'in_progress', %L, now())$$, :S1, :sf),
                '42501', '学习服务不能插入已领取的任务');
SELECT t.throws(format('SELECT platform.take_review_task(%L)', (SELECT id FROM platform.review_task WHERE source_id = :g1)),
                '42501', '学习服务不能领取');
SELECT t.throws($$SELECT count(*) FROM platform.review_queue(NULL, NULL, NULL, 10, NULL)$$, '42501', '学习服务不能调用审核台读函数');
SELECT t.throws($$SELECT platform.review_sample_spot_checks('aya', now() - interval '1 day', 0.1)$$, '42501', '学习服务不能抽检');
SELECT t.throws($$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')$$, '42501', '学习服务不能放画像异议（函数）');
RESET ROLE;
SELECT id AS t1 FROM platform.review_task WHERE source_id = :g1 \gset
SELECT id AS t2 FROM platform.review_task WHERE source_id = :g2 \gset
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'claim', gen_random_uuid(), '{low_confidence}')$$, :S1),
                '23514', '指向判断的任务必须是画像异议');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                         VALUES ('aya', %L, 'grade_result', gen_random_uuid(), '{profile_dispute}')$$, :S1),
                '23514', '画像异议只能指向判断');
SELECT t.throws(format($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons, status)
                         VALUES ('aya', %L, 'skill_observation', gen_random_uuid(), '{low_confidence}', 'in_progress')$$, :S1),
                '23514', '处理中的任务必须有领取人');

-- ================= 审核台只经读函数看队列 =================
SET ROLE starbuddy_review;
SELECT t.as_account(:sf);
SELECT t.throws($$SELECT count(*) FROM platform.review_task$$, '42501', '审核台不能直接读队列表（有 student_id）');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok(t.rq(:sf, $$SELECT count(*)::text FROM platform.review_queue(NULL, 'open', NULL, 50, NULL) WHERE id IN (SELECT NULL::uuid)$$) = '0'
            AND t.rq(:sf, format($$SELECT string_agg(source_id::text, ',' ORDER BY source_id) FROM platform.review_queue(NULL, 'open', NULL, 200, NULL)
                                   WHERE source_id IN (%L, %L)$$, :g1, :g2)) = :g1 || ',' || :g2,
            'staff 经读函数看到待处理任务');
SELECT t.ok(t.rq(:sf, $$SELECT count(*)::text FROM platform.review_queue('aya', 'open', 'student_appeal', 200, NULL)$$)::int >= 1
            AND t.rq(:sf, $$SELECT bool_and(subject = 'aya' AND 'student_appeal' = ANY (reasons))::text
                             FROM platform.review_queue('aya', 'open', 'student_appeal', 200, NULL)$$) = 'true',
            '队列按学科、状态、原因筛选');
SELECT t.ok(pg_get_function_result('platform.review_queue(platform.subject, text, text, integer, timestamptz)'::regprocedure) !~ 'student_id'
            AND t.rq(:sf, format($$SELECT student_ref FROM platform.review_queue(NULL, NULL, NULL, 200, NULL) WHERE id = %L$$, :'t1')) ~ '^s-[0-9a-f]{12}$'
            AND position(replace(:S1, '-', '') IN t.rq(:sf, format($$SELECT platform.review_task_detail(%L)::text$$, :'t1'))) = 0
            AND position(:S1 IN t.rq(:sf, format($$SELECT platform.review_task_detail(%L)::text$$, :'t1'))) = 0,
            '队列与详情只给学生的不透明指针，不给 student_id');
SELECT t.ok(t.rq(:sf, format($$SELECT student_ref FROM platform.review_queue(NULL, NULL, NULL, 200, NULL) WHERE id = %L$$, :'t1'))
            <> t.rq(:sf, format($$SELECT student_ref FROM platform.review_queue(NULL, NULL, NULL, 200, NULL) WHERE id = %L$$, :'t2')),
            '不同学生的指针不同');
SELECT t.ok(t.rq(:sf, format($$SELECT jsonb_array_length(platform.review_task_detail(%L)->'source'->'dimensions')::text$$, :'t1')) = '2'
            AND t.rq(:sf, format($$SELECT (platform.review_task_detail(%L)->'source'->>'model_version')$$, :'t1')) = 'model:fake-a/fake-reasoner@2026-09-01'
            AND t.rq(:sf, format($$SELECT (platform.review_task_detail(%L)->'source'->'dimensions'->0 ? 'confidence')::text$$, :'t1')) = 'true',
            '详情带逐维分、置信度与版本');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :gA, $$SELECT count(*)::text FROM platform.review_queue(NULL, NULL, NULL, 10, NULL)$$),
                '42501', '非 staff 账号不能看队列');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sdis, $$SELECT count(*)::text FROM platform.review_queue(NULL, NULL, NULL, 10, NULL)$$),
                '42501', '已停用的 staff 不能看队列');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, $$SELECT count(*)::text FROM platform.review_queue(NULL, NULL, NULL, 500, NULL)$$),
                '22023', '一页最多 200 条');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, $$SELECT count(*)::text FROM platform.review_queue(NULL, 'closed', NULL, 10, NULL)$$),
                '22023', '状态只收 open / in_progress / resolved');

-- ================= 领取、放回、结案 =================
SELECT t.throws(format('SELECT t.rq(%L, %L)', :gA, format('SELECT platform.take_review_task(%L)::text', :'t1')), '42501', '非 staff 不能领取');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sdis, format('SELECT platform.take_review_task(%L)::text', :'t1')), '42501', '已停用的 staff 不能领取');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'upheld')),
                '23514', '没领取不能结案');
SELECT t.rq(:sf, format('SELECT platform.take_review_task(%L)::text', :'t1'));
SELECT t.ok((SELECT status = 'in_progress' AND assigned_to = :sf::uuid AND assigned_at IS NOT NULL FROM platform.review_task WHERE id = :'t1'),
            '领取后处理中，记领取人与时间');
SELECT t.rq(:sf, format('SELECT platform.take_review_task(%L)::text', :'t1'));
SELECT t.ok((SELECT assigned_to = :sf::uuid FROM platform.review_task WHERE id = :'t1'), '重复领取自己的任务是幂等的');
SELECT t.ok(t.rq(:sf, format($$SELECT assigned_to_me::text FROM platform.review_queue(NULL, NULL, NULL, 200, NULL) WHERE id = %L$$, :'t1')) = 'true'
            AND t.rq(:se, format($$SELECT (assigned AND NOT assigned_to_me)::text FROM platform.review_queue(NULL, NULL, NULL, 200, NULL) WHERE id = %L$$, :'t1')) = 'true',
            '队列标出是否已领取、是否是自己领取的（不给领取人 ID）');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :se, format('SELECT platform.take_review_task(%L)::text', :'t1')), '23514', '别人领取的不能领');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :se, format('SELECT platform.release_review_task(%L)::text', :'t1')), '23514', '别人领取的不能放回');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :se, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'upheld')),
                '23514', '别人领取的不能结案');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'teacher_confirmed')),
                '22023', '结案码不在白名单里被拒');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'dispute_forwarded')),
                '22023', '结案码不适用于这类任务被拒（“已转洞察服务”只用于画像异议）');
SELECT t.rq(:sf, format('SELECT platform.release_review_task(%L)::text', :'t1'));
SELECT t.ok((SELECT status = 'open' AND assigned_to IS NULL AND assigned_at IS NULL FROM platform.review_task WHERE id = :'t1'), '放回后回到待处理');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.release_review_task(%L)::text', :'t1')), '23514', '没领取的不能放回');
SELECT t.rq(:se, format('SELECT platform.take_review_task(%L)::text', :'t1'));
SELECT t.rq(:se, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'regrade_required'));
SELECT t.ok((SELECT status = 'resolved' AND resolution_code = 'regrade_required' AND resolved_by_account = :se::uuid
                    AND assigned_to = :se::uuid AND resolved_at IS NOT NULL FROM platform.review_task WHERE id = :'t1'),
            '领取人结案：码、结案人、时间齐全，领取人保留');
SELECT t.ok((SELECT score_total = 2 AND superseded_by IS NULL FROM platform.grade_result WHERE id = :g1), '结案不改分（改判另写一条判分）');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.take_review_task(%L)::text', :'t1')), '23514', '已结案的不能再领');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :se, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t1', 'upheld')),
                '23514', '已结案的不能再结案');
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, 'SELECT platform.take_review_task(gen_random_uuid())::text'), 'P0002', '不存在的任务');
SELECT t.ok(t.rq(:sf, format($$SELECT (platform.review_task_detail(%L)->>'resolved_by_me')$$, :'t1')) = 'false'
            AND t.rq(:se, format($$SELECT (platform.review_task_detail(%L)->>'resolved_by_me')$$, :'t1')) = 'true',
            '详情标出是否是自己结的案（不给结案人 ID）');
SELECT t.ok(t.rq(:sf, $$SELECT count(*)::text FROM platform.review_resolution_code$$) = '5', '审核台能读结案码白名单');
SELECT t.throws($$SELECT t.as_role('starbuddy_bff', 'SELECT count(*)::text FROM platform.review_resolution_code')$$, '42501', 'BFF 读不到结案码表');
SELECT t.throws(format($$SELECT t.as_role('starbuddy_bff', %L)$$, 'SELECT count(*)::text FROM platform.review_queue(NULL, NULL, NULL, 10, NULL)'),
                '42501', 'BFF 不能调用审核台读函数');
SELECT t.throws(format($$SELECT t.as_role('starbuddy_review', %L)$$, format('UPDATE platform.review_task SET status = %L WHERE id = %L', 'open', :'t1')),
                '42501', '审核台不能直接改任务');

-- ================= 抽检（调度角色） =================
SET ROLE starbuddy_learning;
INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
  calibrated, confidence, source_version, model_version, verifier_id, verifier_version, human_review_required, semantics, evidence_ids,
  source_request_id)
SELECT :S1, 'wenli', 'a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1,
       'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0', false, 'formal',
       '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()
  FROM generate_series(1, 400);
RESET ROLE;
SELECT count(*) AS expected_spot FROM platform.grade_result g
 WHERE g.subject = 'wenli' AND g.superseded_by IS NULL AND g.created_at >= now() - interval '1 day'
   AND ('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(0.1 * 4294967296)
   AND NOT EXISTS (SELECT 1 FROM platform.review_task t WHERE t.source_kind = 'grade_result' AND t.source_id = g.id) \gset
SELECT t.ok(t.as_role('starbuddy_scheduler', $$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.1)::text$$)
            = :'expected_spot', '抽检按判分 ID 的哈希确定性抽样');
SELECT t.ok(:expected_spot BETWEEN 15 AND 70, '抽检比例大致符合（400 条抽 10%）');
SELECT t.ok((SELECT count(*) FROM platform.review_task WHERE subject = 'wenli' AND reasons = '{spot_check}' AND status = 'open') = :expected_spot,
            '抽中的以 spot_check 原因进队');
SELECT t.ok(t.as_role('starbuddy_scheduler', $$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.1)::text$$) = '0',
            '再跑一次不重复进队');
SELECT t.ok(t.as_role('starbuddy_scheduler', $$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.2)::text$$)::int > 0
            AND (SELECT count(*) FROM (SELECT source_id FROM platform.review_task WHERE source_kind = 'grade_result' GROUP BY source_id HAVING count(*) > 1) d) = 0,
            '提高比例只补新抽中的，已进队的不重复');
SELECT t.throws($$SELECT t.as_role('starbuddy_scheduler', $q$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.6)::text$q$)$$,
                '22023', '抽检比例不超过 0.5');
SELECT t.throws($$SELECT t.as_role('starbuddy_scheduler', $q$SELECT platform.review_sample_spot_checks('wenli', now() - interval '40 days', 0.1)::text$q$)$$,
                '22023', '抽检最多往前 31 天');
SELECT t.throws($$SELECT t.as_role('starbuddy_review', $q$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.1)::text$q$)$$,
                '42501', '审核台不能抽检');
SELECT t.throws($$SELECT t.as_role('starbuddy_bff', $q$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.1)::text$q$)$$,
                '42501', 'BFF 不能抽检');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', $q$SELECT platform.review_sample_spot_checks('wenli', now() - interval '1 day', 0.1)::text$q$)$$,
                '42501', '洞察服务不能抽检');

-- ================= 画像异议（洞察服务） =================
SELECT t.as_role('starbuddy_insight', $$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')::text$$) AS t3 \gset
SELECT t.ok((SELECT source_kind = 'claim' AND reasons = '{profile_dispute}' AND subject = 'aya' AND student_id = :S1::uuid AND status = 'open'
               FROM platform.review_task WHERE id = :'t3'), '洞察服务把画像异议放进同一队列');
SELECT t.ok(t.as_role('starbuddy_insight', $$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')::text$$) = :'t3',
            '同一判断未结案时再入队返回原任务');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', $q$SELECT platform.enqueue_claim_dispute(gen_random_uuid())::text$q$)$$,
                '22023', '判断不存在时拒绝');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', $q$SELECT platform.enqueue_claim_dispute('c3a30000-0000-4000-8000-0000000000c3')::text$q$)$$,
                '22023', '跨科判断暂不入队');
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET status = 'withdrawn' WHERE id = 'c2a20000-0000-4000-8000-0000000000c2';
ALTER TABLE insight.claim ENABLE TRIGGER USER;
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', $q$SELECT platform.enqueue_claim_dispute('c2a20000-0000-4000-8000-0000000000c2')::text$q$)$$,
                '22023', '已终结（撤回）的判断拒绝');
SELECT t.throws($$SELECT t.as_role('starbuddy_review', $q$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')::text$q$)$$,
                '42501', '审核台不能放画像异议');
SELECT t.throws($$SELECT t.as_role('starbuddy_bff', $q$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')::text$q$)$$,
                '42501', 'BFF 不能放画像异议');
SELECT t.ok(t.rq(:sf, format($$SELECT (platform.review_task_detail(%L)->'source'->>'template_key')$$, :'t3')) = 'test.node.mastery'
            AND t.rq(:sf, format($$SELECT (platform.review_task_detail(%L)->'source' ? 'value')::text$$, :'t3')) = 'false',
            '画像异议详情给判断的模板与状态，不给判断的 value');
SELECT t.rq(:sf, format('SELECT platform.take_review_task(%L)::text', :'t3'));
SELECT t.throws(format('SELECT t.rq(%L, %L)', :sf, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t3', 'regrade_required')),
                '22023', '画像异议不能用“需要改判”结案');
SELECT t.rq(:sf, format('SELECT platform.resolve_review_task(%L, %L)::text', :'t3', 'dispute_forwarded'));
SELECT t.ok((SELECT status::text FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1') = 'established',
            '结案不改判断（判断的流转由洞察服务做）');
SELECT t.throws($$SELECT t.as_role('starbuddy_insight', $q$SELECT platform.enqueue_claim_dispute('c1a10000-0000-4000-8000-0000000000c1')::text$q$)$$,
                '23514', '已复核过的判断再入队拒绝');

ROLLBACK;
