-- 0032 异议三路（specs/sie-03-dispute-reconcile/spec.md 验收第 1 条的库层部分）。全部合成数据（CLAUDE.md 规则 8）。
-- 学生 S1（c1，账号 a1，家长 gA）、S2（c2，账号 b1，家长 gB）；gC 没有绑定。
-- 前半在一个事务里，结束回滚；最后一段提交两条异议，留给 80_attack_surface 的可见性矩阵（两户）。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set aS2 '''b1000000-0000-4000-8000-0000000000b1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set gB '''b0000000-0000-4000-8000-00000000000b'''
\set gC '''cc000000-0000-4000-8000-0000000000cc'''
\set D1 '''d1d00000-0000-4000-8000-000000000d01'''
\set D2 '''d1d00000-0000-4000-8000-000000000d02'''
\set D3 '''d1d00000-0000-4000-8000-000000000d03'''
\set D4 '''d1d00000-0000-4000-8000-000000000d04'''
\set D5 '''d1d00000-0000-4000-8000-000000000d05'''
\set D6 '''d1d00000-0000-4000-8000-000000000d06'''
\set D7 '''d1d00000-0000-4000-8000-000000000d07'''
\set AT1 '''a7000000-0000-4000-8000-0000000000a7'''
\set AT2 '''a9000000-0000-4000-8000-0000000000a9'''

-- 以某个角色、某个账号执行一条返回单值的 SQL（SET LOCAL：出错时随子事务回滚）
CREATE FUNCTION t.m_as(p_role text, p_acct uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM set_config('app.account_id', coalesce(p_acct::text, ''), true);
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM set_config('app.account_id', '', true);
  RETURN v_out;
END $$;
-- 洞察服务提出异议（p_raised_by 是 BFF 转来的当前账号）
CREATE FUNCTION t.m_raise(p_claim uuid, p_kind text, p_by uuid, p_attempt uuid DEFAULT NULL, p_fact_kind text DEFAULT NULL,
                          p_fact_id uuid DEFAULT NULL) RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format('SELECT insight.raise_dispute(%L, %L, %L, %L, %L, %L)::text',
                                                  p_claim, p_kind, p_by, p_attempt, p_fact_kind, p_fact_id))::uuid $$;
-- 洞察服务按 PR② 的做法提出“我其实会”：同一事务里先提异议，再按 0010 状态机把判断转待验证
CREATE FUNCTION t.m_iknow(p_claim uuid, p_by uuid) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v uuid := t.m_raise(p_claim, 'i_know', p_by);
BEGIN
  PERFORM t.m_as('starbuddy_insight', NULL, format(
    'UPDATE insight.claim SET status = ''to_verify'', change_reason = ''dispute'' WHERE id = %L AND status IN (''hypothesis'', ''established'') RETURNING id::text',
    p_claim));
  RETURN v;
END $$;

BEGIN;
-- 模板与判断（超级用户模拟人工登记；判断以洞察服务身份写入）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.dispute.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：异议测试节点', 'approved', 'test-reviewer', now()),
  ('test.dispute.metric', 1, 'metric', '{agent,student,guardian}', '60 days', '合成模板：异议测试指标', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:D1, :S1, 'aya', 'node', 'K-QF-0301', 'test.dispute.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:D2, :S1, 'aya', 'node', 'K-QF-0302', 'test.dispute.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student}', now() + interval '30 days'),
  (:D3, :S1, 'aya', 'node', 'K-QF-0303', 'test.dispute.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent}', now() + interval '30 days'),
  (:D4, :S2, 'aya', 'node', 'K-QF-0301', 'test.dispute.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:D5, :S1, 'aya', 'node', 'K-QF-0304', 'test.dispute.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:D6, :S1, NULL, 'metric', 'hint_dependency', 'test.dispute.metric', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days'),
  (:D7, :S1, 'aya', 'node', 'K-QF-0305', 'test.dispute.node', 1, 0.6, 'objective', 'established', 'created', '{agent,student,guardian}', now() + interval '30 days');
UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = :D5;
RESET ROLE;

-- ================= 一、提出异议：谁能提、对哪些判断能提 =================
SELECT t.m_iknow(:D1, :aS1) AS dk1 \gset
SELECT t.ok((SELECT status = 'open' AND raised_by_role = 'student' AND source_type = 'self' AND kind = 'i_know' AND student_id = :S1::uuid
                    AND resolved_at IS NULL FROM insight.dispute WHERE id = :'dk1'),
            '学生本人对自己可见的判断提出“我其实会”：异议为 open，来源是学生自述');
SELECT t.ok((SELECT status = 'to_verify' FROM insight.claim WHERE id = :D1), '同一事务里判断转为待验证');
SELECT t.ok((SELECT from_status = 'established' AND to_status = 'to_verify' AND reason = 'dispute' AND evidence_ids IS NOT NULL
               FROM insight.claim_history WHERE claim_id = :D1 ORDER BY at DESC LIMIT 1),
            '判断转待验证经 0010 的状态机与历史：原因 dispute，历史带证据 ID 快照');
SELECT t.ok(t.m_raise(:D1, 'i_know', :aS1) = :'dk1'::uuid, '同一人对同一判断的同一类未结束异议：返回原异议（幂等）');
SELECT t.m_iknow(:D1, :gA) AS dk2 \gset
SELECT t.ok((SELECT raised_by_role = 'guardian' AND source_type = 'guardian' FROM insight.dispute WHERE id = :'dk2')
            AND (SELECT count(*) FROM insight.claim_history WHERE claim_id = :D1) = 2,
            '已绑定家长也能提（来源是家长观察）；判断已是待验证时不再流转（历史不多写）');
-- “我其实会”提交时判断必须已转待验证（延迟约束）
SAVEPOINT iknow_deferred;
SELECT t.m_raise(:D7, 'i_know', :aS1);
SELECT t.throws_like('SET CONSTRAINTS ALL IMMEDIATE', '23514', '必须已转为待验证', '只提“我其实会”而不把判断转待验证：提交时整笔拒绝');
ROLLBACK TO SAVEPOINT iknow_deferred;
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D1, :gB), '42501', 'not allowed', '另一户的家长不能对这个学生的判断提异议');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D1, :gC), '42501', 'not allowed', '没有绑定的家长不能提异议');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D1, :aS2), '42501', 'not allowed', '别的学生不能对这个学生的判断提异议');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', gen_random_uuid(), :aS1), '42501', 'not allowed',
                     '判断不存在与无权报同一个错（不泄露存在性）');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D1, '5f000000-0000-4000-8000-00000000005f'), '42501', 'not allowed',
                     'staff 账号不能以学生或家长身份提异议');
INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via, status, revoked_at)
VALUES (:gC, :S1, 'invite', 'revoked', now());
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''data_error'', %L, NULL, ''attempt'', %L)', :D1, :gC, :AT1), '42501', 'not allowed',
                     '已解除绑定的家长不能提异议');
UPDATE platform.account SET status = 'disabled' WHERE id = :aS1;
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''one_off_slip'', %L)', :D7, :aS1), '42501', 'not allowed', '停用的学生账号不能提异议');
UPDATE platform.account SET status = 'active' WHERE id = :aS1;
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D2, :gA), '42501', 'not allowed', '家长不能对只给学生看的判断提异议');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D3, :aS1), '42501', 'not allowed', '只给 Agent 的判断不能被提异议');
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D5, :aS1), '42501', 'not allowed', '已撤回（终态）的判断不能被提异议');
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET created_at = now() - interval '1 day', expires_at = now() - interval '1 minute' WHERE id = :D7;
ALTER TABLE insight.claim ENABLE TRIGGER USER;
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''i_know'', %L)', :D7, :aS1), '42501', 'not allowed', '已过期的判断不能被提异议');
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET expires_at = now() + interval '30 days' WHERE id = :D7;
ALTER TABLE insight.claim ENABLE TRIGGER USER;
UPDATE insight.claim_template SET status = 'retired' WHERE template_key = 'test.dispute.node';
SELECT t.throws_like(format('SELECT t.m_raise(%L, ''one_off_slip'', %L)', :D7, :aS1), '42501', 'not allowed', '模板已退役的判断不能被提异议');
UPDATE insight.claim_template SET status = 'approved' WHERE template_key = 'test.dispute.node';
SELECT t.throws(format('SELECT t.m_raise(%L, ''careless'', %L)', :D7, :aS1), '22023', '异议类型只收契约的三种（没有 careless）');

-- ================= 二、“那次只是失误”（one_off_slip）：只记学生的说法，不改判断、不贴标签 =================
SELECT t.throws(format('SELECT t.m_raise(%L, ''one_off_slip'', %L)', :D1, :gA), '22023', '“那次只是失误”只能由学生本人提出（规则 6）');
SELECT t.throws(format('SELECT t.m_raise(%L, ''i_know'', %L, %L)', :D7, :aS1, :AT1), '22023', '只有“那次只是失误”可以指明作答');
SELECT t.throws(format('SELECT t.m_raise(%L, ''one_off_slip'', %L, %L)', :D7, :aS1, :AT2), '22023', '指明的作答必须属于该学生');
SELECT count(*) AS h2 FROM insight.claim_history WHERE claim_id = :D2 \gset
SELECT t.m_raise(:D2, 'one_off_slip', :aS1, :AT1) AS dc1 \gset
SELECT t.ok((SELECT status = 'hypothesis' FROM insight.claim WHERE id = :D2)
            AND (SELECT count(*) FROM insight.claim_history WHERE claim_id = :D2) = :h2,
            '“那次只是失误”不改判断，也不写判断历史');
SELECT t.ok((SELECT source_type = 'self' AND attempt_id = :AT1::uuid AND fact_kind IS NULL FROM insight.dispute WHERE id = :'dc1'),
            '学生的说法记在异议行上（来源 self，带那次作答），不另建标签');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE n.nspname IN ('platform', 'insight', 'kge', 'policy') AND a.attnum > 0 AND NOT a.attisdropped
                           AND a.attname ~* '(careless|cuxin|sloppy|粗心)')
            AND to_regclass('insight.dispute_statement') IS NULL,
            '全库没有任何“粗心”标签列，也没有单独的自述表（七科合同第 5 条）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = 'insight.dispute'::regclass AND pg_get_constraintdef(k.oid) ~* 'careless'),
            '异议表的约束里没有 careless');

-- ================= 三、“数据有误”：指明原始事实，进统一复核队列 =================
SELECT t.throws(format('SELECT t.m_raise(%L, ''data_error'', %L)', :D1, :gA), '22023', '“数据有误”必须指明原始事实');
SELECT t.throws(format('SELECT t.m_raise(%L, ''data_error'', %L, NULL, ''attempt'', %L)', :D1, :gA, :AT2), '22023', '原始事实必须属于该学生');
SELECT t.throws(format('SELECT t.m_raise(%L, ''data_error'', %L, NULL, ''upload'', %L)', :D1, :gA, gen_random_uuid()), '22023',
                '上传类事实在上传管道建表前不能指明');
SELECT t.throws(format('SELECT t.m_raise(%L, ''i_know'', %L, NULL, ''attempt'', %L)', :D7, :aS1, :AT1), '22023', '只有“数据有误”带原始事实');
SELECT t.m_raise(:D1, 'data_error', :gA, NULL, 'attempt', :AT1) AS dd1 \gset
SELECT t.ok((SELECT d.status = 'verifying' AND d.fact_kind = 'attempt' AND d.fact_id = :AT1::uuid AND t.source_kind = 'claim'
                    AND t.source_id = :D1::uuid AND t.reasons = '{profile_dispute}'
               FROM insight.dispute d JOIN platform.review_task t ON t.id = d.review_task_id WHERE d.id = :'dd1'),
            '“数据有误”带原始事实，一提出就进统一复核队列（profile_dispute）');
SELECT t.ok(t.m_raise(:D1, 'data_error', :gA, NULL, 'attempt', :AT1) = :'dd1'::uuid, '“数据有误”重复提出返回原异议');
SELECT t.throws(format('SELECT t.m_raise(%L, ''data_error'', %L, NULL, ''attempt'', %L)', :D6, :aS1, :AT1), '22023',
                '跨科判断暂不能进复核队列（0023 拒绝，异议不落库）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.dispute WHERE claim_id = :D6), '入队失败时不留下异议');

-- ================= 四、写入方：只有洞察服务经函数 =================
SELECT t.throws(format('SELECT t.m_as(%L, %L, %L)', r, :aS1, format('SELECT insight.raise_dispute(%L, ''one_off_slip'', %L, NULL, NULL, NULL)::text', :D7, :aS1)),
                '42501', format('%s 不能调用提出异议函数', r))
  FROM unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler', 'starbuddy_report', 'starbuddy_eraser']) r;
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)',
                       format('INSERT INTO insight.dispute (student_id, claim_id, raised_by, raised_by_role, source_type, kind) VALUES (%L, %L, %L, ''student'', ''self'', ''one_off_slip'') RETURNING id::text',
                              :S1, :D7, :aS1)), '42501', '洞察服务不能直接插入异议（只经 raise_dispute）');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('DELETE FROM insight.dispute WHERE id = %L RETURNING id::text', :'dk1')),
                '42501', '洞察服务不能删除异议');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format('UPDATE insight.dispute SET claim_id = %L WHERE id = %L RETURNING id::text', :D7, :'dk1')),
                '42501', '洞察服务不能改异议指向的判断（没有列权限）');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, %L)', :aS1, format('UPDATE insight.dispute SET status = ''withdrawn'' WHERE id = %L RETURNING id::text', :'dk1')),
                '42501', 'BFF 不能改异议');
SELECT t.throws_like(format('DELETE FROM insight.dispute WHERE id = %L', :'dk1'), '42501', '只随删除请求删除',
                     '超级用户也不能单独删除异议（守卫）');
SELECT set_config('starbuddy.erasure_student', :S1, true);
SELECT t.throws_like(format('DELETE FROM insight.dispute WHERE id = %L', :'dk1'), '42501', '只随删除请求删除',
                     '设了删除变量但不是删除执行身份（eraser），仍不能删异议');
SET LOCAL ROLE starbuddy_authz;
SELECT t.throws(format('DELETE FROM insight.dispute WHERE id = %L', :'dk1'), '42501', '函数属主 authz 没有删异议的权限');
RESET ROLE;
SELECT set_config('starbuddy.erasure_student', '', true);
SET CONSTRAINTS ALL IMMEDIATE;  -- 先把延迟核对做掉（前面的“我其实会”都已转待验证），否则 TRUNCATE 报待处理事件
SET CONSTRAINTS ALL DEFERRED;
SELECT t.throws('TRUNCATE insight.dispute CASCADE', '42501', '异议不能清空');

-- ================= 五、流转（洞察服务） =================
CREATE FUNCTION t.m_move(p_id uuid, p_set text) RETURNS void LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format('UPDATE insight.dispute SET %s WHERE id = %L RETURNING id::text', p_set, p_id)) $$;
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''verifying''')$$, :'dk1'), '23514', '进入验证至少要有 1 道验证题');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''verifying'', verification_task_refs = ARRAY[gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid()]')$$, :'dk1'),
                '23514', '验证题最多 5 道');
