-- 0035 个人层表清单、删除请求与导出（specs/sie-03-dispute-reconcile/spec.md 验收第 3、4 条的库层部分）。全部合成数据。
-- 用 79m 的 t.m_as(角色, 账号, SQL)。各段在事务里，结束回滚；最后一段提交两户各一条已撤回的删除请求，留给 80 的可见性矩阵。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set gA2 '''a2000000-0000-4000-8000-0000000000a2'''

CREATE FUNCTION t.app_schemas_p() RETURNS SETOF oid LANGUAGE sql STABLE AS $$
  SELECT oid FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast')
     AND nspname NOT LIKE 'pg_temp%' AND nspname NOT LIKE 'pg_toast_temp%' $$;
CREATE FUNCTION t.p_bff(p_acct uuid, p_sql text) RETURNS text LANGUAGE sql AS $$ SELECT t.m_as('starbuddy_bff', p_acct, p_sql) $$;
-- 自动发现：所有应用 schema 里带 student_id 列的表、经外键（可多层）指向它们的表、指向账号表的表（不含分区子表）
CREATE FUNCTION t.p_discovered() RETURNS SETOF text LANGUAGE sql STABLE AS $$
  WITH RECURSIVE app AS (
    SELECT c.oid FROM pg_class c
     WHERE c.relnamespace IN (SELECT t.app_schemas_p()) AND c.relkind IN ('r', 'p')),
  seed AS (
    SELECT a.oid FROM app a WHERE EXISTS (SELECT 1 FROM pg_attribute x WHERE x.attrelid = a.oid AND x.attname = 'student_id' AND NOT x.attisdropped)
    UNION SELECT k.conrelid FROM pg_constraint k WHERE k.contype = 'f' AND k.confrelid = 'platform.account'::regclass),
  cl(oid) AS (SELECT oid FROM seed UNION SELECT k.conrelid FROM pg_constraint k JOIN cl ON k.confrelid = cl.oid WHERE k.contype = 'f')
  SELECT n.nspname || '.' || c.relname FROM cl JOIN pg_class c ON c.oid = cl.oid JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE NOT c.relispartition AND c.relnamespace IN (SELECT t.app_schemas_p()) $$;
CREATE FUNCTION t.p_unregistered() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT coalesce(string_agg(d, '、' ORDER BY d), '') FROM t.p_discovered() d
   WHERE NOT EXISTS (SELECT 1 FROM platform.personal_table_registry r WHERE r.nspname || '.' || r.relname = d) $$;
-- 按清单的学生键谓词逐表计数（超级用户身份）
CREATE FUNCTION t.p_counts(p_student uuid, p_strategy text DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v bigint; o jsonb := '{}';
BEGIN
  FOR r IN SELECT * FROM platform.personal_table_registry WHERE p_strategy IS NULL OR strategy = p_strategy ORDER BY 1, 2 LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE %s', r.nspname, r.relname, r.student_predicate) USING p_student INTO v;
    o := o || jsonb_build_object(r.nspname || '.' || r.relname, v);
  END LOOP;
  RETURN o;
END $$;
-- 公共层（kge、policy）全部表的行数
CREATE FUNCTION t.p_public_counts() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r record; v bigint; o jsonb := '{}';
BEGIN
  FOR r IN SELECT c.oid::regclass AS rel FROM pg_class c WHERE c.relnamespace IN ('kge'::regnamespace, 'policy'::regnamespace)
             AND c.relkind IN ('r', 'p') AND NOT c.relispartition ORDER BY 1 LOOP
    EXECUTE format('SELECT count(*) FROM %s', r.rel) INTO v;
    o := o || jsonb_build_object(r.rel::text, v);
  END LOOP;
  RETURN o;
END $$;

-- ================= 一、清单与库一致 =================
SELECT t.ok(t.p_unregistered() = '', '个人层表清单与库一致：每张带学生键的表都已登记（未登记：' || t.p_unregistered() || '）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.personal_table_registry r WHERE to_regclass(quote_ident(r.nspname) || '.' || quote_ident(r.relname)) IS NULL),
            '清单里的每张表都真实存在');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE nspname IN ('kge', 'policy'))
            AND NOT EXISTS (SELECT 1 FROM t.p_discovered() d WHERE d LIKE 'kge.%' OR d LIKE 'policy.%'),
            '公共层（kge、policy）没有学生键，不在清单里（规则 13）');
SELECT t.ok(jsonb_typeof(t.p_counts(:S1::uuid)) = 'object', '清单里每张表的学生键谓词都能执行');
SELECT t.ok((SELECT count(*) FROM platform.personal_table_registry WHERE strategy = 'delete') >= 30
            AND NOT EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE strategy = 'exempt' AND exempt_reason IS NULL),
            '清单：至少 30 张表按删除处理；每张豁免表都写了理由');
SELECT t.ok((SELECT bool_and(strategy = 'delete') FROM platform.personal_table_registry
              WHERE nspname || '.' || relname IN ('insight.claim', 'insight.claim_history', 'insight.dispute',
                    'insight.intervention_log', 'insight.exam_prediction', 'insight.exam_reconciliation', 'insight.exam_record',
                    'insight.behavior_event', 'insight.self_report', 'insight.guardian_report', 'platform.diagnosis_session',
                    'platform.evidence_event', 'platform.attempt', 'platform.grade_result', 'platform.auth_session')),
            '判断账本（含历史）、异议、动作记录、预测与对账、事实、行为、诊断、作答、会话都按删除处理');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.personal_table_registry WHERE nspname || '.' || relname IN ('insight.exam_prediction', 'insight.exam_reconciliation')
                          AND export_topic IS NOT NULL),
            '预测与对账卡不进导出包（对外可见范围待人确认，契约审查 H3）');
