-- 集群级角色（由人在每个环境执行一次，不属于迁移；安全审查阻断 5）。
-- 迁移 0001 会断言这些角色存在且属性正确，否则拒绝执行。迁移的 down 不删除角色。
--   starbuddy_bff      ：TS BFF，代表当前登录账号访问；受 RLS 约束
--   starbuddy_learning ：Python 学习服务，唯一能写学习证据与推断的角色（ADR 01）
--   starbuddy_authz    ：授权函数的属主，BYPASSRLS；NOLOGIN，不授予任何登录角色
--   starbuddy_review   ：教研复核后台，只能读复核队列、调用结案函数（安全审查 R6a）
--   starbuddy_scheduler：定时任务（计量过期、订阅续期），只能调用对应函数，没有任何表权限（冲刺 2-E）
--   starbuddy_insight  ：洞察服务，判断账本 insight.claim 的唯一写入方（SIE-00；ADR 13 草稿）
--   starbuddy_kge      ：知识服务与审核台后端，公共知识层 kge.* 的唯一写入方（specs/kge-schema-v0.9）
--   starbuddy_kge_read ：知识服务的对外只读工具，只能读已发布地图版本里的公共知识，不能写任何表（specs/kge-03-compile-layer，迁移 0026）
--   starbuddy_report   ：运营报表只读身份，只能调用聚合统计函数（如 insight.onboarding_stats），读不到任何明细（SIE-01）
-- 部署时的登录角色只能继承 bff、learning、review、scheduler、insight、kge 之一，不能同时继承（kge_read 也一样；learning 的策略对所有学生放行）。
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_bff') THEN
    CREATE ROLE starbuddy_bff NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_learning') THEN
    CREATE ROLE starbuddy_learning NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_review') THEN
    CREATE ROLE starbuddy_review NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_scheduler') THEN
    CREATE ROLE starbuddy_scheduler NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_insight') THEN
    CREATE ROLE starbuddy_insight NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge') THEN
    CREATE ROLE starbuddy_kge NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge_read') THEN
    CREATE ROLE starbuddy_kge_read NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_report') THEN
    CREATE ROLE starbuddy_report NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz') THEN
    CREATE ROLE starbuddy_authz NOLOGIN NOSUPERUSER BYPASSRLS;
  END IF;
END $$;
