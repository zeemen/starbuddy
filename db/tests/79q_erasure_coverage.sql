-- SIE-03 PR④ 删除覆盖测试（全库扫描）：specs/sie-03-dispute-reconcile/spec.md 验收第 3 条“执行后该学生在全部个人层表与档案页零行，
-- 公共层与其他学生不受影响；回执不含被删内容”、Gate 0“删除覆盖全表”。全部合成数据（CLAUDE.md 规则 8）。
-- 与 79p 的分工：79p 测发起 / 确认 / 撤回 / 权限 / 导出，以及“清单与库一致”（按 schema 自动发现：新建带 student_id 的表、经外键挂到
--   个人层表的表不登记即失败——那条断言在 79p 第一段，这里不重复）。本文件补三件事：
--   1. 一户两名学生（兄弟姐妹，同一家长、同一家庭），清单登记的每张个人层表都给两人各写入行（写不了的表逐张断言并说明）；
--      有真实入口的一律经函数写（验证码登录注册家长、create_student 建档、同意、开通试用 / 试点、选科、时长守护、邀请与兑换、学生模式会话、
--      计量预留与结算、自练计次、异议、复核领取与结案、建档事实、行为事件、考试录入、导出）；学习服务的表以 starbuddy_learning 身份写，
--      洞察层以 starbuddy_insight 身份写；只有“人工维护”或没有入口的才用超级用户（逐处注明）；
--   2. 完整走 申请 → 二次确认 → 冷静期内不执行 → 冷静期满（时钟做法沿用 79p：以超级用户把确认时间改到过去）→ 定时任务执行；
--   3. 按“值”做全库扫描（不依赖列名与外键）：
--      - ID 标记：被删学生的档案与账号 ID、其 delete 表各行里的全部 uuid 值（排除兄弟姐妹也有的、别的账号与家庭等共享值）；
--      - 内容标记：每名学生唯一的文本标记（写进每处能写文本的列与键）、delete 表里的幂等键、令牌与内容摘要的 hex、学生手机号；
--      执行前两类标记只出现在清单表里（出现在未登记的表 = 覆盖缺口）；执行后内容标记全库零处，ID 标记只剩按理由保留的豁免表；
--      全库每张表的行数变化恰好等于回执；清单外全部表（含 kge、policy）整表摘要不变；兄弟姐妹、另两户、家长与家庭的行逐行不变。
-- 档案页：KGE-04 第二步（0037）已建学情档案页、阻断记录与复测任务表并登记进清单，t.q_seed 给两名学生各写一套，第六段逐表点名确认。
-- 依赖：79m 的 t.m_as / t.m_raise，79p 的 t.p_bff / t.p_counts / t.p_public_counts / t.p_unregistered / t.app_schemas_p。
-- 整段在一个事务里，结束回滚，不给 80 的可见性矩阵留任何数据。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''

BEGIN;
-- ================= 零、辅助函数（只在本事务里，随回滚消失） =================
-- 按学生派生确定性的合成 ID（只用于没有函数入口、由学习服务写入的行）
CREATE FUNCTION t.q_id(p_student uuid, p_tag text) RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
  SELECT md5('sie03-pr4:' || p_student::text || ':' || p_tag)::uuid $$;
-- 应用 schema 的全部表（不含分区子表）
CREATE FUNCTION t.q_tables() RETURNS TABLE (rel regclass, name text) LANGUAGE sql STABLE AS $$
  SELECT c.oid::regclass, n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE c.relnamespace IN (SELECT t.app_schemas_p()) AND c.relkind IN ('r', 'p') AND NOT c.relispartition ORDER BY 2 $$;
-- 全库每张表的行数
CREATE FUNCTION t.q_all_counts() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v bigint; o jsonb := '{}';
BEGIN
  FOR r IN SELECT * FROM t.q_tables() LOOP
    EXECUTE format('SELECT count(*) FROM %s', r.rel) INTO v;
    o := o || jsonb_build_object(r.name, v);
  END LOOP;
  RETURN o;
