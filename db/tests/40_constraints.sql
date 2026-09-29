-- 库级约束：与契约 v0.9 一致的判分溯源规则，外加契约里表达不了的“分数不超过满分”。
-- 以学习服务身份写入（它是唯一能写的角色），期望 23514 check_violation。
SET ROLE starbuddy_learning;


-- 证据 payload（契约 EvidencePayload*；echo #12）
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2026-10-11 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'aya', 'verifier_trace', 'admitted',
                  '{"sampled_points": 8, "agreeing_points": 8, "feedback": "满分！"}', 'grade', gen_random_uuid(),
                  'synthetic-constraint-0001')$$, '23514', '证据 payload 不能夹带评语字段');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2026-10-11 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'echo', 'interaction_signal', 'admitted',
                  '{"signal_code": "xp_total", "value": 1200}', 'diagnose', gen_random_uuid(), 'synthetic-constraint-0002')$$,
                '23514', '交互信号不能写 XP');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  media_ref, source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2026-10-11 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'ocr_transcription', 'pending_review', '{"text": "（合成）"}',
                  'objstore://老师评语：应给满分', 'diagnose', gen_random_uuid(), 'synthetic-constraint-0003')$$,
                '23514', '对象存储引用不能夹带文字');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2030-01-01 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'aya', 'response_text', 'admitted', '{"text": "x"}', 'grade',
                  gen_random_uuid(), 'synthetic-constraint-0004')$$,
                '23514', '分区范围外的写入直接失败（提醒分区没建好）');

-- 推断必须带证据（规则 6）
SELECT t.throws($$INSERT INTO platform.error_attribution (student_id, subject, subject_category, cross_subject_category, confidence,
                  algorithm_version, evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'shenti', 'reading_the_question', 0.4, 'aya-attr@0.1.0', '{}', gen_random_uuid())$$,
                '23514', '错因归因必须带证据 ID');
SELECT t.throws($$INSERT INTO platform.error_attribution (student_id, subject, subject_category, cross_subject_category, confidence,
                  algorithm_version, evidence_ids, source_request_id) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'shenti', 'reading_the_question', 0.4, '凭感觉', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid())$$,
                '23514', '算法版本必须是 名称@版本');

-- 判分结果（契约 GradeResponse 的结构规则）
CREATE FUNCTION pg_temp.grade_sql(overrides text) RETURNS text LANGUAGE sql AS $f$
  SELECT format($q$INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
    score_total, max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons,
    verifier_id, verifier_version, verifier_reason_codes, semantics, evidence_ids, source_request_id)
    SELECT student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total, calibrated,
           confidence, source_version, model_version, human_review_required, review_reasons::platform.review_reason[],
           verifier_id, verifier_version, verifier_reason_codes, semantics::platform.semantics, evidence_ids, source_request_id
    FROM (SELECT 'c1000000-0000-4000-8000-0000000000c1'::uuid AS student_id, 'aya'::platform.subject AS subject,
                 'a7000000-0000-4000-8000-0000000000a7'::uuid AS attempt_id,
                 '2026-10-08 11:30:00+00'::timestamptz AS attempt_occurred_at, 'correct'::text AS verdict,
                 'aya-symbolic-ts@0.1.0'::text AS rubric_version, 1::numeric AS score_total, 1::numeric AS max_total,
                 true AS calibrated, 1.0::numeric AS confidence, 'rubric:aya-symbolic-ts@0.1.0'::text AS source_version,
                 'algo:aya-symbolic-ts@0.1.0'::text AS model_version, false AS human_review_required,
                 '{}'::text[] AS review_reasons, 'aya-symbolic-ts'::text AS verifier_id, '0.1.0'::text AS verifier_version,
                 '{}'::text[] AS verifier_reason_codes, 'formal'::text AS semantics,
                 '{e1000000-0000-4000-8000-000000000001}'::uuid[] AS evidence_ids, gen_random_uuid() AS source_request_id) base
    %s$q$, overrides)
$f$;
-- 基线能写入
DO $$ BEGIN EXECUTE pg_temp.grade_sql(''); END $$;
SELECT t.ok(true, '合法的验证器判分可以写入');
-- 用 CROSS JOIN LATERAL 覆盖单个字段不方便，这里直接替换 SELECT 列表里的值
SELECT t.throws(replace(pg_temp.grade_sql(''), 'false AS human_review_required', 'true AS human_review_required'),
                '23514', 'human_review_required 为真时必须有原因');
