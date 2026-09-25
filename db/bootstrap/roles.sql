-- 集群级角色（由人在每个环境执行一次，不属于迁移；安全审查阻断 5）。
-- 迁移 0001 会断言这些角色存在且属性正确，否则拒绝执行。迁移的 down 不删除角色。
--   starbuddy_bff      ：TS BFF，代表当前登录账号访问；受 RLS 约束
--   starbuddy_learning ：Python 学习服务，唯一能写学习证据与推断的角色（ADR 01）
--   starbuddy_authz    ：授权函数的属主，BYPASSRLS；NOLOGIN，不授予任何登录角色
-- 部署时的登录角色只能继承 bff 或 learning 之一，不能同时继承（learning 的策略对所有学生放行）。
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_bff') THEN
    CREATE ROLE starbuddy_bff NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_learning') THEN
    CREATE ROLE starbuddy_learning NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz') THEN
    CREATE ROLE starbuddy_authz NOLOGIN NOSUPERUSER BYPASSRLS;
  END IF;
END $$;
