-- 0031 platform · 收回 0002 的旧登录入口对 BFF 的授权（specs/unified-shell-sprint4/lessons.md H1 待人确认 B 的默认，H2）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：权限）。不在任何共享环境执行。
-- 4-H H1 起家长登录只经 0029 的 auth_otp_login（核对验证码 → 找家长账号或注册 → 建会话在同一个函数里）。0002 的两个旧函数
-- BFF 已不再调用，却仍授予 starbuddy_bff：
--   - signup_guardian(text)：不经验证码就能用任意号码注册家长账号（并写成“已验证”）；
--   - lookup_accounts_by_phone(text)：按号码返回账号 ID 与类型，可被当成“号码是否注册过”的查询通道（攻击面 C3）。
-- 这里只收回 EXECUTE，不删函数（数据与函数定义都不动，回滚即恢复授权）。函数属主 starbuddy_authz 仍可执行。
REVOKE EXECUTE ON FUNCTION platform.signup_guardian(text), platform.lookup_accounts_by_phone(text) FROM starbuddy_bff;
