-- 0061 报错主消息不带个人行标识（specs/plt-db-raise-redaction/spec.md 范围第 2 条）。
-- 背景：RDS 设了 log_error_verbosity = terse，DETAIL 不进服务端日志，主消息照写；所以 usage_id、幂等键、个人层行 ID 只能放 DETAIL。
-- 全部合成数据（规则 8）：夹具以超级用户在复制模式下直写（只为夹具，跳过外键与触发器），再切回 origin 触发目标函数。
-- 整个文件在一个事务里，结束回滚，不给 80 的可见性矩阵留任何数据。
-- 判据：
--   一、0061 改的九个函数各触发一次：SQLSTATE 不变（meter_check_entitlement 是 23000，其余 23514），主消息含原有的判定子串
--       （测试与调用方依赖），不含该行标识；DETAIL 含该行标识。
--   二、静态：platform / insight / kge / policy 下现行 plpgsql 函数体（pg_proc.prosrc）里，RAISE 主消息的格式参数（格式串之后、USING 之前）
--       不得含：任意 x.id、x.*_id、x.*_key（NEW. / OLD. 与记录变量如 w.id、r.usage_id）；名字带 id、key（按下划线分段）或含
--       entitlement、student、household、usage、account 的裸 p_* / v_* 变量；整行 NEW / OLD；row_to_json / to_json / to_jsonb。
--       另断言没有 RAISE … USING MESSAGE = / := …（那样主消息就不在格式串里，扫描看不到）。公共层标识按（函数, 引用）显式豁免。
--       局限：格式参数按“格式串之后到第一个分号”截取，参数里若有带分号的字符串字面量会被截断（漏看分号之后的部分）；
--       E'' 以外的特殊引号写法（如 $$…$$ 格式串）、经别的函数间接拼进主消息的标识、名字不含上述词的变量，都不在扫描范围。
\set SU '''f0610000-0000-4000-8000-0000000000c1'''
\set SU2 '''f0610000-0000-4000-8000-0000000000c2'''
\set HH '''f0610000-0000-4000-8000-0000000000d1'''
\set SUB '''f0610000-0000-4000-8000-0000000000e1'''
\set ENT '''f0610000-0000-4000-8000-0000000000e2'''
\set USE '''f0610000-0000-4000-8000-0000000000a1'''
\set GR '''f0610000-0000-4000-8000-0000000000b1'''
\set O1 '''f0610000-0000-4000-8000-000000000001'''
\set O2 '''f0610000-0000-4000-8000-000000000002'''
\set O3 '''f0610000-0000-4000-8000-000000000003'''
\set O4 '''f0610000-0000-4000-8000-000000000004'''
\set EN2 '''f0610000-0000-4000-8000-0000000000e3'''
\set WR '''f0610000-0000-4000-8000-0000000000b2'''
\set IKEY '''synthetic-redact-ingest-key-0061'''
\set MKEY '''synthetic-redact-meter-key-0061'''
BEGIN;
SET LOCAL TIME ZONE 'UTC';

-- 执行 sql，取回 SQLSTATE、主消息与 DETAIL（不出错时 state 为空）
CREATE FUNCTION t.raise_of(p_sql text, OUT state text, OUT msg text, OUT detail text) LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE p_sql;
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS state = RETURNED_SQLSTATE, msg = MESSAGE_TEXT, detail = PG_EXCEPTION_DETAIL;
    RETURN;
  END;