END $$;
-- 清单外全部表（含 kge、policy）的整表摘要（行按文本排序）
CREATE FUNCTION t.q_unregistered_md5() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v text; o jsonb := '{}';
BEGIN
  FOR r IN SELECT * FROM t.q_tables() q
            WHERE NOT EXISTS (SELECT 1 FROM platform.personal_table_registry g WHERE g.nspname || '.' || g.relname = q.name) LOOP
    EXECUTE format('SELECT md5(coalesce(string_agg(to_jsonb(x)::text, E''\n'' ORDER BY to_jsonb(x)::text), '''')) FROM %s x', r.rel) INTO v;
    o := o || jsonb_build_object(r.name, v);
  END LOOP;
  RETURN o;
END $$;
-- 该学生在清单表（可按策略筛）各行里出现的全部 uuid 值
CREATE FUNCTION t.q_uuids(p_student uuid, p_strategy text DEFAULT NULL) RETURNS text[] LANGUAGE plpgsql AS $$
DECLARE r record; v text[]; o text[] := '{}';
BEGIN
  FOR r IN SELECT * FROM platform.personal_table_registry WHERE p_strategy IS NULL OR strategy = p_strategy LOOP
    EXECUTE format($f$SELECT array_agg(DISTINCT m[1]) FROM %I.%I x,
                        regexp_matches(to_jsonb(x)::text, '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', 'g') m WHERE %s$f$,
                   r.nspname, r.relname, r.student_predicate) USING p_student INTO v;
    o := o || coalesce(v, '{}');
  END LOOP;
  RETURN ARRAY(SELECT DISTINCT x FROM unnest(o) x ORDER BY 1);
END $$;
-- ID 标记：档案与账号 ID + delete 表各行的 uuid 值，去掉共享值（另一名学生的清单行里也有的、别的账号、家庭、公共层里的）
CREATE FUNCTION t.q_id_markers(p_student uuid, p_sibling uuid) RETURNS text[] LANGUAGE sql AS $$
  SELECT ARRAY(SELECT DISTINCT m FROM unnest(t.q_uuids(p_student, 'delete')
                                             || ARRAY[p_student::text, (SELECT sp.account_id::text FROM platform.student_profile sp WHERE sp.id = p_student)]) m
                WHERE m <> ALL (t.q_uuids(p_sibling))
                  AND NOT EXISTS (SELECT 1 FROM platform.account a WHERE a.id::text = m
                                   AND a.id <> (SELECT sp.account_id FROM platform.student_profile sp WHERE sp.id = p_student))
                  AND NOT EXISTS (SELECT 1 FROM platform.household h WHERE h.id::text = m)
               ORDER BY 1) $$;
-- 内容标记：学生唯一文本标记；delete 表里的 bytea（令牌、邀请码摘要）hex、名字含 idempotency_key / digest / fingerprint / phone 的文本列（幂等键、内容摘要）；学生手机号
CREATE FUNCTION t.q_content_markers(p_student uuid, p_token text) RETURNS text[] LANGUAGE plpgsql AS $$
DECLARE r record; c record; v text[]; o text[] := ARRAY[p_token];
BEGIN
  FOR r IN SELECT * FROM platform.personal_table_registry WHERE strategy = 'delete' LOOP
    FOR c IN SELECT a.attname, a.atttypid FROM pg_attribute a
              WHERE a.attrelid = format('%I.%I', r.nspname, r.relname)::regclass AND a.attnum > 0 AND NOT a.attisdropped
                AND (a.atttypid = 'bytea'::regtype OR (a.atttypid = 'text'::regtype AND a.attname ~ '(idempotency_key|digest|fingerprint|phone)')) LOOP
      EXECUTE format('SELECT array_agg(DISTINCT %s) FROM %I.%I WHERE %s',
                     CASE WHEN c.atttypid = 'bytea'::regtype THEN format('encode(%I, ''hex'')', c.attname) ELSE quote_ident(c.attname) END,
                     r.nspname, r.relname, r.student_predicate) USING p_student INTO v;
      o := o || coalesce(v, '{}');
    END LOOP;
  END LOOP;
  RETURN ARRAY(SELECT DISTINCT x FROM unnest(o) x WHERE x IS NOT NULL AND char_length(x) >= 8 ORDER BY 1);
END $$;
-- 全库扫描：哪些表（按整行文本）含有这些标记值；返回 {表名: 行数}
CREATE FUNCTION t.q_mentions(p_markers text[]) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v bigint; o jsonb := '{}';
BEGIN
  FOR r IN SELECT * FROM t.q_tables() LOOP
    EXECUTE format('SELECT count(*) FROM %s x WHERE to_jsonb(x)::text LIKE ANY ($1)', r.rel)
      USING ARRAY(SELECT '%' || m || '%' FROM unnest(p_markers) m) INTO v;
    IF v > 0 THEN o := o || jsonb_build_object(r.name, v); END IF;
  END LOOP;
  RETURN o;
END $$;
CREATE FUNCTION t.q_unlisted(p_seen jsonb) RETURNS text LANGUAGE sql AS $$
  SELECT coalesce(string_agg(k, '、' ORDER BY k), '') FROM jsonb_object_keys(p_seen) k
   WHERE NOT EXISTS (SELECT 1 FROM platform.personal_table_registry r WHERE r.nspname || '.' || r.relname = k) $$;
-- 该学生在清单表里的整行内容摘要（逐表 md5，行按文本排序）；p_skip 里的“表.列”不计入（删除执行时按设计会改的列）
CREATE FUNCTION t.q_digest(p_student uuid, p_strategy text DEFAULT NULL, p_skip text[] DEFAULT '{}') RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v text; o jsonb := '{}';
BEGIN
  FOR r IN SELECT * FROM platform.personal_table_registry WHERE p_strategy IS NULL OR strategy = p_strategy ORDER BY 1, 2 LOOP
    EXECUTE format('SELECT md5(coalesce(string_agg(j, E''\n'' ORDER BY j), '''')) FROM (SELECT (to_jsonb(x) - $2)::text AS j FROM %I.%I x WHERE %s) s',
                   r.nspname, r.relname, r.student_predicate)
      USING p_student, ARRAY(SELECT split_part(k, '.', 3) FROM unnest(p_skip) k WHERE k LIKE r.nspname || '.' || r.relname || '.%') INTO v;
    o := o || jsonb_build_object(r.nspname || '.' || r.relname, v);
  END LOOP;
  RETURN o;
