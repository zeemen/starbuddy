-- 0056 insight · 计划完成 / 跳过事件核对任务 ID（specs/today-plan-snapshot-plt15/spec.md PR1 范围第 1 条末项，PLT-15）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限与写入函数）。请以单事务执行（psql -1）。
-- 依赖 0025（insight.record_behavior_event）、0055（platform.today_plan_item 与 authz 对 id、student_id、plan_date 的列读权限）。
-- 问题：0025 的写入函数对 plan_done / plan_skip 只校验 plan_task_id 是 UUID 格式，不核对它是不是该生当天计划里的任务。
-- 做法：写入函数在 payload 与时间核对之后、写入上限之前加一条：plan_done / plan_skip 的 plan_task_id 必须是
--   platform.today_plan_item 里该学生的一行，且 plan_date 是事件发生日（北京时间）当天或前一天（跨零点完成昨天的任务；
--   不收计划日晚于事件日的，审查建议收紧）；否则 22023 拒绝
--   （别人的任务、不存在的任务、越日的任务同一个错误，不泄露存在性；BFF 映射 422）。
--   同一幂等键的重放仍先于一切核对（快照过了保留期被清掉后，已记下的事件重放照样拿到原记录）。
--   其余与 0025 逐字相同；属主 authz 与授权不变（CREATE OR REPLACE 保留）。
-- 只改函数，不改表约束：表上已有的事件（含测试与种子里超级用户直写的行）不受影响。
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