SELECT t.ok((SELECT array_agg(DISTINCT erase_scope ORDER BY erase_scope) FROM platform.personal_table_registry WHERE strategy = 'delete')
            = '{claim_ledger,evidence,facts,inference,reports}', '删除表都映射到契约回执 scopes（uploads 待上传管道建表）');
-- 导出列必须是字段字典里对家长可见的字段（安全审查 N1）；另有通用元数据列与两张家长本人记录的表单列理由
\set dict_guardian_cols `python3 -c "import yaml,json,sys; d=yaml.safe_load(open(sys.argv[1])); o=set(); [o.update([v for v in ((f['storage'].values() if isinstance(f['storage'],dict) else [f['storage']]) ) ] + ([f['source_column']] if f.get('source_column') else [])) for f in d['fields'] if 'guardian' in f['visibility']]; print(json.dumps(sorted(o)))" :testdir/../../docs/design/sie/field-dictionary.v0.9.yaml`
SELECT t.ok(jsonb_array_length(:'dict_guardian_cols'::jsonb) >= 10, '读到字段字典里对家长可见的落库列');
CREATE TABLE t.export_col_ok (col text PRIMARY KEY, reason text NOT NULL);
INSERT INTO t.export_col_ok VALUES
  ('created_at', '元数据：时间'), ('updated_at', '元数据：时间'), ('superseded_at', '元数据：时间'), ('enrolled_at', '元数据：时间'),
  ('computed_at', '元数据：时间'), ('resolved_at', '元数据：时间'), ('expires_at', '元数据：时间'), ('decided_at', '元数据：时间'),
  ('subject', '元数据：学科'), ('exam_type', '元数据：考试类型'), ('exam_date', '元数据：考试日期'), ('full_score', '元数据：满分'),
  ('field_id', '元数据：字段编码'), ('topic', '元数据：主题编码'), ('source_type', '元数据：来源'), ('skill_local_id', '元数据：对象键'),
  ('status', '元数据：处理状态'), ('result_note_code', '元数据：结论码（枚举）'),
  -- 0037（KGE-04 第二步）：掌握记录的节点键与复测任务、档案页的状态与时间（只导 ID、状态与时间，不导引用、文字与推断参数）
  ('node_id', '元数据：对象键（知识节点）'), ('cognitive_level', '元数据：对象键（认知层次）'), ('due_at', '元数据：时间'),
  ('closed_at', '元数据：时间'), ('compiled_at', '元数据：时间'), ('check_status', '元数据：处理状态');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM platform.personal_table_registry g, unnest(g.export_columns) c
   WHERE g.export_columns IS NOT NULL
     AND g.nspname || '.' || g.relname NOT IN ('insight.claim', 'platform.consent_record', 'platform.subscription')
     AND NOT EXISTS (SELECT 1 FROM t.export_col_ok o WHERE o.col = c)
     AND NOT (:'dict_guardian_cols'::jsonb ? (g.nspname || '.' || g.relname || '.' || c))),
  '导出列都是字典里对家长可见的字段或通用元数据（判断账本按行可见范围过滤；同意与订阅是家长本人的记录）');
BEGIN;
CREATE TABLE platform.t_new_personal (id uuid PRIMARY KEY, student_id uuid);
CREATE TABLE insight.t_new_child (id uuid PRIMARY KEY, claim_ref uuid REFERENCES insight.claim (id));
SELECT t.ok(position('platform.t_new_personal' IN t.p_unregistered()) > 0 AND position('insight.t_new_child' IN t.p_unregistered()) > 0,
            '新增带 student_id 的表、经外键挂到判断上的表，不登记就被发现（新增表未登记即失败）');
ROLLBACK;
SELECT t.ok(NOT has_table_privilege(r, 'platform.personal_table_registry', 'SELECT'), format('%s 读不到个人层表清单', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_report']) r;
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM platform.personal_table_registry g,
         unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_insight', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_kge',
                      'starbuddy_authz']) r
   WHERE has_table_privilege(r, format('%I.%I', g.nspname, g.relname), 'DELETE')
     -- 既有例外（0006）：家长经 BFF 删除自己写的自由文本自述（RLS 限本人）
     AND NOT (g.nspname = 'platform' AND g.relname = 'guardian_report' AND r = 'starbuddy_bff')),
  '清单里的表，除 0006 家长删自己的自述外，任何应用角色（含函数属主 authz）都没有 DELETE（删除只经删除执行身份 eraser）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.personal_table_registry g
                         WHERE (g.strategy = 'delete') <> has_table_privilege('starbuddy_eraser', format('%I.%I', g.nspname, g.relname), 'DELETE')),
            '删除执行身份 eraser 恰好能删清单里的 delete 表，豁免表（含计量）一张都删不了');
SELECT t.ok(NOT has_column_privilege('starbuddy_eraser', 'platform.evidence_event', 'payload', 'SELECT')
            AND NOT has_column_privilege('starbuddy_eraser', 'insight.self_report', 'value', 'SELECT')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.evidence_event', 'payload', 'SELECT'),
            '删除执行身份只读学生键列，读不到证据原文与自述内容');