SELECT t.throws(format($$SELECT t.m_move(%L, 'verification_task_refs = ARRAY[gen_random_uuid(), gen_random_uuid()]')$$, :'dk1'),
                '23514', '每次修改都必须是一次流转（不能只改引用）');
SELECT t.m_move(:'dk1', 'status = ''verifying'', verification_task_refs = ARRAY[gen_random_uuid(), gen_random_uuid()]');
SELECT t.ok((SELECT status = 'verifying' AND cardinality(verification_task_refs) = 2 FROM insight.dispute WHERE id = :'dk1'),
            '“我其实会”进入验证，记验证题的引用');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''resolved_supported'', result_note_code = ''verification_supported'', verification_task_refs = ARRAY[gen_random_uuid()]')$$, :'dk1'),
                '23514', '验证任务引用只在进入验证时写一次');
SELECT t.throws_like(format($$SELECT t.m_move(%L, 'status = ''resolved_supported'', result_note_code = ''verification_supported''')$$, :'dk1'),
                     '23514', '不一致', '判断还没推翻时，异议不能以“验证支持”结案');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''resolved_supported'', result_note_code = ''fact_corrected''')$$, :'dk1'),
                '23514', '结论码必须与流转对应（只收枚举）');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''fact_corrected'', result_note_code = ''fact_corrected''')$$, :'dk1'),
                '23514', '“我其实会”不能以事实更正结案');
