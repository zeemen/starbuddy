-- 0029 回滚：删除家庭登录与会话的函数与表（会话、验证码挑战、限流计数一并删除；已登录的设备全部需要重新登录）
DROP FUNCTION platform.auth_rate_hit(text, bytea, integer, integer);
DROP FUNCTION platform.auth_issue_student_session(uuid, text, integer, integer);
DROP FUNCTION platform.auth_session_revoke_all(text);
DROP FUNCTION platform.auth_session_revoke(text);
DROP FUNCTION platform.auth_session_resolve(text);
DROP FUNCTION platform.auth_otp_login(text, bytea, integer, integer, text);
DROP FUNCTION platform.auth_otp_issue(text, bytea, integer, integer);
DROP FUNCTION platform.auth_new_session(uuid, text, uuid, timestamptz, integer, integer);
DROP FUNCTION platform.auth_phone_ok(text);
DROP FUNCTION platform.auth_token_hash(text);
REVOKE SELECT ON platform.consent_record FROM starbuddy_authz;
DROP TABLE platform.auth_rate_counter;
DROP TABLE platform.auth_session;
DROP TABLE platform.auth_otp_challenge;
DROP FUNCTION platform.auth_random_bytes(integer);
DROP FUNCTION platform.auth_now();
