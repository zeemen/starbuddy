-- 0004 回滚（分区随父表一起删除）
DROP TABLE IF EXISTS platform.attempt;
DROP TABLE IF EXISTS platform.evidence_event;
DROP FUNCTION IF EXISTS platform.ensure_monthly_partitions(regclass, date, integer);
DROP FUNCTION IF EXISTS platform.evidence_payload_ok(text, jsonb);
