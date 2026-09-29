-- 0025 行为事件与“能做对吗”探针（specs/behavior-events-sprint2/spec.md 验收）。全部合成：夹具 10_fixtures 的 S1（gA 的孩子）、S2。
-- 用 79d 定义的 t.ob(账号, SQL)：以 BFF 身份、按账号调用。整个文件在一个事务里，结束回滚。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set k1 '''e7000000-0000-4000-8000-0000000000e1'''
\set k2 '''e7000000-0000-4000-8000-0000000000e2'''
\set k3 '''e7000000-0000-4000-8000-0000000000e3'''
BEGIN;

-- ================= 行为事件：学生本人、白名单 payload、来源 objective =================
SELECT t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), %L)).id::text$$, :S1, :k1)) AS ev1 \gset
SELECT t.ok((SELECT source_type::text = 'objective' AND kind = 'session_start' FROM insight.behavior_event WHERE id = :'ev1'),
            '行为事件入库，来源类型是客观记录');
SELECT occurred_at AS ev1_at FROM insight.behavior_event WHERE id = :'ev1' \gset
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', %L, %L)).id::text$$, :S1, :'ev1_at', :k1)) = :'ev1'
            AND (SELECT count(*) FROM insight.behavior_event WHERE student_id = :S1 AND idempotency_key = :k1) = 1,
            '同一幂等键重放返回原记录，不重复入库');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": 60}', now(), %L)).id::text$$, :S1, :k1)),
                '23505', '同一幂等键、内容不同回冲突');
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": 1800}', now(), %L)).id::text$$, :S1, :k2)) IS NOT NULL
            AND t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_done', '{"plan_task_id": "0a000000-0000-4000-8000-000000000001"}', now(), gen_random_uuid())).id::text$$, :S1)) IS NOT NULL
            AND t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_skip', '{"plan_task_id": "0a000000-0000-4000-8000-000000000002"}', now(), gen_random_uuid())).id::text$$, :S1)) IS NOT NULL,
            '学生端四类事件都能写');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S2)),
                '42501', '学生不能给别的学生写事件');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '42501', '家长不能冒充学生写事件');
SELECT t.throws(format('SELECT t.ob(NULL, %L)', format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '42501', '未登录不能写事件');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'exit_after_errors', '{}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '学生端不能写“连错后退出”（派生事件只由编排层按规则写）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'appeal', '{}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '学生端不能直接写申诉事件');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today", "mood": "sad"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', 'payload 多出白名单以外的键（例如情绪）被拒（规则 12）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "我今天不开心"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', 'payload 不收自由文本');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": 999999}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '会话时长有上限');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": "60"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '会话时长必须是数字');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_done', '{"plan_task_id": "x"}', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '计划任务 ID 必须是 UUID');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now() - interval '3 days', gen_random_uuid())).id::text$$, :S1)),
                '22023', '发生时间不能早于 1 天前');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now() + interval '1 hour', gen_random_uuid())).id::text$$, :S1)),
                '22023', '发生时间不能在未来');
-- 表层约束（绕过函数、以超级用户写入也拦得住）
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
                         VALUES (%L, 'session_start', '{"note": "free text"}', now(), gen_random_uuid())$$, :S1), '23514', '表约束：payload 白名单');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, idempotency_key)
                         VALUES (%L, 'session_start', '{"entry": "today"}', 'inferred', now(), gen_random_uuid())$$, :S1), '23514', '表约束：来源只能是客观记录');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
                         VALUES (%L, 'focus_lost', '{}', now(), gen_random_uuid())$$, :S1), '23514', '表约束：事件类型只有六种');

