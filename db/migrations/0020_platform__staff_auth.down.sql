-- 0020 回滚：删除审核台 staff 登录的函数与表（会话与凭据一并删除）
DROP FUNCTION platform.staff_reset(uuid, text, bytea);
DROP FUNCTION platform.staff_enroll(uuid, text, text, bytea);
DROP FUNCTION platform.staff_logout(text);
DROP FUNCTION platform.staff_session_resolve(text);
DROP FUNCTION platform.staff_login(text, text, text);
DROP TABLE platform.staff_session;
DROP TABLE platform.staff_credential;
DROP FUNCTION platform.guard_staff_credential();
DROP FUNCTION platform.staff_totp(bytea, bigint);
DROP FUNCTION platform.staff_random_bytes(integer);
DROP FUNCTION platform.staff_hmac_sha1(bytea, bytea);
DROP FUNCTION platform.staff_bf_salt();
DROP FUNCTION platform.staff_crypt(text, text);
DROP FUNCTION platform.staff_now();
