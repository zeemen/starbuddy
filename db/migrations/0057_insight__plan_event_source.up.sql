-- 0057 insight · 计划完成 / 跳过事件的来源改为学生自述（specs/today-plan-snapshot-plt15/lessons.md 决定记录 2026-10-15 第 3 条，PLT-15 PR3）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、写入函数、画像可见范围相关的来源标注）。请以单事务执行（psql -1）。
-- 依赖 0010（insight.source_type）、0025（insight.behavior_event）、0036（insight.weekly_report 与客观栏形状）、0056（写入函数的现行版本）。
-- 问题（规则 6）：plan_done / plan_skip 是学生自己点的“完成”“先跳过”，是学生自述；0025 却把行为事件的来源固定为客观记录（objective），
--   周报客观栏也把这两项计数标成 objective。
-- 做法（只改 insight schema）：
--   1. 行为事件来源按类别绑定：plan_done / plan_skip → self，其余（会话、连错后退出、异议）→ objective。表约束换成按类别的 CHECK；
--      写入函数 insight.record_behavior_event 写入时按类别设来源（函数体其余与 0056 逐字相同，属主 authz 与授权不变）；
--      已有的计划事件行回填为 self。列默认值仍是 objective：直接写表的计划事件必须显式写 self，否则被约束拒绝（不悄悄改写）。
--   2. 周报客观栏：新形状把计划完成 / 跳过放进单独的 self_marked 栏（来源 self）——
--        {"source_type": "objective", "usage_minutes": n, "subjects": [...], "self_marked": {"source_type": "self", "plan_done": n, "plan_skipped": n}}；
--      旧形状（顶层 plan_done / plan_skipped，0036）只留给已有快照：周报只追加不重算，旧行的算法版本是 sie-weekly@0.1.0 / 0.1.1。
--      新增表约束：旧形状只能出现在这两个算法版本的行上（新版本必须用新形状）。仍是“来自使用记录，不是判断”（决定记录第 2 条的豁免）。
-- 不改判断模板、可见范围、效度门槛；不改授权。

-- ---------- 一、行为事件：来源按类别绑定 ----------
ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_source_type_check;
UPDATE insight.behavior_event SET source_type = 'self' WHERE kind IN ('plan_done', 'plan_skip') AND source_type <> 'self';
ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_source_by_kind
  CHECK (source_type = CASE WHEN kind IN ('plan_done', 'plan_skip') THEN 'self'::insight.source_type ELSE 'objective'::insight.source_type END);
COMMENT ON CONSTRAINT behavior_event_source_by_kind ON insight.behavior_event IS
  '来源按类别（0057，规则 6）：计划完成 / 跳过是学生自己标记的（self），会话、连错后退出、异议是客观记录（objective）';
COMMENT ON TABLE insight.behavior_event IS
  '行为事件（0025，冲刺 2-L；0057 起按类别标来源）：只给 Agent 用。session_* 是客观记录，plan_* 是学生自己标记的（学生自述，不是核实过的完成），'
  '都由学生本人经 insight.record_behavior_event 写入；appeal、exit_after_errors 由后端与编排层按规则写（未实现），是客观记录。'
  '不从这些事件做规则 12 禁止的任何推断；时间戳只用于降负触发（连续中断、连错后退出），不做作息分析（安全审查 H1）';

CREATE OR REPLACE FUNCTION insight.record_behavior_event(p_student uuid, p_kind text, p_payload jsonb, p_occurred_at timestamptz, p_key uuid)
  RETURNS TABLE (id uuid, replayed boolean)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE e insight.behavior_event; v_id uuid; v_day date;
BEGIN
  IF p_student IS NULL OR NOT platform.is_self_student(p_student) THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_key IS NULL THEN
    RAISE EXCEPTION '缺少幂等键' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('insight.behavior_event:' || p_student::text, 0));
  SELECT * INTO e FROM insight.behavior_event b WHERE b.student_id = p_student AND b.idempotency_key = p_key;
  IF FOUND THEN
    IF e.kind IS DISTINCT FROM p_kind OR e.payload IS DISTINCT FROM p_payload OR e.occurred_at IS DISTINCT FROM p_occurred_at THEN
      RAISE EXCEPTION '同一幂等键对应的事件内容不同' USING ERRCODE = 'unique_violation';
    END IF;
    RETURN QUERY SELECT e.id, true;
    RETURN;
  END IF;
  IF p_kind IS NULL OR p_kind NOT IN ('session_start', 'session_end', 'plan_done', 'plan_skip')
     OR p_payload IS NULL OR NOT coalesce(insight.valid_behavior_payload(p_kind, p_payload), false)
     OR p_occurred_at IS NULL OR p_occurred_at < now() - interval '1 day' OR p_occurred_at > now() + interval '5 minutes' THEN
    RAISE EXCEPTION '事件类型、内容或时间不合要求' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 计划完成 / 跳过（0056）：任务 ID 必须是该生的今日计划快照行，且计划日是事件发生日（北京时间）当天或前一天
  IF p_kind IN ('plan_done', 'plan_skip') THEN
    v_day := (p_occurred_at AT TIME ZONE 'Asia/Shanghai')::date;
    IF NOT EXISTS (SELECT 1 FROM platform.today_plan_item i
                    WHERE i.id = (p_payload ->> 'plan_task_id')::uuid AND i.student_id = p_student
                      AND i.plan_date BETWEEN v_day - 1 AND v_day) THEN
      RAISE EXCEPTION 'PLAN_TASK_NOT_FOUND' USING ERRCODE = 'invalid_parameter_value';
    END IF;
  END IF;
  IF (SELECT count(*) FROM insight.behavior_event b WHERE b.student_id = p_student AND b.created_at > now() - interval '24 hours') >= 2000 THEN
    RAISE EXCEPTION '行为事件写入过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  -- 来源按类别（0057，规则 6）：计划完成 / 跳过是学生自己标记的（self），会话事件是客观记录（objective）
  INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, recorded_by, idempotency_key)
  VALUES (p_student, p_kind, p_payload,
          CASE WHEN p_kind IN ('plan_done', 'plan_skip') THEN 'self'::insight.source_type ELSE 'objective'::insight.source_type END,
          p_occurred_at, 'student', p_key)
  RETURNING behavior_event.id INTO v_id;
  RETURN QUERY SELECT v_id, false;
