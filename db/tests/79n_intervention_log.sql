-- 0033 触发规则动作记录（specs/sie-03-dispute-reconcile/spec.md 设计要点 3、待人确认 #7）。全部合成数据。整个文件在一个事务里，结束回滚。
-- 用 79m 的 t.m_as(角色, 账号, SQL)。
\set S1 '''c1000000-0000-4000-8000-0000000000c1'''
\set S2 '''c2000000-0000-4000-8000-0000000000c2'''
\set aS1 '''a1000000-0000-4000-8000-0000000000a1'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
\set N1 '''d1e00000-0000-4000-8000-000000000e01'''
\set N2 '''d1e00000-0000-4000-8000-000000000e02'''
\set N3 '''d1e00000-0000-4000-8000-000000000e03'''
BEGIN;
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
VALUES ('test.intervention.node', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：触发规则测试', 'approved', 'test-reviewer', now());
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  (:N1, :S1, 'aya', 'node', 'K-QF-0301', 'test.intervention.node', 1, 0.6, 'objective', 'established', 'created', '{agent}', now() + interval '30 days'),
  (:N2, :S2, 'aya', 'node', 'K-QF-0301', 'test.intervention.node', 1, 0.6, 'objective', 'established', 'created', '{agent}', now() + interval '30 days'),
  (:N3, :S1, 'aya', 'node', 'K-QF-0302', 'test.intervention.node', 1, 0.6, 'objective', 'hypothesis', 'created', '{agent}', now() + interval '30 days');
UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE id = :N3;
RESET ROLE;

CREATE FUNCTION t.n_log(p_student uuid, p_rule text, p_action text, p_claims uuid[]) RETURNS uuid LANGUAGE sql AS $$
  SELECT t.m_as('starbuddy_insight', NULL, format(
    'INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version) VALUES (%L, %L, %L, %L, ''rules@1.0.0'') RETURNING id::text',
    p_student, p_rule, p_action, p_claims))::uuid $$;

-- ================= 一、新建：每条引用同一学生的非终态判断，只能是建议 =================
SELECT t.n_log(:S1, 'reminder.review_due', 'plan_review_node', ARRAY[:N1::uuid]) AS l1 \gset
SELECT t.ok((SELECT status = 'suggested' AND dispatched_at IS NULL AND result_ref IS NULL AND params_version = 'rules@1.0.0'
               FROM insight.intervention_log WHERE id = :'l1'),
            '洞察服务写一条引用判断的动作建议，状态为 suggested，记规则参数版本');
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                           status, change_reason, visibility, expires_at) VALUES
  ('d1e00000-0000-4000-8000-000000000e04', :S1, 'aya', 'node', 'K-QF-0304', 'test.intervention.node', 1, 0.6, 'objective', 'established', 'created', '{agent}', now() + interval '30 days');
UPDATE insight.claim SET status = 'to_verify', change_reason = 'dispute' WHERE id = 'd1e00000-0000-4000-8000-000000000e04';
RESET ROLE;
SELECT t.throws_like(format('SELECT t.n_log(%L, ''encourage.evidence_progress'', ''encourage_with_evidence'', ARRAY[%L::uuid])', :S1, 'd1e00000-0000-4000-8000-000000000e04'),
                     '23514', '成立 / 假设', '不能引用待验证的判断（鼓励与提醒只基于成立或假设的判断）');
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET created_at = now() - interval '2 days', expires_at = now() - interval '1 minute' WHERE id = 'd1e00000-0000-4000-8000-000000000e04';
UPDATE insight.claim SET status = 'established' WHERE id = 'd1e00000-0000-4000-8000-000000000e04';
ALTER TABLE insight.claim ENABLE TRIGGER USER;
SELECT t.throws_like(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid])', :S1, 'd1e00000-0000-4000-8000-000000000e04'),
                     '23514', '已过期', '不能引用已过期的判断');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format(
  'INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version) VALUES (%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid], ''no version'') RETURNING id::text', :S1, :N1)),
                '23514', '规则参数版本必须是 名称@x.y.z');
SELECT t.throws(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', %L)', :S1, '{}'), '23514', '动作必须引用至少一条判断');
SELECT t.throws(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[NULL::uuid])', :S1), '23514', '判断引用里不能有空值');
SELECT t.throws_like(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid])', :S1, :N2),
                     '23514', '不属于该学生', '不能引用别的学生的判断');
SELECT t.throws_like(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid, gen_random_uuid()])', :S1, :N1),
                     '23514', '不存在', '不能引用不存在的判断');
SELECT t.throws_like(format('SELECT t.n_log(%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid])', :S1, :N3),
                     '23514', '成立 / 假设', '不能引用已撤回的判断');
SELECT t.throws(format('SELECT t.n_log(%L, ''reminder.review_due'', ''shorten_task'', ARRAY[%L::uuid])', :S1, :N1), '23514', '动作必须与规则族对应（提醒不能缩短任务）');
SELECT t.throws(format('SELECT t.n_log(%L, ''reduce_load.inactive_days'', ''add_more_tasks'', ARRAY[%L::uuid])', :S1, :N1), '23514', '降负不能追加任务（动作只收枚举）');
SELECT t.throws(format('SELECT t.n_log(%L, ''praise.generic'', ''encourage_with_evidence'', ARRAY[%L::uuid])', :S1, :N1), '23514', '规则键只收三族');
SELECT t.n_log(:S1, 'encourage.evidence_progress', 'encourage_with_evidence', ARRAY[:N1::uuid]) AS l2 \gset
SELECT t.n_log(:S1, 'reduce_load.inactive_days', 'ask_reason', ARRAY[:N1::uuid, :N1::uuid]) AS l3 \gset
SELECT t.ok(:'l2' IS NOT NULL AND :'l3' IS NOT NULL, '证据化鼓励、降负（询问原因）都能记；重复引用同一判断不算缺失');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)', format(
  'INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version, status) VALUES (%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid], ''rules@1.0.0'', ''dispatched'') RETURNING id::text', :S1, :N1)),
                '42501', '洞察服务新建时不能指定状态（没有列权限）');
SELECT t.throws_like(format($$INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version, status, dispatched_at)
                              VALUES (%L, 'reminder.review_due', 'plan_review_node', ARRAY[%L::uuid], 'rules@1.0.0', 'dispatched', now())$$, :S1, :N1),
                     '23514', '只能是建议', '即使超级用户，新建时也只能是建议（守卫）');

-- ================= 二、下发：规则签字前不能下发 =================
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                            format('UPDATE insight.intervention_log SET status = ''dispatched'' WHERE id = %L RETURNING id::text', :'l1')),
                     '42501', '未签字', '规则未签字：只能生成建议，不能下发（默认签字表为空）');
SELECT t.ok((SELECT count(*) FROM insight.intervention_rule_signoff) = 0, '迁移后签字登记为空：默认不下发任何动作');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_insight'', NULL, %L)',
                       $$INSERT INTO insight.intervention_rule_signoff (rule_key, params_version, signed_by, signed_at) VALUES ('reminder.review_due', 'rules@1.0.0', 'x', now()) RETURNING rule_key$$),
                '42501', '洞察服务不能给规则签字（签字登记只由人写）');
SELECT t.throws('SELECT t.m_as(''starbuddy_learning'', NULL, ''SELECT count(*)::text FROM insight.intervention_rule_signoff'')', '42501', '学科服务读不到签字登记');
INSERT INTO insight.intervention_rule_signoff (rule_key, params_version, signed_by, signed_at) VALUES ('reminder.review_due', 'rules@0.9.0', 'test-signer', now());
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                            format('UPDATE insight.intervention_log SET status = ''dispatched'' WHERE id = %L RETURNING id::text', :'l1')),
                     '42501', '未签字', '签字的参数版本与动作记的不同：不能下发');
UPDATE insight.intervention_rule_signoff SET params_version = 'rules@1.0.0' WHERE rule_key = 'reminder.review_due';
SELECT t.m_as('starbuddy_insight', NULL, format('UPDATE insight.intervention_log SET status = ''dispatched'', result_ref = gen_random_uuid() WHERE id = %L RETURNING id::text', :'l1'));
SELECT t.ok((SELECT status = 'dispatched' AND dispatched_at IS NOT NULL AND result_ref IS NOT NULL FROM insight.intervention_log WHERE id = :'l1'),
            '规则签字后可以下发，记下发时间与结果引用');
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                            format('UPDATE insight.intervention_log SET status = ''dispatched'' WHERE id = %L RETURNING id::text', :'l2')),
                     '42501', '未签字', '签字按规则键：别的规则仍不能下发');
INSERT INTO insight.intervention_rule_signoff (rule_key, params_version, signed_by, signed_at) VALUES ('encourage.evidence_progress', 'rules@1.0.0', 'test-signer', now());
SAVEPOINT recheck;
SET LOCAL ROLE starbuddy_insight;
UPDATE insight.claim SET status = 'to_verify', change_reason = 'dispute' WHERE id = :N1;
RESET ROLE;
SELECT t.throws_like(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                            format('UPDATE insight.intervention_log SET status = ''dispatched'' WHERE id = %L RETURNING id::text', :'l2')),
                     '23514', '不能下发', '下发时再核一次：引用的判断已转待验证，证据化鼓励不能下发');
ROLLBACK TO SAVEPOINT recheck;
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                       format('UPDATE insight.intervention_log SET status = ''suggested'' WHERE id = %L RETURNING id::text', :'l1')),
                '23514', '已下发的动作不能改回建议');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                       format('UPDATE insight.intervention_log SET result_ref = gen_random_uuid() WHERE id = %L RETURNING id::text', :'l1')),
                '23514', '结果引用只能写一次');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$,
                       format('UPDATE insight.intervention_log SET claim_ids = ARRAY[%L::uuid] WHERE id = %L RETURNING id::text', :N1, :'l2')),
                '42501', '洞察服务不能改引用的判断（没有列权限）');
SELECT t.throws(format($$UPDATE insight.intervention_log SET action = 'plan_verification_item' WHERE id = %L$$, :'l1'), '23514', '动作内容只追加不改（守卫）');
UPDATE insight.intervention_log SET dispatched_at = now() WHERE id = :'l2';
SELECT t.ok((SELECT status = 'suggested' AND dispatched_at IS NULL FROM insight.intervention_log WHERE id = :'l2'),
            '不经下发不能写下发时间（守卫保持原值）');

-- ================= 三、删除与可见 =================
SELECT t.throws(format($$SELECT t.m_as('starbuddy_insight', NULL, %L)$$, format('DELETE FROM insight.intervention_log WHERE id = %L RETURNING id::text', :'l2')),
                '42501', '洞察服务不能删除动作记录');
SELECT t.throws_like(format('DELETE FROM insight.intervention_log WHERE id = %L', :'l2'), '42501', '只随删除请求删除', '超级用户也不能单独删除动作记录');
SELECT t.throws('TRUNCATE insight.intervention_log', '42501', '动作记录不能清空');
SELECT t.ok(t.m_as('starbuddy_learning', NULL, 'SELECT count(*)::text FROM insight.intervention_log')::int >= 3, '学科服务（编排层）可读动作记录');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM insight.intervention_log'')', :aS1), '42501',
                '学生读不到动作记录（Agent 可见范围）');
SELECT t.throws(format('SELECT t.m_as(''starbuddy_bff'', %L, ''SELECT count(*)::text FROM insight.intervention_log'')', :gA), '42501',
                '家长读不到动作记录');
SELECT t.throws(format($$SELECT t.m_as('starbuddy_learning', NULL, %L)$$,
                       format('INSERT INTO insight.intervention_log (student_id, rule_key, action, claim_ids, params_version) VALUES (%L, ''reminder.review_due'', ''plan_review_node'', ARRAY[%L::uuid], ''rules@1.0.0'') RETURNING id::text', :S1, :N1)),
                '42501', '学科服务不能写动作记录（只有洞察服务写）');
ROLLBACK;
