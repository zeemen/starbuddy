-- 0010 回滚：删除 insight schema（判断账本与状态机），收回洞察服务对 platform 的 USAGE。角色不删（由 bootstrap 管理）
DROP SCHEMA insight CASCADE;
REVOKE USAGE ON SCHEMA platform FROM starbuddy_insight;