SELECT t.ok((SELECT array_agg(p.oid::regprocedure::text) FROM pg_proc p WHERE p.proowner = 'starbuddy_eraser'::regrole)
            = ARRAY['platform.execute_due_erasures(timestamp with time zone)'], 'eraser 只拥有删除执行函数一个对象');

-- ================= 二、发起、确认、撤回 =================
BEGIN;
INSERT INTO platform.account (id, kind) VALUES (:gA2, 'guardian');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES (:gA2, :S1, 'invite');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :aS1, format('SELECT platform.request_erasure(%L)::text', :S1)), '42501', 'not allowed',
                     '学生本人不能发起删除（家长发起）');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gB, format('SELECT platform.request_erasure(%L)::text', :S1)), '42501', 'not allowed',
                     '另一户的家长不能发起删除');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gC, format('SELECT platform.request_erasure(%L)::text', :S1)), '42501', 'not allowed',
                     '没有绑定的家长不能发起删除');
SELECT t.p_bff(:gA, format('SELECT platform.request_erasure(%L)::text', :S1))::uuid AS e1 \gset
SELECT t.ok((SELECT status = 'pending' AND confirmed_at IS NULL AND requested_by = :gA::uuid FROM platform.erasure_request WHERE id = :'e1'),
            '已绑定家长发起删除：待确认');
SELECT t.ok(t.p_bff(:gA2, format('SELECT platform.request_erasure(%L)::text', :S1))::uuid = :'e1'::uuid,
            '已有未结束的请求时返回它（另一位家长也拿到同一条）');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA2, format('SELECT platform.confirm_erasure(%L)::text', :'e1')), '42501', 'not allowed',
                     '只有发起人本人能二次确认');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gB, format('SELECT platform.confirm_erasure(%L)::text', :'e1')), '42501', 'not allowed',
                     '另一户的家长不能确认');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.confirm_erasure(%L)::text', gen_random_uuid())), '42501', 'not allowed',
                     '请求不存在与无权同一个错');
SELECT t.p_bff(:gA, format('SELECT platform.confirm_erasure(%L)::text', :'e1'))::timestamptz AS e1_until \gset
SELECT t.ok((SELECT cooling_off_until = confirmed_at + interval '7 days' AND cooling_off_until = :'e1_until'::timestamptz
               FROM platform.erasure_request WHERE id = :'e1'), '二次确认后进入 7 天冷静期');
SELECT t.ok(t.p_bff(:gA, format('SELECT platform.confirm_erasure(%L)::text', :'e1'))::timestamptz = :'e1_until'::timestamptz,
            '重复确认返回原冷静期（幂等，不延长也不缩短）');
-- 冷静期内定时任务不执行
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0
            AND (SELECT status = 'pending' FROM platform.erasure_request WHERE id = :'e1'), '冷静期内不执行删除');
SELECT t.throws('SELECT t.m_as(''starbuddy_scheduler'', NULL, ''SELECT platform.execute_due_erasures(now() + interval ''''8 days'''')::text'')',
                '22023', '执行时间不能是将来（不能靠传参提前结束冷静期）');
SELECT t.throws('SELECT t.m_as(''starbuddy_scheduler'', NULL, ''SELECT platform.execute_due_erasures(now() - interval ''''2 days'''')::text'')',
                '22023', '执行时间最多往前 1 天');
SELECT t.throws(format('SELECT t.m_as(%L, NULL, ''SELECT platform.execute_due_erasures(now())::text'')', r), '42501', format('%s 不能执行删除', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_insight', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_report']) r;
-- 冷静期内可撤回：该学生任一有效绑定家长
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gB, format('SELECT platform.cancel_erasure(%L)::text', :'e1')), '42501', 'not allowed',
                     '另一户的家长不能撤回');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :aS1, format('SELECT platform.cancel_erasure(%L)::text', :'e1')), '42501', 'not allowed',
                     '学生本人不能撤回（家长发起、家长撤回）');
SELECT t.p_bff(:gA2, format('SELECT platform.cancel_erasure(%L)::text', :'e1'));
SELECT t.ok((SELECT status = 'cancelled' AND cancelled_by = :gA2::uuid AND cancel_reason = 'guardian' AND cancelled_at IS NOT NULL
               FROM platform.erasure_request WHERE id = :'e1'), '冷静期内另一位已绑定家长也能撤回');
SELECT t.p_bff(:gA, format('SELECT platform.cancel_erasure(%L)::text', :'e1'));
SELECT t.ok((SELECT cancelled_by = :gA2::uuid FROM platform.erasure_request WHERE id = :'e1'), '重复撤回是幂等的（不报错，不改撤回人）');
SELECT t.throws(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.confirm_erasure(%L)::text', :'e1')), '23514', '已撤回的请求不能再确认');
SELECT t.p_bff(:gA, format('SELECT platform.request_erasure(%L)::text', :S1))::uuid AS e2 \gset
SELECT t.ok(:'e2' <> :'e1', '撤回后可以重新发起');
-- 确认超时：发起后 24 小时内没确认，不能再确认，定时任务把它作废
INSERT INTO platform.erasure_request (id, student_id, requested_by, requested_at)
VALUES ('e3000000-0000-4000-8000-0000000000e3', :S2, :gB, now() - interval '25 hours');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gB, 'SELECT platform.confirm_erasure(''e3000000-0000-4000-8000-0000000000e3'')::text'),
                     '23514', '超时', '发起 24 小时后不能再确认');
