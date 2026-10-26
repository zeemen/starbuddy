-- 0060 模型调用记录落库 platform.model_call_log（specs/plt-model-call-log/spec.md 范围第 1、3、5 条；决定记录（2026-10-26）第 1、2 条）。
-- 全部合成数据（规则 8）：调用记录只有编码与计数；对账部分的预留与自练计次以超级用户在复制模式下直写（只为夹具，跳过外键）。
-- 整个文件在一个事务里，结束回滚，不给 80 的可见性矩阵留任何数据。
-- 判据：
--   一、表与列：按月分区、RLS 开启且 FORCE、预建 2026-10 起 15 个分区、列恰好是元数据白名单（没有 uuid 列、没有学生 / 账号 / 原文类列）、
--       在个人层表清单里（决定记录（续）第 4 条：strategy = delete，按计量 usage_id 摘要匹配）；
--   二、写入：只有 starbuddy_learning 能 INSERT，且是列级（id、recorded_at 只能取缺省）；读不到、改不了、删不掉；WITH CHECK 只收
--       现在前 1 天到后 5 分钟；格式约束挡住原值摘要、自由文本与不一致的组合；其余应用角色对表没有任何权限（删除执行身份只有 DELETE
--       与 usage_ref 的 SELECT）；
--   三、聚合读取：只授 starbuddy_report（越权 42501），按北京时间自然日 × 环境 × 任务 × 供应商 × 结果，与手算一致；少于 5 次的格子
--       只给次数（决定记录（续）第 6 条）；窗口越界 22023；报表身份不能直接读表；
--   四、留存删除：只授 starbuddy_scheduler，只删 180 天前的、按时间先后每次至多一批；时钟与批量越界 22023；
--   五、函数属性与授权；报表身份在 platform 下能执行的非触发器函数恰好是登记集合（决定记录（续）第 5 条）；
--   六、对账草案（tools/loadtest/sql/model-call-reconcile.draft.sql）：在本事务里建出，按日、按任务与计量账本的 usage_ref 对齐，与手算一致；
--       清单谓词按该学生的计量 usage_id 摘要恰好匹配到他的调用记录（端到端的删除执行与全库扫描在 79q）。
\set U1 '''f0600000-0000-4000-8000-000000000001'''
\set U2 '''f0600000-0000-4000-8000-000000000002'''
\set U3 '''f0600000-0000-4000-8000-000000000003'''
\set U4 '''f0600000-0000-4000-8000-000000000004'''
\set U5 '''f0600000-0000-4000-8000-000000000005'''
\set SY '''f0600000-0000-4000-8000-0000000000c2'''
\set SX '''f0600000-0000-4000-8000-0000000000c1'''
\set EX '''f0600000-0000-4000-8000-0000000000e1'''
\set FP '''0000000000000000000000000000000000000000000000000000000000000060'''
BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- 摘要：与学习服务 ledger.digest 同一规则（sha256 十六进制前 16 位）；常量由 Python hashlib 算出，两边一致
CREATE FUNCTION t.mcl_ref(v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT left(encode(sha256(convert_to(v, 'UTF8')), 'hex'), 16) $$;
-- 写一条合成调用记录：p_role 为空时以超级用户写（绕过 RLS，只为构造历史数据）；否则以该角色写（SET LOCAL ROLE 在 t.throws 的子事务里失败时随之回滚）
CREATE FUNCTION t.mcl_ins(p_role text, p_at timestamptz, p_task text, p_outcome text, p_vendor text, p_statuses text[], p_in integer,
                          p_out integer, p_cost numeric, p_fallback boolean, p_ref text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF p_role IS NOT NULL THEN EXECUTE format('SET LOCAL ROLE %I', p_role); END IF;
  INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, subject, vendor, model, model_release, model_version,
                                       prompt_template, prompt_version, source_version, outcome, attempt_count, fallback, attempt_statuses,
                                       input_tokens, output_tokens, estimated_cost_cny, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)
  VALUES (p_at, 'test', p_task, 'L1', 'zhixing', p_vendor, CASE WHEN p_vendor IS NOT NULL THEN 'syn-model' END,
          CASE WHEN p_vendor IS NOT NULL THEN '2026-09-01' END,
          CASE WHEN p_outcome = 'ok' THEN format('model:%s/syn-model@2026-09-01', p_vendor) END,
          'syn-grade', 'v1', 'prompt:syn-grade@v1', p_outcome, cardinality(p_statuses), p_fallback, p_statuses, p_in, p_out, p_cost, 12,
          false, p_ref, t.mcl_ref('syn-idem:' || p_ref));
  IF p_role IS NOT NULL THEN RESET ROLE; END IF;
END $$;
-- 以某个角色执行一条语句并返回文本结果
CREATE FUNCTION t.mcl_as(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v;
  RESET ROLE;
  RETURN v;
END $$;

-- ================= 一、表、分区与列 =================
SELECT t.ok((SELECT c.relkind = 'p' AND c.relrowsecurity AND c.relforcerowsecurity FROM pg_class c WHERE c.oid = 'platform.model_call_log'::regclass)
            AND (SELECT string_agg(c.relname::text, ',' ORDER BY c.relname) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                  WHERE i.inhparent = 'platform.model_call_log'::regclass)
                = (SELECT string_agg('model_call_log_' || to_char(m, 'YYYYMM'), ',' ORDER BY m)
                     FROM generate_series(DATE '2026-10-01', DATE '2027-12-01', interval '1 month') m),
            '调用记录：按月分区的父表（RLS ENABLE + FORCE），预建 2026-10 到 2027-12 共 15 个月分区，没有 DEFAULT 分区');
SELECT t.ok((SELECT string_agg(a.attname::text, ',' ORDER BY a.attnum) FROM pg_attribute a
              WHERE a.attrelid = 'platform.model_call_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped)
            = 'id,occurred_at,recorded_at,env,task,data_class,subject,vendor,model,model_release,model_version,prompt_template,'
              'prompt_version,source_version,outcome,attempt_count,fallback,attempt_statuses,input_tokens,output_tokens,estimated_cost_cny,'
              'elapsed_ms,model_mismatch,usage_ref,idempotency_ref',
            '调用记录：列恰好是元数据白名单（时间、环境、任务、级别、学科、供应商、模型、版本、模板、结果、尝试、切备、token、成本、耗时、两个摘要）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = 'platform.model_call_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                         AND (a.atttypid IN ('uuid'::regtype, 'uuid[]'::regtype, 'jsonb'::regtype, 'json'::regtype, 'bytea'::regtype)
                              OR a.attname ~ '(student|account|household|guardian|learner|user|phone|email|message|content|answer|response|body|raw|prompt_text|_text$|^text$|^output$|ip_addr)')),
            '调用记录：没有 uuid / jsonb / bytea 列，也没有学生、账号、家庭、原文、输出、作答类的列（禁止字段静态扫描）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = 'platform.model_call_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                         AND a.atttypid = 'text'::regtype AND a.attname NOT IN ('env', 'task', 'data_class', 'outcome')
                         AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = a.attrelid AND k.contype = 'c' AND a.attnum = ANY (k.conkey)
                                           AND pg_get_constraintdef(k.oid) ~ '~')),
            '调用记录：每个自由形状的文本列都有格式约束（正则），自由文本写不进来');
