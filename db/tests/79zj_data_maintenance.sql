-- 0062 分区续建与 0063 保留期清理（PLT-23，specs/plt23-data-maintenance/spec.md 第二节 A、B；W13 技术债 T18、T41）。
-- 全部合成数据（规则 8）：以超级用户直写历史时间点的过程数据（只为夹具）。整个文件在一个事务里，结束回滚。
-- 判据：
--   一、分区续建：只授 starbuddy_scheduler；把当前月 + 6 个月里缺的分区补齐（模拟“缺”：先删掉还没有数据的将来几个月），再调用一次不建（幂等）；
--       建出的分区属主与父表相同、对任何应用角色都没有权限（只经父表访问）；时钟越界 22023；分区水位与目录一致；
--       函数是全库唯一不属 authz / eraser 的 SECURITY DEFINER 函数，属主就是四张父表的属主（80 号测试登记的例外）；
--   二、保留期清理：五个函数各自只删超过保留期的行（截止线随 p_now 走），每次至多一批、删完幂等；时钟与批量越界 22023；
--       只授 starbuddy_scheduler；属主 authz、SECURITY DEFINER、search_path 固定。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set A1 '''a1000000-0000-4000-8000-0000000000a1'''
BEGIN;
SET LOCAL TIME ZONE 'UTC';

CREATE FUNCTION t.dm_call(p_role text, p_sql text) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v integer;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v;
  RESET ROLE;
  RETURN v;
END $$;
CREATE FUNCTION t.dm_month(p_offset integer) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT to_char(date_trunc('month', now() AT TIME ZONE 'UTC') + make_interval(months => p_offset), 'YYYYMM') $$;

-- ================= 一、分区续建（0062） =================
SELECT t.ok((SELECT count(*) FROM pg_proc p WHERE p.oid = 'platform.ensure_partitions_ahead(timestamptz)'::regprocedure AND p.prosecdef
               AND p.proowner = (SELECT c.relowner FROM pg_class c WHERE c.oid = 'platform.evidence_event'::regclass)
               AND (SELECT count(DISTINCT c.relowner) FROM pg_class c
                     WHERE c.oid IN ('platform.evidence_event'::regclass, 'platform.attempt'::regclass, 'platform.usage_ledger'::regclass,
                                     'platform.model_call_log'::regclass)) = 1
               AND 'search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig) AND 'TimeZone=UTC' = ANY (p.proconfig)) = 1,
            '续建：SECURITY DEFINER，属主就是四张父表的属主，search_path 固定、时区 UTC');
SELECT t.ok(NOT (SELECT prosecdef FROM pg_proc WHERE oid = 'platform.partition_horizon()'::regprocedure),
            '分区水位只读系统目录：SECURITY INVOKER');
SELECT t.ok((SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser', 'starbuddy_authz']) r(role)
              WHERE has_function_privilege(r.role, 'platform.ensure_partitions_ahead(timestamptz)', 'EXECUTE')) = '{starbuddy_scheduler}'
            AND (SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser', 'starbuddy_authz']) r(role)
              WHERE has_function_privilege(r.role, 'platform.partition_horizon()', 'EXECUTE')) = '{starbuddy_scheduler}',
            '续建与水位只授调度器（PUBLIC、报表身份与其他应用角色都不能执行）');
SELECT t.throws($$SELECT t.dm_call('starbuddy_learning', 'SELECT platform.ensure_partitions_ahead(now())')$$, '42501', '续建：学习服务调用被拒');
SELECT t.throws($$SELECT t.dm_call('starbuddy_bff', 'SELECT platform.ensure_partitions_ahead(now())')$$, '42501', '续建：BFF 调用被拒');

-- 模拟“将来几个月的分区还没建”：删掉四张表当前月 +4 到 +6 的分区（还没有数据）
SET LOCAL client_min_messages = warning;
DO $$
DECLARE r text; m integer;
BEGIN
  FOREACH r IN ARRAY ARRAY['evidence_event', 'attempt', 'usage_ledger', 'model_call_log'] LOOP
    FOR m IN 4 .. 6 LOOP
      -- attempt 的分区被别的表的外键引用：CASCADE 连带删掉的约束随本文件的事务回滚恢复
      EXECUTE format('DROP TABLE IF EXISTS platform.%I CASCADE', r || '_' || t.dm_month(m));
    END LOOP;
  END LOOP;
END $$;
SELECT t.ok((SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'platform'
              WHERE c.relname IN ('evidence_event_' || t.dm_month(5), 'attempt_' || t.dm_month(5))) = 0, '夹具：将来的分区已删掉');
SELECT count(*) AS missing FROM unnest(ARRAY['evidence_event', 'attempt', 'usage_ledger', 'model_call_log']) r, generate_series(0, 6) m
 WHERE to_regclass(format('platform.%I', r || '_' || t.dm_month(m))) IS NULL \gset
SELECT t.dm_call('starbuddy_scheduler', 'SELECT platform.ensure_partitions_ahead(now())') AS created \gset
SELECT t.ok(:missing >= 12 AND :created = :missing, format('续建：补齐四张表当前月起 7 个月里缺的分区（缺 %s，建 %s）', :missing, :created));
SELECT t.ok(t.dm_call('starbuddy_scheduler', 'SELECT platform.ensure_partitions_ahead(now())') = 0, '续建：再调用一次不建（幂等）');
SELECT t.ok((SELECT bool_and(to_regclass(format('platform.%I', r || '_' || t.dm_month(m))) IS NOT NULL)
               FROM unnest(ARRAY['evidence_event', 'attempt', 'usage_ledger', 'model_call_log']) r, generate_series(0, 6) m),
            '续建：四张表当前月 + 6 个月的分区都在');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent
   WHERE p.oid IN ('platform.evidence_event'::regclass, 'platform.attempt'::regclass, 'platform.usage_ledger'::regclass, 'platform.model_call_log'::regclass)
     AND (c.relowner <> p.relowner OR c.relacl IS NOT NULL)),
  '续建：分区属主与父表相同，没有任何授权（只经父表访问，攻击面 J1）');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_report',
                             'starbuddy_authz', 'starbuddy_eraser']) r(role)
   WHERE has_table_privilege(r.role, format('platform.%I', 'evidence_event_' || t.dm_month(5)), 'SELECT')
      OR has_table_privilege(r.role, format('platform.%I', 'usage_ledger_' || t.dm_month(5)), 'INSERT')),
  '续建：新建的分区对任何应用角色都不能直接读写');
