-- 0064 回滚：个人层表清单去掉讲评课会话；删除提问放行函数、会话表与守卫。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 注意：回滚会删除全部讲评课会话（进行中的讲评课随之不能再提问；计量预留与流水不受影响），请先导出 platform.lesson_session。
-- 学习服务里调用 platform.lesson_session_ask 与写这张表的路由（③b-4）须先回滚或关掉，否则打开讲评课与课内提问一律报错。
DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'lesson_session';
DROP FUNCTION platform.lesson_session_ask(uuid, uuid, uuid);
DROP TABLE platform.lesson_session;
DROP FUNCTION platform.lesson_session_guard();