END $$;
-- 短信验证码登录（没有账号就注册家长账号）：返回 {"outcome", "session_token", "account_id", "account_kind"}；MAC 用 sha256 代替 BFF 的 HMAC（同 79k）
CREATE FUNCTION t.q_login(p_phone text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE v_mac bytea := sha256(convert_to('otp:' || p_phone || ':246810', 'UTF8'));
BEGIN
  PERFORM t.m_as('starbuddy_bff', NULL, format('SELECT platform.auth_otp_issue(%L, %L::bytea, 300, 5)::text', p_phone, v_mac));
  RETURN t.m_as('starbuddy_bff', NULL, format('SELECT row_to_json(r)::text FROM platform.auth_otp_login(%L, %L::bytea, %s, %s, NULL) r',
                                              p_phone, v_mac, 7 * 86400, 86400))::jsonb;
END $$;

-- ================= 一、一户两名合成学生，清单里的每张表都写入两人的行 =================
-- 家长 g9（家庭创建者）与 g9b（经邀请绑定）：验证码登录首次注册
SELECT quote_literal(t.q_login('+99900000980') ->> 'account_id') AS g9, quote_literal(t.q_login('+99900000981') ->> 'account_id') AS g9b \gset
-- 家长建档：第一个孩子新建家庭，第二个孩子建在同一家庭
SELECT quote_literal(t.p_bff(:g9, 'SELECT platform.create_student(NULL, 8::smallint, ''nanjing'', ''bubian-2024'', DATE ''2013-04-01'')::text')) AS "X1" \gset
SELECT quote_literal(household_id) AS "H9", quote_literal(account_id) AS "aX1" FROM platform.student_profile WHERE id = :X1 \gset
SELECT quote_literal(t.p_bff(:g9, format('SELECT platform.create_student(%L, 9::smallint, ''nanjing'', ''bubian-2024'', DATE ''2012-02-01'')::text', :H9))) AS "X2" \gset
SELECT quote_literal(account_id) AS "aX2" FROM platform.student_profile WHERE id = :X2 \gset
SELECT t.ok((SELECT count(DISTINCT household_id) FROM platform.student_profile WHERE id IN (:X1, :X2)) = 1, '两名学生经 create_student 建在同一家庭');
-- 判断模板与试点名单由人工登记（超级用户，同 79p、70）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.coverage.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：删除覆盖', 'approved', 'test-reviewer', now()),
       ('test.coverage.exam', 1, 'exam', '{agent,student,guardian}', '60 days', '合成模板：删除覆盖考试', 'approved', 'test-reviewer', now());
INSERT INTO platform.pilot_household (household_id, cohort_code) VALUES (:H9, 'synthetic_cohort');

-- 每名学生一套：p_n 区分两人，p_token 是该学生唯一的内容标记（写进每处能写文本的列与键）
CREATE FUNCTION t.q_seed(s uuid, acct uuid, g uuid, g2 uuid, p_n int, p_token text, p_source text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_today date := (now() AT TIME ZONE 'Asia/Shanghai')::date;
  v_plan uuid; v_pred uuid; v_scored uuid; v_task uuid; v_usage uuid; v_out jsonb; v_tok text; v_inv text;
  v_ev uuid := t.q_id(s, 'evidence'); v_at uuid := t.q_id(s, 'attempt'); v_gr uuid := t.q_id(s, 'grade');
  v_node uuid := t.q_id(s, 'claim.node'); v_old uuid := t.q_id(s, 'claim.old'); v_exam uuid := t.q_id(s, 'claim.exam');
  v_key text := 'synthetic-coverage-' || p_token;
BEGIN
  -- ---- 家长侧入口（BFF 函数）：同意、开通、选科、时长守护、自由文本、邀请与另一位家长兑换
  PERFORM t.p_bff(g, format($f$INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
      VALUES (%L, %L, 'privacy_policy', 'consent-doc@1.0.0', 'granted'), (%L, %L, 'guardian_consent_under14', 'consent-doc@1.0.0', 'granted')
      RETURNING 'ok'$f$, s, g, s, g));
  v_out := t.p_bff(g, format('SELECT platform.meter_open_subscription(%L, %L)::text', s, p_source))::jsonb;
  PERFORM t.ok(v_out ->> 'outcome' = 'opened', format('学生 %s：经 meter_open_subscription 开通 %s（%s）', p_n, p_source, v_out));
  v_out := t.p_bff(g, format($f$SELECT platform.enroll_subject(%L, 'aya')::text$f$, s))::jsonb;
  -- 学科都是 beta、只对试点开放：试用开通的学生选不了科（not_launched），这一行由超级用户写（与 10 号夹具同一写法）
  IF p_source = 'trial' AND v_out ->> 'outcome' = 'not_launched' THEN
    INSERT INTO platform.subject_enrollment (student_id, subject) VALUES (s, 'aya');
  END IF;
  PERFORM t.ok(EXISTS (SELECT 1 FROM platform.subject_enrollment WHERE student_id = s), format('学生 %s：有选科行（enroll_subject：%s；not_launched 时由超级用户补写）', p_n, v_out));
  PERFORM t.p_bff(g, format($f$SELECT platform.set_time_guard(%L, 60, '21:30', '06:30')::text$f$, s));
  PERFORM t.p_bff(g, format($f$INSERT INTO platform.guardian_report (student_id, guardian_account_id, topic, content)
      VALUES (%L, %L, 'study_routine', %L) RETURNING 'ok'$f$, s, g, '合成家长备注-' || p_token));
  v_inv := t.p_bff(g, format('SELECT platform.create_guardian_invite(%L)', s));
  PERFORM t.p_bff(g2, format('SELECT platform.redeem_guardian_invite(%L)::text', v_inv));
  -- ---- 学生模式会话：家长重新验证码登录后切换（auth_issue_student_session）；学生登录手机号没有入口，按 10 号夹具的写法由超级用户写
  v_tok := t.q_login((SELECT phone_e164 FROM platform.account_contact WHERE account_id = g)) ->> 'session_token';
  PERFORM t.p_bff(g, format('SELECT platform.auth_issue_student_session(%L, %L, %s, %s)', s, v_tok, 7 * 86400, 86400));
  INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (acct, '+9990000099' || p_n, now());
  -- ---- 学习服务写入（starbuddy_learning）：证据事件与登记、作答、判分与维度、观测、错因、掌握、诊断会话与步骤、写入幂等与重放封存
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind,
      item_id, skill_local_id, admission, payload, source_method, request_id, request_idempotency_key)
      VALUES (%L, '2026-10-08 11:30:00+00', %L, 'aya', 'response_text', 'synthetic-0301-07', 'K-QF-0301', 'admitted', %L, 'grade', %L, %L)
      RETURNING 'ok'$f$, v_ev, s, jsonb_build_object('text', '合成作答-' || p_token), t.q_id(s, 'request'), v_key || '-grade'));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.evidence_registry (evidence_id, occurred_at, student_id, content_digest,
      request_idempotency_key) VALUES (%L, '2026-10-08 11:30:00+00', %L, %L, %L) RETURNING 'ok'$f$,
      v_ev, s, encode(sha256(convert_to(p_token, 'UTF8')), 'hex'), v_key || '-grade'));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id,
      response_evidence_id, request_idempotency_key) VALUES (%L, '2026-10-08 11:30:00+00', %L, 'aya', 'synthetic-0301-07', %L, %L)
      RETURNING 'ok'$f$, v_at, s, v_ev, v_key || '-grade'));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at,
      verdict, rubric_version, score_total, max_total, calibrated, confidence, source_version, model_version, verifier_id, verifier_version,
      human_review_required, semantics, evidence_ids, source_request_id)
      VALUES (%L, %L, 'aya', %L, '2026-10-08 11:30:00+00', 'correct', 'aya-symbolic-ts@0.1.0', 1, 1, true, 1, 'rubric:aya-symbolic-ts@0.1.0',
              'algo:aya-symbolic-ts@0.1.0', 'aya-symbolic-ts', '0.1.0', false, 'formal', %L, %L) RETURNING 'ok'$f$,
      v_gr, s, v_at, ARRAY[v_ev], t.q_id(s, 'grade.request')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score,
      judged_by, subjective, rule_ref, confidence, evidence_ids) VALUES (%L, 'answer', 1, 1, 'verifier', false, 'aya-symbolic-ts@0.1.0', 1, %L)
      RETURNING 'ok'$f$, v_gr, ARRAY[v_ev]));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.skill_observation (id, student_id, subject, skill_local_id, outcome,
      confidence, algorithm_version, evidence_ids, source_request_id) VALUES (%L, %L, 'aya', 'K-QF-0301', 'partial', 0.5, 'aya-diag@0.1.0', %L, %L)
      RETURNING 'ok'$f$, t.q_id(s, 'observation'), s, ARRAY[v_ev], t.q_id(s, 'observation.request')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.error_attribution (id, student_id, subject, skill_local_id,
      subject_category, cross_subject_category, confidence, algorithm_version, evidence_ids, source_request_id)
      VALUES (%L, %L, 'aya', 'K-QF-0301', 'computation', 'computation_or_grammar', 0.5, 'aya-attr@0.1.0', %L, %L) RETURNING 'ok'$f$,
      t.q_id(s, 'attribution'), s, ARRAY[v_ev], t.q_id(s, 'attribution.request')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state,
      p_mastery, confidence, algorithm_version, evidence_ids) VALUES (%L, %L, 'aya', 'K-QF-0301', 'execute', 0.55, 0.6, 'bkt-v0@0.1.0', %L)
      RETURNING 'ok'$f$, t.q_id(s, 'mastery'), s, ARRAY[v_ev]));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id,
      request_fingerprint, attempt_ids, lease_token, lease_until) VALUES ('grade', %L, 'aya', %L, %L, %L, %L, now() + interval '60 seconds')
      RETURNING 'ok'$f$, v_key || '-grade', s, encode(sha256(convert_to('fp-' || p_token, 'UTF8')), 'hex'), ARRAY[v_at], t.q_id(s, 'lease')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.replay_seal (method, idempotency_key, student_id, usage_id)
      VALUES ('grade', %L, %L, %L) RETURNING 'ok'$f$, t.q_id(s, 'seal.key'), s, t.q_id(s, 'seal.usage')));
  -- 诊断会话与步骤：诊断函数的入口在学习服务里（0027 表由 starbuddy_learning 写）
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.diagnosis_session (id, student_id, subject, algorithm_version, map_version,
      usage_id, idempotency_key) VALUES (%L, %L, 'aya', 'aya-diag-trace@0.1.0', '0.1.0', %L, %L) RETURNING 'ok'$f$,
      t.q_id(s, 'diagnosis'), s, t.q_id(s, 'diagnosis.usage'), t.q_id(s, 'diagnosis.key')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.diagnosis_step (id, session_id, seq, kind, node_id, item_id, reason_code,
      status, idempotency_key) VALUES (%L, %L, 1, 'adaptive', 'MATH.FUNC.QUAD.VERTEX_FORM', 'synthetic-0301-07', 'target', 'skipped', %L)
      RETURNING 'ok'$f$, t.q_id(s, 'diagnosis.step'), t.q_id(s, 'diagnosis'), t.q_id(s, 'diagnosis.step.key')));
  -- ---- 学生本人入口（BFF 函数）：教材、自述、建档进度、行为事件、信心探针、计量预留 → 结算、自练计次
  PERFORM t.p_bff(acct, format($f$SELECT platform.set_subject_textbook(%L, 'wenli', 'suke-2024')::text$f$, s));
  PERFORM t.p_bff(acct, format($f$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', %L)::text$f$, s,
                               jsonb_build_object('text', '合成目标-' || p_token)));
  PERFORM t.p_bff(acct, format($f$SELECT insight.record_onboarding_step(%L, 'onboarding', 'started')::text$f$, s));
  PERFORM t.p_bff(acct, format($f$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), %L)).id::text$f$,
                               s, t.q_id(s, 'behavior.key')));
  PERFORM t.p_bff(acct, format($f$SELECT (insight.record_confidence_probe(%L, 'aya', 'synthetic-0301-07', 'yes', now(), %L)).id::text$f$,
                               s, t.q_id(s, 'probe.key')));
  v_out := t.p_bff(acct, format($f$SELECT platform.meter_reserve(%L, 'mistake_analysis', %L, %L, 1, %L, 'grade')::text$f$,
                                s, gen_random_uuid(), repeat('1', 64), encode(sha256(convert_to('content-' || p_token, 'UTF8')), 'hex')))::jsonb;
  v_usage := (v_out ->> 'usage_id')::uuid;
  PERFORM t.ok(v_usage IS NOT NULL, format('学生 %s：经 meter_reserve 预留（%s）', p_n, v_out));
  PERFORM t.p_bff(acct, format('SELECT platform.meter_settle(%L, 1)::text', v_usage));
  v_out := t.p_bff(acct, format('SELECT platform.meter_practice(%L, %L)::text', s, gen_random_uuid()))::jsonb;
  PERFORM t.ok(v_out ->> 'outcome' = 'allowed', format('学生 %s：经 meter_practice 记一次自练（%s）', p_n, v_out));
  -- ---- 洞察层（starbuddy_insight）：判断（含撤回，产生判断历史）、动作记录
  PERFORM t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key,
      template_version, value, confidence, evidence_ids, source_type, status, change_reason, visibility, expires_at) VALUES
      (%L, %L, 'aya', 'node', 'K-QF-0301', 'test.coverage.node', 1, %L, 0.6, %L, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
      (%L, %L, 'aya', 'node', 'K-QF-0302', 'test.coverage.node', 1, '{}', 0.6, '{}', 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
      (%L, %L, 'aya', 'exam', 'midterm', 'test.coverage.exam', 1, '{}', 0.6, '{}', 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days')
      RETURNING id::text$f$, v_node, s, jsonb_build_object('note', '合成判断-' || p_token), ARRAY[v_ev], v_old, s, v_exam, s));
  PERFORM t.m_as('starbuddy_insight', NULL, format($f$UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected'
      WHERE id = %L RETURNING id::text$f$, v_old));
  PERFORM t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version)
      VALUES (%L, 'reminder.review_due', 'plan_review_node', %L, 'rules@1.0.0') RETURNING id::text$f$, s, ARRAY[v_node]));
  -- ---- 异议两路（洞察服务 raise_dispute）：学生“那次只是失误”、家长“数据有误”→ 统一复核队列 → staff 领取并以“转洞察服务”结案；家长观察
  PERFORM t.m_raise(v_node, 'one_off_slip', acct, v_at);
  PERFORM t.m_raise(v_node, 'data_error', g, NULL, 'attempt', v_at);
  SELECT id INTO v_task FROM platform.review_task WHERE student_id = s AND source_kind = 'claim';
  PERFORM t.m_as('starbuddy_review', '5f000000-0000-4000-8000-00000000005f'::uuid, format('SELECT platform.take_review_task(%L)::text', v_task));
  PERFORM t.m_as('starbuddy_review', '5f000000-0000-4000-8000-00000000005f'::uuid, format('SELECT platform.resolve_review_task(%L, ''dispute_forwarded'')::text', v_task));
  PERFORM t.p_bff(g, format($f$SELECT insight.submit_guardian_report(%L, 'outer.g8.cooperation_capacity', '{"level": "regular"}')::text$f$, s));
  -- ---- 考试安排 → 预测 → 冻结 → 成绩 → 对账卡
  v_plan := t.p_bff(acct, format($f$SELECT insight.submit_exam_plan(%L, 'aya', 'midterm', %L, 100, ARRAY['MATH.FUNC.QUAD.GRAPH'])::text$f$,
                                 s, v_today + 5))::uuid;
  v_pred := t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low,
      interval_high, basis, algorithm_version) VALUES (%L, %L, %L, 70, 85, '{}', 'sie-predict@0.1.0') RETURNING id::text$f$, s, v_plan, v_exam))::uuid;
  -- 模拟考试日已到（与 79p 同一做法：超级用户临时停用只追加守卫，把考试日改到昨天），再由定时任务冻结
  ALTER TABLE insight.exam_record DISABLE TRIGGER guard_fact_update;
  UPDATE insight.exam_record SET exam_date = v_today - 1 WHERE id = v_plan;
  ALTER TABLE insight.exam_record ENABLE TRIGGER guard_fact_update;
  PERFORM t.m_as('starbuddy_scheduler', NULL, 'SELECT insight.freeze_due_predictions(now())::text');
  v_scored := t.p_bff(acct, format($f$SELECT insight.submit_exam_score(%L, %L, 90)::text$f$, s, v_plan))::uuid;
  PERFORM t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.exam_reconciliation (student_id, prediction_id, exam_record_id,
      in_interval, deviation_directions, adjustments, audience, sentences, algorithm_version)
    VALUES (%L, %L, %L, false, '{item_type_transfer}', '{none}', '{student,guardian}', %L, 'sie-reconcile@0.1.0') RETURNING id::text$f$,
    s, v_pred, v_scored, jsonb_build_array(jsonb_build_object('claim_id', v_node, 'template_key', 'test.coverage.node'))));
  -- ---- 周报等后续新增的个人层表：在这里给该学生补写入（79q 要求清单每张表两名学生都有行）
  -- 周报（0036）：经 starbuddy_insight 写入，句子带该学生的内容标记
  PERFORM t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status,
      sentences, objective, algorithm_version, policy_version) VALUES (%L, %L, %L, 'student', 'generated', %L,
      '{"source_type": "objective", "usage_minutes": 30, "plan_done": 1, "plan_skipped": 0, "subjects": []}', 'sie-weekly@0.1.0', 'syn-coverage')
      RETURNING id::text$f$, s, date_trunc('week', v_today)::date - 7, date_trunc('week', v_today)::date - 1,
      jsonb_build_array(jsonb_build_object('claim_id', v_node, 'template_key', 'test.coverage.node', 'template_version', 1,
                                           'section', 'working_on', 'text', '合成周报句-' || p_token))));
  -- 掌握节点键、复测任务、学情档案页与阻断记录（0037，KGE-04 第二步）：经 starbuddy_learning 写；档案页引用本学生的节点判断，
  -- 档案页与阻断记录都不存自由文本（衔接语与片段不许含数字，内容标记带数字，放不进去）；它们的覆盖靠 ID 标记与逐表计数
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level,
      map_version_id, state, p_mastery, confidence, algorithm_version, evidence_ids, next_retest_at)
      VALUES (%L, %L, 'aya', 'MATH.FUNC.QUAD.GRAPH', 'apply', '7a000000-0000-4000-8000-000000000037', 'execute', 0.6, 0.6, 'kge-mastery@0.1.0', %L,
              now() + interval '1 day') RETURNING 'ok'$f$, t.q_id(s, 'mastery.node'), s, ARRAY[v_ev]));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.retest_task (id, student_id, subject, node_id, cognitive_level, rung,
      reason, due_at, scheduled_by_estimate_id) VALUES (%L, %L, 'aya', 'MATH.FUNC.QUAD.GRAPH', 'apply', 'rung0', 'scheduled', now() + interval '1 day', %L)
      RETURNING 'ok'$f$, t.q_id(s, 'retest'), s, t.q_id(s, 'mastery.node')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.profile_page (id, student_id, subject, map_version_id, sections,
      claims_digest, compiler_version, check_status) VALUES (%L, %L, 'aya', '7a000000-0000-4000-8000-000000000037', %L, %L, 'kge-profile@0.1.0', 'blocked_fallback')
      RETURNING 'ok'$f$, t.q_id(s, 'profile.page'), s,
      jsonb_build_array(jsonb_build_object('key', 'working_on', 'connective', '', 'todo', '[]'::jsonb,
                                           'statements', jsonb_build_array(jsonb_build_object('claim_id', v_node, 'template_key', 'test.coverage.node',
                                                                                              'template_version', 1)))),
      encode(sha256(convert_to('digest-' || p_token, 'UTF8')), 'hex')));
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.profile_page_check (id, student_id, subject, profile_page_id,
      compiler_version, violations) VALUES (%L, %L, 'aya', %L, 'kge-profile@0.1.0', %L) RETURNING 'ok'$f$,
      t.q_id(s, 'profile.check'), s, t.q_id(s, 'profile.page'),
      jsonb_build_array(jsonb_build_object('code', 'connective_unapproved', 'section', 'working_on', 'claim_ids', jsonb_build_array(v_node),
                                           'fragment', '合成衔接'))));
  -- 届别更正记录（0045）：家长经 BFF 更正一次（每次 ±1；方向取更正后当前年级仍在 7–9 的一边）
  PERFORM t.p_bff(g, format($f$SELECT platform.correct_exam_cohort(%L, (CASE WHEN platform.current_grade((p.exam_cohort + 1)::smallint, now()) IS NOT NULL
      THEN p.exam_cohort + 1 ELSE p.exam_cohort - 1 END)::smallint)::text FROM platform.student_profile p WHERE p.id = %L$f$, s, s));
  -- 家长诉求（0053，PLT-14）：家长经 BFF 提交一版（补充说明带该学生的内容标记；X1 初二、X2 初三都不设学校）
  PERFORM t.p_bff(g, format($f$SELECT insight.submit_guardian_goal(%L, '{aya}', '[{"subject": "aya", "full_score": 120, "target_score": 100}]',
                                                                    NULL, %L)::text$f$, s, '合成诉求-' || p_token));
  -- 月度计划快照（0054，PLT-14 第 2 步）：写入函数 insight.save_monthly_plan 要求本月有 Pro / Max 权益，这两名学生走试用（基础档）与
  -- 试点开通、周报档位不一定含 monthly，所以以超级用户直接写一份本月快照（表守卫照常核对形状、月份与输入引用；不引用判断）。
  -- 快照只存编码与模板键、不存自由文本，内容标记放不进去；覆盖靠 ID 标记与逐表计数
  INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, algorithm_version, config_version,
                                    template_version, policy_weights, inputs_digest, input_refs)
  VALUES (s, date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai')::date,
          jsonb_build_object('priority_nodes', '[]'::jsonb,
                             'weekly_rhythm', '{"sessions_per_week": 5, "minutes_per_session": 30, "copy_key": "monthly_plan.student.weekly_rhythm"}'::jsonb,
                             'retests', '{"count": 0, "dates": [], "by_subject": [], "copy_key": "monthly_plan.student.retests"}'::jsonb,
                             'mini_reviews', '[]'::jsonb),
          '[]', '{"last_month": null, "score_gaps": []}', '{"items": []}', 'sie-monthly@0.1.0', 'syn-coverage', 'syn-coverage', 'default',
          encode(sha256(convert_to('monthly-' || p_token, 'UTF8')), 'hex'),
          '{"as_of": "2026-01-01T00:00:00Z", "claims": [], "mastery": [], "exam_records": [], "exam_plans": [], "goals": [],
            "retest_tasks": [], "prev_plan_id": null,
            "fingerprints": {"claims_policy": "0000000000000000", "config": "0000000000000000",
                             "templates": "0000000000000000", "today": "0000000000000000"}}');
  -- 今日计划快照（0055，PLT-15）：经 starbuddy_learning 写当天的两条（计划任务一条、再练条目一条）；快照只存编码与引用、不存自由文本，
  -- 内容标记放不进去；覆盖靠 ID 标记（任务 ID、条目引用）与逐表计数
  PERFORM t.m_as('starbuddy_learning', NULL, format($f$INSERT INTO platform.today_plan_item (id, student_id, seq, subject, kind, source, node_id,
      estimated_minutes, entry_ref, rationale_code) VALUES
      (%L, %L, 1, 'aya', 'practice', 'practice', 'MATH.FUNC.QUAD.GRAPH', 8, %L, NULL),
      (%L, %L, 2, 'aya', 'lesson', 'plan', NULL, 5, NULL, 'prerequisite_of_many') RETURNING 'ok'$f$,
      t.q_id(s, 'today.1'), s, t.q_id(s, 'today.entry'), t.q_id(s, 'today.2'), s));
  -- 内部测试反馈与退出测试（0059，LT-06）：学生本人一条（学生自述）、家长一条（家长观察），文本都带该学生的内容标记，经 BFF 身份写；
  -- 随后家长发起退出测试（撤回同意、作废学生会话、写退出记录；退出记录是豁免表，已有数据保留到删除执行）
  PERFORM t.m_as('starbuddy_bff', acct, format($f$WITH i AS (INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body,
      route_template) VALUES (%L, %L, 'student_self_report', 'malfunction', %L, '/s/[studentId]/today')) SELECT 'ok'$f$, acct, s, '合成反馈-' || p_token));
  PERFORM t.p_bff(g, format($f$WITH i AS (INSERT INTO platform.feedback (submitter_account_id, student_id, source, category, body)
      VALUES (%L, %L, 'guardian_observation', 'slow', %L)) SELECT 'ok'$f$, g, s, '合成家长反馈-' || p_token));
  PERFORM t.p_bff(g, format('SELECT newly::text FROM platform.exit_internal_test(%L)', s));
  -- ---- 导出（家长，0035）：导出任务是豁免表
  PERFORM t.p_bff(g, format('SELECT platform.export_student_data(%L)::text', s));
