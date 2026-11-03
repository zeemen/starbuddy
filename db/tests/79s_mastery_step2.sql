-- 0037 掌握节点键、复测任务、学情档案页与阻断记录（specs/kge-04-mastery-step2/spec.md 设计要点 1 与验收第 2 条的库层部分）。
-- 全部合成数据（CLAUDE.md 规则 8）。学生 S1（c1，账号 a1，家长 gA）、S2（c2，账号 b1，家长 gB）；gC 没有绑定。
-- 用 79m 的 t.m_as(角色, 账号, SQL)、79p 的 t.p_unregistered()。第二到六段在一个事务里，结束回滚；
-- 最后一段提交两户各一条复测任务、一页档案页、一条阻断记录，留给 80 的可见性矩阵（按 can_read_student 的默认规则）。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set E1 '''{e1000000-0000-4000-8000-000000000001}'''
\set E2 '''{e2000000-0000-4000-8000-000000000002}'''
\set MV '''7a000000-0000-4000-8000-000000000037'''
\set M1 '''3e700000-0000-4000-8000-000000000001'''
\set M1b '''3e700000-0000-4000-8000-00000000001b'''
\set M2 '''3e700000-0000-4000-8000-000000000002'''
\set M3 '''3e700000-0000-4000-8000-000000000003'''
\set M4 '''3e700000-0000-4000-8000-000000000004'''
\set T1 '''3e710000-0000-4000-8000-000000000001'''
\set T2 '''3e710000-0000-4000-8000-000000000002'''
\set C1 '''3e720000-0000-4000-8000-000000000001'''
\set C2 '''3e720000-0000-4000-8000-000000000002'''
\set C3 '''3e720000-0000-4000-8000-000000000003'''
\set C4 '''3e720000-0000-4000-8000-000000000004'''
\set C5 '''3e720000-0000-4000-8000-000000000005'''
\set C6 '''3e720000-0000-4000-8000-000000000006'''
\set C7 '''3e720000-0000-4000-8000-000000000007'''
\set C8 '''3e720000-0000-4000-8000-000000000008'''
\set P1 '''3e730000-0000-4000-8000-000000000001'''
\set P2 '''3e730000-0000-4000-8000-000000000002'''
\set X1b '''3e740000-0000-4000-8000-0000000000b1'''
\set aX1b '''3e750000-0000-4000-8000-0000000000b1'''
\set gR '''3e760000-0000-4000-8000-0000000000e1'''
\set SZ '''3e740000-0000-4000-8000-0000000000ff'''
\set aSZ '''3e750000-0000-4000-8000-0000000000ff'''

-- 掌握记录（学习服务身份）：节点键或能力点键
CREATE FUNCTION t.s_est(p_id uuid, p_student uuid, p_node text, p_level text, p_mv uuid, p_skill text, p_ev uuid[]) RETURNS text
  LANGUAGE sql AS $$
  SELECT format('INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, skill_local_id, state,
                   p_mastery, confidence, algorithm_version, evidence_ids, next_retest_at) VALUES (%L, %L, ''aya'', %L, %L, %L, %L, ''execute'', 0.6, 0.6,
                   ''kge-mastery@0.1.0'', %L, now() + interval ''1 day'')', p_id, p_student, p_node, p_level, p_mv, p_skill, p_ev) $$;
-- 复测任务的 INSERT 语句
CREATE FUNCTION t.s_task(p_student uuid, p_level text, p_rung text, p_reason text, p_est uuid, p_node text DEFAULT 'MATH.FUNC.QUAD.GRAPH',
                         p_id uuid DEFAULT gen_random_uuid(), p_status text DEFAULT NULL) RETURNS text LANGUAGE sql AS $$
  SELECT format('INSERT INTO platform.retest_task (id, student_id, subject, node_id, cognitive_level, rung, reason, due_at, scheduled_by_estimate_id%s)
                 VALUES (%L, %L, ''aya'', %L, %L, %L, %L, now() + interval ''1 day'', %L%s)',
                CASE WHEN p_status IS NULL THEN '' ELSE ', status, closed_at' END, p_id, p_student, p_node, p_level, p_rung, p_reason, p_est,
                CASE WHEN p_status IS NULL THEN '' ELSE format(', %L, now()', p_status) END) $$;
-- 档案页的一节、一项
CREATE FUNCTION t.s_item(p_claim uuid, p_version integer DEFAULT 1, p_key text DEFAULT 'test.profile.node') RETURNS jsonb
  LANGUAGE sql IMMUTABLE AS $$ SELECT jsonb_build_object('claim_id', p_claim, 'template_key', p_key, 'template_version', p_version) $$;
CREATE FUNCTION t.s_sec(p_key text, p_connective text, p_statements jsonb, p_todo jsonb DEFAULT '[]') RETURNS jsonb
  LANGUAGE sql IMMUTABLE AS $$ SELECT jsonb_build_object('key', p_key, 'connective', p_connective, 'statements', p_statements, 'todo', p_todo) $$;
-- 档案页的 INSERT 语句
CREATE FUNCTION t.s_page(p_student uuid, p_subject text, p_sections jsonb, p_status text DEFAULT 'passed', p_id uuid DEFAULT gen_random_uuid(),
                         p_digest text DEFAULT repeat('a', 64)) RETURNS text LANGUAGE sql AS $$
  SELECT format('INSERT INTO platform.profile_page (id, student_id, subject, map_version_id, sections, claims_digest, compiler_version, check_status)
                 VALUES (%L, %L, %L, ''7a000000-0000-4000-8000-000000000037'', %L, %L, ''kge-profile@0.1.0'', %L)',
                p_id, p_student, p_subject, p_sections, p_digest, p_status) $$;
