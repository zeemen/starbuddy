-- 合成夹具（CLAUDE.md 规则 8）：两户家庭 + 一位没有绑定的家长。
--   家庭 H1：家长 gA 创建，学生 S1
--   家庭 H2：家长 gB 创建，学生 S2；S2 的登录手机号与 gA 相同（用来测“同手机号不能跨档案”）
--   gC：没有任何绑定的家长（用来测邀请兑换）
-- 以超级用户写入（绕过 RLS），只用于准备数据。
INSERT INTO platform.account (id, kind) VALUES
  ('a0000000-0000-4000-8000-00000000000a', 'guardian'),   -- gA
  ('b0000000-0000-4000-8000-00000000000b', 'guardian'),   -- gB
  ('cc000000-0000-4000-8000-0000000000cc', 'guardian'),   -- gC
  ('a1000000-0000-4000-8000-0000000000a1', 'student'),    -- S1 的账号
  ('b1000000-0000-4000-8000-0000000000b1', 'student');    -- S2 的账号
INSERT INTO platform.account_contact (account_id, phone_e164) VALUES
  ('a0000000-0000-4000-8000-00000000000a', '+8613800000001'),
  ('b0000000-0000-4000-8000-00000000000b', '+8613800000002'),
  ('b1000000-0000-4000-8000-0000000000b1', '+8613800000001');  -- 与 gA 同号
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
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission, admitted_at) VALUES
  ('aya', 'synthetic-0301-07', 'e0000000-0000-4000-8000-0000000000e0', 'K-QF-0301', 'admitted', now()),
  ('aya', 'synthetic-0301-08', 'e0000000-0000-4000-8000-0000000000e0', 'K-QF-0301', 'pending_review', NULL);

-- 两个学生各一条证据（2026-10，UTC）
INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
  admission, payload, source_method, request_id, request_idempotency_key) VALUES
  ('e1000000-0000-4000-8000-000000000001', '2026-10-08 11:30:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
   'response_text', 'synthetic-0301-07', 'K-QF-0301', 'admitted', '{"text": "2(x-1)"}', 'grade',
   'f0000000-0000-4000-8000-000000000001', 'synthetic-aya-grade-0001'),
  ('e2000000-0000-4000-8000-000000000002', '2026-10-09 11:30:00+00', 'c2000000-0000-4000-8000-0000000000c2', 'aya',
   'response_text', 'synthetic-0301-07', 'K-QF-0301', 'admitted', '{"text": "x^2"}', 'grade',
   'f0000000-0000-4000-8000-000000000002', 'synthetic-aya-grade-0002');
INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version, evidence_ids) VALUES
  ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'execute', 0.55, 0.6, 'bkt-v0@0.1.0', '{e1000000-0000-4000-8000-000000000001}'),
  ('c2000000-0000-4000-8000-0000000000c2', 'aya', 'K-QF-0301', 'understand', 0.30, 0.5, 'bkt-v0@0.1.0', '{e2000000-0000-4000-8000-000000000002}');