SELECT t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text');
SELECT t.ok((SELECT status = 'cancelled' AND cancel_reason = 'confirm_expired' FROM platform.erasure_request WHERE id = 'e3000000-0000-4000-8000-0000000000e3'),
            '超时未确认的请求由定时任务作废');
SELECT t.ok((SELECT status = 'pending' FROM platform.erasure_request WHERE id = :'e2'), '刚发起的请求不受影响');
-- 读：家长经 BFF 读删除请求与回执；学生本人、别户读不到
SELECT t.ok(t.p_bff(:gA, 'SELECT string_agg(status, '','' ORDER BY requested_at) FROM platform.erasure_request') = 'cancelled,pending',
            '已绑定家长读得到这个孩子的删除请求');
SELECT t.ok(t.p_bff(:aS1, 'SELECT count(*)::text FROM platform.erasure_request')::int = 0, '学生本人读不到删除请求');
SELECT t.ok(t.p_bff(:gB, 'SELECT string_agg(student_id::text, '','') FROM platform.erasure_request') = :S2, '另一户的家长只读得到自己孩子的');
SELECT t.throws(format('SELECT t.p_bff(%L, ''SELECT count(requested_by)::text FROM platform.erasure_request'')', :gA), '42501', 'BFF 读不到发起人与撤回人账号列');
SELECT t.throws(format('SELECT t.p_bff(%L, %L)', :gA, format('INSERT INTO platform.erasure_request (student_id, requested_by) VALUES (%L, %L) RETURNING id::text', :S1, :gA)),
                '42501', 'BFF 不能直接写删除请求');
SELECT t.throws_like(format('DELETE FROM platform.erasure_request WHERE id = %L', :'e1'), '42501', '不能删除', '删除请求与回执不能删除（守卫）');
SELECT t.throws_like(format('UPDATE platform.erasure_request SET status = ''pending'', cancelled_at = NULL, cancelled_by = NULL, cancel_reason = NULL WHERE id = %L', :'e1'),
                     '23514', '已结束', '已结束的删除请求不能改回');