SELECT t.ok((SELECT count(*) FROM platform.partition_horizon()) = 4
            AND (SELECT bool_and(last_month >= (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '6 months')::date) FROM platform.partition_horizon()),
            '分区水位：四张表各一行，都至少到当前月 + 6 个月');
SELECT t.throws($$SELECT t.dm_call('starbuddy_scheduler', 'SELECT platform.ensure_partitions_ahead(now() + interval ''1 hour'')')$$, '22023',
                '续建：未来时钟 22023');
SELECT t.throws($$SELECT t.dm_call('starbuddy_scheduler', 'SELECT platform.ensure_partitions_ahead(now() - interval ''2 days'')')$$, '22023',
                '续建：时钟最多往前 1 天');
SELECT t.throws($$SELECT t.dm_call('starbuddy_scheduler', 'SELECT platform.ensure_partitions_ahead(NULL)')$$, '22023', '续建：时钟为空 22023');

-- ================= 二、保留期清理（0063） =================
-- 夹具：每张表“超过保留期”与“还在保留期内”各一组
INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, status, summary, created_at, updated_at)
VALUES ('grade', 'dm-ingest-old-succeeded-01', 'aya', :S1, repeat('a', 64), '{d0630000-0000-4000-8000-000000000001}', 'succeeded', '{}',
        now() - interval '31 days', now() - interval '31 days'),
       ('grade', 'dm-ingest-new-succeeded-01', 'aya', :S1, repeat('a', 64), '{d0630000-0000-4000-8000-000000000002}', 'succeeded', '{}',
        now() - interval '29 days', now() - interval '29 days');
INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, lease_token, lease_until, created_at)
VALUES ('grade', 'dm-ingest-old-abandoned-01', 'aya', :S1, repeat('a', 64), '{d0630000-0000-4000-8000-000000000003}',
        'd0630000-0000-4000-8000-0000000000b3', now() - interval '31 days', now() - interval '40 days'),
       ('grade', 'dm-ingest-old-leased-0001', 'aya', :S1, repeat('a', 64), '{d0630000-0000-4000-8000-000000000004}',
        'd0630000-0000-4000-8000-0000000000b4', now() + interval '1 minute', now() - interval '40 days');
INSERT INTO platform.auth_otp_challenge (id, phone_digest, code_mac, created_at, expires_at, max_attempts) VALUES
  ('d0630000-0000-4000-8000-0000000000e1', sha256('dm-otp-old'), sha256('m1'), now() - interval '2 days 1 hour', now() - interval '2 days', 5),
  ('d0630000-0000-4000-8000-0000000000e2', sha256('dm-otp-new'), sha256('m2'), now() - interval '13 hours', now() - interval '12 hours', 5);
