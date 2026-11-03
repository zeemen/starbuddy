-- 0031 回滚：恢复 0002 的旧登录入口对 BFF 的授权
GRANT EXECUTE ON FUNCTION platform.signup_guardian(text), platform.lookup_accounts_by_phone(text) TO starbuddy_bff;