CREATE FUNCTION t.s_check(p_student uuid, p_page uuid, p_violations jsonb) RETURNS text LANGUAGE sql AS $$
  SELECT format('INSERT INTO platform.profile_page_check (student_id, subject, profile_page_id, compiler_version, violations)
                 VALUES (%L, ''aya'', %L, ''kge-profile@0.1.0'', %L)', p_student, p_page, p_violations) $$;
CREATE FUNCTION t.s_viol(p_code text, p_fragment text DEFAULT '', p_section text DEFAULT 'working_on') RETURNS jsonb
  LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_array(jsonb_build_object('code', p_code, 'section', p_section, 'claim_ids', '[]'::jsonb, 'fragment', p_fragment)) $$;
-- 以 BFF 身份按某个账号看三张表里出现的学生（排好序，逗号连接；看不到为空串）
CREATE FUNCTION t.s_seen(p_acct uuid, p_rel text) RETURNS text LANGUAGE sql AS $$
  SELECT coalesce(t.m_as('starbuddy_bff', p_acct, format('SELECT string_agg(DISTINCT student_id::text, '','' ORDER BY student_id::text) FROM %s', p_rel)), '') $$;
-- 以 BFF 身份按某个账号读陈述视图里的判断；与该账号经 insight.claim（claim_front_read）能读到的、在页里作陈述、成立或假设的判断比较
CREATE FUNCTION t.s_view(p_acct uuid) RETURNS text LANGUAGE sql AS $$
  SELECT coalesce(t.m_as('starbuddy_bff', p_acct, 'SELECT string_agg(DISTINCT claim_id::text, '','' ORDER BY claim_id::text) FROM platform.profile_page_statement_v'), '') $$;
CREATE FUNCTION t.s_front(p_acct uuid) RETURNS text LANGUAGE sql AS $$
  SELECT coalesce(t.m_as('starbuddy_bff', p_acct, format(
    'SELECT string_agg(DISTINCT id::text, '','' ORDER BY id::text) FROM insight.claim WHERE status IN (''hypothesis'', ''established'') AND id = ANY (%L::uuid[])',
    (SELECT array_agg((i ->> 'claim_id')::uuid) FROM platform.profile_page p, jsonb_array_elements(p.sections) sec,
            jsonb_array_elements(sec -> 'statements') i))), '') $$;
CREATE FUNCTION t.s_ids(VARIADIC p uuid[]) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(string_agg(x::text, ',' ORDER BY x::text), '') FROM unnest(p) x $$;

-- ================= 一、表、清单与权限 =================
SELECT t.ok((SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = format('platform.%I', r)::regclass), format('%s ENABLE + FORCE RLS', r))
  FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r;
SELECT t.ok((SELECT confdeltype = 'c' FROM pg_constraint WHERE conrelid = format('platform.%I', r)::regclass AND contype = 'f'
              AND confrelid = 'platform.student_profile'::regclass), format('%s 的 student_id 外键 ON DELETE CASCADE', r))
  FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r;
SELECT t.ok(t.p_unregistered() = '', '个人层表清单与库一致（未登记：' || t.p_unregistered() || '）');
SELECT t.ok((SELECT array_agg(relname || ':' || strategy || ':' || delete_order || ':' || erase_scope || ':' || coalesce(export_topic, '-') ORDER BY delete_order)
               FROM platform.personal_table_registry WHERE nspname = 'platform' AND relname IN ('retest_task', 'profile_page', 'profile_page_check')
                 AND student_predicate = 'student_id = $1' AND export_filter IS NULL)
            = ARRAY['profile_page_check:delete:36:reports:-', 'profile_page:delete:37:reports:profile_pages', 'retest_task:delete:38:inference:retest_tasks'],
            '三张新表登记进个人层表清单：随删除请求删除，先于掌握记录（45）；阻断记录不进导出包');
SELECT t.ok((SELECT export_columns FROM platform.personal_table_registry WHERE relname = 'retest_task' AND nspname = 'platform')
              = ARRAY['subject', 'node_id', 'cognitive_level', 'status', 'due_at', 'created_at', 'closed_at']
            AND (SELECT export_columns FROM platform.personal_table_registry WHERE relname = 'profile_page' AND nspname = 'platform')
              = ARRAY['subject', 'check_status', 'compiled_at'],
            '导出只含 ID、状态与时间：复测任务不导轮次、原因、引用与作答，档案页不导引用、衔接语与摘要');
SELECT t.ok((SELECT export_columns FROM platform.personal_table_registry WHERE relname = 'mastery_estimate' AND nspname = 'platform')
            = ARRAY['subject', 'skill_local_id', 'node_id', 'cognitive_level', 'state', 'next_retest_at', 'computed_at'],
            '掌握记录的导出列补了节点键（node_id、cognitive_level），仍不导推断参数');
-- 学习服务：读、写，不能删；只能改状态类列
SELECT t.ok(has_table_privilege('starbuddy_learning', format('platform.%I', r), 'SELECT') AND has_table_privilege('starbuddy_learning', format('platform.%I', r), 'INSERT')
            AND NOT has_table_privilege('starbuddy_learning', format('platform.%I', r), 'DELETE')
            AND NOT has_table_privilege('starbuddy_learning', format('platform.%I', r), 'TRUNCATE'),
            format('学习服务能读写 %s，不能删', r))
  FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r;
SELECT t.ok(has_column_privilege('starbuddy_learning', 'platform.retest_task', 'status', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.retest_task', 'due_at', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.retest_task', 'closed_at', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.profile_page', 'student_id', 'UPDATE')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.profile_page', 'compiled_at', 'UPDATE')
            AND NOT has_any_column_privilege('starbuddy_learning', 'platform.profile_page_check', 'UPDATE'),
            '学习服务只能改任务状态与关闭作答、档案页内容列；关闭与编译时间由库写；阻断记录只追加');
-- BFF：复测任务只读；档案页只读元数据列（读不到 sections），陈述只经视图；阻断记录没有任何权限
SELECT t.ok(has_any_column_privilege('starbuddy_bff', format('platform.%I', r), 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_bff', format('platform.%I', r), 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_bff', format('platform.%I', r), 'UPDATE')
            AND NOT has_table_privilege('starbuddy_bff', format('platform.%I', r), 'DELETE'),
            format('BFF 对 %s 只读', r))
  FROM unnest(ARRAY['retest_task', 'profile_page']) r;
SELECT t.ok((SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
              WHERE a.attrelid = 'platform.profile_page'::regclass AND a.attnum > 0 AND NOT a.attisdropped
                AND has_column_privilege('starbuddy_bff', a.attrelid, a.attname, 'SELECT'))
            = ARRAY['check_status', 'compiled_at', 'id', 'map_version_id', 'student_id', 'subject'],
            'BFF 对档案页只读元数据列，读不到 sections（Agent 版待办与不可见判断的引用）、摘要与编译器版本');
SELECT t.ok(has_table_privilege('starbuddy_bff', 'platform.profile_page_statement_v', 'SELECT')
            AND (SELECT relowner = 'starbuddy_authz'::regrole AND 'security_barrier=true' = ANY (reloptions) FROM pg_class
                  WHERE oid = 'platform.profile_page_statement_v'::regclass),
            'BFF 经陈述视图读档案页陈述（视图属主 authz、security_barrier）');
SELECT t.ok(NOT has_any_column_privilege('starbuddy_bff', 'platform.profile_page_check', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_bff', 'platform.profile_page_check', 'INSERT')
            AND NOT has_table_privilege('starbuddy_bff', 'platform.profile_page_check', 'DELETE')
            AND NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'platform.profile_page_check'::regclass
                             AND 'starbuddy_bff'::regrole = ANY (polroles)),
            'BFF 对阻断记录没有任何权限，也没有策略');
-- 其他应用角色：没有任何表权限
SELECT t.ok(NOT has_any_column_privilege(role, format('platform.%I', r), 'SELECT') AND NOT has_any_column_privilege(role, format('platform.%I', r), 'INSERT')
            AND NOT has_any_column_privilege(role, format('platform.%I', r), 'UPDATE') AND NOT has_table_privilege(role, format('platform.%I', r), 'DELETE')
            AND NOT has_table_privilege(role, format('platform.%I', r), 'TRUNCATE'),
            format('%s 对 %s 没有任何权限', role, r))
  FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r,
       unnest(ARRAY['starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) role;
-- 0035 机制的两个函数属主：删除执行身份只有 DELETE 与学生键列；导出函数属主只读导出白名单列
SELECT t.ok(has_table_privilege('starbuddy_eraser', format('platform.%I', r), 'DELETE')
            AND has_column_privilege('starbuddy_eraser', format('platform.%I', r), 'student_id', 'SELECT')
            AND (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = format('platform.%I', r)::regclass AND a.attnum > 0 AND NOT a.attisdropped
                   AND has_column_privilege('starbuddy_eraser', a.attrelid, a.attname, 'SELECT')) = 1
            AND NOT has_any_column_privilege('starbuddy_eraser', format('platform.%I', r), 'INSERT')
            AND NOT has_any_column_privilege('starbuddy_eraser', format('platform.%I', r), 'UPDATE'),
            format('删除执行身份对 %s 只有 DELETE 与学生键列', r))
  FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r;
SELECT t.ok(NOT has_column_privilege('starbuddy_authz', 'platform.profile_page', 'claims_digest', 'SELECT')
            AND NOT has_column_privilege('starbuddy_authz', 'platform.retest_task', 'closing_attempt_id', 'SELECT')
            AND NOT has_any_column_privilege('starbuddy_authz', 'platform.profile_page_check', 'SELECT')
            AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['retest_task', 'profile_page', 'profile_page_check']) r
                             WHERE has_any_column_privilege('starbuddy_authz', format('platform.%I', r), 'INSERT')
                                OR has_any_column_privilege('starbuddy_authz', format('platform.%I', r), 'UPDATE')
                                OR has_table_privilege('starbuddy_authz', format('platform.%I', r), 'DELETE')),
            '导出函数属主 authz 只读导出白名单列与陈述视图要用的 sections（读不到摘要与阻断记录），不能写');
SELECT t.ok(has_column_privilege('starbuddy_insight', 'platform.mastery_estimate', 'node_id', 'SELECT')
            AND has_column_privilege('starbuddy_insight', 'platform.mastery_estimate', 'cognitive_level', 'SELECT')
            AND NOT has_column_privilege('starbuddy_learning', 'platform.mastery_estimate', 'node_id', 'UPDATE'),
            '洞察服务经 0028 能读节点键；学习服务不能改节点键（只能写新记录）');

BEGIN;
-- ================= 二、掌握记录的节点键 =================
SET LOCAL ROLE starbuddy_learning;
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'MATH.FUNC.QUAD.GRAPH', 'apply', :MV, 'K-QF-0399', :E1), '23514', 'mastery_estimate_one_key',
                     '节点键与能力点键同时给：拒绝（两种键恰好一种）');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, NULL, NULL, NULL, NULL, :E1), '23514', 'mastery_estimate_one_key',
                     '两种键都不给：拒绝');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'MATH.FUNC.QUAD.GRAPH', NULL, :MV, NULL, :E1), '23514', 'mastery_estimate_node_key_complete',
                     '节点键缺认知层次：拒绝（三列同空同填）');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'MATH.FUNC.QUAD.GRAPH', 'apply', NULL, NULL, :E1), '23514', 'mastery_estimate_node_key_complete',
                     '节点键缺地图版本：拒绝');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'math.func', 'apply', :MV, NULL, :E1), '23514', 'mastery_estimate_node_id_format',
                     '节点 ID 格式不对：拒绝');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'MATH.FUNC.QUAD.GRAPH', 'analyze', :MV, NULL, :E1), '23514', 'mastery_estimate_cognitive_level_check',
                     '认知层次不在 know / understand / apply / synthesize 里：拒绝');
