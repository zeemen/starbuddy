-- 0017 回滚：删除证据与作答的补列
ALTER TABLE platform.attempt
  DROP CONSTRAINT attempt_map_version_fk, DROP CONSTRAINT attempt_hinted_kind,
  DROP COLUMN source_type, DROP COLUMN map_version_id, DROP COLUMN channel, DROP COLUMN evidence_level, DROP COLUMN evidence_kind;
ALTER TABLE platform.evidence_event
  DROP CONSTRAINT evidence_event_map_version_fk, DROP CONSTRAINT evidence_event_kind_applies, DROP CONSTRAINT evidence_event_source_type,
  DROP COLUMN source_type, DROP COLUMN map_version_id, DROP COLUMN channel, DROP COLUMN evidence_level, DROP COLUMN evidence_kind;
DROP TRIGGER fill_evidence_source_type ON platform.evidence_event;
DROP FUNCTION platform.fill_evidence_source_type();
DROP FUNCTION platform.evidence_source_type(text);