-- 写入量上限
SELECT t.p_bff(:gA, format('SELECT platform.cancel_erasure(%L)::text', :'e2'));
SELECT t.p_bff(:gA, format('SELECT platform.cancel_erasure(platform.request_erasure(%L))::text', :S1)) FROM generate_series(1, 3);
SELECT t.throws(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.request_erasure(%L)::text', :S1)), '54000', '同一学生 24 小时内最多 5 次删除请求');
ROLLBACK;

-- ================= 三、执行：冷静期满后按清单删除该学生全部个人层行 =================
BEGIN;
-- 给 S1 补齐各层数据（判断含推翻与撤回、异议、自述、动作记录、冻结的预测与对账卡、建档事实、行为事件、会话、联系方式）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.erasure.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：删除测试', 'approved', 'test-reviewer', now()),
       ('test.erasure.exam', 1, 'exam', '{agent,student,guardian}', '60 days', '合成模板：删除测试考试', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  ('e1c00000-0000-4000-8000-000000000001', :S1, 'aya', 'node', 'K-QF-0301', 'test.erasure.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('e1c00000-0000-4000-8000-000000000002', :S1, 'aya', 'node', 'K-QF-0302', 'test.erasure.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('e1c00000-0000-4000-8000-000000000003', :S1, 'aya', 'node', 'K-QF-0303', 'test.erasure.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('e1c00000-0000-4000-8000-000000000004', :S1, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.erasure.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('e2c00000-0000-4000-8000-000000000001', :S2, 'aya', 'node', 'K-QF-0301', 'test.erasure.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  ('e1c00000-0000-4000-8000-000000000005', :S1, 'aya', 'exam', 'midterm', 'test.erasure.exam', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days');
UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = 'e1c00000-0000-4000-8000-000000000002';
UPDATE insight.claim SET status = 'to_verify', change_reason = 'dispute' WHERE id = 'e1c00000-0000-4000-8000-000000000003';
UPDATE insight.claim SET status = 'refuted', change_reason = 'verification_refuted' WHERE id = 'e1c00000-0000-4000-8000-000000000003';
INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version)
VALUES (:S1, 'reminder.review_due', 'plan_review_node', '{e1c00000-0000-4000-8000-000000000001}', 'rules@1.0.0'),
       (:S2, 'reminder.review_due', 'plan_review_node', '{e2c00000-0000-4000-8000-000000000001}', 'rules@1.0.0');
RESET ROLE;
SELECT t.m_raise('e1c00000-0000-4000-8000-000000000001', 'one_off_slip', :aS1, 'a7000000-0000-4000-8000-0000000000a7');
SELECT t.m_raise('e1c00000-0000-4000-8000-000000000001', 'data_error', :gA, NULL, 'attempt', 'a7000000-0000-4000-8000-0000000000a7');
SELECT t.m_raise('e2c00000-0000-4000-8000-000000000001', 'one_off_slip', :aS2);
SELECT t.p_bff(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成目标"}')::text$$, :S1));
SELECT t.p_bff(:gA, format($$SELECT insight.submit_guardian_report(%L, 'outer.g8.cooperation_capacity', '{"level": "regular"}')::text$$, :S1));
SELECT t.p_bff(:aS1, format($$SELECT insight.record_onboarding_step(%L, 'onboarding', 'started')::text$$, :S1));
SELECT t.p_bff(:aS1, format($$SELECT (insight.record_behavior_event(%L, 'session_start', '{"entry": "today"}', now(), gen_random_uuid())).id::text$$, :S1));
SELECT t.p_bff(:aS1, format($$SELECT platform.set_subject_textbook(%L, 'wenli', 'suke-2024')::text$$, :S1));
SELECT t.p_bff(:gB, format($$SELECT insight.submit_guardian_report(%L, 'outer.g8.cooperation_capacity', '{"level": "occasional"}')::text$$, :S2));
SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS today \gset
SELECT t.p_bff(:aS1, format($$SELECT insight.submit_exam_plan(%L, 'aya', 'midterm', %L, 100, ARRAY['MATH.FUNC.QUAD.GRAPH'])::text$$, :S1, :'today'::date + 5))::uuid AS plan \gset
SELECT t.m_as('starbuddy_insight', NULL, format($$INSERT INTO insight.exam_prediction (student_id, exam_record_id, claim_id, interval_low, interval_high, basis, algorithm_version)
                                                 VALUES (%L, %L, 'e1c00000-0000-4000-8000-000000000005', 70, 85, '{}', 'sie-predict@0.1.0') RETURNING id::text$$, :S1, :'plan'))::uuid AS pred \gset
ALTER TABLE insight.exam_record DISABLE TRIGGER guard_fact_update;
UPDATE insight.exam_record SET exam_date = :'today'::date - 1 WHERE id = :'plan';
ALTER TABLE insight.exam_record ENABLE TRIGGER guard_fact_update;
SELECT t.m_as('starbuddy_scheduler', NULL, 'SELECT insight.freeze_due_predictions(now())::text');
SELECT t.p_bff(:aS1, format($$SELECT insight.submit_exam_score(%L, %L, 90)::text$$, :S1, :'plan'))::uuid AS scored \gset
SELECT t.m_as('starbuddy_insight', NULL, format($$INSERT INTO insight.exam_reconciliation (student_id, prediction_id, exam_record_id, in_interval,
    deviation_directions, adjustments, audience, sentences, algorithm_version)
  VALUES (%L, %L, %L, false, '{item_type_transfer}', '{none}', '{student,guardian}',
          '[{"claim_id": "e1c00000-0000-4000-8000-000000000004", "template_key": "test.erasure.node"}]', 'sie-reconcile@0.1.0') RETURNING id::text$$,
  :S1, :'pred', :'scored'));
INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES (:aS1, '+99900000091', now());
INSERT INTO platform.auth_session (token_hash, account_id, origin, issued_by_account, created_at, last_seen_at, expires_at, idle_timeout)
VALUES (sha256('synthetic-erasure-s1'), :aS1, 'guardian_handoff', :gA, now(), now(), now() + interval '1 day', interval '1 hour'),
       (sha256('synthetic-erasure-ga'), :gA, 'otp', NULL, now(), now(), now() + interval '1 day', interval '1 hour'),
       (sha256('synthetic-erasure-s2'), :aS2, 'guardian_handoff', :gB, now(), now(), now() + interval '1 day', interval '1 hour');
INSERT INTO platform.guardian_invite (student_id, token_sha256, created_by_account, expires_at)
VALUES (:S1, sha256('synthetic-erasure-invite'), :gA, now() + interval '1 day');
INSERT INTO platform.student_time_guard (student_id, daily_cap_minutes, curfew_start, curfew_end, updated_at, updated_by)
VALUES (:S1, 60, '21:30', '06:30', now(), :gA) ON CONFLICT DO NOTHING;

-- 冷静期已满的请求（以超级用户写入过去的确认时间，模拟 7 天过去）
INSERT INTO platform.erasure_request (id, student_id, requested_by, requested_at, confirmed_at, cooling_off_until)
VALUES ('e1e00000-0000-4000-8000-0000000000e1', :S1, :gA, now() - interval '8 days', now() - interval '8 days', now() - interval '1 day');
SELECT t.p_counts(:S1::uuid, 'delete') AS s1_before, t.p_counts(:S1::uuid, 'exempt') AS s1_exempt_before,
       t.p_counts(:S2::uuid) AS s2_before, t.p_public_counts() AS public_before \gset
SELECT t.ok((SELECT count(*) FROM jsonb_each(:'s1_before'::jsonb) e WHERE e.value::int > 0) >= 25,
            format('执行前 S1 在至少 25 张 delete 表里有数据（%s 张）', (SELECT count(*) FROM jsonb_each(:'s1_before'::jsonb) e WHERE e.value::int > 0)));
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 1, '冷静期满：定时任务执行 1 条删除');
SELECT receipt AS receipt FROM platform.erasure_request WHERE id = 'e1e00000-0000-4000-8000-0000000000e1' \gset
SELECT t.ok((SELECT count(*) FROM jsonb_each(t.p_counts(:S1::uuid, 'delete')) e WHERE e.value::int <> 0) = 0,
            '执行后该学生在清单里全部 delete 表零行');
-- 不经清单谓词的独立复核：delete 表凡有 student_id 列的直接按列数；经外键、账号关联的逐张写死
CREATE FUNCTION t.p_direct_left(p_student uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE r record; v bigint; o text[] := '{}';
BEGIN
  FOR r IN SELECT g.nspname, g.relname FROM platform.personal_table_registry g
            WHERE g.strategy = 'delete' AND EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = format('%I.%I', g.nspname, g.relname)::regclass AND a.attname = 'student_id' AND NOT a.attisdropped) LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE student_id = $1', r.nspname, r.relname) USING p_student INTO v;
    IF v > 0 THEN o := o || (r.nspname || '.' || r.relname); END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM insight.claim_history h WHERE NOT EXISTS (SELECT 1 FROM insight.claim c WHERE c.id = h.claim_id)) THEN o := o || 'insight.claim_history'::text; END IF;
  IF EXISTS (SELECT 1 FROM platform.diagnosis_step x WHERE NOT EXISTS (SELECT 1 FROM platform.diagnosis_session s WHERE s.id = x.session_id)) THEN o := o || 'platform.diagnosis_step'::text; END IF;
  IF EXISTS (SELECT 1 FROM platform.grade_dimension x WHERE NOT EXISTS (SELECT 1 FROM platform.grade_result g WHERE g.id = x.grade_result_id)) THEN o := o || 'platform.grade_dimension'::text; END IF;
  IF EXISTS (SELECT 1 FROM platform.auth_session x JOIN platform.student_profile sp ON sp.account_id = x.account_id WHERE sp.id = p_student) THEN o := o || 'platform.auth_session'::text; END IF;
  IF EXISTS (SELECT 1 FROM platform.account_contact x JOIN platform.student_profile sp ON sp.account_id = x.account_id WHERE sp.id = p_student) THEN o := o || 'platform.account_contact'::text; END IF;
  RETURN array_to_string(o, '、');
END $$;
SELECT t.ok(t.p_direct_left(:S1::uuid) = '', '独立复核（不经清单谓词）：该学生没有残留行（残留：' || t.p_direct_left(:S1::uuid) || '）');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA, 'SELECT platform.cancel_erasure(''e1e00000-0000-4000-8000-0000000000e1'')::text'),
                     '23514', '已执行', '已执行的删除不能撤回');
SELECT t.ok(t.p_counts(:S1::uuid, 'exempt') = :'s1_exempt_before'::jsonb, '豁免表（档案骨架、授权、同意、计量、回执）保持不变');
SELECT t.ok(t.p_counts(:S2::uuid) - 'platform.erasure_request' = :'s2_before'::jsonb - 'platform.erasure_request', '其他学生在每张清单表里的行数都不受影响');
SELECT t.ok(t.p_public_counts() = :'public_before'::jsonb, '公共层（kge、policy）不受影响');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id::text LIKE 'e1c00000-%')
            AND NOT EXISTS (SELECT 1 FROM insight.claim_history WHERE claim_id::text LIKE 'e1c00000-%'),
            '推翻、撤回的判断与判断历史一并删除（待人确认 #4 默认）');
