-- 0051 回滚：platform.enroll_subject 回到 0015 原文（不核对当前年级；“不在初中年级不能新选”回到只由 BFF 拦）。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。没有数据前提：0051 不加表、列与数据。
-- 回滚说明：已删除档案（erased_at 不为空）的选科拒绝随回滚去掉（回到 0015：绑定家长仍可给已删除的学生选科）；BFF 仍保留自身的前置检查（请求时钟推算年级为空时 409 GRADE_OUT_OF_RANGE），库层兜底随回滚去掉；
-- BFF 对 55000 GRADE_OUT_OF_RANGE 的映射在回滚后不会被触发，不需要先回滚 BFF。

CREATE OR REPLACE FUNCTION platform.enroll_subject(p_student uuid, p_subject platform.subject) RETURNS jsonb
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  s platform.subscription;
  c platform.subject_catalog;
  v_value text;
  v_max integer;
  v_status text;
  v_active integer;
  v_frozen integer;
  v_limit integer;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  s := platform.meter_main_subscription(p_student);
  SELECT * INTO c FROM platform.subject_catalog WHERE product = p_subject;
  -- 上线门控（规格 §5）：planned 不可选，另须有已发布的课程版本；beta 只对试点开放
  IF c.code IS NULL OR c.status = 'planned'
     OR NOT EXISTS (SELECT 1 FROM platform.curriculum_version WHERE subject = p_subject AND status = 'published') THEN
    RETURN jsonb_build_object('outcome', 'not_launched');
  END IF;
  IF s.id IS NULL THEN
    RETURN jsonb_build_object('outcome', 'no_subscription');
  END IF;
  IF c.status = 'beta' AND s.source <> 'pilot_virtual' THEN
    RETURN jsonb_build_object('outcome', 'not_launched');
  END IF;
  SELECT status INTO v_status FROM platform.subject_enrollment WHERE student_id = p_student AND subject = p_subject;
  IF v_status = 'active' THEN
    RETURN jsonb_build_object('outcome', 'already_active');
  END IF;

  IF platform.meter_uses_slots(s.config_version, s.tier) THEN
    v_limit := platform.meter_slot_limit(s.config_version, s.tier, c.is_core);
    SELECT count(*) FILTER (WHERE e.status = 'active'),
           count(*) FILTER (WHERE e.status = 'paused' AND e.paused_at >= s.period_start AND e.subject <> p_subject)
      INTO v_active, v_frozen
      FROM platform.subject_enrollment e JOIN platform.subject_catalog k ON k.product = e.subject
     WHERE e.student_id = p_student AND k.is_core = c.is_core;
    IF v_limit IS NOT NULL AND v_active >= v_limit THEN
      RETURN jsonb_build_object('outcome', 'limit_reached', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END,
                                'limit', v_limit);
    END IF;
    -- 本周期换下的科目仍占着槽，换上的新科目下个周期才能开（规格 §5“下个周期生效”）
    IF v_limit IS NOT NULL AND v_active + v_frozen >= v_limit THEN
      RETURN jsonb_build_object('outcome', 'change_next_period', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END);
    END IF;
  ELSE
    SELECT f.value_code INTO v_value FROM platform.plan_feature f
     WHERE f.config_version = s.config_version AND f.tier = s.tier AND f.feature_code = 'max_active_subjects';
    v_max := platform.meter_subject_limit(v_value);
    SELECT count(*) INTO v_active FROM platform.subject_enrollment WHERE student_id = p_student AND status = 'active';
    IF v_active >= v_max THEN
      RETURN jsonb_build_object('outcome', 'limit_reached', 'max_active_subjects', v_max);
    END IF;
  END IF;

  IF v_status IS NULL THEN
    INSERT INTO platform.subject_enrollment (student_id, subject) VALUES (p_student, p_subject);
  ELSE
    UPDATE platform.subject_enrollment SET status = 'active', paused_at = NULL WHERE student_id = p_student AND subject = p_subject;
  END IF;
  IF platform.meter_uses_slots(s.config_version, s.tier) THEN
    RETURN jsonb_build_object('outcome', 'enrolled', 'slot', CASE WHEN c.is_core THEN 'core' ELSE 'elective' END, 'limit', v_limit);
  END IF;
  RETURN jsonb_build_object('outcome', 'enrolled', 'max_active_subjects', v_max);
END $$;
COMMENT ON FUNCTION platform.enroll_subject(uuid, platform.subject) IS NULL;