END $$;
SELECT t.q_seed(:X1, :aX1, :g9, :g9b, 1, 'q4mark1zz', 'trial');
SELECT t.q_seed(:X2, :aX2, :g9, :g9b, 2, 'q4mark2zz', 'pilot_virtual');

-- 写不了的表（逐张说明）：staff 九表只收教研账号——凭据表与 staff 权限表（0048）有库层守卫，下面直接证明学生账号写不进去；
-- 会话、五张签字表（含 0039 的内容版本与准入审核记录 release_record、0040 的考情签字 policy_signoff）与来源清单操作记录（0048）
-- 只由属主 authz 的函数写入（函数只认 staff 账号），应用角色都没有 INSERT，学生账号在这几张表里只能是零行
-- （这七张没有库层守卫，只靠函数路径，见 lessons PR④ 缺口 1）
SELECT t.throws_like(format($$INSERT INTO platform.staff_credential (account_id, login, password_hash, totp_secret)
                             VALUES (%L, 'synthetic-student-login', 'x', '\x00')$$, :aX1), '23514', 'staff', 'staff_credential：学生账号写不进去（库层守卫）');
SELECT t.throws_like(format($$INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES (%L, 'policy_source_admin', 'synthetic-dba')$$, :aX1),
                     '23514', 'staff', 'staff_permission：学生账号写不进去（库层守卫，0048）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY['platform.staff_session', 'platform.content_signoff', 'platform.map_change_signoff',
                                                   'platform.tag_candidate_signoff', 'platform.release_record', 'platform.policy_signoff',
                                                   'platform.staff_permission', 'platform.policy_source_domain_change']) tb,
                              unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight',
                                           'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report', 'starbuddy_eraser']) r(role)
                         WHERE has_table_privilege(r.role, tb, 'INSERT')),
            'staff 会话、五张签字表、staff 权限表与来源清单操作记录：只有属主 authz 能写（staff 权限只由迁移或 DBA 授予），学生账号写不进去');