END $$;
COMMENT ON FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid) IS
  '学生本人写行为事件（0025；0056 起 plan_done / plan_skip 的任务 ID 必须是该生今日计划快照里、计划日是事件日当天或前一天的行；'
  '0057 起 plan_done / plan_skip 的来源记为学生自述 self，会话事件记为客观记录 objective）';

-- ---------- 二、周报客观栏：计划完成 / 跳过单独一栏，来源 self ----------
-- 新形状：{"source_type": "objective", "usage_minutes": n, "subjects": [...], "self_marked": {"source_type": "self", "plan_done": n, "plan_skipped": n}}
-- 旧形状（0036）：{"source_type": "objective", "usage_minutes": n, "plan_done": n, "plan_skipped": n, "subjects": [...]}，只留给已有快照
-- 整体包 coalesce(…, false)（安全审查阻断）：空对象 '{}'、self_marked 为 {} 时各项比较都是 NULL，CHECK 遇到 NULL 会放行；这里一律判为不合格
CREATE OR REPLACE FUNCTION insight.valid_weekly_objective(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT coalesce((jsonb_typeof(p) = 'object'
           AND CASE WHEN p ? 'self_marked' THEN
                 (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['self_marked', 'source_type', 'subjects', 'usage_minutes']
                 AND jsonb_typeof(p -> 'self_marked') = 'object'
                 AND (SELECT array_agg(k ORDER BY k)
                        FROM jsonb_object_keys(CASE WHEN jsonb_typeof(p -> 'self_marked') = 'object' THEN p -> 'self_marked' ELSE '{}'::jsonb END) k)
                     = ARRAY['plan_done', 'plan_skipped', 'source_type']
                 AND p -> 'self_marked' -> 'source_type' = '"self"'::jsonb
                 AND jsonb_typeof(p -> 'self_marked' -> 'plan_done') = 'number' AND (p -> 'self_marked' ->> 'plan_done') ~ '^[0-9]{1,4}$'
                 AND jsonb_typeof(p -> 'self_marked' -> 'plan_skipped') = 'number' AND (p -> 'self_marked' ->> 'plan_skipped') ~ '^[0-9]{1,4}$'
               ELSE
                 (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['plan_done', 'plan_skipped', 'source_type', 'subjects', 'usage_minutes']
                 AND jsonb_typeof(p -> 'plan_done') = 'number' AND (p ->> 'plan_done') ~ '^[0-9]{1,4}$'
                 AND jsonb_typeof(p -> 'plan_skipped') = 'number' AND (p ->> 'plan_skipped') ~ '^[0-9]{1,4}$'
               END
           AND p -> 'source_type' = '"objective"'::jsonb
           AND jsonb_typeof(p -> 'usage_minutes') = 'number' AND (p ->> 'usage_minutes') ~ '^[0-9]{1,5}$' AND (p ->> 'usage_minutes')::integer <= 10080
           AND jsonb_typeof(p -> 'subjects') = 'array' AND jsonb_array_length(p -> 'subjects') <= 8
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p -> 'subjects') s
              WHERE jsonb_typeof(s) <> 'object'
                 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k) IS DISTINCT FROM ARRAY['attempts', 'practice_minutes', 'subject']
                 OR (s ->> 'subject') IS NULL OR (s ->> 'subject') NOT IN ('aya', 'zhixing', 'wenli')  -- 英语搁置，不出现
                 OR jsonb_typeof(s -> 'practice_minutes') IS DISTINCT FROM 'number' OR (s ->> 'practice_minutes') !~ '^[0-9]{1,5}$'
                 OR (s ->> 'practice_minutes')::integer > 10080
                 OR jsonb_typeof(s -> 'attempts') IS DISTINCT FROM 'number' OR (s ->> 'attempts') !~ '^[0-9]{1,5}$')
           AND (SELECT count(DISTINCT s ->> 'subject') FROM jsonb_array_elements(p -> 'subjects') s) = jsonb_array_length(p -> 'subjects')), false) $$;
-- 旧形状只留给已有快照：新算法版本的周报必须把计划计数放在 self_marked 栏（ADD CONSTRAINT 会核对已有行：已有行都是这两个版本）
ALTER TABLE insight.weekly_report ADD CONSTRAINT weekly_report_objective_plan_source
  CHECK (objective IS NULL OR objective ? 'self_marked' OR algorithm_version IN ('sie-weekly@0.1.0', 'sie-weekly@0.1.1'));
COMMENT ON CONSTRAINT weekly_report_objective_plan_source ON insight.weekly_report IS
  '0057（规则 6）：计划完成 / 跳过是学生自己标记的，新周报放在客观栏的 self_marked 栏并标来源 self；顶层计数的旧形状只留给 sie-weekly@0.1.0 / 0.1.1 的已有快照';
