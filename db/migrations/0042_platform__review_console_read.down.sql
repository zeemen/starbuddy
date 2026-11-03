-- 0042 回滚：删除审核台读内容版本与发布记录的三个只读函数。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。只读函数，不带数据，回滚不丢任何记录。
DROP FUNCTION platform.release_record_export(platform.subject);
DROP FUNCTION platform.content_version_snapshot(uuid);
DROP FUNCTION platform.content_version_list(platform.subject);