SET LOCAL ROLE starbuddy_insight;
UPDATE insight.claim SET status = 'refuted', change_reason = 'verification_refuted' WHERE id = :D1;
RESET ROLE;
SELECT t.m_move(:'dk1', 'status = ''resolved_supported'', result_note_code = ''verification_supported''');
SELECT t.ok((SELECT status = 'resolved_supported' AND resolved_at IS NOT NULL FROM insight.dispute WHERE id = :'dk1'),
            '验证支持异议：判断先推翻，异议再结案，记结案时间');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''withdrawn'', result_note_code = ''withdrawn_by_raiser''')$$, :'dk1'),
                '23514', '已结束的异议不能再修改');
SELECT t.throws(format($$UPDATE insight.dispute SET kind = 'one_off_slip' WHERE id = %L$$, :'dk2'), '23514', '异议类型不能修改（守卫，超级用户也不行）');
SELECT t.throws(format($$UPDATE insight.dispute SET source_type = 'self' WHERE id = %L$$, :'dk2'), '23514', '异议来源必须与提出人一致');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''withdrawn'', result_note_code = ''duplicate''')$$, :'dk2'), '23514', '撤回只能用撤回的结论码');
SELECT t.m_move(:'dk2', 'status = ''withdrawn'', result_note_code = ''withdrawn_by_raiser''');
SELECT t.ok((SELECT status = 'withdrawn' FROM insight.dispute WHERE id = :'dk2'), '提出人可以撤回异议');
SELECT t.throws(format($$SELECT t.m_move(%L, 'status = ''verifying''')$$, :'dc1'), '23514', '“那次只是失误”没有验证环节');
SELECT t.m_move(:'dc1', 'status = ''resolved_unsupported'', result_note_code = ''self_statement_recorded''');
SELECT t.ok((SELECT status = 'resolved_unsupported' FROM insight.dispute WHERE id = :'dc1')
            AND (SELECT status = 'hypothesis' FROM insight.claim WHERE id = :D2),
            '“那次只是失误”以“已记下说法”结案，判断不变');