SELECT t.ok((SELECT g.strategy = 'delete' AND g.erase_scope = 'inference' AND g.export_topic IS NULL AND g.delete_order IS NOT NULL
                    AND g.student_predicate ~ 'platform\.usage_reservation r\M' AND g.student_predicate ~ 'platform\.practice_log p\M'
                    AND g.student_predicate ~ 'left\(encode\(sha256\(convert_to\([rp]\.usage_id::text, ''UTF8''\)\), ''hex''\), 16\)'
               FROM platform.personal_table_registry g WHERE g.nspname = 'platform' AND g.relname = 'model_call_log'),
            '调用记录：在个人层表清单中（strategy = delete、回执范围 inference、不进导出包），谓词按该学生计量预留与自练计次的 usage_id 重算摘要匹配');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM t.p_discovered() d WHERE d = 'platform.model_call_log'),
            '调用记录：没有学生键（79p 的自动发现找不到它），是按 usage_ref 摘要主动登记的');

-- ================= 二、写入权限 =================
SELECT t.ok((SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
              WHERE a.attrelid = 'platform.model_call_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                AND has_column_privilege('starbuddy_learning', a.attrelid, a.attnum, 'INSERT'))
            = (SELECT array_agg(x ORDER BY x) FROM unnest(ARRAY['occurred_at', 'env', 'task', 'data_class', 'subject', 'vendor', 'model',
                 'model_release', 'model_version', 'prompt_template', 'prompt_version', 'source_version', 'outcome', 'attempt_count', 'fallback',
                 'attempt_statuses', 'input_tokens', 'output_tokens', 'estimated_cost_cny', 'elapsed_ms', 'model_mismatch', 'usage_ref',
                 'idempotency_ref']) x)
            AND NOT has_table_privilege('starbuddy_learning', 'platform.model_call_log', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_learning', 'platform.model_call_log', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_learning', 'platform.model_call_log', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_learning', 'platform.model_call_log', 'DELETE')
            AND NOT has_table_privilege('starbuddy_learning', 'platform.model_call_log', 'TRUNCATE'),
            '学习服务只有列级 INSERT（id、recorded_at 不在里面），没有 SELECT / UPDATE / DELETE / TRUNCATE');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_kge',
                                                    'starbuddy_kge_read', 'starbuddy_report']) r(role)
                         WHERE has_any_column_privilege(r.role, 'platform.model_call_log', 'SELECT')
                            OR has_any_column_privilege(r.role, 'platform.model_call_log', 'INSERT')
                            OR has_any_column_privilege(r.role, 'platform.model_call_log', 'UPDATE')
                            OR has_table_privilege(r.role, 'platform.model_call_log', 'DELETE')
                            OR has_table_privilege(r.role, 'platform.model_call_log', 'TRUNCATE')),
            '调用记录：BFF、复核、调度、洞察、知识、报表对表没有任何权限（报表与调度只经函数）');
