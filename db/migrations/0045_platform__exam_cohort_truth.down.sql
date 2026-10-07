-- 0045 回滚：恢复 0014 的届别保护触发器（建档与改届别都与年级核对 ±1），删除届别来源列、改届别守卫、更正函数、更正记录与年级推算函数。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。先回滚 0046（往年方案）及之后的迁移。
-- 已有更正记录或非推算来源的届别时中止：回滚会丢掉更正记录与来源（规则 6），且更正过的届别可能已与建档年级相差超过 1，由人决定怎么处理。
LOCK TABLE platform.student_profile, platform.exam_cohort_change IN SHARE ROW EXCLUSIVE MODE;
DO $$
DECLARE v_changes bigint; v_sourced bigint;
BEGIN
  SELECT count(*) INTO v_changes FROM platform.exam_cohort_change;
  SELECT count(*) INTO v_sourced FROM platform.student_profile WHERE exam_cohort_source <> 'derived';
  IF v_changes + v_sourced > 0 THEN
    RAISE EXCEPTION '0045 回滚中止：已有 % 条届别更正记录、% 个档案的届别来源不是推算值；回滚会丢掉更正记录与来源，请人先决定处理办法',
                    v_changes, v_sourced USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

DROP FUNCTION platform.correct_exam_cohort(uuid, smallint);
REVOKE UPDATE (exam_cohort, exam_cohort_source) ON platform.student_profile FROM starbuddy_authz;
DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'exam_cohort_change';
DROP TABLE platform.exam_cohort_change;

DROP TRIGGER guard_student_cohort_update ON platform.student_profile;
DROP FUNCTION platform.guard_student_cohort_update();
-- 保护触发器恢复 0014 原文
CREATE OR REPLACE FUNCTION platform.guard_student_cohort() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_expected smallint := platform.expected_cohort(NEW.grade, coalesce(NEW.created_at, now()));
BEGIN
  IF NEW.exam_cohort IS NULL THEN
    NEW.exam_cohort := v_expected;
  ELSIF abs(NEW.exam_cohort - v_expected) > 1
        AND (TG_OP = 'INSERT' OR NEW.exam_cohort IS DISTINCT FROM OLD.exam_cohort OR NEW.grade IS DISTINCT FROM OLD.grade) THEN
    RAISE EXCEPTION '中考届别 % 与年级 % 不符（推算为 %）', NEW.exam_cohort, NEW.grade, v_expected USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER guard_student_cohort ON platform.student_profile;
CREATE TRIGGER guard_student_cohort BEFORE INSERT OR UPDATE OF grade, exam_cohort ON platform.student_profile
  FOR EACH ROW EXECUTE FUNCTION platform.guard_student_cohort();

ALTER TABLE platform.student_profile DROP COLUMN exam_cohort_source;
COMMENT ON COLUMN platform.student_profile.grade IS NULL;
COMMENT ON COLUMN platform.student_profile.exam_cohort IS '中考届别（中考年份）。考情规则按届别读取（policy.exam_scheme.cohort）';
DROP FUNCTION platform.current_grade(smallint, timestamptz);
