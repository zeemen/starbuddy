-- 0043 回滚：删除时政下架不可逆的触发器与时政过期下架函数，收回函数属主对 kge.current_affair.review_status 的 UPDATE。
-- 状态：草稿，由人执行，先在副本演练。请以单事务执行（psql -1）。
-- 已写入的系统动作审核记录（basis_code = expired_auto）与已下架的时政保留：它们在 0019 / 0038 的约束下本来就合法，
-- 回滚只是去掉以后再自动下架的能力与“下架不可逆”的守卫，不改任何数据（审核记录只追加，也不能删）。
DROP TRIGGER guard_affair_retired ON kge.current_affair;
DROP FUNCTION kge.guard_affair_retired();
DROP FUNCTION kge.expire_current_affairs(date);
REVOKE UPDATE (review_status) ON kge.current_affair FROM starbuddy_authz;