SELECT t.ok(has_table_privilege('starbuddy_eraser', 'platform.model_call_log', 'DELETE')
            AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
                  WHERE a.attrelid = 'platform.model_call_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                    AND has_column_privilege('starbuddy_eraser', a.attrelid, a.attnum, 'SELECT')) = '{usage_ref}'
            AND NOT has_any_column_privilege('starbuddy_eraser', 'platform.model_call_log', 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_eraser', 'platform.model_call_log', 'UPDATE')
            AND NOT has_table_privilege('starbuddy_eraser', 'platform.model_call_log', 'TRUNCATE')
            AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
                  WHERE a.attrelid = 'platform.usage_reservation'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                    AND has_column_privilege('starbuddy_eraser', a.attrelid, a.attnum, 'SELECT')) = '{student_id,usage_id}'
            AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
                  WHERE a.attrelid = 'platform.practice_log'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                    AND has_column_privilege('starbuddy_eraser', a.attrelid, a.attnum, 'SELECT')) = '{student_id,usage_id}'
            AND NOT has_table_privilege('starbuddy_eraser', 'platform.usage_reservation', 'DELETE')
            AND NOT has_table_privilege('starbuddy_eraser', 'platform.practice_log', 'DELETE'),
            '删除执行身份：调用记录只有 DELETE 与 usage_ref 的 SELECT；两张计量表只读 usage_id 与 student_id（仍是豁免，删不了）');
SELECT t.mcl_ins('starbuddy_learning', now(), 'T2', 'ok', 'bailian', ARRAY['ok'], 10, 5, 0.01, false, t.mcl_ref(:U1));
SELECT t.ok((SELECT count(*) FROM platform.model_call_log WHERE usage_ref = t.mcl_ref(:U1)) = 1, '学习服务能写一条合格的调用记录（id、recorded_at 取缺省）');
SELECT t.throws($$SELECT t.mcl_as('starbuddy_learning', 'SELECT count(*)::text FROM platform.model_call_log')$$, '42501', '学习服务读调用记录被拒');
SELECT t.throws($$SELECT t.mcl_as('starbuddy_learning', 'WITH x AS (UPDATE platform.model_call_log SET input_tokens = 0 RETURNING 1) SELECT count(*)::text FROM x')$$,
                '42501', '学习服务改调用记录被拒');
SELECT t.throws($$SELECT t.mcl_as('starbuddy_learning', 'WITH x AS (DELETE FROM platform.model_call_log RETURNING 1) SELECT count(*)::text FROM x')$$,
                '42501', '学习服务删调用记录被拒');