SELECT t.ok(EXISTS (SELECT 1 FROM platform.auth_session WHERE account_id = :gA::uuid) AND EXISTS (SELECT 1 FROM platform.account_contact WHERE account_id = :gA::uuid),
            '家长的会话与联系方式不受影响（只删学生账号的）');
SELECT t.ok((SELECT status = 'executed' AND executed_at IS NOT NULL AND scopes = '{claim_ledger,evidence,facts,inference,reports}'
               FROM platform.erasure_request WHERE id = 'e1e00000-0000-4000-8000-0000000000e1'),
            '请求标为已执行，回执带契约 scopes');
SELECT t.ok((SELECT a.status = 'disabled' AND s.erased_at IS NOT NULL FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id
              WHERE s.id = :S1::uuid)
            AND (SELECT a.status = 'active' FROM platform.student_profile s JOIN platform.account a ON a.id = s.account_id WHERE s.id = :S2::uuid),
            '同一事务停用学生账号、档案标记已删除；别的学生不受影响');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "x"}')::text$$, :S1)),
                     '42501', 'not allowed', '删除后学生账号已停用：学生的写入口一律拒绝');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.request_erasure(%L)::text', :S1)), '23514', '已删除',
                     '删除后不能再发起删除');
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'receipt'::jsonb) k)
            = (SELECT array_agg(nspname || '.' || relname ORDER BY nspname || '.' || relname) FROM platform.personal_table_registry WHERE strategy = 'delete')
            AND :'receipt'::jsonb = :'s1_before'::jsonb,
            '回执：每张 delete 表一项，值是删除前的行数' || coalesce('（不一致：' || (SELECT string_agg(a.key || ' ' || a.value || '/' || coalesce(b.value::text, '-'), '、')
              FROM jsonb_each(:'receipt'::jsonb) a LEFT JOIN jsonb_each(:'s1_before'::jsonb) b ON b.key = a.key WHERE a.value IS DISTINCT FROM b.value) || '）', ''));
SELECT t.ok(NOT jsonb_path_exists(:'receipt'::jsonb, '$.* ? (@.type() != "number")')
            AND position(:S1 IN :'receipt') = 0 AND position('合成' IN :'receipt') = 0 AND position('K-QF' IN :'receipt') = 0,
            '回执只含表名与行数，不含被删内容（没有学生 ID、文本、节点）');
SELECT t.ok(coalesce(current_setting('starbuddy.erasure_student', true), '') = '', '执行结束后删除变量已清空');
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0, '再跑一次不重复执行');
SELECT t.throws_like('UPDATE platform.erasure_request SET receipt = ''{}'' WHERE id = ''e1e00000-0000-4000-8000-0000000000e1''', '23514', '已结束',
                     '回执不能改');
