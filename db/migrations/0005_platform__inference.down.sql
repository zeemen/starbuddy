-- 0005 回滚
DROP TABLE IF EXISTS platform.grade_dimension;
DROP FUNCTION IF EXISTS platform.grade_dimension_requires_review();
DROP TABLE IF EXISTS platform.grade_result;
DROP TABLE IF EXISTS platform.mastery_estimate;
DROP TABLE IF EXISTS platform.error_attribution;
DROP TABLE IF EXISTS platform.skill_observation;
