-- 0056 回滚：行为事件写入函数恢复 0025 的写法（plan_done / plan_skip 只校验 plan_task_id 是 UUID 格式）。属主与授权不变。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。没有数据前提。
CREATE OR REPLACE FUNCTION insight.record_behavior_event(p_student uuid, p_kind text, p_payload jsonb, p_occurred_at timestamptz, p_key uuid)
  RETURNS TABLE (id uuid, replayed boolean)
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, platform, pg_temp
  AS $$
DECLARE e insight.behavior_event; v_id uuid;
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
  IF (SELECT count(*) FROM insight.behavior_event b WHERE b.student_id = p_student AND b.created_at > now() - interval '24 hours') >= 2000 THEN
    RAISE EXCEPTION '行为事件写入过多' USING ERRCODE = 'program_limit_exceeded';
  END IF;
  INSERT INTO insight.behavior_event (student_id, kind, payload, occurred_at, recorded_by, idempotency_key)
  VALUES (p_student, p_kind, p_payload, p_occurred_at, 'student', p_key)
  RETURNING behavior_event.id INTO v_id;
  RETURN QUERY SELECT v_id, false;
END $$;
COMMENT ON FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid) IS NULL;