SELECT t.m_as('starbuddy_learning', NULL, t.s_est(:M1, :S1, 'MATH.FUNC.QUAD.GRAPH', 'apply', :MV, NULL, :E1) || ' RETURNING id::text');
SELECT t.throws_like(t.s_est(gen_random_uuid(), :S1, 'MATH.FUNC.QUAD.GRAPH', 'apply', :MV, NULL, :E1), '23505', 'mastery_estimate_node_current_uq',
                     '同一单元（学生 × 学科 × 节点 × 层次）不能有两条当前记录');
SELECT t.m_as('starbuddy_learning', NULL, t.s_est(:M2, :S1, 'MATH.FUNC.QUAD.GRAPH', 'understand', :MV, NULL, :E1) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_est(:M3, :S2, 'MATH.FUNC.QUAD.GRAPH', 'apply', :MV, NULL, :E2) || ' RETURNING id::text');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate WHERE node_id = 'MATH.FUNC.QUAD.GRAPH' AND is_current) = 3,
            '同一节点的另一层次、另一学生的同一单元都各有自己的当前记录');
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate WHERE student_id = :S1 AND skill_local_id = 'K-QF-0301' AND is_current) = 1,
            '旧的能力点键当前记录不受影响（不迁移、不删除，待人确认 #10）');