-- “我其实会”验证不支持：判断回到成立
SELECT t.m_iknow(:D7, :aS1) AS dk3 \gset
SELECT t.m_move(:'dk3', 'status = ''verifying'', verification_task_refs = ARRAY[gen_random_uuid()]');
SELECT t.throws_like(format($$SELECT t.m_move(%L, 'status = ''resolved_unsupported'', result_note_code = ''verification_not_supported''')$$, :'dk3'),
                     '23514', '不一致', '判断还是待验证时，异议不能以“验证不支持”结案');
SET LOCAL ROLE starbuddy_insight;
UPDATE insight.claim SET status = 'established', change_reason = 'verification_supported' WHERE id = :D7;
RESET ROLE;
SELECT t.m_move(:'dk3', 'status = ''resolved_unsupported'', result_note_code = ''verification_not_supported''');
SELECT t.ok((SELECT status = 'resolved_unsupported' FROM insight.dispute WHERE id = :'dk3'), '验证不支持：判断回到成立，异议结案');
-- “数据有误”事实更正：判断撤回、原始事实已有更正行后结案（改用另一户的 D4，由其家长指明考试记录）
SELECT t.m_as('starbuddy_bff', :gB, format($$SELECT insight.submit_exam_record(%L, 'aya', 'monthly', '2026-09-20', 100, 60, NULL, NULL, NULL)::text$$, :S2))::uuid AS ex2 \gset
SELECT t.m_raise(:D4, 'data_error', :gB, NULL, 'exam_record', :'ex2') AS dd2 \gset
SET LOCAL ROLE starbuddy_insight;
UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = :D4;
RESET ROLE;
SELECT t.throws_like(format($$SELECT t.m_move(%L, 'status = ''fact_corrected'', result_note_code = ''fact_corrected''')$$, :'dd2'),
                     '23514', '还没有更正行', '原始事实还没被更正时，不能以事实更正结案');