END $$;
-- 断言：以 p_state 失败，主消息含 p_keep、不含 p_ident，DETAIL 含 p_ident
CREATE FUNCTION t.redacted(p_sql text, p_state text, p_keep text, p_ident text, p_label text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  r := t.raise_of(p_sql);
  IF r.state IS DISTINCT FROM p_state OR position(p_keep IN coalesce(r.msg, '')) = 0 OR position(p_ident IN coalesce(r.msg, '')) > 0
     OR position(p_ident IN coalesce(r.detail, '')) = 0 THEN
    RAISE EXCEPTION 'FAIL: 报错脱敏：% — 期望 % 主消息含“%”且不含标识、DETAIL 含标识；实际 % 主消息“%” DETAIL“%”',
      p_label, p_state, p_keep, r.state, r.msg, r.detail;
  END IF;
  RAISE NOTICE 'ok - 报错脱敏：%（主消息“%”，标识只在 DETAIL）', p_label, r.msg;
END $$;

-- ---------- 夹具（复制模式：不触发外键与触发器） ----------
SET LOCAL session_replication_role = replica;
-- 学生档案：更新同一事务里插入的行时外键会重查，所以要有档案行（账号、家庭不需要）
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, exam_cohort) VALUES
  (:SU, 'f0610000-0000-4000-8000-0000000000a5', :HH, 9, 'synthcity', 2027),
  (:SU2, 'f0610000-0000-4000-8000-0000000000a6', :HH, 8, 'synthcity', 2028);
INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint, attempt_ids, status, summary)
VALUES ('grade', :IKEY, 'aya', :SU, repeat('a', 64), '{f0610000-0000-4000-8000-0000000000f1}', 'succeeded', '{"verdict": "correct"}');
INSERT INTO platform.subscription (id, household_id, student_id, tier, config_version, source, status, period_start, period_end)
VALUES (:SUB, :HH, :SU, 'pro', 'synthetic-plan@1', 'pilot_virtual', 'ended', '2026-10-01 00:00:00+00', '2026-10-30 00:00:00+00');
INSERT INTO platform.usage_reservation (usage_id, idempotency_key, request_fingerprint, entitlement_id, student_id, resource_type, state,
  reserved_qty, settled_qty, source_method, reserved_at, expires_at, finished_at)
VALUES (:USE, :MKEY, repeat('1', 64), :ENT, :SU, 'mistake_analysis', 'settled', 1, 1, 'grade',
        '2026-10-08 11:30:00+00', '2026-10-08 11:40:00+00', '2026-10-08 11:31:00+00');
INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code) VALUES
  ('2026-10-08 11:30:00+00', :ENT, :SU, :USE, 'reserve', 1, 'bff', 'reserve'),
  ('2026-10-08 11:31:00+00', :ENT, :SU, :USE, 'settle', 1, 'bff', 'settle');
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES (:GR, :SU, 'wenli', 'f0610000-0000-4000-8000-0000000000a8', '2026-10-09 11:30:00+00', 'partial', 'wenli-essay-5d@1.2.0', 8, 10,
        false, 0.9, 'rubric:wenli-essay-5d@1.2.0', 'model:deepseek/deepseek-chat@2026-09', false, '{}', 'formal',
        '{f0610000-0000-4000-8000-0000000000e9}', gen_random_uuid());
-- O1 已被 O2 取代；O2、O3 同一学生同一能力点（当前）；O4 是另一学生的
INSERT INTO platform.skill_observation (id, student_id, subject, skill_local_id, outcome, confidence, algorithm_version, evidence_ids,
  source_request_id, superseded_by, superseded_at)
SELECT v.id, v.student_id, 'aya', 'K-QF-0301', 'demonstrated', 0.6, 'aya-diag@0.1.1', '{f0610000-0000-4000-8000-0000000000e9}',
       gen_random_uuid(), v.sup, CASE WHEN v.sup IS NOT NULL THEN now() END
  FROM (VALUES (:O1::uuid, :SU::uuid, :O2::uuid), (:O2::uuid, :SU::uuid, NULL::uuid), (:O3::uuid, :SU::uuid, NULL::uuid),
               (:O4::uuid, :SU2::uuid, NULL::uuid)) v(id, student_id, sup);