-- ================= 探针：学生自述，不存实际对错 =================
SELECT t.ob(:aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'unsure', now(), %L)).id::text$$, :S1, :k3)) AS pr1 \gset
SELECT t.ok((SELECT source_type::text = 'self' AND prediction = 'unsure' AND attempt_id IS NULL FROM insight.confidence_probe WHERE id = :'pr1'),
            '探针入库，来源类型是学生自述');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'insight' AND table_name = 'confidence_probe'
                          AND column_name IN ('actual_correct', 'correct', 'verdict', 'score')),
            '探针表不存实际对错（按作答去判分表取，规则 2）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), %L)).id::text$$, :S1, :k3)),
                '23505', '同一幂等键、内容不同回冲突');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'maybe', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '预测只有能 / 不能 / 不确定');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-nope', 'yes', now(), gen_random_uuid())).id::text$$, :S1)),
                '22023', '题目必须已登记');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS2, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid())).id::text$$, :S1)),
                '42501', '学生不能给别的学生写探针');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid())).id::text$$, :S1)),
                '42501', '家长不能冒充学生写探针');
-- 关联作答：必须是本人、同一道题，作答在探针写入后 2 小时内（以库里写入探针的时间为准，不看客户端给的提问时间）。
-- 夹具作答 a7（S1）、a9（S2）在 2026-10-08 / 09 11:30 UTC；把探针的写入时间挪到它们附近（超级用户，只在测试里）
SELECT t.ob(:aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'no', now(), gen_random_uuid())).id::text$$, :S1)) AS pr2 \gset
SELECT t.ob(:aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid())).id::text$$, :S1)) AS pr3 \gset
SELECT t.ob(:aS1, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid())).id::text$$, :S1)) AS pr4 \gset
UPDATE insight.confidence_probe SET created_at = '2026-10-08 11:00:00+00' WHERE id = :'pr1';
-- pr2 挪到 a8（S1 的语文作答）、a9（S2 的同一道数学题）之前半小时：下面两条只靠“同一道题”“学生本人”的核对拦下，不靠时间窗
UPDATE insight.confidence_probe SET created_at = '2026-10-09 11:00:00+00' WHERE id = :'pr2';
UPDATE insight.confidence_probe SET created_at = '2026-10-08 08:00:00+00' WHERE id = :'pr3';  -- 作答在 3.5 小时后
UPDATE insight.confidence_probe SET created_at = '2026-10-08 12:00:00+00' WHERE id = :'pr4';  -- 作答在探针之前半小时
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S1, :'pr3')),
                '22023', '作答在探针写入 2 小时之后，不能关联（防止挂到之后做对的那次）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S1, :'pr4')),
                '22023', '作答早于探针写入，不能关联（防止做完知道对错再补预测）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S1, :'pr1')),
                '42501', '家长不能关联探针');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a9000000-0000-4000-8000-0000000000a9')::text$$, :S1, :'pr2')),
                '22023', '不能关联别人的作答（即使是同一道题）');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a8000000-0000-4000-8000-0000000000a8')::text$$, :S1, :'pr2')),
                '22023', '关联的作答必须是同一道题');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS2, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S2, :'pr1')),
                '22023', '不能关联别人的探针');
SELECT t.ob(:aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S1, :'pr1'));
SELECT t.ok((SELECT attempt_id = 'a7000000-0000-4000-8000-0000000000a7' FROM insight.confidence_probe WHERE id = :'pr1'), '探针关联到作答');
SELECT t.ok(t.ob(:aS1, format($$SELECT insight.link_probe_attempt(%L, %L, 'a7000000-0000-4000-8000-0000000000a7')::text$$, :S1, :'pr1')) IS NOT DISTINCT FROM ''
            AND (SELECT attempt_id = 'a7000000-0000-4000-8000-0000000000a7' FROM insight.confidence_probe WHERE id = :'pr1'),
            '重复关联同一作答是幂等的');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$SELECT insight.link_probe_attempt(%L, %L, gen_random_uuid())::text$$, :S1, :'pr1')),
                '23505', '已关联的探针不能改关联别的作答');
SELECT t.ok((SELECT count(*) FROM insight.confidence_probe p
               JOIN platform.grade_result g ON g.attempt_id = p.attempt_id AND g.student_id = p.student_id AND g.superseded_by IS NULL
              WHERE p.id = :'pr1') >= 1,
            '实际对错按作答关联判分表取（未被取代的那条）');

