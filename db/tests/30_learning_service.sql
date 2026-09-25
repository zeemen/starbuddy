-- 学习服务角色：能写证据与推断，不能碰联系方式，推断只能“更正”不能“改写”（规则 6）
SET ROLE starbuddy_learning;
SELECT t.throws($$SELECT * FROM platform.account_contact$$, '42501', '学习服务读不到联系方式');
SELECT t.throws($$SELECT * FROM platform.guardian_invite$$, '42501', '学习服务读不到邀请表');

INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
  admission, payload, source_method, request_id, request_idempotency_key) VALUES
  ('e1000000-0000-4000-8000-000000000003', '2026-10-10 12:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
   'verifier_trace', 'synthetic-0301-07', 'K-QF-0301', 'admitted', '{"sampled_points": 8, "agreeing_points": 8}', 'grade',
   'f0000000-0000-4000-8000-000000000003', 'synthetic-aya-grade-0003');
SELECT t.ok(true, '学习服务可以写证据');
SELECT t.throws($$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, admission, payload,
                  source_method, request_id, request_idempotency_key) VALUES
                  ('e1000000-0000-4000-8000-000000000003', '2026-10-10 12:00:00+00', 'c1000000-0000-4000-8000-0000000000c1', 'aya',
                   'verifier_trace', 'admitted', '{"sampled_points": 8, "agreeing_points": 8}', 'grade',
                   'f0000000-0000-4000-8000-000000000003', 'synthetic-aya-grade-0003')$$,
                '23505', '同一 evidence_id + occurred_at 重复写入被拒（重试幂等）');
SELECT t.throws($$UPDATE platform.evidence_event SET admission = 'admitted'$$, '42501', '证据只追加，不能修改');
SELECT t.throws($$DELETE FROM platform.evidence_event$$, '42501', '证据不能由学习服务删除');

-- 更正掌握度：同一事务里先把旧记录标为被取代，再插入新的当前记录（superseded_by 外键延迟检查）
SELECT t.throws($$INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, confidence, algorithm_version, evidence_ids)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'stable', 0.7, 'bkt-v0@0.1.0',
                  '{e1000000-0000-4000-8000-000000000003}')$$,
                '23505', '同一能力点不能同时有两条当前掌握度');
BEGIN;
UPDATE platform.mastery_estimate SET is_current = false, superseded_by = 'ab000000-0000-4000-8000-0000000000ab'
 WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND is_current;
INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version, evidence_ids)
VALUES ('ab000000-0000-4000-8000-0000000000ab', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'K-QF-0301', 'stable', 0.75, 0.7,
        'bkt-v0@0.1.0', '{e1000000-0000-4000-8000-000000000001,e1000000-0000-4000-8000-000000000003}');
COMMIT;
SELECT t.ok((SELECT state FROM platform.mastery_estimate
              WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' AND is_current) = 'stable',
            '更正后当前掌握度是新记录，旧记录保留为历史');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1') = 2,
            '更正历史保留');
SELECT t.throws($$UPDATE platform.mastery_estimate SET confidence = 1$$, '42501', '推断内容不能被改写，只能更正');
RESET ROLE;