SELECT t.throws_like($$INSERT INTO platform.erasure_request (student_id, requested_by, requested_at, confirmed_at, cooling_off_until, status, executed_at, receipt)
                       VALUES ('c2000000-0000-4000-8000-0000000000c2', 'b0000000-0000-4000-8000-00000000000b', now() - interval '9 days', now() - interval '9 days',
                               now() - interval '2 days', 'executed', now(), '{"platform.account": 1, "x.secret_note": 3}')$$,
                     '23514', '清单里的表', '回执只能列清单里的表');
SELECT t.throws($$INSERT INTO platform.erasure_request (student_id, requested_by, receipt) VALUES ('c2000000-0000-4000-8000-0000000000c2',
                    'b0000000-0000-4000-8000-00000000000b', '{"insight.claim": "合成文本"}')$$, '23514', '回执的值只能是数字');
-- 发起人执行前已不是有效绑定家长：作废，不执行
INSERT INTO platform.erasure_request (id, student_id, requested_by, requested_at, confirmed_at, cooling_off_until)
VALUES ('e2e00000-0000-4000-8000-0000000000e2', :S2, :gB, now() - interval '8 days', now() - interval '8 days', now() - interval '1 day');
UPDATE platform.account SET status = 'disabled' WHERE id = :gB;
SELECT t.ok(t.m_as('starbuddy_scheduler', NULL, 'SELECT platform.execute_due_erasures(now())::text')::int = 0, '发起人执行前已停用：不执行');
SELECT t.ok((SELECT status = 'cancelled' AND cancel_reason = 'link_revoked' FROM platform.erasure_request WHERE id = 'e2e00000-0000-4000-8000-0000000000e2'),
            '发起人执行前已停用或解除绑定：请求作废（link_revoked）');
SELECT t.ok(t.p_counts(:S2::uuid) - 'platform.erasure_request' = :'s2_before'::jsonb - 'platform.erasure_request', '请求作废后该学生的数据不动');
ROLLBACK;

-- ================= 四、绕过守卫的机制：只有删除函数的属主在删除进行中才能删 =================
BEGIN;
SELECT t.p_bff(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成目标"}')::text$$, :S1));
SELECT set_config('starbuddy.erasure_student', :S1, true);
SELECT t.throws_like(format('DELETE FROM insight.self_report WHERE student_id = %L', :S1), '42501', '只追加',
                     '设了删除变量的超级用户（不是删除执行身份）仍不能删建档事实');
SELECT t.throws(format('SELECT t.p_bff(%L, %L)', :aS1, format('DELETE FROM insight.self_report WHERE student_id = %L RETURNING id::text', :S1)),
                '42501', '应用角色设了删除变量也不能删（没有 DELETE 权限）');
SET LOCAL ROLE starbuddy_authz;
SELECT set_config('starbuddy.erasure_student', :S1, true);
SELECT t.throws(format('DELETE FROM insight.self_report WHERE student_id = %L', :S1), '42501', '函数属主 authz 设了删除变量也不能删（没有 DELETE 权限）');
RESET ROLE;
SAVEPOINT authz_misgrant;
GRANT DELETE, SELECT (student_id) ON insight.self_report TO starbuddy_authz;
SET LOCAL ROLE starbuddy_authz;
SELECT t.throws_like(format('DELETE FROM insight.self_report WHERE student_id = %L', :S1), '42501', '只追加',
                     '即使 authz 被误授 DELETE，守卫也只认删除执行身份 eraser');
RESET ROLE;
ROLLBACK TO SAVEPOINT authz_misgrant;
-- 下面以删除执行身份直接执行 DELETE（模拟删除函数内部），检查守卫的判定
SET LOCAL ROLE starbuddy_eraser;
SELECT set_config('starbuddy.erasure_student', :S2, true);
SELECT t.throws_like(format('DELETE FROM insight.self_report WHERE student_id = %L', :S1), '42501', '只追加',
                     '删除执行身份在删除另一个学生时，也不能删这个学生的事实（变量必须等于行的学生）');
SELECT set_config('starbuddy.erasure_student', '', true);
SELECT t.throws_like(format('DELETE FROM insight.onboarding_progress WHERE student_id = %L', :S1), '42501', '只追加',
                     '删除执行身份没在执行删除请求时不能删建档进度');
SELECT set_config('starbuddy.erasure_student', :S1, true);
DELETE FROM insight.self_report WHERE student_id = :S1;
RESET ROLE;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.self_report WHERE student_id = :S1), '删除进行中（删除执行身份且变量等于该学生）才放行');
ROLLBACK;

-- ================= 五、导出 =================
BEGIN;
SELECT t.p_bff(:aS1, format($$SELECT insight.submit_self_report(%L, 'through.g9.near_term_goal', '{"text": "合成自述不给家长"}')::text$$, :S1));
SELECT t.p_bff(:gA, format($$SELECT insight.submit_guardian_report(%L, 'outer.g8.cooperation_capacity', '{"level": "regular"}')::text$$, :S1));
SELECT t.p_bff(:gA, format('SELECT platform.export_student_data(%L)::text', :S1))::jsonb AS ex \gset
SELECT t.ok((:'ex'::jsonb ->> 'format') = 'starbuddy-export@1' AND (:'ex'::jsonb ->> 'student_id') = :S1
            AND (:'ex'::jsonb ->> 'status') = 'ready' AND (:'ex'::jsonb ->> 'download_ref') ~ '^dl_[0-9a-f]{64}$'
            AND (:'ex'::jsonb ->> 'expires_at')::timestamptz <= now() + interval '24 hours',
            '已绑定家长导出这个孩子的数据：导出任务 ready，一次性下载凭据（dl_…），24 小时内失效');