INSERT INTO platform.auth_session (id, token_hash, account_id, origin, created_at, last_seen_at, expires_at, idle_timeout, revoked_at, revoked_reason) VALUES
  -- 注销 31 天：删
  ('d0630000-0000-4000-8000-0000000000f1', sha256('dm-s1'), :A1, 'otp', now() - interval '40 days', now() - interval '31 days',
   now() + interval '1 day', interval '1 day', now() - interval '31 days', 'logout'),
  -- 空闲超时 39 天前（最后活动 40 天前 + 空闲 1 天），到期在将来：删
  ('d0630000-0000-4000-8000-0000000000f2', sha256('dm-s2'), :A1, 'otp', now() - interval '41 days', now() - interval '40 days',
   now() + interval '1 day', interval '1 day', NULL, NULL),
  -- 到期 29 天前：留
  ('d0630000-0000-4000-8000-0000000000f3', sha256('dm-s3'), :A1, 'otp', now() - interval '36 days', now() - interval '30 days',
   now() - interval '29 days', interval '7 days', NULL, NULL),
  -- 正在用：留
  ('d0630000-0000-4000-8000-0000000000f4', sha256('dm-s4'), :A1, 'otp', now() - interval '1 hour', now(), now() + interval '6 days', interval '1 day', NULL, NULL);
INSERT INTO platform.auth_rate_counter (bucket, key_digest, window_start, hits) VALUES
  ('dm.test.day', sha256('dm-k1'), now() - interval '3 days', 1),
  ('dm.test.day', sha256('dm-k2'), now() - interval '1 day', 1);
INSERT INTO insight.behavior_event (id, student_id, kind, payload, occurred_at, idempotency_key, created_at) VALUES
  ('d0630000-0000-4000-8000-0000000000a1', :S1, 'session_start', '{"entry": "today"}', now() - interval '181 days',
   'd0630000-0000-4000-8000-0000000000c1', now() - interval '181 days'),
  ('d0630000-0000-4000-8000-0000000000a2', :S1, 'session_start', '{"entry": "today"}', now() - interval '179 days 12 hours',
   'd0630000-0000-4000-8000-0000000000c2', now() - interval '179 days 12 hours');

-- 每个函数：批量 1 每次至多删 1 行；循环到删不满为止
CREATE FUNCTION t.dm_drain(p_func text) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v integer; total integer := 0;
BEGIN
  LOOP
    v := t.dm_call('starbuddy_scheduler', format('SELECT %s(now(), 1)', p_func));
    IF v > 1 THEN RAISE EXCEPTION 'FAIL: % 一批删了 % 行（批量 1）', p_func, v; END IF;
    total := total + v;
    EXIT WHEN v = 0 OR total > 100000;
  END LOOP;
  RETURN total;
END $$;
SELECT t.dm_drain('platform.retention_purge_ingest_request') >= 2 AS ok_ingest \gset
SELECT t.ok(:'ok_ingest'::boolean
            AND NOT EXISTS (SELECT 1 FROM platform.ingest_request WHERE idempotency_key IN ('dm-ingest-old-succeeded-01', 'dm-ingest-old-abandoned-01'))
            AND (SELECT count(*) FROM platform.ingest_request WHERE idempotency_key IN ('dm-ingest-new-succeeded-01', 'dm-ingest-old-leased-0001')) = 2,
            '写入幂等：删掉 30 天前已结束的与租约早已过期的；30 天内的、租约仍有效的进行中请求都留着');
SELECT t.dm_drain('platform.retention_purge_auth_otp') >= 1 AS ok_otp \gset
SELECT t.ok(:'ok_otp'::boolean AND NOT EXISTS (SELECT 1 FROM platform.auth_otp_challenge WHERE id = 'd0630000-0000-4000-8000-0000000000e1')
            AND EXISTS (SELECT 1 FROM platform.auth_otp_challenge WHERE id = 'd0630000-0000-4000-8000-0000000000e2'),
            '验证码：只删过期超过 1 天的');
SELECT t.dm_drain('platform.retention_purge_auth_session') >= 2 AS ok_sess \gset
SELECT t.ok(:'ok_sess'::boolean
            AND NOT EXISTS (SELECT 1 FROM platform.auth_session WHERE id IN ('d0630000-0000-4000-8000-0000000000f1', 'd0630000-0000-4000-8000-0000000000f2'))
            AND (SELECT count(*) FROM platform.auth_session WHERE id IN ('d0630000-0000-4000-8000-0000000000f3', 'd0630000-0000-4000-8000-0000000000f4')) = 2,
            '会话：失效（注销、空闲超时、到期三者最早）超过 30 天的删，其余留着');
