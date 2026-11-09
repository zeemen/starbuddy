-- 0063 回滚：删掉五个保留期清理函数与参数核对辅助函数，收回本迁移给 authz 的 DELETE（与 ingest_request 五列的 SELECT）。
DROP FUNCTION insight.retention_purge_behavior_event(timestamptz, integer);
DROP FUNCTION platform.retention_purge_auth_rate_counter(timestamptz, integer);
DROP FUNCTION platform.retention_purge_auth_session(timestamptz, integer);
DROP FUNCTION platform.retention_purge_auth_otp(timestamptz, integer);
DROP FUNCTION platform.retention_purge_ingest_request(timestamptz, integer);
DROP FUNCTION platform.retention_args_ok(timestamptz, integer);
REVOKE DELETE ON insight.behavior_event FROM starbuddy_authz;
REVOKE SELECT (method, idempotency_key, created_at, status, lease_until) ON platform.ingest_request FROM starbuddy_authz;
REVOKE DELETE ON platform.ingest_request FROM starbuddy_authz;
REVOKE DELETE ON platform.auth_otp_challenge, platform.auth_session FROM starbuddy_authz;