UPDATE insight.exam_record SET superseded_at = now() WHERE id = :'ex2';
SELECT t.m_move(:'dd2', 'status = ''fact_corrected'', result_note_code = ''fact_corrected''');
SELECT t.ok((SELECT status = 'fact_corrected' AND resolved_at IS NOT NULL FROM insight.dispute WHERE id = :'dd2'),
            '“数据有误”经复核：原考试记录已被替换、判断已撤回，异议以事实更正结案');
SELECT t.throws_like(format($$SELECT t.m_move(%L, 'status = ''fact_corrected'', result_note_code = ''fact_corrected''')$$, :'dd1'),
                     '23514', '不一致', '判断没撤回时，“数据有误”不能以事实更正结案');

-- ================= 六、可见：学生、家长只读自己提出的（逐行） =================
CREATE FUNCTION t.m_seen(p_acct uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_bff', p_acct, 'SELECT coalesce(string_agg(id::text, '','' ORDER BY id), '''') FROM insight.dispute') $$;
SELECT t.ok(t.m_seen(:aS1) = (SELECT string_agg(id::text, ',' ORDER BY id) FROM insight.dispute WHERE raised_by = :aS1::uuid),
            '学生本人只看到自己提出的异议');
SELECT t.ok(position(:'dk1' IN t.m_seen(:aS1)) > 0 AND position(:'dc1' IN t.m_seen(:aS1)) > 0
            AND position(:'dk2' IN t.m_seen(:aS1)) = 0 AND position(:'dd1' IN t.m_seen(:aS1)) = 0,
            '逐行：学生看得到自己提的两条，看不到家长替自己提的两条');
SELECT t.ok(position(:'dk2' IN t.m_seen(:gA)) > 0 AND position(:'dd1' IN t.m_seen(:gA)) > 0
            AND position(:'dk1' IN t.m_seen(:gA)) = 0 AND position(:'dc1' IN t.m_seen(:gA)) = 0,
            '逐行：家长看得到自己提的两条，看不到孩子提的两条');
SELECT t.ok(t.m_seen(:gB) = (SELECT string_agg(id::text, ',' ORDER BY id) FROM insight.dispute WHERE raised_by = :gB::uuid)
            AND position(:'dk2' IN t.m_seen(:gB)) = 0, '另一户的家长看不到这户的异议');
SELECT t.ok(t.m_seen(:gC) = '' AND t.m_seen(NULL) = '', '没有绑定的家长、未登录都看不到异议');
DELETE FROM platform.guardian_student_link WHERE guardian_account_id = :gC;
UPDATE platform.account SET status = 'disabled' WHERE id = :gA;
SELECT t.ok(t.m_seen(:gA) = '', '停用的家长看不到自己提过的异议');
UPDATE platform.account SET status = 'active' WHERE id = :gA;
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, %L)', :aS1, format('SELECT count(%s)::text FROM insight.dispute', c)), '42501',
                format('BFF 读不到异议的 %s 列', c))
  FROM unnest(ARRAY['verification_task_refs', 'review_task_id', 'attempt_id', 'fact_id', 'source_type']) c;
SELECT t.throws('SELECT t.m_as(''starbuddy_learning'', NULL, ''SELECT count(*)::text FROM insight.dispute'')', '42501', '学科服务读不到异议');
SELECT t.ok(t.m_as('starbuddy_insight', NULL, 'SELECT count(*)::text FROM insight.dispute')::int = (SELECT count(*) FROM insight.dispute),
            '洞察服务读得到全部异议');

-- ================= 七、写入量上限：学生与家长分开 =================
SAVEPOINT limits;
ALTER TABLE insight.claim DISABLE TRIGGER USER;
INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at)
SELECT :S1, 'aya', 'node', 'K-QF-9' || lpad(i::text, 3, '0'), 'test.dispute.node', 1, 0.5, 'objective', 'hypothesis', 'created',
       '{agent,student,guardian}', now() + interval '10 days' FROM generate_series(1, 50) i;
ALTER TABLE insight.claim ENABLE TRIGGER USER;
INSERT INTO insight.dispute (student_id, claim_id, raised_by, raised_by_role, source_type, kind)
SELECT :S1, c.id, :aS1, 'student', 'self', 'one_off_slip' FROM insight.claim c WHERE c.target_id LIKE 'K-QF-9%';
SELECT t.throws(format('SELECT t.m_raise(%L, ''one_off_slip'', %L)', :D2, :aS1), '54000', '学生本人 24 小时内最多 50 条异议');
SELECT t.ok(t.m_raise(:D7, 'data_error', :gA, NULL, 'attempt', :AT1) IS NOT NULL, '学生的额度用完不影响家长提异议（分开限额）');
ROLLBACK TO SAVEPOINT limits;

ROLLBACK;

-- ================= 留给 80 的可见性矩阵：两户各有一条异议（学生本人提、家长提各一条） =================
SELECT t.m_raise('c1a10000-0000-4000-8000-0000000000c1', 'one_off_slip', 'a1000000-0000-4000-8000-0000000000a1');
BEGIN;
SELECT t.m_iknow('c2a20000-0000-4000-8000-0000000000c2', 'b0000000-0000-4000-8000-00000000000b');
COMMIT;
SELECT t.ok((SELECT count(DISTINCT student_id) FROM insight.dispute) = 2, '可见性矩阵的异议数据来自两户');