SELECT t.throws($$SELECT t.mcl_as('starbuddy_learning', $q$WITH x AS (INSERT INTO platform.model_call_log (occurred_at, recorded_at, env, task, data_class,
                    source_version, outcome, attempt_count, fallback, attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref,
                    idempotency_ref, prompt_template, prompt_version) VALUES (now(), now() - interval '9 days', 'test', 'T1', 'L0', 'prompt:a@v1', 'rejected',
                    0, false, '{}', 0, 0, 1, false, repeat('a', 16), repeat('b', 16), 'a', 'v1') RETURNING 1) SELECT count(*)::text FROM x$q$)$$,
                '42501', '学习服务不能自填 recorded_at（列级授权）');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now() - interval '2 days', 'T2', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, %L)$$,
                       t.mcl_ref(:U2)), '42501', '学习服务不能补写 1 天以前的记录（RLS WITH CHECK）');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now() + interval '1 hour', 'T2', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, %L)$$,
                       t.mcl_ref(:U2)), '42501', '学习服务不能写未来时刻的记录（RLS WITH CHECK）');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now(), 'T2', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, %L)$$, :U2),
                '23514', 'usage_ref 写原值（不是 16 位十六进制摘要）被约束拒绝');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now(), 'T2', 'weird', 'bailian', ARRAY['ok'], 1, 1, NULL, false, %L)$$,
                       t.mcl_ref(:U2)), '23514', '结果只收固定编码');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now(), 'T2', 'ok', 'bailian', ARRAY['ok', '学生原话'], 1, 1, NULL, false, %L)$$,
                       t.mcl_ref(:U2)), '23514', '尝试状态只收路由的固定编码（自由文本写不进来）');
SELECT t.throws(format($$SELECT t.mcl_ins('starbuddy_learning', now(), 'T2', 'ok', 'bai lian@x', ARRAY['ok'], 1, 1, NULL, false, %L)$$,
                       t.mcl_ref(:U2)), '23514', '供应商只收编码格式');
SELECT t.throws($$SET LOCAL ROLE starbuddy_learning; INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, source_version, outcome,
                  attempt_count, fallback, attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref, idempotency_ref,
                  prompt_template, prompt_version) VALUES (now(), 'test', 'T2', 'L1', 'prompt:a@v1', 'ok', 1, false, '{ok}', 1, 1, 1, false,
                  repeat('a', 16), repeat('b', 16), 'a', 'v1')$$,
                '23514', '成功的调用必须带 model_version');
SELECT t.throws($$SET LOCAL ROLE starbuddy_learning; INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, source_version, outcome,
                  attempt_count, fallback, attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)
                  VALUES (now(), 'test', 'T2', 'L1', 'prompt:a@v1', 'rejected', 0, false, '{}', 0, 0, 1, false, repeat('a', 16), repeat('b', 16))$$,
                '23514', '合格的 source_version 必须拆出模板与版本（两列与 source_version 一致）');
SELECT t.throws($$SET LOCAL ROLE starbuddy_learning; INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, source_version, outcome,
                  attempt_count, fallback, attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)
                  VALUES (now(), 'test', 'T2', 'L1', 'invalid', 'rejected', 1, false, '{}', 0, 0, 1, false, repeat('a', 16), repeat('b', 16))$$,
                '23514', '尝试次数与状态数组长度不一致被拒');
SELECT t.ok(t.mcl_as('starbuddy_learning', $q$WITH x AS (INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, source_version, outcome,
                  attempt_count, fallback, attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)
                  VALUES (now(), 'test', 'T1', 'L0', 'invalid', 'rejected', 0, false, '{}', 0, 0, 1, false, repeat('a', 16), repeat('b', 16))
                  RETURNING 1) SELECT count(*)::text FROM x$q$) = '1',
            '被拒的请求（source_version = invalid、没有尝试、没有供应商）也能记一条');

