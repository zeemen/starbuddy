-- 0007 学习服务写入幂等表：只有学习服务能读写；成功是终态；指纹与学生不能改；登记表只追加（规则 5：RLS 变更附测试）
SET ROLE starbuddy_learning;
INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, lease_token, lease_until)
VALUES ('grade', 'synthetic-ingest-key-0001', 'aya', 'c1000000-0000-4000-8000-0000000000c1', repeat('a', 64),
        '{a7100000-0000-4000-8000-000000000001}', 'b7100000-0000-4000-8000-000000000001', now() + interval '60 seconds');
SELECT t.ok(true, '学习服务可以登记写入请求');
SELECT t.throws($$INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, lease_token, lease_until)
                  VALUES ('grade', 'synthetic-ingest-key-0001', 'aya', 'c1000000-0000-4000-8000-0000000000c1', repeat('b', 64),
                          '{a7100000-0000-4000-8000-000000000002}', 'b7100000-0000-4000-8000-000000000002', now())$$,
                '23505', '同一 (method, idempotency_key) 只能登记一次（不分区，全局唯一）');
SELECT t.throws($$UPDATE platform.ingest_request SET request_fingerprint = repeat('c', 64)$$, '42501', '请求体指纹不能改');
SELECT t.throws($$UPDATE platform.ingest_request SET student_id = 'c2000000-0000-4000-8000-0000000000c2'$$, '42501', '学生不能改');
SELECT t.throws($$UPDATE platform.ingest_request SET attempt_ids = '{a7100000-0000-4000-8000-000000000009}'$$, '42501', 'attempt_id 不能改');
SELECT t.throws($$UPDATE platform.ingest_request SET status = 'succeeded'$$, '23514', '成功必须带结果摘要');
UPDATE platform.ingest_request SET status = 'succeeded', summary = '{"verdict": "correct"}', lease_token = NULL, lease_until = NULL
 WHERE idempotency_key = 'synthetic-ingest-key-0001';
SELECT t.throws($$UPDATE platform.ingest_request SET summary = '{"verdict": "incorrect"}'$$, '23514',
                '成功是终态，不能再改');
SELECT t.throws($$DELETE FROM platform.ingest_request$$, '42501', '学习服务不能删除写入请求');

INSERT INTO platform.evidence_registry (evidence_id, occurred_at, student_id, content_digest, request_idempotency_key)
VALUES ('e7100000-0000-4000-8000-000000000001', now(), 'c1000000-0000-4000-8000-0000000000c1', repeat('d', 64), 'synthetic-ingest-key-0001');
SELECT t.throws($$INSERT INTO platform.evidence_registry (evidence_id, occurred_at, student_id, content_digest, request_idempotency_key)
                  VALUES ('e7100000-0000-4000-8000-000000000001', now() - interval '40 days', 'c1000000-0000-4000-8000-0000000000c1',
                          repeat('d', 64), 'synthetic-ingest-key-0002')$$,
                '23505', '证据 ID 全局唯一：换一个 occurred_at 也不能再登记');
SELECT t.throws($$UPDATE platform.evidence_registry SET content_digest = repeat('e', 64)$$, '42501', '证据登记只追加，不能修改');
SELECT t.throws($$DELETE FROM platform.evidence_registry$$, '42501', '证据登记不能删除');
RESET ROLE;

SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT * FROM platform.ingest_request$$, '42501', 'BFF 读不到写入幂等表');
SELECT t.throws($$INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, lease_token, lease_until)
                  VALUES ('grade', 'synthetic-ingest-key-0009', 'aya', 'c1000000-0000-4000-8000-0000000000c1', repeat('a', 64),
                          '{a7100000-0000-4000-8000-000000000009}', 'b7100000-0000-4000-8000-000000000009', now())$$,
                '42501', 'BFF 不能登记写入请求');
