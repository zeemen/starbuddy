-- 0006 回滚
DROP FUNCTION IF EXISTS platform.resolve_review_task(uuid, text);
DROP TABLE IF EXISTS platform.review_task;
DROP TABLE IF EXISTS platform.guardian_report;