-- ================= 三、聚合读取（只授报表身份） =================
DELETE FROM platform.model_call_log;
SELECT t.mcl_ins(NULL, '2026-10-25 09:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 100, 50, 0.1, false, t.mcl_ref(:U1));
SELECT t.mcl_ins(NULL, '2026-10-25 10:00+08', 'T2', 'ok', 'bailian', ARRAY['timeout', 'ok'], 200, 100, 0.2, false, t.mcl_ref(:U1));
SELECT t.mcl_ins(NULL, '2026-10-25 11:00+08', 'T2', 'unavailable', 'ark', ARRAY['server', 'server'], 0, 0, NULL, true, t.mcl_ref(:U2));
SELECT t.mcl_ins(NULL, '2026-10-25 00:30+08', 'T1', 'rejected', NULL, ARRAY[]::text[], 0, 0, NULL, false, t.mcl_ref(:U3));   -- UTC 仍是 10-24
SELECT t.mcl_ins(NULL, '2026-10-24 23:30+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 10, 10, NULL, false, t.mcl_ref(:U4));
-- 凑满 5 次的格子（10-25 T2 bailian ok：前面 2 次 + 这 3 次，其中 1 次切备、1 次无单价）
SELECT t.mcl_ins(NULL, '2026-10-25 12:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 10, 5, 0.01, false, t.mcl_ref(:U2));
SELECT t.mcl_ins(NULL, '2026-10-25 12:10+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 10, 5, 0.01, false, t.mcl_ref(:U3));
SELECT t.mcl_ins(NULL, '2026-10-25 12:20+08', 'T2', 'ok', 'bailian', ARRAY['timeout', 'ok'], 10, 5, NULL, true, t.mcl_ref(:U4));
-- 空值显示为 -（concat_ws 会跳过空值）
CREATE FUNCTION t.mcl_stats(p_role text, p_from date, p_to date) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  SELECT string_agg(concat_ws('|', s.day, s.env, s.task, s.vendor, s.outcome, s.calls, coalesce(s.attempts::text, '-'), coalesce(s.fallback_calls::text, '-'),
                              coalesce(s.input_tokens::text, '-'), coalesce(s.output_tokens::text, '-'), coalesce(s.estimated_cost_cny::text, '-'),
                              coalesce(s.unpriced_calls::text, '-')), E'\n' ORDER BY s.day, s.task, s.vendor, s.outcome)
    INTO v FROM platform.model_call_stats(p_from, p_to) s;
  RESET ROLE;
  RETURN v;
END $$;
SELECT t.ok(t.mcl_stats('starbuddy_report', '2026-10-24', '2026-10-25')
            = E'2026-10-24|test|T2|bailian|ok|1|-|-|-|-|-|-\n'
              '2026-10-25|test|T1|none|rejected|1|-|-|-|-|-|-\n'
              '2026-10-25|test|T2|ark|unavailable|1|-|-|-|-|-|-\n'
              '2026-10-25|test|T2|bailian|ok|5|7|1|330|165|0.320000|1',
            '聚合：按北京时间自然日 × 环境 × 任务 × 供应商 × 结果汇总次数、尝试、切备、token、估算成本与无单价次数，与手算一致；'
            '少于 5 次的格子只给次数，其余为空（小样本门槛）');
SELECT t.ok(t.mcl_stats('starbuddy_report', '2026-10-25', '2026-10-25') NOT LIKE '%2026-10-24%'
            AND t.mcl_stats('starbuddy_report', '2026-10-25', '2026-10-25') LIKE '2026-10-25|test|T1|none|rejected|1|%',
            '聚合：按北京时间切日（UTC 前一天 16:30 的调用算北京时间当天，前一天 23:30 的不算）');
SELECT t.ok(t.mcl_stats('starbuddy_report', '2026-10-26', '2026-10-27') IS NULL, '聚合：窗口里没有调用时不返回行');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_report', NULL, '2026-10-25')$$, '22023', '聚合：起点为空 22023');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_report', '2026-10-25', '2026-10-24')$$, '22023', '聚合：止早于起 22023');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_report', '2026-01-01', '2026-07-07')$$, '22023', '聚合：跨度超过 186 天 22023');
SELECT t.ok(t.mcl_stats('starbuddy_report', '2026-04-22', '2026-10-25') IS NOT NULL, '聚合：跨度恰好 186 天可以');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_bff', '2026-10-24', '2026-10-25')$$, '42501', '聚合：BFF 调用被拒');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_learning', '2026-10-24', '2026-10-25')$$, '42501', '聚合：学习服务调用被拒');
SELECT t.throws($$SELECT t.mcl_stats('starbuddy_scheduler', '2026-10-24', '2026-10-25')$$, '42501', '聚合：调度器调用被拒');
SELECT t.throws($$SELECT t.mcl_as('starbuddy_report', 'SELECT count(*)::text FROM platform.model_call_log')$$, '42501',
                '报表身份直接读调用记录被拒（只经聚合函数）');
SELECT t.ok(pg_get_function_result('platform.model_call_stats(date, date)'::regprocedure) !~ '(usage_ref|idempotency|subject|id |occurred_at)',
            '聚合函数的输出只有日、环境、任务、供应商、结果与计数合计（没有摘要、学科与单条记录的字段）');
DELETE FROM platform.model_call_log WHERE occurred_at = '2026-10-25 12:20+08';
SELECT t.ok(t.mcl_stats('starbuddy_report', '2026-10-25', '2026-10-25') LIKE E'%\n2026-10-25|test|T2|bailian|ok|4|-|-|-|-|-|-',
            '聚合：小样本门槛的边界——恰好 4 次的格子也只给次数（门槛是 5，与 insight.onboarding_stats 同口径）');