SELECT t.p_counts(:X1::uuid) AS x1_all \gset
SELECT t.ok((SELECT string_agg(e.key, '、' ORDER BY e.key) FROM jsonb_each(:'x1_all'::jsonb) e WHERE e.value::int = 0)
            = 'platform.content_signoff、platform.erasure_request、platform.map_change_signoff、platform.policy_signoff、'
              'platform.policy_source_domain_change、platform.release_record、'
              'platform.staff_credential、platform.staff_permission、platform.staff_session、platform.tag_candidate_signoff',
            '清单里除 staff 九表与删除请求（第三段由申请流程写入）外的每张表（delete 与 exempt）都有学生 X1 的行（零行的：'
            || coalesce((SELECT string_agg(e.key, '、' ORDER BY e.key) FROM jsonb_each(:'x1_all'::jsonb) e WHERE e.value::int = 0), '无') || '）');
SELECT t.ok((SELECT string_agg(e.key, '、' ORDER BY e.key) FROM jsonb_each(t.p_counts(:X2::uuid)) e WHERE e.value::int = 0)
            = 'platform.content_signoff、platform.erasure_request、platform.map_change_signoff、platform.policy_signoff、'
              'platform.policy_source_domain_change、platform.release_record、'
              'platform.staff_credential、platform.staff_permission、platform.staff_session、platform.tag_candidate_signoff、platform.trial_claim',
            '兄弟姐妹 X2 同样写满（删除请求只给 X1 发起；试用按家庭只能领一次，X2 走试点开通，试用领取行的学生键是 X1；零行的：'
            || coalesce((SELECT string_agg(e.key, '、' ORDER BY e.key) FROM jsonb_each(t.p_counts(:X2::uuid)) e WHERE e.value::int = 0), '无') || '）');