SELECT t.throws($$UPDATE platform.ingest_request SET status = 'failed', error_code = 'INTERNAL'$$, '42501', 'BFF 不能推进写入请求的状态');
SELECT t.throws($$SELECT * FROM platform.evidence_registry$$, '42501', 'BFF 读不到证据登记表');
SELECT t.throws($$INSERT INTO platform.evidence_registry (evidence_id, occurred_at, student_id, content_digest, request_idempotency_key)
                  VALUES ('e7100000-0000-4000-8000-000000000002', now(), 'c1000000-0000-4000-8000-0000000000c1', repeat('d', 64),
                          'synthetic-ingest-key-0003')$$, '42501', 'BFF 不能写证据登记表');
RESET ROLE;

SET ROLE starbuddy_review;
SELECT t.throws($$SELECT * FROM platform.ingest_request$$, '42501', '复核角色读不到写入幂等表');
SELECT t.throws($$SELECT * FROM platform.evidence_registry$$, '42501', '复核角色读不到证据登记表');
RESET ROLE;

SET ROLE starbuddy_authz;
SELECT t.throws($$SELECT * FROM platform.ingest_request$$, '42501', '授权函数属主读不到写入幂等表');
SELECT t.throws($$SELECT * FROM platform.evidence_registry$$, '42501', '授权函数属主读不到证据登记表');
RESET ROLE;

-- 0016 重放封存：只有学习服务能读写；只追加（不能改、不能删）；学生档案删除时级联删除
SET ROLE starbuddy_learning;
INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id)
VALUES ('grade', 'b0b16000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-0000000000c1', 'a0b16000-0000-4000-8000-000000000001');
SELECT t.ok((SELECT count(*) FROM platform.replay_seal WHERE idempotency_key = 'b0b16000-0000-4000-8000-000000000001') = 1,
            '学习服务可以封存 key，也能读回');
SELECT t.throws($$INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id)
                  VALUES ('grade', 'b0b16000-0000-4000-8000-000000000001', 'c1000000-0000-4000-8000-0000000000c1',
                          'a0b16000-0000-4000-8000-000000000002')$$, '23505', '同一 (method, key) 只封存一次');
SELECT t.throws($$INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id)
                  VALUES ('teach', 'b0b16000-0000-4000-8000-000000000003', 'c1000000-0000-4000-8000-0000000000c1',
                          'a0b16000-0000-4000-8000-000000000003')$$, '23514', '只封存写入类方法');
SELECT t.throws($$UPDATE platform.replay_seal SET usage_id = 'a0b16000-0000-4000-8000-000000000009'$$, '42501', '封存不能改');
SELECT t.throws($$UPDATE platform.replay_seal SET sealed_at = now() - interval '1 day'$$, '42501', '封存时间不能改');
SELECT t.throws($$DELETE FROM platform.replay_seal$$, '42501', '学习服务不能解封');
SELECT t.ok((SELECT count(*) FROM platform.replay_seal WHERE idempotency_key = 'b0b16000-0000-4000-8000-000000000001') = 1,
            '被拒绝的修改与删除后封存仍在');
RESET ROLE;
SELECT t.throws($$UPDATE platform.replay_seal SET usage_id = 'a0b16000-0000-4000-8000-000000000009'$$, '42501',
                '触发器兜底：连表属主也不能改封存');

SET ROLE starbuddy_bff;
SELECT t.throws($$SELECT * FROM platform.replay_seal$$, '42501', 'BFF 读不到封存表');
SELECT t.throws($$INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id)
                  VALUES ('grade', 'b0b16000-0000-4000-8000-000000000004', 'c1000000-0000-4000-8000-0000000000c1',
                          'a0b16000-0000-4000-8000-000000000004')$$, '42501', 'BFF 不能直接封存 key（只能经学习服务的重放接口）');
SELECT t.throws($$SELECT usage_id FROM platform.ingest_request$$, '42501', 'BFF 读不到幂等表的 usage_id');
RESET ROLE;
SET ROLE starbuddy_review;
SELECT t.throws($$SELECT * FROM platform.replay_seal$$, '42501', '复核角色读不到封存表');
RESET ROLE;
SET ROLE starbuddy_authz;
SELECT t.throws($$SELECT * FROM platform.replay_seal$$, '42501', '授权函数属主读不到封存表');
RESET ROLE;