-- ================= 四、留存删除（只授调度器） =================
DELETE FROM platform.model_call_log;
SELECT platform.ensure_monthly_partitions('platform.model_call_log', (now() - interval '200 days')::date, 8);
SELECT t.mcl_ins(NULL, now() - interval '200 days', 'T8', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, t.mcl_ref('old-200'));
SELECT t.mcl_ins(NULL, now() - interval '181 days', 'T4', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, t.mcl_ref('old-181'));
SELECT t.mcl_ins(NULL, now() - interval '180 days' + interval '1 hour', 'T2', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, t.mcl_ref('keep-179'));
SELECT t.mcl_ins(NULL, now() - interval '1 day', 'T1', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, t.mcl_ref('keep-1'));
CREATE FUNCTION t.mcl_purge(p_role text, p_now timestamptz, p_batch integer) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v integer;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  v := platform.model_call_log_purge(p_now, p_batch);
  RESET ROLE;
  RETURN v;
END $$;
-- 删除与核对分成两条语句：同一条语句里的子查询用语句开始时的快照，看不到函数里刚删的行
SELECT t.mcl_purge('starbuddy_scheduler', now(), 1) AS purged_first \gset
SELECT t.ok(:purged_first = 1
            AND NOT EXISTS (SELECT 1 FROM platform.model_call_log WHERE usage_ref = t.mcl_ref('old-200'))
            AND EXISTS (SELECT 1 FROM platform.model_call_log WHERE usage_ref = t.mcl_ref('old-181')),
            '留存：每次至多一批，按时间先后先删最早的（批量 1 只删 200 天前那条）');
SELECT t.ok(t.mcl_purge('starbuddy_scheduler', now(), 10) = 1 AND t.mcl_purge('starbuddy_scheduler', now(), 10) = 0,
            '留存：第二批删掉 181 天前那条，第三批没有可删的（幂等）');
SELECT t.ok((SELECT string_agg(task, ',' ORDER BY occurred_at) FROM platform.model_call_log) = 'T2,T1',
            '留存：只删 180 天前的行，留存期内的（179 天多、1 天前）都在');
SELECT t.ok(t.mcl_purge('starbuddy_scheduler', now() - interval '23 hours', 10) = 0,
            '留存：时钟往前拨 23 小时，留存期内的仍然不删（截止线随 p_now 走，不提前）');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', now() + interval '1 hour', 10)$$, '22023', '留存：未来时钟 22023（不能提前删）');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', now() - interval '2 days', 10)$$, '22023', '留存：时钟最多往前 1 天');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', NULL, 10)$$, '22023', '留存：时钟为空 22023');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', now(), 0)$$, '22023', '留存：批量 0 被拒');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', now(), 10001)$$, '22023', '留存：批量超过 10000 被拒');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_scheduler', now(), NULL)$$, '22023', '留存：批量为空被拒');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_learning', now(), 10)$$, '42501', '留存：学习服务调用被拒');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_report', now(), 10)$$, '42501', '留存：报表身份调用被拒');
SELECT t.throws($$SELECT t.mcl_purge('starbuddy_bff', now(), 10)$$, '42501', '留存：BFF 调用被拒');

-- ================= 五、函数属性与授权 =================
SELECT t.ok((SELECT bool_and(p.prosecdef AND pg_get_userbyid(p.proowner) = 'starbuddy_authz'
                             AND coalesce('search_path=pg_catalog, platform, pg_temp' = ANY (p.proconfig), false))
               FROM pg_proc p WHERE p.oid IN ('platform.model_call_stats(date, date)'::regprocedure,
                                              'platform.model_call_log_purge(timestamptz, integer)'::regprocedure)),
            '聚合与留存函数：SECURITY DEFINER、属主 starbuddy_authz、search_path 固定且以 pg_temp 结尾');
SELECT t.ok((SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r(role)
              WHERE has_function_privilege(r.role, 'platform.model_call_stats(date, date)', 'EXECUTE')) = '{starbuddy_report}'
            AND (SELECT array_agg(r.role ORDER BY r.role) FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r(role)
              WHERE has_function_privilege(r.role, 'platform.model_call_log_purge(timestamptz, integer)', 'EXECUTE')) = '{starbuddy_scheduler}'
            AND NOT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a
                             WHERE p.oid IN ('platform.model_call_stats(date, date)'::regprocedure,
                                             'platform.model_call_log_purge(timestamptz, integer)'::regprocedure) AND a.grantee = 0),
            '聚合只授报表身份，留存删除只授调度器；PUBLIC 都不能执行');