-- 额度桶：计数写 5，流水与预留里没有对应记录，核对必然不一致
INSERT INTO platform.entitlement (id, subscription_id, student_id, resource_type, period_start, period_end, granted)
VALUES (:EN2, :SUB, :SU, 'mistake_analysis', '2026-10-01 00:00:00+00', '2026-10-30 00:00:00+00', 5);
-- 上周（上海时区）的一份已生成周报，第一句引用一个不存在的判断：抽样时取不到学科
INSERT INTO insight.weekly_report (id, student_id, week_start, week_end, audience, status, sentences, objective, algorithm_version, policy_version)
SELECT :WR, :SU, d, d + 6, 'student', 'generated',
       jsonb_build_array(jsonb_build_object('claim_id', 'f0610000-0000-4000-8000-0000000000e8', 'template_key', 'test.weekly.node',
                                            'template_version', 1, 'section', 'lit', 'text', '合成渲染句')),
       '{"source_type": "objective", "usage_minutes": 95, "plan_done": 4, "plan_skipped": 1,
         "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18}]}'::jsonb,
       'sie-weekly@0.1.0', 'syn-weekly-0.1.0'
  FROM (SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7 AS d) w;
SET LOCAL session_replication_role = origin;

-- ---------- 一、逐个触发 ----------
SELECT t.redacted(format($$UPDATE platform.ingest_request SET summary = summary WHERE idempotency_key = %L$$, :IKEY),
                  '23514', 'ingest request already succeeded', :IKEY, 'guard_ingest_request 不在主消息里写幂等键');
SELECT t.redacted(format($$UPDATE platform.subscription SET status = 'active' WHERE id = %L$$, :SUB),
                  '23514', 'has ended', :SUB, 'guard_subscription 不在主消息里写订阅 ID');
SELECT t.redacted(format($$UPDATE platform.usage_reservation SET state = 'released', settled_qty = NULL WHERE usage_id = %L$$, :USE),
                  '23514', 'already settled', :USE, 'guard_usage_reservation 不在主消息里写 usage_id（状态仍在主消息里）');
SELECT t.redacted(format($$INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code)
                           VALUES ('2026-10-08 11:30:01+00', %L, %L, %L, 'reserve', 1, 'bff', 'reserve')$$, :ENT, :SU, :USE),
                  '23514', 'already has a reserve entry', :USE, 'guard_ledger_insert（reserve）不在主消息里写 usage_id');
SELECT t.redacted(format($$INSERT INTO platform.usage_ledger (occurred_at, entitlement_id, student_id, usage_id, kind, quantity, actor, reason_code)
                           VALUES ('2026-10-08 11:31:01+00', %L, %L, %L, 'settle', 1, 'bff', 'settle')$$, :ENT, :SU, :USE),
                  '23514', 'already has a terminal entry', :USE, 'guard_ledger_insert（终态）不在主消息里写 usage_id');
SELECT t.redacted(format($$SET CONSTRAINTS platform.grade_dimension_requires_review IMMEDIATE;
                           INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, confidence, evidence_ids)
                           VALUES (%L, 'structure', 8, 10, 'rubric_model', true, 0.4, '{f0610000-0000-4000-8000-0000000000e9}')$$, :GR),
                  '23514', 'not flagged for review', :GR, 'grade_dimension_requires_review 不在主消息里写判分 ID');
SELECT t.redacted(format($$UPDATE platform.skill_observation SET superseded_by = %L WHERE id = %L$$, :O3, :O1),
                  '23514', 'skill_observation has already been superseded', :O1, 'guard_supersede 不在主消息里写行 ID（表名仍在主消息里）');
SELECT t.redacted(format($$SET CONSTRAINTS platform.check_supersede_target IMMEDIATE;
                           UPDATE platform.skill_observation SET superseded_by = %L WHERE id = %L$$, :O4, :O2),
                  '23514', 'invalid supersede target for skill_observation', :O2, 'check_supersede_target 不在主消息里写行 ID（表名仍在主消息里）');