SELECT t.throws(replace(pg_temp.grade_sql(''), '''algo:aya-symbolic-ts@0.1.0''::text AS model_version', '''routed''::text AS model_version'),
                '23514', 'model_version 不能写 routed');
SELECT t.throws(replace(pg_temp.grade_sql(''), '1::numeric AS score_total', '2::numeric AS score_total'),
                '23514', '总分不能超过满分（契约里表达不了，由库约束）');
SELECT t.throws(replace(pg_temp.grade_sql(''), '''correct''::text AS verdict', '''needs_review''::text AS verdict'),
                '23514', 'needs_review 必须复核且低置信');
SELECT t.throws(replace(pg_temp.grade_sql(''), '''{}''::text[] AS verifier_reason_codes', '''{sampling_insufficient}''::text[] AS verifier_reason_codes'),
                '23514', '验证器报了边界原因码必须复核');
SELECT t.throws(replace(pg_temp.grade_sql(''), '''0.1.0''::text AS verifier_version', 'NULL::text AS verifier_version'),
                '23514', '验证器必须带版本');
SELECT t.throws(replace(replace(replace(replace(pg_temp.grade_sql(''),
                  '''correct''::text AS verdict', '''needs_review''::text AS verdict'),
                  'false AS human_review_required', 'true AS human_review_required'),
                  '1.0::numeric AS confidence', '0.3::numeric AS confidence'),
                  '''{}''::text[] AS review_reasons', '''{spot_check}''::text[] AS review_reasons'),
                '23514', 'needs_review 的复核原因必须含 verifier_boundary_case');
SELECT t.throws(replace(replace(replace(pg_temp.grade_sql(''),
                  '''{}''::text[] AS verifier_reason_codes', '''{老师说对}''::text[] AS verifier_reason_codes'),
                  'false AS human_review_required', 'true AS human_review_required'),
                  '''{}''::text[] AS review_reasons', '''{verifier_boundary_case}''::text[] AS review_reasons'),
                '23514', '验证器原因码只能取已知枚举');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2026-10-11 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'aya', 'response_text', 'not-registered-item', 'admitted', '{"text": "x"}',
                  'grade', gen_random_uuid(), 'synthetic-constraint-0005')$$,
                '23503', '证据引用的题目必须已登记');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES (gen_random_uuid(), '2026-10-11 00:00:00+00',
                  'c1000000-0000-4000-8000-0000000000c1', 'aya', 'response_text', 'admitted',
                  jsonb_build_object('text', repeat('x', 20001)), 'grade', gen_random_uuid(), 'synthetic-constraint-0006')$$,
                '23514', '证据文本不能超过 20000 字');
SELECT t.throws(replace(pg_temp.grade_sql(''), '''a7000000-0000-4000-8000-0000000000a7''::uuid AS attempt_id', 'gen_random_uuid() AS attempt_id'),
                '23503', '判分必须对应一次真实作答');

-- 判分维度
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES ('9a000000-0000-4000-8000-00000000009a', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'partial',
  'wenli-essay-5d@1.2.0', 8, 10, false, 0.4, 'rubric:wenli-essay-5d@1.2.0', 'model:deepseek/deepseek-chat@2026-09', true,
  '{subjective_dimension}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()),
       ('9b000000-0000-4000-8000-00000000009b', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'partial',
  'wenli-essay-5d@1.2.0', 8, 10, false, 0.9, 'rubric:wenli-essay-5d@1.2.0', 'model:deepseek/deepseek-chat@2026-09', false,
  '{}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid());
INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
VALUES ('9a000000-0000-4000-8000-00000000009a', 'structure', 8, 10, 'rubric_model', true, 0.4, '{e1000000-0000-4000-8000-000000000001}');
SELECT t.ok(true, '合法的主观维度（父判分已复核）可以写入');
SELECT t.throws($$INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                  VALUES ('9a000000-0000-4000-8000-00000000009a', 'content', 8, 10, 'rubric_model', false, 0.4, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '模型判分必须标为主观维度');
SELECT t.throws($$INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                  VALUES ('9a000000-0000-4000-8000-00000000009a', 'content', 8, 10, 'rubric_model', true, 0.9, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '主观维度置信度不能超过 0.5');
SELECT t.throws($$INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                  VALUES ('9a000000-0000-4000-8000-00000000009a', 'convention', 8, 10, 'rubric_rule', false, 0.9, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '规则判分必须带已登记的 rule_ref');
SELECT t.throws($$INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids)
                  VALUES ('9a000000-0000-4000-8000-00000000009a', 'convention', 11, 10, 'rubric_rule', false, 'wenli-rules@1.0.0', 0.9, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '维度分不能超过该维满分');
SELECT t.throws($$SET CONSTRAINTS platform.grade_dimension_requires_review IMMEDIATE;
                  INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                  VALUES ('9b000000-0000-4000-8000-00000000009b', 'structure', 8, 10, 'rubric_model', true, 0.4, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '有主观维度时父判分必须复核（跨行约束）');
-- 父判分已复核、原因也对，但整体置信度 0.9：主观维度要求父判分置信度 ≤ 0.5
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES ('9d000000-0000-4000-8000-00000000009d', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'a8000000-0000-4000-8000-0000000000a8',
  '2026-10-09 11:30:00+00', 'partial', 'wenli-essay-5d@1.2.0', 8, 10, false, 0.9, 'rubric:wenli-essay-5d@1.2.0',
  'model:deepseek/deepseek-chat@2026-09', true, '{subjective_dimension}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid());
SELECT t.throws($$SET CONSTRAINTS platform.grade_dimension_requires_review IMMEDIATE;
                  INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                  VALUES ('9d000000-0000-4000-8000-00000000009d', 'structure', 8, 10, 'rubric_model', true, 0.4, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '有主观维度时父判分置信度不能超过 0.5');
SELECT t.throws($$SET CONSTRAINTS platform.grade_dimension_requires_review IMMEDIATE;
                  INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids)
                  VALUES ('9a000000-0000-4000-8000-00000000009a', 'pronunciation', NULL, 10, 'verifier', false, 'echo-eval@0.1.0', 0.9, '{e1000000-0000-4000-8000-000000000001}')$$,
                '23514', '维度分为 null 时父判分必须带 dimension_unavailable 原因');

-- 复核队列必须有原因
SELECT t.throws($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
                  VALUES ('wenli', 'c1000000-0000-4000-8000-0000000000c1', 'grade_result', '9a000000-0000-4000-8000-00000000009a', '{}')$$,
                '23514', '复核任务必须有原因');
INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
VALUES ('wenli', 'c1000000-0000-4000-8000-0000000000c1', 'grade_result', '9a000000-0000-4000-8000-00000000009a', '{subjective_dimension}');
SELECT t.ok((SELECT count(*) FROM platform.review_task WHERE status = 'open') = 1, '主观判分进入统一复核队列');

-- 结案人不是教研账号时（例如人工误操作留下的数据），不能据此放行更正
RESET ROLE;
INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons, status, resolved_at, resolved_by_account, resolution_code)
VALUES ('wenli', 'c1000000-0000-4000-8000-0000000000c1', 'grade_result', '9d000000-0000-4000-8000-00000000009d', '{subjective_dimension}',
        'resolved', now(), 'a0000000-0000-4000-8000-00000000000a', 'guardian_says_ok');
SET ROLE starbuddy_learning;
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
VALUES ('9e000000-0000-4000-8000-00000000009e', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'a8000000-0000-4000-8000-0000000000a8',
  '2026-10-09 11:30:00+00', 'correct', 'wenli-essay-5d@1.2.0', 10, 10, false, 0.95, 'rubric:wenli-essay-5d@1.2.0',
  'model:deepseek/deepseek-chat@2026-09', false, 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid());
SELECT t.throws($$SET CONSTRAINTS platform.check_supersede_target IMMEDIATE;
                  UPDATE platform.grade_result SET superseded_by = '9e000000-0000-4000-8000-00000000009e'
                  WHERE id = '9d000000-0000-4000-8000-00000000009d'$$,
                '23514', '非教研账号结的案，不能用来放行“不复核”的更正');

-- 插入时不能一出生就是“已被取代”，创建时间不能伪造（安全审查 R6b）
SELECT t.throws($$INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
                  max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids,
                  source_request_id, superseded_by, superseded_at) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'a7000000-0000-4000-8000-0000000000a7', '2026-10-08 11:30:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1,
                  'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', false, 'formal', '{e1000000-0000-4000-8000-000000000001}',
                  gen_random_uuid(), '92000000-0000-4000-8000-000000000092', '2000-01-01')$$,
                '23514', '新判分不能插入成“已被取代”');
SELECT t.throws($$INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, confidence, algorithm_version,
                  evidence_ids, is_current, superseded_by, superseded_at) VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya',
                  'K-QF-0301', 'retain', 0.9, 'bkt-v0@0.1.0', '{e1000000-0000-4000-8000-000000000001}', false,
                  'ab000000-0000-4000-8000-0000000000ab', now())$$,
                '23514', '新掌握度不能插入成非当前');
INSERT INTO platform.error_attribution (id, student_id, subject, subject_category, cross_subject_category, confidence, algorithm_version,
  evidence_ids, source_request_id, created_at)
VALUES ('0e000000-0000-4000-8000-00000000000e', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'x', 'knowledge', 0.4, 'x@1.0.0',
        '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid(), '2099-01-01');
SELECT t.ok((SELECT created_at < now() + interval '1 minute' FROM platform.error_attribution WHERE id = '0e000000-0000-4000-8000-00000000000e'),
            '创建时间由库写 now()，不能伪造成未来');
-- 更正不能成环：两条判分互相取代
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, verifier_id, verifier_version, human_review_required, semantics,
  evidence_ids, source_request_id)
SELECT v.id, 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'a7000000-0000-4000-8000-0000000000a7', '2026-10-08 11:30:00+00', 'correct',
       'aya-symbolic-ts@0.1.0', 1, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0', 'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0',
       false, 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()
  FROM (VALUES ('f1000000-0000-4000-8000-0000000000f1'::uuid), ('f2000000-0000-4000-8000-0000000000f2'::uuid)) v(id);
SELECT t.throws($$SET CONSTRAINTS platform.check_supersede_target IMMEDIATE;
                  UPDATE platform.grade_result SET superseded_by = 'f2000000-0000-4000-8000-0000000000f2' WHERE id = 'f1000000-0000-4000-8000-0000000000f1';
                  UPDATE platform.grade_result SET superseded_by = 'f1000000-0000-4000-8000-0000000000f1' WHERE id = 'f2000000-0000-4000-8000-0000000000f2'$$,
                '23514', '更正不能成环（生效判分不能消失）');

-- 不能借“更正”绕过复核（安全审查阻断 6）：待复核的主观判分被一条不复核、高置信的判分取代
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
VALUES ('9c000000-0000-4000-8000-00000000009c', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', 'a8000000-0000-4000-8000-0000000000a8',
  '2026-10-09 11:30:00+00', 'correct', 'wenli-essay-5d@1.2.0', 10, 10, false, 0.95, 'rubric:wenli-essay-5d@1.2.0',
  'model:deepseek/deepseek-chat@2026-09', false, 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid());
SELECT t.throws($$SET CONSTRAINTS platform.check_supersede_target IMMEDIATE;
                  UPDATE platform.grade_result SET superseded_by = '9c000000-0000-4000-8000-00000000009c'
                  WHERE id = '9a000000-0000-4000-8000-00000000009a'$$,
                '23514', '复核未结案时，不能用“不复核”的判分取代待复核判分');
-- 学习服务不能自己结案，也不能插入“已结案”的复核任务（安全审查 R6a）
SELECT t.throws($$UPDATE platform.review_task SET status = 'resolved', resolved_at = now(), resolution_code = 'auto_ok'
                  WHERE source_id = '9a000000-0000-4000-8000-00000000009a'$$, '42501', '学习服务不能给复核任务结案');
SELECT t.throws($$INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons, status, resolved_at,
                  resolved_by_account, resolution_code) VALUES ('wenli', 'c1000000-0000-4000-8000-0000000000c1', 'grade_result',
                  '9d000000-0000-4000-8000-00000000009d', '{subjective_dimension}', 'resolved', now(),
                  '5f000000-0000-4000-8000-00000000005f', 'auto_ok')$$, '42501', '学习服务不能插入已结案的复核任务');
SELECT t.throws($$SELECT platform.resolve_review_task((SELECT id FROM platform.review_task LIMIT 1), 'auto_ok')$$,
                '42501', '学习服务不能调用结案函数');
-- 教研人员领取后结案可以（0023 起先领取、结案码在白名单里；审核台读不到队列表，任务 ID 由超级用户取出）
RESET ROLE;
SELECT id AS rt9a FROM platform.review_task WHERE source_id = '9a000000-0000-4000-8000-00000000009a' \gset
SET ROLE starbuddy_review;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws(format('SELECT platform.resolve_review_task(%L, %L)', :'rt9a', 'upheld'), '42501', '非教研账号不能结案');
SELECT t.as_account('5f000000-0000-4000-8000-00000000005f');
SELECT platform.take_review_task(:'rt9a');
SELECT platform.resolve_review_task(:'rt9a', 'upheld');
SELECT t.as_account(NULL);
RESET ROLE;
SELECT t.ok((SELECT resolved_by_account FROM platform.review_task WHERE id = :'rt9a')
            = '5f000000-0000-4000-8000-00000000005f', '结案记录结案人');
SET ROLE starbuddy_learning;
BEGIN;
UPDATE platform.grade_result SET superseded_by = '9c000000-0000-4000-8000-00000000009c' WHERE id = '9a000000-0000-4000-8000-00000000009a';
COMMIT;
SELECT t.ok((SELECT superseded_by FROM platform.grade_result WHERE id = '9a000000-0000-4000-8000-00000000009a') = '9c000000-0000-4000-8000-00000000009c',
            '复核结案后，判分可以被复核结果取代');
RESET ROLE;