-- ================= 重放标记、超窗重放、写入方 =================
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', %L, %L)).replayed::text$$, :S1, :'ev1_at', :k1)) = 'true',
            '重放返回 replayed = true（BFF 据此加 idempotent-replayed 响应头）');
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "other"}', now(), gen_random_uuid())).replayed::text$$, :S1)) = 'false',
            '首次写入 replayed = false');
INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
VALUES (:S1, 'session_end', '{"duration_sec": 60}', now() - interval '3 days', 'e7000000-0000-4000-8000-0000000000e9');
SELECT occurred_at AS old_at FROM insight.behavior_event WHERE idempotency_key = 'e7000000-0000-4000-8000-0000000000e9' \gset
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": 60}', %L, 'e7000000-0000-4000-8000-0000000000e9')).replayed::text$$, :S1, :'old_at')) = 'true',
            '超过 1 天的重放返回原记录，不报时间窗错误');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key)
                         VALUES (%L, 'appeal', '{}', now(), 'student', gen_random_uuid())$$, :S1), '23514', '申诉事件只能由后端写（写入方约束）');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key)
                         VALUES (%L, 'session_start', '{"entry": "today"}', now(), 'orchestrator', gen_random_uuid())$$, :S1), '23514', '会话事件只能由学生本人写');
SELECT t.ok((SELECT bool_and(recorded_by = 'student') FROM insight.behavior_event WHERE student_id = :S1 AND kind LIKE 'session%'), '学生写入的事件记为 student');

-- ================= 写入上限（24 小时内事件 2000 条、探针 500 条） =================
SAVEPOINT limits;
INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
SELECT :S2, 'session_end', '{"duration_sec": 1}', now(), gen_random_uuid() FROM generate_series(1, 2000);
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS2, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S2)),
                '54000', '24 小时内事件超过 2000 条被拒');
INSERT INTO insight.confidence_probe (student_id, subject, item_id, prediction, asked_at, idempotency_key)
SELECT :S2, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid() FROM generate_series(1, 500);
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS2, format($$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), gen_random_uuid())).id::text$$, :S2)),
                '54000', '24 小时内探针超过 500 条被拒');
ROLLBACK TO SAVEPOINT limits;

-- ================= 可见范围：只给 Agent =================
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, 'SELECT count(*)::text FROM insight.behavior_event'), '42501', '学生读不到行为事件');
SELECT t.throws(format('SELECT t.ob(%L, %L)', :gA, 'SELECT count(*)::text FROM insight.confidence_probe'), '42501', '家长读不到探针');
SET LOCAL ROLE starbuddy_insight;
SELECT t.ok((SELECT count(*) FROM insight.behavior_event WHERE student_id = :S1) >= 4 AND (SELECT count(*) FROM insight.confidence_probe) >= 1,
            '洞察服务可读（只给 Agent 用）');
RESET ROLE;
SET LOCAL ROLE starbuddy_learning;
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
                         VALUES (%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())$$, :S1), '42501', '学习服务不能直接写行为事件');
SELECT t.throws(format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id$$, :S1),
                '42501', '学习服务不能调用学生端写入函数');
RESET ROLE;
SELECT t.throws(format('SELECT t.ob(%L, %L)', :aS1, format($$UPDATE insight.confidence_probe SET prediction = 'yes' WHERE id = %L$$, :'pr1')),
                '42501', '学生不能改探针（只能经函数写）');

-- ================= 删除学生档案时一起删除 =================
-- S1 在别的表里有不级联的数据，这里核对两张表指向学生档案的外键是 ON DELETE CASCADE
SELECT t.ok((SELECT count(*) FROM pg_constraint k WHERE k.contype = 'f' AND k.confrelid = 'platform.student_profile'::regclass
               AND k.conrelid IN ('insight.behavior_event'::regclass, 'insight.confidence_probe'::regclass) AND k.confdeltype = 'c') = 2,
            '删除学生档案时行为事件与探针一起删除（外键级联）');
ROLLBACK;
