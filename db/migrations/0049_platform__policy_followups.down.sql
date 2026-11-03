-- 0049 回滚（platform 部分）：退回额度函数、诊断会话的建立年级与放弃原因、导出清单、客服更正的权限要求。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。先回滚 0050（policy 部分）。
-- 回滚说明（会丢掉或改变的东西）：
--   - 客服更正：回滚后客服分支一律 42501（不恢复 0045 的“任何 active staff 都能更正”，PR3 审查意见 3）。要恢复客服更正，
--     须重新执行 0049，并由 DBA 重新授予 cohort_correction；家长分支不变；
--   - 已授予的 staff 权限 cohort_correction 随回滚删除（同 0048 回滚删除授予的处理，lessons 待人确认 #5 已接受）；
--   - 诊断会话的 start_grade、abandon_reason 两列删除（会话参数与放弃原因；回滚后中途改届别回到 PR2 的行为）。学习服务与 BFF
--     须先回滚到不读这两列、不调退回函数的版本；
--   - 已写入的退回流水（adjust，原因 grade_changed_refund）是只追加的计量流水，不删、不冲回：额度按已退回的结果保留。

-- ---------- 4. 退回额度 ----------
DROP FUNCTION platform.meter_refund_grade_changed(uuid);

-- ---------- 3. 诊断会话 ----------
DROP TRIGGER diagnosis_session_write_guard ON platform.diagnosis_session;
DROP FUNCTION platform.diagnosis_session_write_guard();
DROP FUNCTION platform.diagnosis_grade_changed(uuid, timestamptz, smallint);
REVOKE SELECT (usage_id, student_id, status, abandon_reason, idempotency_key, start_grade, created_at) ON platform.diagnosis_session FROM starbuddy_authz;
REVOKE UPDATE (abandon_reason) ON platform.diagnosis_session FROM starbuddy_learning;
ALTER TABLE platform.diagnosis_session DROP CONSTRAINT diagnosis_session_abandon_reason_status;
ALTER TABLE platform.diagnosis_session DROP COLUMN abandon_reason, DROP COLUMN start_grade;

-- ---------- 2. 家长导出（0035、0045 原登记） ----------
UPDATE platform.personal_table_registry
   SET export_columns = ARRAY['grade', 'region', 'birth_year_month', 'created_at']
 WHERE nspname = 'platform' AND relname = 'student_profile';
UPDATE platform.personal_table_registry
   SET export_topic = NULL, export_columns = NULL
 WHERE nspname = 'platform' AND relname = 'exam_cohort_change';

-- ---------- 1. 客服更正：回滚后客服分支一律拒绝（不退回“任何 active staff”） ----------
CREATE OR REPLACE FUNCTION platform.correct_exam_cohort(p_student uuid, p_new_cohort smallint) RETURNS smallint
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE
  -- 调用角色：SET ROLE 设置的角色；没有 SET ROLE 时是登录角色（SECURITY DEFINER 改的是 current_user，不改这两者）
  v_caller text := CASE WHEN current_setting('role') = 'none' THEN session_user::text ELSE current_setting('role') END;
  v_via_bff boolean := pg_has_role(v_caller, 'starbuddy_bff', 'MEMBER');
  v_via_review boolean := pg_has_role(v_caller, 'starbuddy_review', 'MEMBER');
  v_actor uuid;
  v_source text;
  v_old smallint;
  v_erased timestamptz;
  v_at timestamptz := clock_timestamp();
BEGIN
  IF v_via_bff AND NOT v_via_review THEN
    -- 家长端（经 BFF）：按档案授权（规则 5）；不是有效绑定家长与学生不存在同样拒绝，不暴露存在性
    IF p_student IS NULL OR NOT platform.is_linked_guardian(p_student) THEN
      RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_actor := platform.current_account_id();
    v_source := 'guardian_report';
  ELSE
    -- 客服分支（审核台签字连接）在 0049 回滚后一律拒绝：要恢复须重新执行 0049 并由 DBA 重新授予 cohort_correction
    RAISE EXCEPTION 'not allowed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF p_new_cohort IS NULL THEN
    RAISE EXCEPTION '更正后的届别不能为空' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- 锁住档案行：同一学生的更正（以及别的改档案的事务）串行，旧值取提交后的当前值
  SELECT s.exam_cohort, s.erased_at INTO v_old, v_erased FROM platform.student_profile s WHERE s.id = p_student FOR UPDATE;
  IF v_old IS NULL OR v_erased IS NOT NULL THEN
    RAISE EXCEPTION '学生档案不存在或已删除' USING ERRCODE = 'no_data_found';
  END IF;
  IF abs(p_new_cohort - v_old) <> 1 THEN
    RAISE EXCEPTION '届别每次只能更正 ±1（当前 %）', v_old USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF platform.current_grade(p_new_cohort, v_at) IS NULL THEN
    RAISE EXCEPTION '更正后的届别 % 在当前对应的年级不在 7–9', p_new_cohort USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE platform.student_profile SET exam_cohort = p_new_cohort, exam_cohort_source = v_source WHERE id = p_student;
  INSERT INTO platform.exam_cohort_change (student_id, old_cohort, new_cohort, source, changed_by, changed_at)
  VALUES (p_student, v_old, p_new_cohort, v_source, v_actor, v_at);
  RETURN p_new_cohort;
END $$;
COMMENT ON FUNCTION platform.correct_exam_cohort(uuid, smallint) IS
  '更正学生档案届别（0045；0049 已回滚）：只有经 BFF 的有效绑定家长能更正（来源 guardian_report）；客服分支一律拒绝，'
  '恢复须重新执行 0049 并由 DBA 重新授予 cohort_correction；每次 ±1，更正后当前年级须在 7–9；锁档案行；写 platform.exam_cohort_change';
DELETE FROM platform.staff_permission WHERE permission = 'cohort_correction';
ALTER TABLE platform.staff_permission DROP CONSTRAINT staff_permission_permission_check;
ALTER TABLE platform.staff_permission ADD CONSTRAINT staff_permission_permission_check CHECK (permission IN ('policy_source_admin'));
COMMENT ON TABLE platform.staff_permission IS
  'staff 的附加权限（0048）：只由迁移或 DBA 授予与收回；审核台签字身份只读自己的权限。首个权限 policy_source_admin（维护官方来源清单）';
