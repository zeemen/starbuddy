-- 0057 计划完成 / 跳过事件的来源改为学生自述（specs/today-plan-snapshot-plt15/lessons.md 决定记录 2026-10-15 第 3 条，PLT-15 PR3）。
-- 全部合成数据（规则 8）：10 号夹具的 S1（学生账号 aS1）。用 79d 的 t.ob(账号, SQL)（BFF 身份）。整个文件在一个事务里，结束回滚。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set P1 '''7d570000-0000-4000-8000-000000000001'''
\set P2 '''7d570000-0000-4000-8000-000000000002'''
\set K1 '''7d570000-0000-4000-8000-0000000000e1'''
BEGIN;
-- 两条当天的合成快照行（超级用户直写；快照的逐条断言在 79zd）
INSERT INTO platform.today_plan_item (id, student_id, seq, subject, kind, source, estimated_minutes)
VALUES (:P1, :S1, 31, 'aya', 'lesson', 'plan', 5), (:P2, :S1, 32, 'aya', 'review', 'plan', 5);

-- ================= 一、写入函数按类别设来源 =================
SELECT t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_done', %L, now(), %L)).id::text$$, :S1,
                         jsonb_build_object('plan_task_id', :P1), :K1)) AS ev_done \gset
SELECT t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_skip', %L, now(), gen_random_uuid())).id::text$$, :S1,
                         jsonb_build_object('plan_task_id', :P2))) AS ev_skip \gset
SELECT t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S1)) AS ev_start \gset
SELECT t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_end', '{"duration_sec": 60}', now(), gen_random_uuid())).id::text$$, :S1)) AS ev_end \gset
SELECT t.ok((SELECT source_type::text FROM insight.behavior_event WHERE id = :'ev_done') = 'self',
            '学生点“完成”（plan_done）记为学生自述（self），不是客观记录');
SELECT t.ok((SELECT source_type::text FROM insight.behavior_event WHERE id = :'ev_skip') = 'self',
            '学生点“先跳过”（plan_skip）记为学生自述（self）');
SELECT t.ok((SELECT bool_and(source_type = 'objective') FROM insight.behavior_event WHERE id IN (:'ev_start', :'ev_end')),
            '会话开始、结束仍是客观记录（objective）');
SELECT occurred_at AS done_at FROM insight.behavior_event WHERE id = :'ev_done' \gset
SELECT t.ok(t.ob(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'plan_done', %L, %L, %L)).id::text$$, :S1,
                              jsonb_build_object('plan_task_id', :P1), :'done_at', :K1)) = :'ev_done'
            AND (SELECT count(*) FROM insight.behavior_event WHERE student_id = :S1 AND idempotency_key = :K1) = 1
            AND (SELECT source_type::text FROM insight.behavior_event WHERE id = :'ev_done') = 'self',
            '计划事件重放返回原记录，来源不变');

-- ================= 二、表约束：来源由类别决定（绕过函数、以超级用户写入也拦得住） =================
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, idempotency_key)
                         VALUES (%L, 'plan_done', %L, 'objective', now(), gen_random_uuid())$$, :S1, jsonb_build_object('plan_task_id', :P1)),
                '23514', '表约束：计划完成不能标客观记录');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, idempotency_key)
                         VALUES (%L, 'plan_skip', %L, now(), gen_random_uuid())$$, :S1, jsonb_build_object('plan_task_id', :P2)),
                '23514', '表约束：直接写计划事件不写来源时被拒（列默认值 objective 不悄悄套到计划事件上）');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, idempotency_key)
                         VALUES (%L, 'plan_skip', %L, 'inferred', now(), gen_random_uuid())$$, :S1, jsonb_build_object('plan_task_id', :P2)),
                '23514', '表约束：计划事件不能标模型推断');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, idempotency_key)
                         VALUES (%L, 'plan_done', %L, 'guardian', now(), gen_random_uuid())$$, :S1, jsonb_build_object('plan_task_id', :P1)),
                '23514', '表约束：计划事件不能标家长观察');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, idempotency_key)
                         VALUES (%L, 'session_start', '{"entry": "today"}', 'self', now(), gen_random_uuid())$$, :S1),
                '23514', '表约束：会话事件不能标学生自述');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, recorded_by, idempotency_key)
                         VALUES (%L, 'exit_after_errors', '{}', 'self', now(), 'orchestrator', gen_random_uuid())$$, :S1),
                '23514', '表约束：连错后退出不能标学生自述');
SELECT t.throws(format($$INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, recorded_by, idempotency_key)
                         VALUES (%L, 'appeal', '{}', 'self', now(), 'backend', gen_random_uuid())$$, :S1),
                '23514', '表约束：异议事件不能标学生自述');
