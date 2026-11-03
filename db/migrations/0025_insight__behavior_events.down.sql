-- 0025 回滚：删除行为事件与探针两张表及写入函数（数据一并删除；只给 Agent 用，没有对外展示依赖它）
DROP FUNCTION insight.link_probe_attempt(uuid, uuid, uuid);
DROP FUNCTION insight.record_confidence_probe(uuid, platform.subject, text, text, timestamptz, uuid);
DROP FUNCTION insight.record_behavior_event(uuid, text, jsonb, timestamptz, uuid);
REVOKE SELECT ON platform.attempt, platform.item FROM starbuddy_authz;
DROP TABLE insight.confidence_probe;
DROP TABLE insight.behavior_event;
DROP FUNCTION insight.valid_behavior_payload(text, jsonb);