SELECT t.dm_drain('platform.retention_purge_auth_rate_counter') >= 1 AS ok_rate \gset
SELECT t.ok(:'ok_rate'::boolean AND NOT EXISTS (SELECT 1 FROM platform.auth_rate_counter WHERE key_digest = sha256('dm-k1'))
            AND EXISTS (SELECT 1 FROM platform.auth_rate_counter WHERE key_digest = sha256('dm-k2')),
            '限流计数：只删窗口开始超过 2 天的');
SELECT t.dm_drain('insight.retention_purge_behavior_event') >= 1 AS ok_be \gset
SELECT t.ok(:'ok_be'::boolean AND NOT EXISTS (SELECT 1 FROM insight.behavior_event WHERE id = 'd0630000-0000-4000-8000-0000000000a1')
            AND EXISTS (SELECT 1 FROM insight.behavior_event WHERE id = 'd0630000-0000-4000-8000-0000000000a2'),
            '行为事件：只删写入超过 180 天的');
SELECT t.ok(t.dm_call('starbuddy_scheduler', 'SELECT insight.retention_purge_behavior_event(now() - interval ''23 hours'', 10)') = 0,
            '截止线随 p_now 走：时钟往前拨 23 小时，保留期内的仍然不删');

-- 参数与授权（五个函数同一口径）
DO $$
DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY['platform.retention_purge_ingest_request', 'platform.retention_purge_auth_otp', 'platform.retention_purge_auth_session',
                           'platform.retention_purge_auth_rate_counter', 'insight.retention_purge_behavior_event'] LOOP
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_scheduler', 'SELECT %s(now() + interval ''1 hour'', 10)')$q$, f), '22023', f || '：未来时钟 22023');
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_scheduler', 'SELECT %s(now() - interval ''2 days'', 10)')$q$, f), '22023', f || '：时钟最多往前 1 天');
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_scheduler', 'SELECT %s(now(), 0)')$q$, f), '22023', f || '：批量 0 被拒');
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_scheduler', 'SELECT %s(now(), 10001)')$q$, f), '22023', f || '：批量超过 10000 被拒');
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_learning', 'SELECT %s(now(), 10)')$q$, f), '42501', f || '：学习服务调用被拒');
    PERFORM t.throws(format($q$SELECT t.dm_call('starbuddy_bff', 'SELECT %s(now(), 10)')$q$, f), '42501', f || '：BFF 调用被拒');
  END LOOP;
END $$;
SELECT t.ok((SELECT bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                             AND EXISTS (SELECT 1 FROM unnest(p.proconfig) c WHERE c ~ '^search_path=pg_catalog, (platform|insight), pg_temp$'))
               FROM pg_proc p WHERE p.oid IN ('platform.retention_purge_ingest_request(timestamptz, integer)'::regprocedure,
                                              'platform.retention_purge_auth_otp(timestamptz, integer)'::regprocedure,
                                              'platform.retention_purge_auth_session(timestamptz, integer)'::regprocedure,
                                              'platform.retention_purge_auth_rate_counter(timestamptz, integer)'::regprocedure,
                                              'insight.retention_purge_behavior_event(timestamptz, integer)'::regprocedure)),
            '清理函数：SECURITY DEFINER、属主 starbuddy_authz、search_path 固定且以 pg_temp 结尾');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM unnest(ARRAY['platform.retention_purge_ingest_request(timestamptz, integer)', 'platform.retention_purge_auth_otp(timestamptz, integer)',
                             'platform.retention_purge_auth_session(timestamptz, integer)', 'platform.retention_purge_auth_rate_counter(timestamptz, integer)',
                             'insight.retention_purge_behavior_event(timestamptz, integer)']) f,
         unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read',
                      'starbuddy_report', 'starbuddy_eraser']) r(role)
   WHERE has_function_privilege(r.role, f::regprocedure, 'EXECUTE'))
  AND (SELECT bool_and(has_function_privilege('starbuddy_scheduler', f::regprocedure, 'EXECUTE'))
         FROM unnest(ARRAY['platform.retention_purge_ingest_request(timestamptz, integer)', 'platform.retention_purge_auth_otp(timestamptz, integer)',
                           'platform.retention_purge_auth_session(timestamptz, integer)', 'platform.retention_purge_auth_rate_counter(timestamptz, integer)',
                           'insight.retention_purge_behavior_event(timestamptz, integer)']) f)
  AND NOT has_function_privilege('starbuddy_scheduler', 'platform.retention_args_ok(timestamptz, integer)', 'EXECUTE'),
  '清理函数只授调度器（参数核对辅助函数只给属主 authz 用）');
ROLLBACK;