-- 合法更正：同一单元，旧记录指向新 ID → 插入新记录；提交时检查（这里用 SET CONSTRAINTS 立即检查）
UPDATE platform.mastery_estimate SET is_current = false, superseded_by = :M1b WHERE id = :M1;
SELECT t.m_as('starbuddy_learning', NULL, t.s_est(:M1b, :S1, 'MATH.FUNC.QUAD.GRAPH', 'apply', :MV, NULL, :E1) || ' RETURNING id::text');
SET CONSTRAINTS platform.check_supersede_target IMMEDIATE;
SET CONSTRAINTS platform.check_supersede_target DEFERRED;
SELECT t.ok((SELECT NOT is_current AND superseded_by = :M1b::uuid AND superseded_at IS NOT NULL FROM platform.mastery_estimate WHERE id = :M1)
            AND (SELECT is_current FROM platform.mastery_estimate WHERE id = :M1b),
            '节点键的更正链：同学生、同学科、同节点、同层次的新记录可以取代旧记录');
-- 更正链不能跨单元、跨键、跨学生
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L WHERE id = %L; %s;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M4, :M2,
                            t.s_est(:M4, :S1, 'MATH.FUNC.QUAD.GRAPH', 'synthesize', :MV, NULL, :E1)),
                     '23514', 'invalid supersede target', '更正链跨认知层次（同节点 understand → synthesize）被拒');
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L WHERE id = %L; %s;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M4, :M2,
                            t.s_est(:M4, :S1, 'MATH.FUNC.QUAD.AXIS', 'understand', :MV, NULL, :E1)),
                     '23514', 'invalid supersede target', '更正链跨节点（同层次 GRAPH → AXIS）被拒');
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L WHERE id = %L; %s;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M4, :M2,
                            t.s_est(:M4, :S1, NULL, NULL, NULL, 'K-QF-0399', :E1)),
                     '23514', 'invalid supersede target', '更正链跨键（节点键 → 能力点键）被拒');
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L
                               WHERE student_id = %L AND skill_local_id = ''K-QF-0301'' AND is_current; %s;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M4, :S1,
                            t.s_est(:M4, :S1, 'MATH.FUNC.QUAD.ROOT', 'apply', :MV, NULL, :E1)),
                     '23514', 'invalid supersede target', '更正链跨键（能力点键 → 节点键）被拒');
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L
                               WHERE student_id = %L AND skill_local_id = ''K-QF-0301'' AND is_current; %s;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M4, :S1,
                            t.s_est(:M4, :S1, NULL, NULL, NULL, 'K-QF-0399', :E1)),
                     '23514', 'invalid supersede target', '旧能力点键的更正链照旧只认同一能力点（K-QF-0301 → K-QF-0399 被拒）');
SELECT t.throws_like(format('UPDATE platform.mastery_estimate SET is_current = false, superseded_by = %L WHERE id = %L;
                             SET CONSTRAINTS platform.check_supersede_target IMMEDIATE', :M3, :M2),
                     '23514', 'invalid supersede target', '更正链跨学生（指向另一学生的当前记录）被拒');
SELECT t.throws(format('UPDATE platform.mastery_estimate SET node_id = ''MATH.FUNC.QUAD.AXIS'' WHERE id = %L', :M2), '42501',
                '学习服务不能改节点键');
RESET ROLE;
SET LOCAL ROLE starbuddy_insight;
SELECT t.ok((SELECT count(*) FROM platform.mastery_estimate WHERE node_id IS NOT NULL) >= 4, '洞察服务能读到节点键记录（0028 照旧生效）');
RESET ROLE;

-- ================= 三、复测任务 =================
SET LOCAL ROLE starbuddy_learning;
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S1, 'apply', 'rung0', 'scheduled', :M1b, 'MATH.FUNC.QUAD.GRAPH', :T1) || ' RETURNING id::text');
SELECT t.ok((SELECT status = 'open' AND closed_at IS NULL AND created_at = now() FROM platform.retest_task WHERE id = :T1),
            '复测任务建成开着的，创建时间由库写');
SELECT t.throws_like(t.s_task(:S1, 'apply', 'rung0', 'scheduled', :M1b), '23505', 'retest_task_open_uq',
                     '同一单元同一原因最多一条开着的任务');
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S1, 'apply', NULL, 'decay', NULL, 'MATH.FUNC.QUAD.GRAPH', :T2) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S1, 'apply', NULL, 'verify_self_report', NULL) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S1, 'understand', 'rung0', 'scheduled', :M2) || ' RETURNING id::text');
SELECT t.ok((SELECT count(*) FROM platform.retest_task WHERE student_id = :S1 AND status = 'open') = 4,
            '同一单元的不同原因、同一节点的另一层次可以各有一条开着的任务');
SELECT t.throws_like(t.s_task(:S1, 'know', NULL, 'scheduled', :M1b), '23514', 'retest_task_rung_reason', '复测轮没有轮次：拒绝');
SELECT t.throws_like(replace(t.s_task(:S1, 'understand', 'rung0', 'scheduled', :M2), 'now() + interval ''1 day''', 'now() + interval ''2 days'''),
                     '23514', '到期时间必须等于', '复测轮的到期时间必须等于所引掌握记录的 next_retest_at（真相源）');
