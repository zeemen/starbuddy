-- 0057 回滚：计划完成 / 跳过事件的来源恢复为客观记录（0025 的表约束与表说明、0056 的写入函数原文），周报客观栏恢复 0036 的形状核对。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 数据前提：已有新形状客观栏（带 self_marked）的周报时中止——周报只追加、不能改写，恢复 0036 的形状核对后这些行会违反约束。
-- 计划事件行回填为 objective（来源完全由类别决定，回滚不丢信息）。属主与授权不变（CREATE OR REPLACE 保留）。
-- 回滚前须先停掉洞察服务的周报任务，或先把洞察服务回退到 sie-weekly@0.1.1（它只写旧形状）：否则新形状周报会一直挡住回滚。
-- 先锁周报表（SHARE ROW EXCLUSIVE：挡住并发写入，读不受影响），再做中止检查，免得检查之后、换回旧形状核对之前又写进新形状的行
LOCK TABLE insight.weekly_report IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM insight.weekly_report WHERE objective ? 'self_marked') THEN
    RAISE EXCEPTION '0057 回滚中止：已有 % 份周报的客观栏是新形状（self_marked），恢复 0036 的形状核对后会违反约束',
      (SELECT count(*) FROM insight.weekly_report WHERE objective ? 'self_marked');
  END IF;
END $$;

-- ---------- 二、周报客观栏：恢复 0036 ----------
ALTER TABLE insight.weekly_report DROP CONSTRAINT weekly_report_objective_plan_source;
CREATE OR REPLACE FUNCTION insight.valid_weekly_objective(p jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
  AS $$ SELECT jsonb_typeof(p) = 'object'
           AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k) = ARRAY['plan_done', 'plan_skipped', 'source_type', 'subjects', 'usage_minutes']
           AND p -> 'source_type' = '"objective"'::jsonb
           AND jsonb_typeof(p -> 'usage_minutes') = 'number' AND (p ->> 'usage_minutes') ~ '^[0-9]{1,5}$' AND (p ->> 'usage_minutes')::integer <= 10080
           AND jsonb_typeof(p -> 'plan_done') = 'number' AND (p ->> 'plan_done') ~ '^[0-9]{1,4}$'
           AND jsonb_typeof(p -> 'plan_skipped') = 'number' AND (p ->> 'plan_skipped') ~ '^[0-9]{1,4}$'
           AND jsonb_typeof(p -> 'subjects') = 'array' AND jsonb_array_length(p -> 'subjects') <= 8
           AND NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(p -> 'subjects') s
              WHERE jsonb_typeof(s) <> 'object'
                 OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k) IS DISTINCT FROM ARRAY['attempts', 'practice_minutes', 'subject']
                 OR (s ->> 'subject') IS NULL OR (s ->> 'subject') NOT IN ('aya', 'zhixing', 'wenli')  -- 英语搁置，不出现
                 OR jsonb_typeof(s -> 'practice_minutes') IS DISTINCT FROM 'number' OR (s ->> 'practice_minutes') !~ '^[0-9]{1,5}$'
                 OR (s ->> 'practice_minutes')::integer > 10080
                 OR jsonb_typeof(s -> 'attempts') IS DISTINCT FROM 'number' OR (s ->> 'attempts') !~ '^[0-9]{1,5}$')
           AND (SELECT count(DISTINCT s ->> 'subject') FROM jsonb_array_elements(p -> 'subjects') s) = jsonb_array_length(p -> 'subjects') $$;

-- ---------- 一、行为事件：写入函数恢复 0056 原文；来源恢复为固定 objective ----------
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
  INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key)
  VALUES (p_student, p_kind, p_payload, p_occurred_at, 'student', p_key)
  RETURNING behavior_event.id INTO v_id;
  RETURN QUERY SELECT v_id, false;
END $$;
COMMENT ON FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid) IS
  '学生本人写行为事件（0025；0056 起 plan_done / plan_skip 的任务 ID 必须是该生今日计划快照里、计划日是事件日当天或前一天的行）';

ALTER TABLE insight.behavior_event DROP CONSTRAINT behavior_event_source_by_kind;
UPDATE insight.behavior_event SET source_type = 'objective' WHERE source_type <> 'objective';
ALTER TABLE insight.behavior_event ADD CONSTRAINT behavior_event_source_type_check CHECK (source_type = 'objective');
COMMENT ON TABLE insight.behavior_event IS
  '行为事件（0025，冲刺 2-L）：客观记录，只给 Agent 用。session_* 与 plan_* 由学生本人经 insight.record_behavior_event 写入；'
  'appeal、exit_after_errors 由后端与编排层按规则写（未实现）。不从这些事件推断情绪、性格、专注力（规则 12）；'
  '时间戳只用于降负触发（连续中断、连错后退出），不做作息分析（安全审查 H1）';
