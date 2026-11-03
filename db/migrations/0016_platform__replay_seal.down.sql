-- 0016 回滚：删除封存表与幂等表的 usage_id 列
DROP TABLE platform.replay_seal;
DROP FUNCTION platform.reject_replay_seal_change();
ALTER TABLE platform.ingest_request DROP COLUMN usage_id;