SELECT t.ok((SELECT count(*) FROM insight.behavior_event WHERE student_id = :S1 AND kind = 'plan_done' AND source_type = 'self') >= 1
            AND (SELECT count(*) FROM pg_constraint WHERE conrelid = 'insight.behavior_event'::regclass AND conname = 'behavior_event_source_type_check') = 0,
            '原来“来源只能是客观记录”的约束已换成按类别的约束');

-- ================= 三、周报客观栏：计划计数在 self_marked 栏（来源 self） =================
CREATE FUNCTION t.ze_obj() RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT '{"source_type": "objective", "usage_minutes": 95, "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}],
           "self_marked": {"source_type": "self", "plan_done": 4, "plan_skipped": 1}}'::jsonb $$;
CREATE FUNCTION t.ze_legacy() RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT '{"source_type": "objective", "usage_minutes": 95, "plan_done": 4, "plan_skipped": 1,
           "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}]}'::jsonb $$;
SELECT t.ok(insight.valid_weekly_objective(t.ze_obj()), '新形状：计划完成 / 跳过单独一栏（self_marked，来源学生自述）');
SELECT t.ok(insight.valid_weekly_objective(t.ze_legacy()), '旧形状（0036）仍认：已有快照只追加、不重算');
-- 严格为 false（CHECK 遇到 NULL 会放行，所以不能只看“不为真”）
SELECT t.ok(insight.valid_weekly_objective(o) IS FALSE, m)
  FROM (VALUES
    (jsonb_set(t.ze_obj(), '{self_marked,source_type}', '"objective"'), '自己标记栏不能标客观记录'),
    (t.ze_obj() #- '{self_marked,source_type}', '自己标记栏必须标来源'),
    (jsonb_set(t.ze_obj(), '{self_marked,source_type}', '"inferred"'), '自己标记栏不能标模型推断'),
    (jsonb_set(t.ze_obj(), '{self_marked,rank}', '3'), '自己标记栏不能带别的键'),
    (t.ze_obj() #- '{self_marked,plan_skipped}', '自己标记栏要有跳过计数'),
    (jsonb_set(t.ze_obj(), '{self_marked,plan_done}', '-1'), '完成计数是非负整数'),
    (jsonb_set(t.ze_obj(), '{self_marked,plan_skipped}', '"1"'), '跳过计数必须是数字'),
    (jsonb_set(t.ze_obj(), '{self_marked,plan_done}', '10000'), '完成计数有上限'),
    (jsonb_set(t.ze_obj(), '{self_marked}', '[1]'), '自己标记栏必须是对象'),
    (t.ze_obj() || '{"plan_done": 4}', '新形状的顶层不再放计划计数（不能两处都有）'),
    (t.ze_obj() || '{"source_type": "self"}', '客观栏本身仍只能标 objective'),
    (t.ze_obj() - 'usage_minutes', '新形状仍要有使用时长'),
    (t.ze_obj() || '{"subjects": [{"subject": "echo", "practice_minutes": 1, "attempts": 1}]}', '新形状也不含英语'),
    -- 安全审查阻断：各项比较都是 NULL 时不能返回 NULL（CHECK 会放行）
    (jsonb_set(t.ze_obj(), '{self_marked}', '{}'), '自己标记栏是空对象：判为不合格（不是 NULL）'),
    ('{}'::jsonb, '整个客观栏是空对象：判为不合格（不是 NULL）')) AS x(o, m);
-- 表约束：旧形状只留给 sie-weekly@0.1.0 / 0.1.1 的已有快照。只核约束：以超级用户、关掉触发器（守卫）写入，CHECK 照常生效
SET LOCAL session_replication_role = replica;
SELECT t.throws(format($$INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version)
                         VALUES (%L, '2026-09-07', '2026-09-13', 'student', 'generated', %L, %L, 'sie-weekly@0.2.0', 'syn')$$, :S1,
                       jsonb_build_array(jsonb_build_object('claim_id', gen_random_uuid(), 'template_key', 'test.weekly.node', 'template_version', 1,
                                                            'section', 'lit', 'text', '合成渲染句')), t.ze_legacy()),
                '23514', '新算法版本的周报不能再把计划计数标成客观记录（旧形状）');
WITH w AS (
  INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version)
  SELECT :S1, v.w, v.w + 6, 'student', 'generated',
         jsonb_build_array(jsonb_build_object('claim_id', gen_random_uuid(), 'template_key', 'test.weekly.node', 'template_version', 1,
                                              'section', 'lit', 'text', '合成渲染句')), v.o, v.ver, 'syn'
    FROM (VALUES ('2026-09-14'::date, 'sie-weekly@0.1.1', t.ze_legacy()), ('2026-09-21'::date, 'sie-weekly@0.2.0', t.ze_obj())) v(w, ver, o)
  RETURNING 1)
SELECT t.ok((SELECT count(*) FROM w) = 2, '旧版本的旧形状、新版本的新形状都能写');
SET LOCAL session_replication_role = origin;
ROLLBACK;