SELECT t.throws_like(t.s_task(:S1, 'understand', 'rung1', 'scheduled', :M2), '23514', '轮次与所引掌握记录的门槛不对应',
                     '复测轮的轮次必须与所引掌握记录的门槛对应（执行 → rung0）');
SELECT t.throws_like(t.s_task(:S1, 'understand', 'rung2', 'early', :M2), '23514', '轮次与所引掌握记录的门槛不对应',
                     '提前复测只对已亮的星（所引记录是执行时拒绝）');
SELECT t.throws_like(t.s_task(:S1, 'know', 'rung1', 'decay', NULL), '23514', 'retest_task_rung_reason', '衰减复习带轮次：拒绝');
SELECT t.throws_like(t.s_task(:S1, 'know', 'rung0', 'early', :M1b), '23514', 'retest_task_early_rung2', '提前复测只对第三轮');
SELECT t.throws_like(t.s_task(:S1, 'know', 'rung0', 'scheduled', NULL), '23514', 'retest_task_scheduled_by', '复测轮必须指向掌握记录');
SELECT t.throws_like(t.s_task(:S1, 'know', NULL, 'refresh', NULL), '23514', 'retest_task_reason_check', '原因不在枚举里：拒绝');
SELECT t.throws_like(t.s_task(:S1, 'analyze', NULL, 'decay', NULL), '23514', 'retest_task_cognitive_level_check', '认知层次不在枚举里：拒绝');
SELECT t.throws_like(t.s_task(:S1, 'know', NULL, 'decay', NULL, 'math.x'), '23514', 'retest_task_node_id_check', '节点 ID 格式不对：拒绝');
SELECT t.throws_like(t.s_task(:S1, 'synthesize', 'rung0', 'scheduled', :M1b), '23503', 'retest_task_estimate_fk',
                     '引用的掌握记录必须是同一单元（层次不同被拒）');
SELECT t.throws_like(t.s_task(:S1, 'apply', 'rung2', 'early', :M3), '23503', 'retest_task_estimate_fk', '不能引用另一学生的掌握记录');
SELECT t.throws_like(t.s_task(:S1, 'apply', 'rung2', 'early',
                              (SELECT id FROM platform.mastery_estimate WHERE student_id = :S1 AND skill_local_id = 'K-QF-0301' AND is_current)),
                     '23503', 'retest_task_estimate_fk', '不能引用旧的能力点键记录');
SELECT t.throws_like(t.s_task(:S1, 'know', NULL, 'decay', NULL, 'MATH.FUNC.QUAD.GRAPH', gen_random_uuid(), 'passed'), '23514', '必须是开着的',
                     '不能直接建成已关闭的任务');
-- 关闭：通过或失败必须记下作答；关闭时间由库写；只能关闭一次
SELECT t.throws_like(format('UPDATE platform.retest_task SET status = ''passed'' WHERE id = %L', :T1), '23514', 'retest_task_closing_attempt',
                     '通过必须记下关闭它的作答');
UPDATE platform.retest_task SET status = 'passed', closing_attempt_id = 'a7000000-0000-4000-8000-0000000000a7' WHERE id = :T1;
SELECT t.ok((SELECT status = 'passed' AND closed_at = now() FROM platform.retest_task WHERE id = :T1), '复测通过：关闭时间由库写');
SELECT t.throws_like(format('UPDATE platform.retest_task SET status = ''failed'' WHERE id = %L', :T1), '23514', '已关闭',
                     '已关闭的任务不能再改');
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S1, 'apply', 'rung0', 'scheduled', :M1b) || ' RETURNING id::text');
SELECT t.ok(true, '上一轮关闭后同一单元可以再开下一轮');
SELECT t.throws_like(format('UPDATE platform.retest_task SET status = ''cancelled'', closing_attempt_id = ''a7000000-0000-4000-8000-0000000000a7'' WHERE id = %L', :T2),
                     '23514', 'retest_task_closing_attempt', '撤销不记作答');
UPDATE platform.retest_task SET status = 'cancelled' WHERE id = :T2;
SELECT t.ok((SELECT closed_at IS NOT NULL FROM platform.retest_task WHERE id = :T2), '撤销的任务也记关闭时间');
SELECT t.throws(format('UPDATE platform.retest_task SET due_at = now() WHERE id = %L', :T1), '42501', '学习服务不能改到期时间（重排就撤销旧任务、建新任务）');
SELECT t.throws(format('DELETE FROM platform.retest_task WHERE id = %L', :T1), '42501', '学习服务不能删复测任务');
RESET ROLE;

