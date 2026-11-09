-- 0063 platform / insight · 过程数据的保留期清理（PLT-23，specs/plt23-data-maintenance/spec.md 第二节 B；W13 技术债 T41）
-- 状态：草稿。只写文件，由人先在副本演练再执行（CLAUDE.md 必停项：迁移、权限）。不在任何共享环境执行。
-- 依赖：0007（ingest_request）、0025（behavior_event）、0029（auth_otp_challenge、auth_session、auth_rate_counter）、
--       0009（starbuddy_scheduler 的 schema 使用权）、0035（个人层表清单）、0060（留存函数的写法）。
--
-- 保留期（spec 决定：2026-11-09 用户按默认确认；须合规负责人在隐私政策与同意书里写同一组数）：
--   platform.ingest_request      写入幂等      created_at 起 30 天（仍在进行中、租约未过期的不删）
--   platform.auth_otp_challenge  验证码        过期后 1 天
--   platform.auth_session        会话          失效（到期、空闲超时、注销三者最早）后 30 天
--   platform.auth_rate_counter   限流计数      窗口开始后 2 天（最长的窗口是 1 天，config/auth.json）
--   insight.behavior_event       行为事件      写入（created_at，服务端时间）后 180 天
-- 写法照 0060 的 model_call_log_purge：SECURITY DEFINER、属主 starbuddy_authz（只得到这几张表的 SELECT 与 DELETE）、每次至多一批
-- （1–10000，按时间先后）、p_now 只能在数据库 now() 往前 1 天之内且不晚于 now()（越界 22023）、只按时间删、不按学生删、只授 starbuddy_scheduler。
-- 按学生删仍只经删除执行身份 eraser（0035）：79p、80 号测试把这几个函数登记为与 0060 同类的例外（只删超过保留期的行）。
-- 不在本迁移：replay_seal（封存记录随学生档案级联，计量与幂等口径另议）；证据、作答、计量流水（学习记录本身，保留随导出与删除规则）。
-- 请以单事务执行（psql -1）。

GRANT DELETE ON platform.auth_otp_challenge, platform.auth_session TO starbuddy_authz;
-- 写入幂等表：只授判断“是否超过保留期”要用的五列（读不到学生、attempt_id、指纹与结果摘要；50 号测试核对）
GRANT SELECT (method, idempotency_key, created_at, status, lease_until), DELETE ON platform.ingest_request TO starbuddy_authz;
GRANT DELETE ON insight.behavior_event TO starbuddy_authz;
-- auth_rate_counter：0029 已授 authz SELECT、DELETE（限流函数顺手清理同键旧窗口），不用再授

CREATE FUNCTION platform.retention_args_ok(p_now timestamptz, p_batch integer) RETURNS void
  LANGUAGE plpgsql STABLE SET search_path = pg_catalog, platform, pg_temp
  AS $$
BEGIN
  IF p_now IS NULL OR p_now > now() OR p_now < now() - interval '1 day' THEN
    RAISE EXCEPTION 'p_now must be within the last day and not in the future' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_batch IS NULL OR p_batch < 1 OR p_batch > 10000 THEN
    RAISE EXCEPTION 'batch must be between 1 and 10000' USING ERRCODE = 'invalid_parameter_value';
  END IF;
END $$;

