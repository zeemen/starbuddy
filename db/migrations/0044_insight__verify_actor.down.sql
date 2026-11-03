-- 0044 回滚：删除洞察服务的授权再核对函数。状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 回滚后洞察服务的缺省核对（DbActorVerifier）调不到函数，按 fail closed 一律不通过（BFF 代表学生 / 家长的请求回 404）。
DROP FUNCTION insight.verify_actor(uuid, text, uuid);
