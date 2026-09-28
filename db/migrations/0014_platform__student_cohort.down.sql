-- 0014 回滚：去掉学生档案的中考届别
DROP TRIGGER guard_student_cohort ON platform.student_profile;
DROP FUNCTION platform.guard_student_cohort();
ALTER TABLE platform.student_profile DROP COLUMN exam_cohort;