-- ================= 二、执行前：按值全库扫描，学生 X1 的标记只出现在清单表里 =================
SELECT t.q_id_markers(:X1::uuid, :X2::uuid) AS x1_ids, t.q_content_markers(:X1::uuid, 'q4mark1zz') AS x1_content \gset
SELECT t.ok(cardinality(:'x1_ids'::text[]) >= 40 AND NOT (:'x1_ids'::text[] && t.q_uuids(:X2::uuid)),
            format('ID 标记：学生 X1 自有的 uuid 至少 40 个（%s 个），且都不出现在兄弟姐妹的清单行里', cardinality(:'x1_ids'::text[])));
SELECT t.ok(cardinality(:'x1_content'::text[]) >= 7, format('内容标记：唯一文本标记、幂等键、令牌与摘要 hex、学生手机号至少 7 个（%s 个）',
                                                             cardinality(:'x1_content'::text[])));
SELECT t.q_mentions(:'x1_ids'::text[]) AS x1_ids_before, t.q_mentions(:'x1_content'::text[]) AS x1_content_before \gset
SELECT t.ok(t.q_unlisted(:'x1_ids_before'::jsonb) = '' AND t.q_unlisted(:'x1_content_before'::jsonb) = '',
            '全库扫描：含学生 X1 标记（ID 或内容）的表都在个人层表清单里（未登记：'
            || t.q_unlisted(:'x1_ids_before'::jsonb) || ' / ' || t.q_unlisted(:'x1_content_before'::jsonb) || '）');
SELECT t.ok((SELECT count(*) FROM jsonb_object_keys(:'x1_content_before'::jsonb)) >= 8,
            format('内容标记确实写进了多张表（%s 张）', (SELECT count(*) FROM jsonb_object_keys(:'x1_content_before'::jsonb))));
SELECT t.ok(NOT EXISTS (SELECT 1 FROM jsonb_object_keys(:'x1_ids_before'::jsonb || :'x1_content_before'::jsonb) k WHERE k LIKE 'kge.%' OR k LIKE 'policy.%'),
            '全库扫描：公共层（kge、policy）里没有任何学生 X1 的标记（规则 13）');