CREATE FUNCTION platform.retention_purge_ingest_request(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  PERFORM platform.retention_args_ok(p_now, p_batch);
  DELETE FROM platform.ingest_request r
   WHERE (r.method, r.idempotency_key) IN (
     SELECT x.method, x.idempotency_key FROM platform.ingest_request x
      WHERE x.created_at < p_now - interval '30 days'
        AND (x.status <> 'pending' OR x.lease_until < p_now - interval '30 days')
      ORDER BY x.created_at LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

CREATE FUNCTION platform.retention_purge_auth_otp(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  PERFORM platform.retention_args_ok(p_now, p_batch);
  DELETE FROM platform.auth_otp_challenge o
   WHERE o.id IN (SELECT x.id FROM platform.auth_otp_challenge x
                   WHERE x.expires_at < p_now - interval '1 day'
                   ORDER BY x.expires_at LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

CREATE FUNCTION platform.retention_purge_auth_session(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  PERFORM platform.retention_args_ok(p_now, p_batch);
  -- 会话的失效时刻：到期、空闲超时（最后活动 + 空闲时长）、注销三者最早
  DELETE FROM platform.auth_session s
   WHERE s.id IN (SELECT x.id FROM platform.auth_session x
                   WHERE LEAST(x.expires_at, x.last_seen_at + x.idle_timeout, coalesce(x.revoked_at, 'infinity'::timestamptz))
                         < p_now - interval '30 days'
                   ORDER BY x.created_at LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

CREATE FUNCTION platform.retention_purge_auth_rate_counter(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, platform, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  PERFORM platform.retention_args_ok(p_now, p_batch);
  DELETE FROM platform.auth_rate_counter c
   WHERE (c.bucket, c.key_digest, c.window_start) IN (
     SELECT x.bucket, x.key_digest, x.window_start FROM platform.auth_rate_counter x
      WHERE x.window_start < p_now - interval '2 days'
      ORDER BY x.window_start LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

CREATE FUNCTION insight.retention_purge_behavior_event(p_now timestamptz, p_batch integer) RETURNS integer
  LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, insight, pg_temp
  AS $$
DECLARE v_n integer;
BEGIN
  PERFORM platform.retention_args_ok(p_now, p_batch);
  DELETE FROM insight.behavior_event e
   WHERE e.id IN (SELECT x.id FROM insight.behavior_event x
                   WHERE x.created_at < p_now - interval '180 days'
                   ORDER BY x.created_at LIMIT p_batch);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

ALTER FUNCTION platform.retention_purge_ingest_request(timestamptz, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.retention_purge_auth_otp(timestamptz, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.retention_purge_auth_session(timestamptz, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION platform.retention_purge_auth_rate_counter(timestamptz, integer) OWNER TO starbuddy_authz;
ALTER FUNCTION insight.retention_purge_behavior_event(timestamptz, integer) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION platform.retention_args_ok(timestamptz, integer), platform.retention_purge_ingest_request(timestamptz, integer),
  platform.retention_purge_auth_otp(timestamptz, integer), platform.retention_purge_auth_session(timestamptz, integer),
  platform.retention_purge_auth_rate_counter(timestamptz, integer), insight.retention_purge_behavior_event(timestamptz, integer) FROM PUBLIC;
-- 参数核对是内部辅助函数：只给五个清理函数的属主 authz 用
GRANT EXECUTE ON FUNCTION platform.retention_args_ok(timestamptz, integer) TO starbuddy_authz;
GRANT EXECUTE ON FUNCTION platform.retention_purge_ingest_request(timestamptz, integer), platform.retention_purge_auth_otp(timestamptz, integer),
  platform.retention_purge_auth_session(timestamptz, integer), platform.retention_purge_auth_rate_counter(timestamptz, integer),
  insight.retention_purge_behavior_event(timestamptz, integer) TO starbuddy_scheduler;

COMMENT ON FUNCTION platform.retention_purge_ingest_request(timestamp with time zone, integer) IS
  '写入幂等记录保留期清理（0063，PLT-23）：每次至多 p_batch 行，只删 created_at 早于 p_now − 30 天、且不在进行中的；只授 starbuddy_scheduler';
COMMENT ON FUNCTION platform.retention_purge_auth_otp(timestamp with time zone, integer) IS
  '验证码保留期清理（0063，PLT-23）：只删过期超过 1 天的；只授 starbuddy_scheduler';
COMMENT ON FUNCTION platform.retention_purge_auth_session(timestamp with time zone, integer) IS
  '会话保留期清理（0063，PLT-23）：只删失效（到期、空闲超时、注销三者最早）超过 30 天的；只授 starbuddy_scheduler';
COMMENT ON FUNCTION platform.retention_purge_auth_rate_counter(timestamp with time zone, integer) IS
  '限流计数保留期清理（0063，PLT-23）：只删窗口开始超过 2 天的；只授 starbuddy_scheduler';
COMMENT ON FUNCTION insight.retention_purge_behavior_event(timestamp with time zone, integer) IS
  '行为事件保留期清理（0063，PLT-23）：只删写入超过 180 天的（按服务端写入时间，不按学生）；只授 starbuddy_scheduler';
