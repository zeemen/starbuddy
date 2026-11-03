-- 0060 回滚：删除模型调用记录表（连同全部月分区）、聚合函数与留存删除函数，从个人层表清单里去掉它，收回删除执行身份为它得到的授权，
-- 收回报表身份对 platform schema 的使用权。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 回滚会删掉表里全部调用记录（只有元数据；结构化日志仍是完整兜底）。回滚前先把学习服务的 LEARNING_MODEL_CALL_LOG 关掉（不设或 0），
-- 否则学习服务下次启动时因表不存在拒绝启动；已在运行的进程写库失败只计数告警，不影响路由。
-- 调度器的留存清理任务（model-call-purge）在签字时间表里启用过的，回滚前一并停掉（库函数不在了，子进程会以拒绝退出）。
-- 报表身份：REVOKE USAGE ON SCHEMA platform 收回的是 0060 授的那一项（0022 只授了 insight）；回滚后 starbuddy_report 在 platform 下
-- 任何函数都调不到（含对 PUBLIC 开放的辅助函数）。以后若有别的迁移也要给报表身份 platform 的使用权，回滚前须核对，免得一并收回。
DELETE FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname = 'model_call_log';
REVOKE SELECT (usage_id, student_id) ON platform.usage_reservation, platform.practice_log FROM starbuddy_eraser;
DROP FUNCTION platform.model_call_log_purge(timestamptz, integer);
DROP FUNCTION platform.model_call_stats(date, date);
REVOKE USAGE ON SCHEMA platform FROM starbuddy_report;
DROP TABLE platform.model_call_log;