SELECT t.ok((SELECT status = 'ready' AND download_ref_digest = sha256(convert_to(:'ex'::jsonb ->> 'download_ref', 'UTF8'))
                    AND position((:'ex'::jsonb ->> 'download_ref') IN row_to_json(j)::text) = 0
               FROM platform.export_job j WHERE j.id = (:'ex'::jsonb ->> 'export_job_id')::uuid),
            '导出任务只存下载凭据的摘要，凭据原文不入库');
SELECT t.ok((SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(:'ex'::jsonb -> 'topics') k)
            = (SELECT array_agg(export_topic ORDER BY export_topic) FROM platform.personal_table_registry WHERE export_topic IS NOT NULL)
            AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(:'ex'::jsonb -> 'topics') k WHERE k LIKE '%.%'),
            '导出包按稳定主题键组织（不暴露 schema.table），只含清单里有列白名单的表');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM jsonb_each(:'ex'::jsonb -> 'topics') tb, jsonb_array_elements(tb.value) row, jsonb_object_keys(row) col
   WHERE NOT col = ANY ((SELECT export_columns FROM platform.personal_table_registry r WHERE r.export_topic = tb.key)::text[])),
  '每行只含白名单列');
SELECT t.ok(position(:S2 IN :'ex') = 0 AND position('c2000000' IN :'ex') = 0, '导出不含别的学生');
SELECT t.ok(position('合成自述不给家长' IN :'ex') = 0 AND NOT (:'ex'::jsonb -> 'topics') ? 'self_reports',
            '学生自述不导出给家长（互不可见，待人确认 #5）');
SELECT t.ok(NOT (:'ex' ~ '"(basis|p_mastery|algorithm_version|model_version|assigned_to|resolved_by_account|evidence_ids|payload|visibility|raised_by)"'),
            '不含推断内部参数、审核员身份、证据原文与可见范围');
SELECT t.ok(jsonb_array_length(:'ex'::jsonb -> 'topics' -> 'disputes')
            = (SELECT count(*) FROM insight.dispute WHERE student_id = :S1::uuid AND raised_by = :gA::uuid)
            AND EXISTS (SELECT 1 FROM insight.dispute WHERE student_id = :S1::uuid AND raised_by <> :gA::uuid),
            '异议只导出家长自己提出的（孩子提的不导出）');
SELECT t.ok(jsonb_array_length(:'ex'::jsonb -> 'topics' -> 'guardian_observations') >= 1
            AND jsonb_array_length(:'ex'::jsonb -> 'topics' -> 'profile') = 1,
            '家长自己写的观察与档案骨架都在导出里');
SELECT t.ok((SELECT requested_by = :gA::uuid AND student_id = :S1::uuid AND row_count > 0 AND cardinality(topics) >= 8
               FROM platform.export_job ORDER BY created_at DESC LIMIT 1), '导出记审计（谁、哪个学生、哪些主题、多少行，不含内容）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM jsonb_array_elements(:'ex'::jsonb -> 'topics' -> 'statements') c
                         JOIN insight.claim_template tp ON tp.template_key = c ->> 'template_key' AND tp.version = (c ->> 'template_version')::int
                        WHERE tp.status <> 'approved'),
            '导出的判断都用已审核模板');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :aS1, format('SELECT platform.export_student_data(%L)::text', :S1)), '42501', 'not allowed',
                     '学生本人不能导出（家长发起）');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gB, format('SELECT platform.export_student_data(%L)::text', :S1)), '42501', 'not allowed',
                     '另一户的家长不能导出');
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.export_student_data(%L)::text', gen_random_uuid())), '42501', 'not allowed',
                     '不存在的学生与无权同一个错');
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :gA AND student_id = :S1;
SELECT t.throws_like(format('SELECT t.p_bff(%L, %L)', :gA, format('SELECT platform.export_student_data(%L)::text', :S1)), '42501', 'not allowed',
                     '绑定解除后不能导出（规则 5）');
ROLLBACK;
BEGIN;
SELECT t.m_as('starbuddy_bff', :gA, format('SELECT platform.export_student_data(%L)::text', :S1)) FROM generate_series(1, 10);
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, %L)', :gA, format('SELECT platform.export_student_data(%L)::text', :S1)), '54000',
                '同一学生 24 小时内最多导出 10 次');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM platform.export_job'')', :gA), '42501', 'BFF 读不到导出任务表');
SELECT t.throws_like('DELETE FROM platform.export_job', '42501', '只追加', '导出任务记录不能删');
ROLLBACK;

-- ================= 留给 80 的可见性矩阵：两户各一条已撤回的删除请求 =================
SELECT t.m_as('starbuddy_bff', 'a0000000-0000-4000-8000-00000000000a',
              'SELECT platform.cancel_erasure(platform.request_erasure(''c1000000-0000-4000-8000-0000000000c1''))::text');
SELECT t.m_as('starbuddy_bff', 'b0000000-0000-4000-8000-00000000000b',
              'SELECT platform.cancel_erasure(platform.request_erasure(''c2000000-0000-4000-8000-0000000000c2''))::text');
SELECT t.ok((SELECT count(DISTINCT student_id) FROM platform.erasure_request WHERE status = 'cancelled') = 2, '可见性矩阵的删除请求数据来自两户');