-- ================= 四、学情档案页与阻断记录 =================
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.profile.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：档案页', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:C1, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C2, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.AXIS', 'test.profile.node', 1, 0.5, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C3, :S2, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C4, :S1, 'aya', 'node', 'MATH.FUNC.QUAD.ROOT', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{student,guardian}', now() + interval '30 days'),
  (:C5, :S1, 'aya', 'node', 'MATH.FUNC.LIN.SLOPE', 'test.profile.node', 1, 0.5, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C6, :S1, 'wenli', 'node', 'CHN.READ.MAIN.IDEA', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:C7, :S1, 'aya', 'node', 'MATH.FUNC.LIN.GRAPH', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{agent,student}', now() + interval '30 days'),
  (:C8, :S1, 'aya', 'node', 'MATH.FUNC.LIN.ROOT', 'test.profile.node', 1, 0.8, 'objective', 'established', 'created', '{agent,guardian}', now() + interval '30 days');
UPDATE insight.claim SET status = 'to_verify', change_reason = 'exam_deviation' WHERE id = :C2;
RESET ROLE;
SET LOCAL ROLE starbuddy_learning;
SELECT t.m_as('starbuddy_learning', NULL, t.s_page(:S1, 'aya', jsonb_build_array(
         t.s_sec('working_on', '先看正在攻克的部分', jsonb_build_array(t.s_item(:C1), t.s_item(:C5)), jsonb_build_array(t.s_item(:C2))),
         t.s_sec('next_step', '', '[]')), 'passed', :P1) || ' RETURNING id::text');
SELECT t.ok((SELECT compiled_at = now() AND model_version IS NULL FROM platform.profile_page WHERE id = :P1),
            '档案页：陈述引用成立或假设的判断、待办引用待验证的判断；编译时间由库写；不调模型');
SELECT t.throws_like(t.s_page(:S1, 'aya', '[]'), '23505', 'profile_page_student_id_subject_key', '每生每科只有一页');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C3))))),
                     '23514', '档案页引用的判断', '陈述不能引用另一学生的判断');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C2))))),
                     '23514', '档案页引用的判断', '陈述不能引用待验证的判断（规则 11）');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', '[]', jsonb_build_array(t.s_item(:C1))))),
                     '23514', '档案页引用的判断', '待办只放待验证的判断');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C4))))),
                     '23514', '档案页引用的判断', '读不到的判断（可见范围不含 agent）不能进页');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C1, 2))))),
                     '23514', '档案页引用的判断', '模板版本对不上：拒绝');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C6))))),
                     '23514', '档案页引用的判断', '数学的档案页不能引用语文的判断（学科核对）');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('personality', '', '[]'))),
                     '23514', 'profile_page_sections', '节只能是固定六节');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C1) || '{"text": "合成陈述"}')))),
                     '23514', 'profile_page_sections', '陈述项不能带文字（陈述文字不落库）');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', '[]') || '{"summary": "合成总结"}')),
                     '23514', 'profile_page_sections', '节里不能有别的键（不存自由文本）');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '这周做对了3题', '[]'))),
                     '23514', 'profile_page_sections', '衔接语不能含数字');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '先看正在攻克的部分', '[]')), 'blocked_fallback'),
                     '23514', 'profile_page_sections', '降级的纯模板版本没有衔接语');
SELECT t.throws_like(t.s_page(:S1, 'aya', jsonb_build_array(t.s_sec('working_on', '', '[]'), t.s_sec('working_on', '', '[]'))),
                     '23514', 'profile_page_sections', '同一节不能出现两次');
SELECT t.throws_like(t.s_page(:S1, 'aya', '[]', 'passed', gen_random_uuid(), 'not-a-digest'), '23514', 'profile_page_claims_digest_check',
                     '判断摘要必须是 sha256 hex');
UPDATE platform.profile_page SET sections = jsonb_build_array(t.s_sec('working_on', '', jsonb_build_array(t.s_item(:C1)))),
                                 check_status = 'blocked_fallback' WHERE id = :P1;
SELECT t.ok((SELECT check_status = 'blocked_fallback' FROM platform.profile_page WHERE id = :P1), '重编：学习服务改写本页内容');
SELECT t.throws(format('UPDATE platform.profile_page SET student_id = %L WHERE id = %L', :S2, :P1), '42501', '档案页的学生不能改');
SELECT t.throws(format('DELETE FROM platform.profile_page WHERE id = %L', :P1), '42501', '学习服务不能删档案页');
-- 阻断记录
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:S1, :P1, t.s_viol('degree_escalation', '薄弱')) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:S1, NULL, t.s_viol('forbidden_inference', '', '*')) || ' RETURNING id::text');
SELECT t.ok((SELECT count(*) FROM platform.profile_page_check WHERE student_id = :S1) = 2, '阻断记录：挂在页上或页还不存在时都能记');
SELECT t.m_as('starbuddy_learning', NULL, t.s_page(:S2, 'aya', '[]', 'passed', :P2) || ' RETURNING id::text');
SELECT t.throws_like(t.s_check(:S1, :P2, t.s_viol('new_conclusion', '掌握')), '23503', 'profile_page_check_page_fk', '阻断记录不能挂到另一学生的页上');
SELECT t.throws_like(t.s_check(:S1, :P1, t.s_viol('forbidden_inference', '合成命中词', '*')), '23514', 'profile_page_check_violations',
                     '禁用推断命中不存片段（规则 12）');
SELECT t.throws_like(t.s_check(:S1, :P1, t.s_viol('personality_hint', '')), '23514', 'profile_page_check_violations', '阻断编码不在枚举里：拒绝');
SELECT t.throws_like(t.s_check(:S1, :P1, '[]'), '23514', 'profile_page_check_violations', '阻断记录至少一条阻断');
SELECT t.throws_like(t.s_check(:S1, :P1, jsonb_build_array(t.s_viol('new_conclusion', '掌握') -> 0 || '{"text": "合成全文"}')),
                     '23514', 'profile_page_check_violations', '阻断记录不能带别的键（不存陈述全文）');
SELECT t.throws_like(t.s_check(:S1, :P1, t.s_viol('new_conclusion', repeat('长', 61))), '23514', 'profile_page_check_violations', '片段不超过 60 字');
SELECT t.throws_like(t.s_check(:S1, :P1, t.s_viol('number_in_connective', '做对了3题')), '23514', 'profile_page_check_violations', '片段不含数字');
SELECT t.throws_like(t.s_check(:S1, :P1, jsonb_build_array(jsonb_build_object('code', 'new_conclusion', 'section', 'working_on',
                                                                              'claim_ids', jsonb_build_array(:C3), 'fragment', ''))),
                     '23514', '阻断记录引用的判断', '阻断记录不能引用另一学生的判断');
SELECT t.throws_like(t.s_check(:S1, :P1, jsonb_build_array(jsonb_build_object('code', 'claim_unreadable', 'section', 'working_on',
                                                                              'claim_ids', jsonb_build_array(:C3), 'fragment', ''))),
                     '23514', '阻断记录引用的判断', '“读不到”也不能拿另一学生读得到的判断冒充');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:S1, :P1, jsonb_build_array(jsonb_build_object('code', 'claim_unreadable', 'section', 'working_on',
                                                                              'claim_ids', jsonb_build_array(:C4), 'fragment', ''))) || ' RETURNING id::text');
