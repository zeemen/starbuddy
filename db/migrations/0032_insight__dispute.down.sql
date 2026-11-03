-- 0032 回滚：删除异议与提出函数，收回 0032 新授的模板读取权限。
-- 回滚前异议里指向的复核任务（profile_dispute）留在队列里，由人处理（不静默删复核记录）。
DROP FUNCTION insight.raise_dispute(uuid, text, uuid, uuid, text, uuid);
DROP TABLE insight.dispute;
DROP FUNCTION insight.dispute_i_know_moved();
DROP FUNCTION insight.dispute_guard();
DROP FUNCTION insight.dispute_step_allowed(text, text, text, text);
DROP FUNCTION platform.erasure_in_progress(uuid);
REVOKE SELECT ON insight.claim_template FROM starbuddy_authz;