SELECT t.redacted(format($$SELECT platform.meter_check_entitlement(%L)$$, :EN2),
                  '23000', 'entitlement counters do not match the ledger', :EN2, 'meter_check_entitlement 不在主消息里写额度桶 ID');
SELECT t.redacted(format($$SELECT platform.review_sample_weekly_reports(%L, 0.5, 10)$$,
                         date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7),
                  '23514', '取不到复核任务的学科', :WR, 'review_sample_weekly_reports 不在主消息里写周报 ID');

-- ---------- 二、静态：RAISE 主消息的参数不是个人层行标识 ----------
-- 对一段函数源码返回命中（每条一行：规则:引用）。规则：
--   dot      格式参数里的 x.id、x.*_id、x.*_key（NEW. / OLD. 与任何记录变量）
--   var      裸的 p_* / v_* 变量，名字按下划线分段含 id / ids / key / keys，或含 entitlement、student、household、usage、account
--   row      整行 NEW / OLD 当参数（后面不跟“.列”）
--   json     row_to_json / to_json / to_jsonb 当参数
--   message  RAISE … USING … MESSAGE = / := …（主消息不在格式串里）
-- 格式参数 = 格式串之后到第一个分号，再去掉 USING 及其后（DETAIL / HINT 不进 terse 日志）。
CREATE FUNCTION t.raise_hits(p_src text) RETURNS SETOF text LANGUAGE sql IMMUTABLE AS $f$
  WITH a AS (
    SELECT regexp_replace(m[1], $re$\mUSING\M.*$$re$, '', 'i') AS args
      FROM regexp_matches(p_src, $re$RAISE\s+(?:EXCEPTION|WARNING|NOTICE|INFO|LOG|DEBUG)?\s*[Ee]?'(?:[^']|'')*'([^;]*);$re$, 'gi') m)
  SELECT 'dot:' || lower(x[1])
    FROM a, regexp_matches(a.args, $re$\m([A-Za-z_][A-Za-z0-9_]*\.(?:id|[A-Za-z0-9_]*_id|[A-Za-z0-9_]*_key))\M$re$, 'gi') x
  UNION ALL
  SELECT 'var:' || lower(x[2])
    FROM a, regexp_matches(a.args, $re$(^|[^.A-Za-z0-9_])([pPvV]_[A-Za-z0-9_]+)$re$, 'g') x
   WHERE lower(x[2]) ~ '(^|_)(id|ids|key|keys)(_|$)|entitlement|student|household|usage|account'
  UNION ALL
  SELECT 'row:' || lower(x[1]) FROM a, regexp_matches(a.args, $re$\m(NEW|OLD)\M(?!\s*\.)$re$, 'gi') x
  UNION ALL
  SELECT 'json:' || lower(x[1]) FROM a, regexp_matches(a.args, $re$\m(row_to_json|to_jsonb|to_json)\s*\($re$, 'gi') x
  UNION ALL
  SELECT 'message' WHERE p_src ~* $re$\mRAISE\M[^;]*\mUSING\M[^;]*\mMESSAGE\s*:?=$re$
$f$;
-- 豁免清单（公共层标识，不指向任何学生；按（函数, 命中）登记，新增须说明理由）：
--   insight.claim_guard()        dot:new.template_key  判断模板键（公共层模板目录，与 template_version 一起定位模板，不含学生信息）
--   insight.intervention_guard() dot:new.rule_key      干预规则键（公共层规则目录；params_version 不命中规则，不需豁免）
--   kge.guard_node()             dot:new.id            知识节点 ID（公共知识层的节点编码，如 MATH.xxx）
-- 扫描结果（2026-10-27，0061 之后，四个 schema、放宽后的规则）：除这三处外没有命中。
-- 放宽前漏掉的两处（meter_check_entitlement 的 p_entitlement、review_sample_weekly_reports 的 w.id）已由 0061 改掉。
CREATE TEMP TABLE raise_exempt (fn text, hit text) ON COMMIT DROP;
INSERT INTO raise_exempt VALUES ('insight.claim_guard()', 'dot:new.template_key'),
                                ('insight.intervention_guard()', 'dot:new.rule_key'),
                                ('kge.guard_node()', 'dot:new.id');
CREATE TEMP TABLE raise_scan ON COMMIT DROP AS
  SELECT p.oid::regprocedure::text AS fn, h AS hit
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang,
         t.raise_hits(p.prosrc) h
   WHERE n.nspname IN ('platform', 'insight', 'kge', 'policy') AND l.lanname = 'plpgsql';
SELECT t.ok(v IS NULL, '报错脱敏（静态）：platform / insight / kge / policy 函数的 RAISE 主消息参数不含个人层标识、整行记录或 JSON 化的行，'
                       || '也不用 USING MESSAGE（豁免公共层标识）' || coalesce('；违规：' || v, ''))
  FROM (SELECT string_agg(DISTINCT s.fn || ' ' || s.hit, '，') AS v
          FROM raise_scan s WHERE NOT EXISTS (SELECT 1 FROM raise_exempt e WHERE e.fn = s.fn AND e.hit = s.hit)) x;
-- 豁免不过期：清单里的每一条都确实被扫到（函数改掉后要从清单里删）
SELECT t.ok(NOT EXISTS (SELECT 1 FROM raise_exempt e WHERE NOT EXISTS (SELECT 1 FROM raise_scan s WHERE s.fn = e.fn AND s.hit = e.hit)),
            '报错脱敏（静态）：豁免清单的三条都仍被扫到（没有过期豁免）');
-- 扫描本身在工作（防止正则失效后静态断言空转通过）：每条规则在合成源码上各命中一次；标识只在 DETAIL 里时不命中
SELECT t.ok(
  (SELECT array_agg(h ORDER BY h) FROM t.raise_hits($src$
     RAISE EXCEPTION 'a %', NEW.usage_id USING ERRCODE = 'check_violation';
     RAISE EXCEPTION 'b %', w.id;
     RAISE EXCEPTION 'b2 %', r.template_key;
     RAISE NOTICE 'c % %', p_entitlement, v_student;
     RAISE EXCEPTION E'd %', OLD;
     RAISE EXCEPTION 'e %', row_to_json(NEW.x);
     RAISE EXCEPTION 'f' USING MESSAGE = 'x';
     RAISE EXCEPTION 'g %', v_state USING DETAIL = format('%s %s', NEW.id, p_student);
   $src$) h) = ARRAY['dot:new.usage_id', 'dot:r.template_key', 'dot:w.id', 'json:row_to_json', 'message', 'row:old', 'var:p_entitlement', 'var:v_student']
  AND (SELECT count(*) FROM pg_proc p, t.raise_hits(p.prosrc) h
        WHERE p.oid = 'platform.guard_ledger_insert()'::regprocedure) = 0
  AND (SELECT count(*) FROM regexp_matches((SELECT prosrc FROM pg_proc WHERE oid = 'platform.guard_ledger_insert()'::regprocedure),
                                           $re$RAISE\s+(?:EXCEPTION|WARNING|NOTICE|INFO|LOG|DEBUG)?\s*[Ee]?'(?:[^']|'')*'([^;]*);$re$, 'gi')) = 7,
  '报错脱敏（静态）：扫描规则自检（五类规则各命中、DETAIL 里的标识不命中；guard_ledger_insert 的 7 条 RAISE 都被识别且无命中）');
SELECT t.ok((SELECT array_agg(h) FROM t.raise_hits($src$ RAISE EXCEPTION 'f' USING MESSAGE := 'x'; $src$) h) = ARRAY['message'],
            '报错脱敏（静态）：扫描规则自检（USING MESSAGE := 写法同样命中）');

ROLLBACK;