-- 按值扫描能发现 79p 按列名发现不了的表：列名不叫 student_id、也没有外键，只是存了学生的 ID 或内容
SAVEPOINT shadow;
CREATE TABLE platform.t_shadow_copy (id uuid PRIMARY KEY, note jsonb);
INSERT INTO platform.t_shadow_copy VALUES (gen_random_uuid(), jsonb_build_object('ref', :X1::text)), (gen_random_uuid(), '{"text": "q4mark1zz"}');
SELECT t.ok(t.q_mentions(:'x1_ids'::text[]) ? 'platform.t_shadow_copy' AND t.q_mentions(:'x1_content'::text[]) ? 'platform.t_shadow_copy'
            AND position('platform.t_shadow_copy' IN t.p_unregistered()) = 0,
            '反例：没有 student_id 列、没有外键、把学生 ID 或内容存在 jsonb 里的未登记表，按列名扫描漏掉，按值扫描能发现');
ROLLBACK TO SAVEPOINT shadow;

-- ================= 三、申请 → 二次确认 → 冷静期内不执行 → 冷静期满后执行 =================
SELECT t.p_bff(:g9, format('SELECT platform.request_erasure(%L)::text', :X1))::uuid AS req \gset
SELECT t.p_bff(:g9, format('SELECT platform.confirm_erasure(%L)::text', :'req'));
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0
            AND t.p_counts(:X1::uuid) = :'x1_all'::jsonb - 'platform.erasure_request' || jsonb_build_object('platform.erasure_request', 1),
            '冷静期内：定时任务不执行，学生 X1 的数据原样');
-- 冷静期满：沿用 79p 的时钟做法（超级用户把时间改到过去；守卫不许改确认时间，所以临时停用守卫，只动这三列）
ALTER TABLE platform.erasure_request DISABLE TRIGGER erasure_request_guard;
UPDATE platform.erasure_request SET requested_at = now() - interval '8 days', confirmed_at = now() - interval '8 days',
                                    cooling_off_until = now() - interval '1 day' WHERE id = :'req';
ALTER TABLE platform.erasure_request ENABLE TRIGGER erasure_request_guard;
-- 快照：清单逐表计数、全库逐表计数、清单外全部表整表摘要、兄弟姐妹与另两户的整行摘要、X1 豁免表的整行摘要、家长与家庭的行
SELECT t.p_counts(:X1::uuid, 'delete') AS x1_before, t.p_counts(:X1::uuid, 'exempt') AS x1_exempt_before,
       t.q_digest(:X1::uuid, 'exempt', ARRAY['platform.student_profile.erased_at', 'platform.account.status', 'platform.erasure_request.status',
                  'platform.erasure_request.executed_at', 'platform.erasure_request.receipt', 'platform.erasure_request.scopes']) AS x1_exempt_digest,
       t.p_counts(:X2::uuid) AS x2_counts, t.q_digest(:X2::uuid) AS x2_digest, t.q_digest(:S1::uuid) AS s1_digest, t.q_digest(:S2::uuid) AS s2_digest,
       t.p_public_counts() AS public_before, t.q_all_counts() AS all_before, t.q_unregistered_md5() AS unreg_before,
       (SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY x.id)) FROM platform.account x WHERE x.id IN (:g9, :g9b)) AS guardians_before,
       (SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY x.account_id)) FROM platform.account_contact x WHERE x.account_id IN (:g9, :g9b)) AS contacts_before,
       (SELECT md5(to_jsonb(x)::text) FROM platform.household x WHERE x.id = :H9) AS household_before \gset
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 1, '冷静期满：定时任务执行 1 条删除');
SELECT receipt::text AS receipt, scopes::text AS scopes FROM platform.erasure_request WHERE id = :'req' \gset

-- ================= 四、执行后：学生 X1 在全部 delete 表零行；按值全库扫描 =================
SELECT t.ok((SELECT count(*) FROM jsonb_each(t.p_counts(:X1::uuid, 'delete')) e WHERE e.value::int <> 0) = 0,
            '学生 X1 在清单全部 delete 表零行（非零：' || coalesce((SELECT string_agg(e.key, '、') FROM jsonb_each(t.p_counts(:X1::uuid, 'delete')) e
                                                                  WHERE e.value::int <> 0), '无') || '）');
SELECT t.q_mentions(:'x1_ids'::text[]) AS x1_ids_after, t.q_mentions(:'x1_content'::text[]) AS x1_content_after \gset
SELECT t.ok(:'x1_content_after'::jsonb = '{}', '全库扫描：执行后学生 X1 的内容标记（文本、幂等键、令牌与摘要、手机号）全库零处（残留：'
            || coalesce((SELECT string_agg(k, '、') FROM jsonb_object_keys(:'x1_content_after'::jsonb) k), '无') || '）');
-- ID 标记只剩理由已确认的豁免表（档案骨架、账号、监护绑定、同意、计量、回执、导出审计）；试用领取另列（见下一条）
SELECT t.ok((SELECT string_agg(k, '、' ORDER BY k) FROM jsonb_object_keys(:'x1_ids_after'::jsonb - 'platform.trial_claim') k)
            = 'platform.account、platform.consent_record、platform.entitlement、platform.erasure_request、platform.export_job、'
              'platform.guardian_student_link、platform.internal_test_exit、platform.practice_log、platform.student_profile、platform.subscription、'
              'platform.usage_ledger、platform.usage_reservation',
            '全库扫描：执行后学生 X1 的 ID 只出现在理由已确认的豁免表里（实际：'
            || coalesce((SELECT string_agg(k, '、' ORDER BY k) FROM jsonb_object_keys(:'x1_ids_after'::jsonb) k), '无') || '）');
-- 现状，待人确认（lessons PR④ 缺口 2）：试用领取的豁免理由写“档案删除时学生键置空”，但删除执行不删档案，学生键仍在
SELECT t.ok((:'x1_ids_after'::jsonb ->> 'platform.trial_claim')::int = 1
            AND EXISTS (SELECT 1 FROM platform.trial_claim WHERE student_id = :X1::uuid),
            '现状（待人确认）：试用领取行在删除执行后仍带被删学生的 ID（理由所说的置空只在档案行被删时发生）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM jsonb_object_keys(:'x1_ids_after'::jsonb) k
                         JOIN platform.personal_table_registry r ON r.nspname || '.' || r.relname = k WHERE r.strategy <> 'exempt')
            AND t.q_unlisted(:'x1_ids_after'::jsonb) = '',
            '执行后仍含学生 X1 ID 的表全部是清单里的 exempt 表');
-- 豁免表按理由保留：行数不变；整行内容不变，只有设计上会改的列（档案 erased_at、账号停用、本条删除请求的状态与回执）
SELECT t.ok(t.p_counts(:X1::uuid, 'exempt') = :'x1_exempt_before'::jsonb, '豁免表行数不变（没有新增，也没有误删）');
SELECT t.ok(t.q_digest(:X1::uuid, 'exempt', ARRAY['platform.student_profile.erased_at', 'platform.account.status', 'platform.erasure_request.status',
                       'platform.erasure_request.executed_at', 'platform.erasure_request.receipt', 'platform.erasure_request.scopes'])
            = :'x1_exempt_digest'::jsonb,
            '豁免表整行内容不变：除档案删除标记、账号停用与回执外，没有写入任何新数据');