SELECT t.ok(true, '读不到的本学生判断可以记为 claim_unreadable');
SELECT t.throws('UPDATE platform.profile_page_check SET compiler_version = ''x@1''', '42501', '阻断记录只追加，不能改');
SELECT t.throws('DELETE FROM platform.profile_page_check', '42501', '学习服务不能删阻断记录');
RESET ROLE;

-- ================= 五、RLS 矩阵：本人、已绑定家长可读；兄妹、另一户家长、未绑定、撤销绑定后读不到 =================
-- 同一家庭再加一个孩子 X1b（兄妹，家长 gA），另有一位曾绑定 S1 的家长 gR（先有效，后撤销）
INSERT INTO platform.account (id, kind) VALUES (:aX1b, 'student'), (:gR, 'guardian');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
VALUES (:X1b, :aX1b, 'd1000000-0000-4000-8000-0000000000d1', 7, 'nanjing', 'bubian-2024', '2014-06-01');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
  (:gA, :X1b, 'household_creator'), (:gR, :S1, 'invite');
SET LOCAL ROLE starbuddy_learning;
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:X1b, 'know', NULL, 'verify_self_report', NULL) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:S2, 'apply', 'rung0', 'scheduled', :M3) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_page(:X1b, 'aya', '[]') || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:X1b, NULL, t.s_viol('no_claim')) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:S2, :P2, t.s_viol('no_claim')) || ' RETURNING id::text');
-- 档案页陈述：S1 的页放四条陈述（对学生与家长都可见 C1、假设 C5、只对学生 C7、只对家长 C8）与一条待办 C2
UPDATE platform.profile_page SET check_status = 'passed', sections = jsonb_build_array(
  t.s_sec('working_on', '先看正在攻克的部分', jsonb_build_array(t.s_item(:C1), t.s_item(:C5), t.s_item(:C7), t.s_item(:C8)), jsonb_build_array(t.s_item(:C2))))
 WHERE id = :P1;
RESET ROLE;
SELECT t.ok(t.s_view(:aS1) = t.s_ids(:C1::uuid, :C5::uuid, :C7::uuid), '陈述视图：学生本人只看到可见范围含 student 的陈述（没有只给家长的、没有待办）');
SELECT t.ok(t.s_view(:gA) = t.s_ids(:C1::uuid, :C5::uuid, :C8::uuid), '陈述视图：家长只看到可见范围含 guardian 的陈述（没有只给学生的、没有待办）');
SELECT t.ok(t.s_view(:gB) = '' AND t.s_view(:gC) = '' AND t.s_view(:aX1b) = '' AND t.s_view(NULL) = '',
            '陈述视图：另一户家长、未绑定家长、兄妹、未登录都看不到 S1 的陈述');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY[:aS1, :gA, :gB, :gC, :aX1b, :gR, :aS2]::uuid[]) a WHERE t.s_view(a) <> t.s_front(a)),
            '陈述视图与判断账本的前端可见策略一致：每个账号看到的 = 它经 claim_front_read 读得到的 ∩ 页里成立或假设的陈述');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY[:aS1, :gA, :gR]::uuid[]) a WHERE position(:C2 IN t.s_view(a)) > 0),
            '陈述视图从不暴露待办（待验证判断）');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT sections::text FROM platform.profile_page LIMIT 1'')', :aS1), '42501',
                'BFF 直接读 sections 被拒');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM platform.profile_page_check'')', :gA), '42501',
                'BFF 读不到阻断记录');
