-- 0011 回滚：删除 kge schema，收回授权。角色不删（由 bootstrap 管理）。platform 上的引用列由 0012 的回滚先去掉
DROP SCHEMA kge CASCADE;
REVOKE USAGE ON SCHEMA platform FROM starbuddy_kge;
