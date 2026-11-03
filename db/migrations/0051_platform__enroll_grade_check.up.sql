-- 0051 platform · 选科在库里核对当前年级（specs/policy-decisions-v0.9.1/spec.md“PR4”；lessons“PR3 / 已知风险”第 2 条）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、计量与权益）。请以单事务执行（psql -1）。
-- 只改 platform（ADR 02）。依赖 0015（enroll_subject 现行版本）、0045（platform.current_grade、student_profile.exam_cohort）。
-- 要点：
--   1. platform.enroll_subject（新选与重新开通已暂停的学科）在授权核对之后、任何业务判断之前，按届别与计量时钟推算当前年级
--      platform.current_grade(exam_cohort, platform.meter_now())；不在 7–9（未入学或已毕业，函数返回 NULL）时拒绝：
--      RAISE 'GRADE_OUT_OF_RANGE'，SQLSTATE 55000（object_not_in_prerequisite_state），不带年级、届别等细节；
--      BFF 把这个错误映射成与自身前置检查相同的 409 GRADE_OUT_OF_RANGE（services/bff/src/http/problems.ts）。
--      之前只有 BFF 拦（PR3 #10），库函数被直接调用（或 BFF 前置检查与库的时钟不一致）时能给已毕业的学生新开学科；
--   2. 顺序：未授权（不是本人或有效绑定家长、学生不存在）仍先以 42501 拒绝，不泄露该学生的年级；授权通过后先取选科咨询锁，
--      再以 FOR SHARE 读档案行的届别：与并发的届别更正（platform.correct_exam_cohort 对档案行 FOR UPDATE）串行，
--      不会按更正前的届别放行。authz 对 student_profile 有 exam_cohort 列的 UPDATE 授权（0045），FOR SHARE 可用；
--      同时读 erased_at：档案已删除（0035 删除请求已执行）时同样 42501 'not permitted'，与未授权同一个错误，不泄露删除状态
--      （PR4 安全审查：can_read_student 不看删除标记，已删除的学生原来仍可被绑定家长重新选科）；
--   3. 不受影响：暂停（platform.pause_subject）、系统按上限暂停（meter_apply_subject_limits）不查年级，已开通的学科照常保留、可暂停；
--      “已开通再开通”（already_active）也走年级核对（与 BFF 一致：年级为空时一律 409，没有可新选的学科）。
--   其余逻辑与 0015 原文逐字相同（上线门控、试点、核心 + 选修槽、换科下个周期生效、旧配置的 max_active_subjects）。

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
  v_cohort smallint;
  v_erased timestamptz;
BEGIN
  IF p_student IS NULL OR p_subject IS NULL OR NOT platform.can_read_student(p_student) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform.enroll_subject:' || p_student, 0));
  -- 当前年级（0051）：按届别与计量时钟推算，不在 7–9 不能新选（只能暂停已开通的）；锁档案行，与届别更正串行
  SELECT sp.exam_cohort, sp.erased_at INTO v_cohort, v_erased FROM platform.student_profile sp WHERE sp.id = p_student FOR SHARE;
  -- 档案已删除（0035）：与未授权同一个错误，不泄露删除状态（0051，PR4 安全审查）
  IF v_erased IS NOT NULL THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF platform.current_grade(v_cohort, platform.meter_now()) IS NULL THEN
    RAISE EXCEPTION 'GRADE_OUT_OF_RANGE' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
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
COMMENT ON FUNCTION platform.enroll_subject(uuid, platform.subject) IS
  '开通或重新开通学科（0015；0051 起核对当前年级）：先核对授权（本人或有效绑定家长，否则 42501；档案已删除同样 42501）；'
  '按届别与计量时钟推算的当前年级不在 7–9 时拒绝（55000 GRADE_OUT_OF_RANGE，BFF 映射为 409）；其余按上线门控与选科槽规则返回 outcome';
