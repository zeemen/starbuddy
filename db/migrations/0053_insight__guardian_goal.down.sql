-- 0053 回滚：删除家长诉求表、读写函数与取值校验函数；个人层表清单去掉这张表。
-- 注意：回滚会删除全部家长诉求（含历史版本），请先导出 insight.guardian_goal。
DELETE FROM platform.personal_table_registry WHERE nspname = 'insight' AND relname = 'guardian_goal';
DROP FUNCTION insight.guardian_goal_view(uuid);
DROP FUNCTION insight.submit_guardian_goal(uuid, platform.subject[], jsonb, uuid, text);
REVOKE SELECT (student_id) ON insight.guardian_goal FROM starbuddy_eraser;
REVOKE DELETE ON insight.guardian_goal FROM starbuddy_eraser;
DROP TABLE insight.guardian_goal;
DROP FUNCTION insight.valid_goal_note(text);
DROP FUNCTION insight.valid_goal_targets(jsonb);
DROP FUNCTION insight.valid_goal_subjects(platform.subject[]);