-- 决定记录（续）第 5 条：报表身份为调用聚合函数得到 platform 的 USAGE；锁定它在 platform 下能执行的非触发器函数集合（实际：聚合函数
-- 与两个对 PUBLIC 开放、无副作用的辅助函数 current_account_id()、evidence_payload_ok(text, jsonb)）。以后谁在 platform 新建函数忘了
-- REVOKE … FROM PUBLIC，或把别的函数授给报表身份，这条就失败
SELECT t.ok(has_schema_privilege('starbuddy_report', 'platform', 'USAGE')
            AND (SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text) FROM pg_proc p
                  WHERE p.pronamespace = 'platform'::regnamespace AND p.prokind = 'f' AND p.prorettype <> 'trigger'::regtype
                    AND has_function_privilege('starbuddy_report', p.oid, 'EXECUTE'))
                = '{platform.current_account_id(),"platform.evidence_payload_ok(text,jsonb)","platform.model_call_stats(date,date)"}',
            '报表身份在 platform 下能执行的非触发器函数恰好是 model_call_stats、current_account_id、evidence_payload_ok（实际：'
            || coalesce((SELECT string_agg(p.oid::regprocedure::text, '、' ORDER BY p.oid::regprocedure::text) FROM pg_proc p
                          WHERE p.pronamespace = 'platform'::regnamespace AND p.prokind = 'f' AND p.prorettype <> 'trigger'::regtype
                            AND has_function_privilege('starbuddy_report', p.oid, 'EXECUTE')), '无') || '）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid IN ('platform.current_account_id()'::regprocedure,
                                                                 'platform.evidence_payload_ok(text, jsonb)'::regprocedure)
                         AND (p.prosecdef OR p.provolatile = 'v')),
            '报表身份能调的两个 PUBLIC 辅助函数都不是 SECURITY DEFINER、也不是 VOLATILE（不能借它们读表或写入）');

-- ================= 六、对账草案（tools/loadtest/sql/model-call-reconcile.draft.sql，只在本事务里） =================
SELECT t.ok(t.mcl_ref(:U1) = 'a2b2bba541b791ed', '对账：数据库算的 usage_ref 与学习服务 ledger.digest（Python hashlib）同一截断规则');
\i :testdir/../../tools/loadtest/sql/model-call-reconcile.draft.sql
DELETE FROM platform.model_call_log;
SET LOCAL session_replication_role = replica;  -- 只为夹具：跳过外键（预留与自练计次挂不上真实的额度桶与学生）
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type, state, reserved_qty,
                                        settled_qty, source_method, reserved_at, expires_at, finished_at)
VALUES (:U1, 'syn-mcl-key-0000001', :FP, :EX, :SX, 'mistake_analysis', 'settled', 1, 1, 'grade', '2026-10-25 08:59+08', '2026-10-25 09:09+08', '2026-10-25 09:01+08'),
       (:U2, 'syn-mcl-key-0000002', :FP, :EX, :SX, 'mistake_analysis', 'released', 1, NULL, 'grade', '2026-10-25 10:59+08', '2026-10-25 11:09+08', '2026-10-25 11:01+08'),
       (:U3, 'syn-mcl-key-0000003', :FP, :EX, :SX, 'essay_review', 'reserved', 1, NULL, 'grade', '2026-10-25 11:59+08', '2026-10-25 12:09+08', NULL);
-- 复看（24 小时内同题，0008）：预留量与结算量都是 0，引用首次的 U1
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type, state, reserved_qty,
                                        settled_qty, dedup_key, dedup_of, source_method, reserved_at, expires_at, finished_at)
VALUES (:U5, 'syn-mcl-key-0000005', :FP, :EX, :SX, 'mistake_analysis', 'settled', 0, 0, :FP, :U1, 'grade', '2026-10-25 14:59+08', '2026-10-25 15:09+08',
        '2026-10-25 15:01+08');
