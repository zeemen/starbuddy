-- 合成夹具（CLAUDE.md 规则 8）：两户家庭 + 一位没有绑定的家长。
--   家庭 H1：家长 gA 创建，学生 S1
--   家庭 H2：家长 gB 创建，学生 S2；S2 的登录手机号与 gA 相同（用来测“同手机号不能跨档案”）
--   gC：没有任何绑定的家长（用来测邀请兑换）
-- 以超级用户写入（绕过 RLS），只用于准备数据。正式的注册、建档、绑定路径在 20_rls_household.sql 里
-- 以 BFF 身份通过函数走一遍（安全审查指出：只用超级用户夹具会漏掉这些路径的漏洞）。
INSERT INTO platform.account (id, kind) VALUES
  ('a0000000-0000-4000-8000-00000000000a', 'guardian'),   -- gA
  ('b0000000-0000-4000-8000-00000000000b', 'guardian'),   -- gB
  ('cc000000-0000-4000-8000-0000000000cc', 'guardian'),   -- gC
  ('a1000000-0000-4000-8000-0000000000a1', 'student'),    -- S1 的账号
  ('b1000000-0000-4000-8000-0000000000b1', 'student'),    -- S2 的账号
  ('5f000000-0000-4000-8000-00000000005f', 'staff');      -- 教研复核人员
-- 手机号用未分配的国家码 +999，避免碰到真实号码（安全审查建议 11）
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES
  ('a0000000-0000-4000-8000-00000000000a', '+99900000001', now()),
  ('b0000000-0000-4000-8000-00000000000b', '+99900000002', now()),
  ('b1000000-0000-4000-8000-0000000000b1', '+99900000001', now());  -- 与 gA 同号
INSERT INTO platform.household (id, created_by_account) VALUES
  ('d1000000-0000-4000-8000-0000000000d1', 'a0000000-0000-4000-8000-00000000000a'),
  ('d2000000-0000-4000-8000-0000000000d2', 'b0000000-0000-4000-8000-00000000000b');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'a1000000-0000-4000-8000-0000000000a1', 'd1000000-0000-4000-8000-0000000000d1', 9, 'nanjing', 'bubian-2024', '2012-03-01'),
  ('c2000000-0000-4000-8000-0000000000c2', 'b1000000-0000-4000-8000-0000000000b1', 'd2000000-0000-4000-8000-0000000000d2', 8, 'nanjing', 'bubian-2024', '2013-05-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  ('a0000000-0000-4000-8000-00000000000a', 'c1000000-0000-4000-8000-0000000000c1', 'household_creator'),
  ('b0000000-0000-4000-8000-00000000000b', 'c2000000-0000-4000-8000-0000000000c2', 'household_creator');

INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
VALUES ('e0000000-0000-4000-8000-0000000000e0', 'nanjing', 2027, 9, 'bubian-2024', 'aya', '0.1.0', 'published', now());
INSERT INTO platform.skill_node (subject, local_id, curriculum_version_id, label_code) VALUES
  ('aya', 'K-QF-0301', 'e0000000-0000-4000-8000-0000000000e0', 'quadratic-completing-square');
INSERT INTO platform.curriculum_version (id, region, exam_year, grade, textbook_version, subject, content_version, status, published_at)
VALUES ('e9000000-0000-4000-8000-0000000000e9', 'nanjing', 2027, 9, 'bubian-2024', 'wenli', '0.1.0', 'published', now()),
       ('ed000000-0000-4000-8000-0000000000ed', 'nanjing', 2027, 9, 'bubian-2024', 'aya', '0.2.0', 'draft', NULL);
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at) VALUES
  ('aya', 'synthetic-0301-07', 'e0000000-0000-4000-8000-0000000000e0', 'K-QF-0301', 'admitted', now()),
  ('aya', 'synthetic-0301-08', 'e0000000-0000-4000-8000-0000000000e0', 'K-QF-0301', 'pending_review', NULL),
  ('wenli', 'synthetic-essay-01', 'e9000000-0000-4000-8000-0000000000e9', NULL, 'admitted', now());

-- 两个学生各一条证据（2026-10，UTC）
INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
  admission, payload, source_method, request_id, request_idempotency_key) VALUES
  ('e1000000-0000-4000-8000-000000000001', '2026-10-08 11:30:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
   'response_text', 'synthetic-0301-07', 'K-QF-0301', 'admitted', '{"text": "2(x-1)"}', 'grade',
   'f0000000-0000-4000-8000-000000000001', 'synthetic-aya-grade-0001'),
  ('e2000000-0000-4000-8000-000000000002', '2026-10-09 11:30:00+00', 'c2000000-0000-4000-8000-0000000000c2', 'aya',
   'response_text', 'synthetic-0301-07', 'K-QF-0301', 'admitted', '{"text": "x^2"}', 'grade',
   'f0000000-0000-4000-8000-000000000002', 'synthetic-aya-grade-0002');
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, request_idempotency_key) VALUES
  ('a7000000-0000-4000-8000-0000000000a7', '2026-10-08 11:30:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
   'synthetic-0301-07', 'e1000000-0000-4000-8000-000000000001', 'synthetic-aya-grade-0001'),
  ('a8000000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'wenli',
   'synthetic-essay-01', 'e1000000-0000-4000-8000-000000000001', 'synthetic-wenli-grade-0001');
INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version, evidence_ids) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'execute', 0.55, 0.6, 'bkt-v0@0.1.0', '{e1000000-0000-4000-8000-000000000001}'),
  ('c2000000-0000-4000-8000-0000000000c2', 'aya', 'K-QF-0301', 'understand', 0.30, 0.5, 'bkt-v0@0.1.0', '{e2000000-0000-4000-8000-000000000002}');

-- 每个学生各一条观测、错因、判分（含维度）、同意、选科，用来验证“只看到自己的”
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, request_idempotency_key) VALUES
  ('a9000000-0000-4000-8000-0000000000a9', '2026-10-09 11:30:00+00', 'c2000000-0000-4000-8000-0000000000c2', 'aya',
   'synthetic-0301-07', 'e2000000-0000-4000-8000-000000000002', 'synthetic-aya-grade-0002');
INSERT INTO platform.skill_observation (student_id, subject, skill_local_id, outcome, confidence, algorithm_version, evidence_ids, source_request_id)
SELECT s, 'aya', 'K-QF-0301', 'partial', 0.5, 'aya-diag@0.1.0', ARRAY[e], gen_random_uuid()
  FROM (VALUES ('c1000000-0000-4000-8000-0000000000c1'::uuid, 'e1000000-0000-4000-8000-000000000001'::uuid),
               ('c2000000-0000-4000-8000-0000000000c2'::uuid, 'e2000000-0000-4000-8000-000000000002'::uuid)) v(s, e);
INSERT INTO platform.error_attribution (student_id, subject, skill_local_id, subject_category, cross_subject_category, confidence,
  algorithm_version, evidence_ids, source_request_id)
SELECT s, 'aya', 'K-QF-0301', 'computation', 'computation_or_grammar', 0.5, 'aya-attr@0.1.0', ARRAY[e], gen_random_uuid()
  FROM (VALUES ('c1000000-0000-4000-8000-0000000000c1'::uuid, 'e1000000-0000-4000-8000-000000000001'::uuid),
               ('c2000000-0000-4000-8000-0000000000c2'::uuid, 'e2000000-0000-4000-8000-000000000002'::uuid)) v(s, e);
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, verifier_id, verifier_version, human_review_required, semantics,
  evidence_ids, source_request_id) VALUES
  ('91000000-0000-4000-8000-000000000091', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'a7000000-0000-4000-8000-0000000000a7',
   '2026-10-08 11:30:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0',
   'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0', false, 'formal', '{e1000000-0000-4000-8000-000000000001}', gen_random_uuid()),
  ('92000000-0000-4000-8000-000000000092', 'c2000000-0000-4000-8000-0000000000c2', 'aya', 'a9000000-0000-4000-8000-0000000000a9',
   '2026-10-09 11:30:00+00', 'incorrect', 'aya-symbolic-ts@0.1.0', 0, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0',
   'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0', false, 'formal', '{e2000000-0000-4000-8000-000000000002}', gen_random_uuid());
INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids) VALUES
  ('91000000-0000-4000-8000-000000000091', 'answer', 1, 1, 'verifier', false, 'aya-symbolic-ts@0.1.0', 1, '{e1000000-0000-4000-8000-000000000001}'),
  ('92000000-0000-4000-8000-000000000092', 'answer', 0, 1, 'verifier', false, 'aya-symbolic-ts@0.1.0', 1, '{e2000000-0000-4000-8000-000000000002}');
INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'a0000000-0000-4000-8000-00000000000a', 'guardian_consent_under14', 'consent-doc@1.0.0', 'granted'),
  ('c2000000-0000-4000-8000-0000000000c2', 'b0000000-0000-4000-8000-00000000000b', 'guardian_consent_under14', 'consent-doc@1.0.0', 'granted');
INSERT INTO platform.subject_enrollment (student_id, subject) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'aya'), ('c2000000-0000-4000-8000-0000000000c2', 'aya');
-- 诊断会话（0027）：每个学生一个已结束的会话、一步，供 80 的可见性矩阵使用（只有学生本人看得到）
INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version, usage_id, idempotency_key) VALUES
  ('d1f00000-0000-4000-8000-0000000000f1', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'aya-diag-trace@0.1.0', '0.1.0',
   'e5f00000-0000-4000-8000-0000000000f1', 'e6f00000-0000-4000-8000-0000000000f1'),
  ('d2f00000-0000-4000-8000-0000000000f2', 'c2000000-0000-4000-8000-0000000000c2', 'aya', 'aya-diag-trace@0.1.0', '0.1.0',
   'e5f00000-0000-4000-8000-0000000000f2', 'e6f00000-0000-4000-8000-0000000000f2');
INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, status, idempotency_key)
SELECT id, 1, 'adaptive', 'MATH.FUNC.QUAD.VERTEX_FORM', 'synthetic-0301-07', 'target', 'skipped', gen_random_uuid()
  FROM platform.diagnosis_session WHERE id IN ('d1f00000-0000-4000-8000-0000000000f1', 'd2f00000-0000-4000-8000-0000000000f2');
UPDATE platform.diagnosis_session SET status = 'finished', finished_at = now()
 WHERE id IN ('d1f00000-0000-4000-8000-0000000000f1', 'd2f00000-0000-4000-8000-0000000000f2');