SELECT t.ok(t.s_seen(:aS1, r) = t.s_ids(:S1::uuid), format('%s：学生本人只看到自己的（看不到兄妹与别户）', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_seen(:aX1b, r) = t.s_ids(:X1b::uuid), format('%s：兄妹只看到自己的', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_seen(:gA, r) = t.s_ids(:S1::uuid, :X1b::uuid), format('%s：已绑定家长看到自己名下的两个孩子', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_seen(:gB, r) = t.s_ids(:S2::uuid), format('%s：另一户家长只看到自己的孩子', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_seen(:gC, r) = '' AND t.s_seen(NULL, r) = '' AND t.s_seen('00000000-0000-4000-8000-000000000000', r) = '',
            format('%s：未绑定家长、未登录、不存在的账号都看不到', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_seen(:gR, r) = t.s_ids(:S1::uuid), format('%s：绑定有效时第二位家长看得到', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = :gR AND student_id = :S1;
SELECT t.ok(t.s_seen(:gR, r) = '', format('%s：撤销绑定后读不到', r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page']) r;
SELECT t.ok(t.s_view(:gR) = '', '陈述视图：撤销绑定的家长读不到陈述');
-- 模板退役（KGE-04 第二步 PR 4 修订）：claim_front_read 经模板表 RLS 挡掉退役模板，陈述视图（属主绕过 RLS）须显式要求已审核
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.profile.retire', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：将退役', 'approved', 'test-reviewer', now());
SELECT t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key,
         template_version, confidence, source_type, status, change_reason, visibility, expires_at) VALUES (%L, %L, 'aya', 'node', 'MATH.FUNC.QUAD.VERTEX',
         'test.profile.retire', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days') RETURNING id::text$f$,
         '3e720000-0000-4000-8000-000000000009', :S1));
SELECT t.m_as('starbuddy_learning', NULL, format($f$UPDATE platform.profile_page SET sections = jsonb_build_array(
         jsonb_build_object('key', 'working_on', 'connective', '先看正在攻克的部分',
                            'statements', jsonb_build_array(%s, %s), 'todo', '[]'::jsonb)) WHERE id = %L RETURNING id::text$f$,
         quote_literal(t.s_item(:C1)) || '::jsonb', quote_literal(t.s_item('3e720000-0000-4000-8000-000000000009', 1, 'test.profile.retire')) || '::jsonb', :P1));
SELECT t.ok(t.s_view(:aS1) = t.s_ids(:C1::uuid, '3e720000-0000-4000-8000-000000000009'::uuid) AND t.s_view(:aS1) = t.s_front(:aS1),
            '陈述视图：模板已审核时返回该陈述');
UPDATE insight.claim_template SET status = 'retired' WHERE template_key = 'test.profile.retire' AND version = 1;
SELECT t.ok(t.s_view(:aS1) = t.s_ids(:C1::uuid) AND t.s_view(:gA) = t.s_ids(:C1::uuid),
            '陈述视图：模板退役后该陈述对学生与家长都不再返回');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM unnest(ARRAY[:aS1, :gA]::uuid[]) a WHERE t.s_view(a) <> t.s_front(a)),
            '陈述视图与 claim_front_read 在模板退役后仍一致');
-- BFF 不能写；其他服务角色读不到
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, %L)', :gA, sql), '42501', format('BFF 不能写：%s', left(sql, 40)))
  FROM unnest(ARRAY[t.s_task(:S1, 'know', NULL, 'decay', NULL) || ' RETURNING id::text',
                    format('UPDATE platform.retest_task SET status = ''cancelled'' WHERE student_id = %L RETURNING id::text', :S1),
                    format('DELETE FROM platform.retest_task WHERE student_id = %L RETURNING id::text', :S1),
                    t.s_page(:S1, 'wenli', '[]') || ' RETURNING id::text',
                    format('UPDATE platform.profile_page SET check_status = ''passed'' WHERE student_id = %L RETURNING id::text', :S1),
                    format('DELETE FROM platform.profile_page WHERE student_id = %L RETURNING id::text', :S1),
                    t.s_check(:S1, NULL, t.s_viol('no_claim')) || ' RETURNING id::text',
                    format('DELETE FROM platform.profile_page_check WHERE student_id = %L RETURNING id::text', :S1)]) sql;
SELECT t.throws(format('SELECT t.m_as(%L, NULL, ''SELECT count(*)::text FROM %s'')', role, r), '42501', format('%s 读不到 %s', role, r))
  FROM unnest(ARRAY['platform.retest_task', 'platform.profile_page', 'platform.profile_page_check']) r,
       unnest(ARRAY['starbuddy_review', 'starbuddy_scheduler', 'starbuddy_insight', 'starbuddy_kge', 'starbuddy_kge_read', 'starbuddy_report']) role;

-- ================= 六、删除级联 =================
-- 档案行被删时三张表随之删除（外键 ON DELETE CASCADE）；按删除请求执行的路径（清单 delete、eraser）由 79q 全库扫描覆盖
INSERT INTO platform.account (id, kind) VALUES (:aSZ, 'student');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
VALUES (:SZ, :aSZ, 'd1000000-0000-4000-8000-0000000000d1', 7, 'nanjing', 'bubian-2024', '2014-01-01');
SET LOCAL ROLE starbuddy_learning;
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(:SZ, 'know', NULL, 'verify_self_report', NULL) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_page(:SZ, 'aya', '[]', 'passed', '3e730000-0000-4000-8000-0000000000ff') || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:SZ, '3e730000-0000-4000-8000-0000000000ff', t.s_viol('no_claim')) || ' RETURNING id::text');
SELECT t.m_as('starbuddy_learning', NULL, t.s_check(:SZ, NULL, t.s_viol('no_claim')) || ' RETURNING id::text');
RESET ROLE;
SELECT t.ok((SELECT count(*) FROM platform.retest_task WHERE student_id = :SZ) + (SELECT count(*) FROM platform.profile_page WHERE student_id = :SZ)
            + (SELECT count(*) FROM platform.profile_page_check WHERE student_id = :SZ) = 4, '删除级联：准备了 4 行');
DELETE FROM platform.student_profile WHERE id = :SZ;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.retest_task WHERE student_id = :SZ) AND NOT EXISTS (SELECT 1 FROM platform.profile_page WHERE student_id = :SZ)
            AND NOT EXISTS (SELECT 1 FROM platform.profile_page_check WHERE student_id = :SZ),
            '删除级联：档案行删除后复测任务、档案页、阻断记录全部删除');
SELECT t.ok((SELECT count(*) FROM platform.profile_page WHERE student_id IN (:S1, :S2, :X1b)) = 3, '删除级联：别的学生不受影响');
-- 档案页删除时挂在它上面的阻断记录随之删除
DELETE FROM platform.profile_page WHERE id = :P2;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM platform.profile_page_check WHERE profile_page_id = :P2), '档案页删除时，挂在它上面的阻断记录随之删除');
ROLLBACK;

-- ================= 七、留给 80 可见性矩阵的数据（提交） =================
-- 两户各一条自述验证任务、一页纯模板档案页（一条对学生与家长都可见的陈述，陈述视图因此有两户数据）、一条没挂页的阻断记录
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.profile.matrix', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：档案页（可见性矩阵）', 'approved', 'test-reviewer', now());
SELECT t.m_as('starbuddy_insight', NULL, format($f$INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key,
         template_version, confidence, source_type, status, change_reason, visibility, expires_at) VALUES (%L, %L, 'aya', 'node', 'MATH.FUNC.QUAD.GRAPH',
         'test.profile.matrix', 1, 0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days') RETURNING id::text$f$,
         md5('79s-matrix:' || s)::uuid, s))
  FROM unnest(ARRAY[:S1::uuid, :S2::uuid]) s;
SELECT t.m_as('starbuddy_learning', NULL, t.s_task(s, 'know', NULL, 'verify_self_report', NULL) || ' RETURNING id::text'),
       t.m_as('starbuddy_learning', NULL, t.s_page(s, 'aya', jsonb_build_array(t.s_sec('working_on', '',
         jsonb_build_array(t.s_item(md5('79s-matrix:' || s)::uuid, 1, 'test.profile.matrix')))), 'blocked_fallback') || ' RETURNING id::text'),
       t.m_as('starbuddy_learning', NULL, t.s_check(s, NULL, t.s_viol('no_claim')) || ' RETURNING id::text')
  FROM unnest(ARRAY[:S1::uuid, :S2::uuid]) s;