INSERT INTO platform.practice_log (usage_id, idempotency_key, student_id, occurred_at) VALUES (:U4, 'syn-mcl-key-0000004', :SX, '2026-10-25 13:00+08');
SET LOCAL session_replication_role = origin;
SELECT t.mcl_ins(NULL, '2026-10-25 09:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 100, 50, 0.1, false, t.mcl_ref(:U1));
SELECT t.mcl_ins(NULL, '2026-10-25 09:00:30+08', 'T2', 'unavailable', 'ark', ARRAY['server'], 0, 0, NULL, false, t.mcl_ref(:U1));
SELECT t.mcl_ins(NULL, '2026-10-25 11:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 10, 10, NULL, false, t.mcl_ref(:U2));
SELECT t.mcl_ins(NULL, '2026-10-25 12:00+08', 'T4', 'ok', 'bailian', ARRAY['ok'], 30, 20, 0.05, false, t.mcl_ref(:U3));
SELECT t.mcl_ins(NULL, '2026-10-25 13:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 1, 1, NULL, false, t.mcl_ref(:U4));
SELECT t.mcl_ins(NULL, '2026-10-25 14:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 2, 2, NULL, false, t.mcl_ref('syn-unmetered'));
SELECT t.mcl_ins(NULL, '2026-10-25 15:00+08', 'T2', 'ok', 'bailian', ARRAY['ok'], 3, 3, NULL, false, t.mcl_ref(:U5));
SELECT t.ok((SELECT string_agg(concat_ws('|', r.day, r.task, r.calls, r.ok_calls, r.input_tokens, r.output_tokens, r.estimated_cost_cny, r.usage_refs,
                                         r.matched_reservation, r.matched_practice, r.unmatched, r.reservation_settled, r.reservation_review,
                                         r.reservation_released, r.reservation_open), E'\n' ORDER BY r.day, r.task)
               FROM platform.model_call_reconcile('2026-10-25', '2026-10-25') r)
            = E'2026-10-25|T2|6|5|116|66|0.100000|5|3|1|1|1|1|1|0\n'
              '2026-10-25|T4|1|1|30|20|0.050000|1|1|0|0|0|0|0|1',
            '对账：按日 × 任务汇总次数与 token，usage_ref 与计量预留、自练计次逐个对齐（同一 usage_ref 多次调用只算一次），对不上的单列；'
            '复看（已结算但结算量为 0）单独计数，不算进已结算');
SELECT t.ok((SELECT count(*) FROM platform.model_call_reconcile('2026-10-26', '2026-10-26')) = 0, '对账：窗口外的调用不计');
SELECT t.throws($$SELECT count(*) FROM platform.model_call_reconcile('2026-10-01', '2026-11-05')$$, '22023', '对账：跨度超过 31 天 22023');
-- 按整词匹配：计数列 usage_refs（不同摘要的个数）不是摘要本身
SELECT t.ok(pg_get_function_result('platform.model_call_reconcile(date, date)'::regprocedure) !~ '(\musage_ref\M|usage_id|student|idempotency)'
            AND pg_get_function_result('platform.model_call_reconcile(date, date)'::regprocedure) ~ '\musage_refs bigint',
            '对账：输出只有日、任务与计数（没有摘要、usage_id、学生与幂等键；usage_refs 只是个数）');

-- 清单谓词（决定记录（续）第 4 条）：按该学生计量预留与自练计次的 usage_id 重算摘要，恰好匹配到他的调用记录（U1 两次、U2、U3、
-- 复看 U5、自练 U4，共 6 条）；对不上计量的那条与别的学生都不受影响。删除执行身份按谓词删得掉（端到端执行与全库扫描在 79q）
CREATE FUNCTION t.mcl_pred(p_student uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  EXECUTE format('SELECT count(*)::text FROM platform.model_call_log WHERE %s',
                 (SELECT g.student_predicate FROM platform.personal_table_registry g WHERE g.nspname = 'platform' AND g.relname = 'model_call_log'))
    USING p_student INTO v;
  RETURN v;
END $$;
SELECT t.ok(t.mcl_pred(:SX) = '6' AND t.mcl_pred(:SY) = '0',
            '清单谓词：按该学生的计量 usage_id 摘要匹配到他的 6 条调用记录（预留、复看与自练计次都算），别的学生 0 条');
-- 删除与核对分成两条语句（同一条语句里的子查询看不到函数里刚删的行）
SELECT t.mcl_as('starbuddy_eraser', format($q$WITH x AS (DELETE FROM platform.model_call_log WHERE %s RETURNING 1) SELECT count(*)::text FROM x$q$,
                replace((SELECT g.student_predicate FROM platform.personal_table_registry g
                          WHERE g.nspname = 'platform' AND g.relname = 'model_call_log'), '$1', quote_literal(:SX) || '::uuid'))) AS mcl_erased \gset
SELECT t.ok(:'mcl_erased' = '6' AND (SELECT string_agg(usage_ref, ',') FROM platform.model_call_log) = t.mcl_ref('syn-unmetered'),
            '清单谓词：删除执行身份按谓词删掉该学生的 6 条，对不上计量的那条留下（等 180 天留存删除）');

ROLLBACK;
