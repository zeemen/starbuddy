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
                 gen_random_uuid() AS attempt_id, now() AS attempt_occurred_at, 'correct'::text AS verdict,
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

-- 判分维度
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES ('9a000000-0000-4000-8000-00000000009a', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', gen_random_uuid(), now(), 'partial',
  'wenli-essay-5d@1.2.0', 8, 10, false, 0.4, 'rubric:wenli-essay-5d@1.2.0', 'model:deepseek/deepseek-chat@2026-09', true,
  '{subjective_dimension}', 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()),
       ('9b000000-0000-4000-8000-00000000009b', 'c1000000-0000-4000-8000-0000000000c1', 'wenli', gen_random_uuid(), now(), 'partial',
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
RESET ROLE;