SELECT t.ok((SELECT s.erased_at IS NOT NULL AND a.status = 'disabled' FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
              WHERE s.id = :X1::uuid)
            AND (SELECT s.erased_at IS NULL AND a.status = 'active' FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
                  WHERE s.id = :X2::uuid),
            '只有被删的学生 X1 档案标记已删除、账号停用；同一家庭的 X2 不受影响');
-- 全库逐表行数：变化的表恰好是回执里行数非零的 delete 表，减少的行数恰好等于回执（别的学生一行没删、任何表都没新增行）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM jsonb_each(:'all_before'::jsonb) b JOIN jsonb_each(t.q_all_counts()) a ON a.key = b.key
   WHERE b.value::bigint - a.value::bigint IS DISTINCT FROM coalesce((:'receipt'::jsonb ->> b.key)::bigint, 0)),
  '全库逐表行数：减少量恰好等于回执，其余表行数不变（不一致：'
  || coalesce((SELECT string_agg(b.key || ' ' || b.value || '→' || a.value, '、') FROM jsonb_each(:'all_before'::jsonb) b
                 JOIN jsonb_each(t.q_all_counts()) a ON a.key = b.key
                WHERE b.value::bigint - a.value::bigint IS DISTINCT FROM coalesce((:'receipt'::jsonb ->> b.key)::bigint, 0)), '无') || '）');
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(t.q_all_counts()) k) = (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'all_before'::jsonb) k),
            '执行前后全库的表集合相同');
SELECT t.ok(t.q_unregistered_md5() = :'unreg_before'::jsonb,
            '清单外全部表（含 kge.*、policy.*、平台配置、题目、复核码表、验证码与限流）整表内容不变（变了的：'
            || coalesce((SELECT string_agg(b.key, '、') FROM jsonb_each_text(:'unreg_before'::jsonb) b
                          JOIN jsonb_each_text(t.q_unregistered_md5()) a ON a.key = b.key WHERE a.value <> b.value), '无') || '）');
-- 另一学生（同一家庭）与另两户：逐表行数与整行内容都不变；家长账号、联系方式与家庭整行不变
SELECT t.ok(t.p_counts(:X2::uuid) = :'x2_counts'::jsonb, '同一家庭的兄弟姐妹 X2：每张清单表行数不变');
SELECT t.ok(t.q_digest(:X2::uuid) = :'x2_digest'::jsonb, '同一家庭的兄弟姐妹 X2：每张清单表整行内容不变（家长、家庭、计量共用行也不变）');
SELECT t.ok(t.q_digest(:S1::uuid) = :'s1_digest'::jsonb AND t.q_digest(:S2::uuid) = :'s2_digest'::jsonb, '另两户的学生：整行内容不变');
SELECT t.ok(t.p_public_counts() = :'public_before'::jsonb, '公共层（kge.*、policy.*）逐表行数不变');
SELECT t.ok((SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY x.id)) FROM platform.account x WHERE x.id IN (:g9, :g9b)) = :'guardians_before'
            AND (SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY x.account_id)) FROM platform.account_contact x WHERE x.account_id IN (:g9, :g9b))
                = :'contacts_before'
            AND (SELECT md5(to_jsonb(x)::text) FROM platform.household x WHERE x.id = :H9) = :'household_before',
            '两位家长的账号与联系方式、家庭行整行不变');
SELECT t.ok(EXISTS (SELECT 1 FROM platform.pilot_household WHERE household_id = :H9::uuid)
            AND EXISTS (SELECT 1 FROM platform.trial_claim_phone WHERE household_id = :H9::uuid)
            AND (SELECT count(*) FROM platform.guardian_student_link WHERE student_id = :X2::uuid AND status = 'active') = 2,
            '家庭级记录保留；两位家长与另一个孩子的绑定都保留');

-- ================= 五、回执只含表名、行数与范围，不含被删内容 =================
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'receipt'::jsonb) k)
            = (SELECT array_agg(nspname || '.' || relname ORDER BY nspname || '.' || relname) FROM platform.personal_table_registry WHERE strategy = 'delete')
            AND :'receipt'::jsonb = :'x1_before'::jsonb,
            '回执：每张 delete 表一项（表名 → 删除前行数），与执行前计数一致');
SELECT t.ok(NOT jsonb_path_exists(:'receipt'::jsonb, '$.* ? (@.type() != "number")'), '回执的值只有数字');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(:'x1_ids'::text[] || :'x1_content'::text[]) m WHERE position(m IN :'receipt' || :'scopes') > 0)
            AND position(:g9 IN :'receipt') = 0
            AND :'receipt' !~ '合成|q4mark|K-QF|MATH\.|synthetic|midterm|[0-9a-f]{8}-[0-9a-f]{4}-',
            '回执不含任何被删内容：没有学生或家长 ID、被删行的 uuid、内容标记、文本、节点、题目或考试');
SELECT t.ok(:'scopes'::text[] <@ ARRAY['claim_ledger', 'facts', 'evidence', 'inference', 'reports', 'uploads']
            AND :'scopes'::text[] = (SELECT array_agg(DISTINCT erase_scope ORDER BY erase_scope) FROM platform.personal_table_registry WHERE strategy = 'delete'),
            '回执范围只用契约的范围编码');
SELECT t.ok((SELECT jsonb_object_keys(to_jsonb(r) - ARRAY['id', 'student_id', 'requested_by', 'requested_at', 'confirmed_at', 'cooling_off_until',
                                                          'status', 'cancelled_at', 'cancelled_by', 'cancel_reason', 'executed_at', 'receipt', 'scopes'])
               FROM platform.erasure_request r WHERE r.id = :'req') IS NULL,
            '删除请求行没有回执、范围之外的内容列');

-- ================= 六、档案页（KGE-04 第二步，0037） =================
-- 学情档案页与阻断记录、复测任务已建表并登记（delete）；t.q_seed 给两名学生各写了一页、一条阻断记录、一条复测任务，
-- 上面第四段的“清单 delete 表零行”“内容标记全库零处”“行数变化恰好等于回执”已覆盖它们；这里再逐表点名确认
SELECT t.ok((:'receipt'::jsonb ->> 'platform.profile_page')::int = 1 AND (:'receipt'::jsonb ->> 'platform.profile_page_check')::int = 1
            AND (:'receipt'::jsonb ->> 'platform.retest_task')::int = 1
            AND NOT EXISTS (SELECT 1 FROM platform.profile_page WHERE student_id = :X1::uuid)
            AND NOT EXISTS (SELECT 1 FROM platform.profile_page_check WHERE student_id = :X1::uuid)
            AND NOT EXISTS (SELECT 1 FROM platform.retest_task WHERE student_id = :X1::uuid)
            AND (SELECT count(*) FROM platform.profile_page WHERE student_id = :X2::uuid) = 1
            AND (SELECT count(*) FROM platform.retest_task WHERE student_id = :X2::uuid) = 1,
            '档案页、阻断记录与复测任务：删除执行后学生 X1 零行（回执各 1 行），兄弟姐妹 X2 的不受影响');
ROLLBACK;
