-- 0010 回滚：删除 insight schema（判断账本与状态机），收回授权。角色不删（由 bootstrap 管理）
DROP SCHEMA insight CASCADE;
REVOKE SELECT (id, student_id) ON platform.mastery_estimate, platform.skill_observation, platform.error_attribution FROM starbuddy_authz;
REVOKE USAGE ON SCHEMA platform FROM starbuddy_insight;
