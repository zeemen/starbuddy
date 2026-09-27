-- 判断账本（0010，specs/sie-00-field-dictionary/spec.md 设计要点 3）：模板、约束、状态机、只追加的历史、写入方、可见性、级联删除。
-- 合成数据（CLAUDE.md 规则 8）。留下的 C1（S1）、C2（S2）两条判断给 80_attack_surface 的跨家庭可见性矩阵用。

-- 模板由人登记（超级用户身份模拟人工登记；应用角色都不能写模板）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.node.mastery', 1, 'node', '{agent,student,guardian}', '合成模板：节点掌握', 'approved', 'test-reviewer', now()),
  ('test.metric.agent_only', 1, 'metric', '{agent}', NULL, 'approved', 'test-reviewer', now()),
  ('test.node.draft', 1, 'node', '{agent,student}', '合成模板：草稿', 'draft', NULL, NULL);
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, status)
                  VALUES ('test.node.visible_no_text', 1, 'node', '{student}', 'draft')$$,
                '23514', '对学生或家长可见的模板必须有文本模板');
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, text_template, status)
                  VALUES ('test.node.unreviewed', 1, 'node', '{agent}', NULL, 'approved')$$,
                '23514', '已审核的模板必须写审核人与审核时间');

-- 测试辅助：按指定原因流转状态、数历史行（以调用者身份执行）
CREATE FUNCTION t.move(p_claim uuid, p_to text, p_reason text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('insight.transition_reason', p_reason, false);
  EXECUTE format('UPDATE insight.claim SET status = %L WHERE id = %L', p_to, p_claim);
END $$;
GRANT EXECUTE ON FUNCTION t.move(uuid, text, text) TO PUBLIC;
CREATE FUNCTION t.history_count(p_claim uuid) RETURNS bigint LANGUAGE sql AS $$
  SELECT count(*) FROM insight.claim_history WHERE claim_id = p_claim $$;
GRANT EXECUTE ON FUNCTION t.history_count(uuid) TO PUBLIC;

-- ================= 洞察服务：写入与约束 =================
SET ROLE starbuddy_insight;
SELECT set_config('insight.transition_reason', 'created', false);
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence,
                           evidence_ids, source_type, model_version, status, visibility, expires_at) VALUES
  ('c1a10000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1,
   '{"gate": "execute"}', 0.7, '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'established',
   '{agent,student,guardian}', now() + interval '30 days'),
  ('c2a20000-0000-4000-8000-0000000000c2', 'c2000000-0000-4000-8000-0000000000c2', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1,
   '{"gate": "understand"}', 0.5, '{e2000000-0000-4000-8000-000000000002}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis',
   '{agent,student,guardian}', now() + interval '30 days'),
  ('c3a30000-0000-4000-8000-0000000000c3', 'c1000000-0000-4000-8000-0000000000c1', NULL, 'metric', 'hint_dependency', 'test.metric.agent_only', 1,
   '{"value": 0.4}', 0.3, '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:metric-v0@0.1.0', 'hypothesis',
   '{agent}', now() + interval '14 days'),
  ('c4a40000-0000-4000-8000-0000000000c4', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0302', 'test.node.mastery', 1,
   '{}', 0.6, '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'established',
   '{agent,student}', now() + interval '30 days');
SELECT t.ok((SELECT count(*) FROM insight.claim) = 4, '洞察服务可以写判断');
SELECT t.ok((SELECT count(*) FROM insight.claim_history WHERE from_status IS NULL AND reason = 'created') = 4,
            '新建判断自动写一行历史（原状态为空、原因 created）');

SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.draft', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', '未登记或未审核', '未审核的模板不能用');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.nope', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', '未登记或未审核', '未登记的模板不能用');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'metric', 'hint_dependency', 'test.metric.agent_only', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:m@1', 'hypothesis', '{agent,student}', now() + interval '1 day')$$,
                     '23514', '超出模板上限', '可见范围不能超出模板上限');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'skill', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', '与模板', '判断对象类型必须与模板一致');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', 'claim_inferred_has_evidence', '推断类判断必须有证据');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', 'claim_inferred_has_evidence', '推断类判断必须有模型版本');
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  evidence_ids, source_type, model_version, status, visibility)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}')$$,
                '23502', '判断必须有到期时间（会过期）');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() - interval '1 day')$$,
                     '23514', 'claim_expires_after_created', '到期时间必须晚于创建时间');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                     '23514', 'claim_subject_required', '节点类判断必须属于某一科');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'to_verify', '{agent}', now() + interval '1 day')$$,
                     '23514', '新建判断的状态', '新建判断不能直接是待验证');

-- ================= 状态机 =================

SELECT t.throws_like($$SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'refuted', 'verification_refuted')$$,
                     '23514', '不允许的状态流转', '假设不能直接推翻（待人确认 #1 默认）');
SELECT t.throws_like($$SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', 'expired')$$,
                     '23514', '不允许的状态流转', '流转原因不对也被拒绝');
SELECT t.throws_like($$SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', '')$$,
                     '23514', '不允许的状态流转', '没有流转原因被拒绝');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', 'evidence_threshold');
SELECT t.ok((SELECT status FROM insight.claim WHERE id = 'c2a20000-0000-4000-8000-0000000000c2') = 'established', '假设 → 成立（证据达标）');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'to_verify', 'exam_deviation');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', 'verification_supported');
SELECT t.ok(t.history_count('c2a20000-0000-4000-8000-0000000000c2') = 4, '每次状态变化都写一行历史（新建 + 3 次流转）');
SELECT t.ok((SELECT array_agg(reason ORDER BY at) FROM insight.claim_history WHERE claim_id = 'c2a20000-0000-4000-8000-0000000000c2')
            = '{created,evidence_threshold,exam_deviation,verification_supported}', '历史按顺序记录流转原因');

SELECT t.move('c3a30000-0000-4000-8000-0000000000c3', 'to_verify', 'dispute');
SELECT t.move('c3a30000-0000-4000-8000-0000000000c3', 'refuted', 'verification_refuted');
SELECT t.throws_like($$SELECT t.move('c3a30000-0000-4000-8000-0000000000c3', 'to_verify', 'dispute')$$,
                     '23514', '终态判断', '推翻之后不能再流转');
SELECT t.throws_like($$UPDATE insight.claim SET confidence = 0.9 WHERE id = 'c3a30000-0000-4000-8000-0000000000c3'$$,
                     '23514', '终态判断', '终态判断的任何字段都不能改');
SELECT t.move('c4a40000-0000-4000-8000-0000000000c4', 'withdrawn', 'fact_corrected');
SELECT t.ok((SELECT status FROM insight.claim WHERE id = 'c4a40000-0000-4000-8000-0000000000c4') = 'withdrawn', '成立 → 撤回（依据的事实被更正）');

SELECT set_config('insight.transition_reason', '', false);
UPDATE insight.claim SET confidence = 0.75 WHERE id = 'c1a10000-0000-4000-8000-0000000000c1';
SELECT t.ok(t.history_count('c1a10000-0000-4000-8000-0000000000c1') = 1, '只改置信度不写历史，也不需要流转原因');
SELECT t.throws_like($$UPDATE insight.claim SET visibility = '{agent,student,guardian}' WHERE id = 'c3a30000-0000-4000-8000-0000000000c3'$$,
                     '23514', '终态判断', '终态判断不能改可见范围');
SELECT t.throws($$UPDATE insight.claim SET target_id = 'K-QF-0399' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                '42501', '洞察服务也不能改判断的对象（没有列权限）');
SELECT t.throws($$DELETE FROM insight.claim$$, '42501', '洞察服务不能删除判断');
SELECT t.throws($$INSERT INTO insight.claim_history (claim_id, to_status, reason, evidence_ids, confidence)
                  VALUES ('c1a10000-0000-4000-8000-0000000000c1', 'refuted', 'forged', '{}', 0.1)$$,
                '42501', '洞察服务不能自己写历史（防止伪造）');
SELECT t.throws($$UPDATE insight.claim_history SET reason = 'x'$$, '42501', '洞察服务不能改历史');
SELECT t.throws($$DELETE FROM insight.claim_history$$, '42501', '洞察服务不能删历史');
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility) VALUES ('test.x.y', 1, 'node', '{agent}')$$,
                '42501', '洞察服务不能登记模板');
RESET ROLE;

-- 不可变列即使由表属主改也被触发器拦下
SELECT t.throws_like($$UPDATE insight.claim SET target_id = 'K-QF-0399' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '不能修改', '判断的对象不可变（触发器）');
-- 历史只能追加：即使超级用户也不能改，也不能在判断还在时单独删除
SELECT t.throws_like($$UPDATE insight.claim_history SET reason = 'x'$$, '42501', '只能追加', '历史不能修改（触发器）');
SELECT t.throws_like($$DELETE FROM insight.claim_history WHERE claim_id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '42501', '不能单独删除', '判断还在时历史不能单独删除（触发器）');

-- ================= 其他应用角色不能写 =================
SET ROLE starbuddy_learning;
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  evidence_ids, source_type, model_version, status, visibility, expires_at)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                '42501', '学科服务不能写判断');
SELECT t.throws($$UPDATE insight.claim SET confidence = 0.1$$, '42501', '学科服务不能改判断');
SELECT t.throws($$SELECT * FROM insight.claim_history$$, '42501', '学科服务读不到判断历史');
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM insight.claim)
            = '{c1a10000-0000-4000-8000-0000000000c1,c2a20000-0000-4000-8000-0000000000c2}',
            'Agent 视图只看到可见范围含 agent 的非终态判断（推翻、撤回的看不到）');
RESET ROLE;

SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  evidence_ids, source_type, model_version, status, visibility, expires_at)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent}', now() + interval '1 day')$$,
                '42501', 'BFF 不能写判断');
SELECT t.throws($$SELECT * FROM insight.claim_history$$, '42501', 'BFF 读不到判断历史');
SELECT t.ok((SELECT array_agg(template_key ORDER BY template_key) FROM insight.claim_template)
            = '{test.metric.agent_only,test.node.mastery}', 'BFF 只看得到已审核的模板');
-- 可见性：家长 gA 只看到 S1 可见范围含 guardian 的判断；学生 S1 看到含 student 的；推翻、撤回、仅 agent 的都看不到
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM insight.claim) = '{c1a10000-0000-4000-8000-0000000000c1}',
            '家长只看到自己孩子、可见范围含 guardian 的非终态判断');
SELECT t.as_account('a1000000-0000-4000-8000-0000000000a1');
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM insight.claim) = '{c1a10000-0000-4000-8000-0000000000c1}',
            '学生只看到自己的、可见范围含 student 的非终态判断（撤回的 C4 看不到）');
-- S2 的登录手机号与 gA 相同：只能看到 S2 自己的判断（同手机号不能跨档案，规则 5）
SELECT t.as_account('b1000000-0000-4000-8000-0000000000b1');
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM insight.claim) = '{c2a20000-0000-4000-8000-0000000000c2}',
            '与家长同手机号的学生只看到自己的判断');
SELECT t.as_account('cc000000-0000-4000-8000-0000000000cc');
SELECT t.ok((SELECT count(*) FROM insight.claim) = 0, '没有绑定的家长看不到任何判断');
SELECT t.as_account(NULL);
SELECT t.ok((SELECT count(*) FROM insight.claim) = 0, '未设身份时看不到任何判断');
RESET ROLE;

-- 家长可见性：只含 student 的判断，家长看不到
BEGIN;
SET LOCAL ROLE starbuddy_insight;
SELECT set_config('insight.transition_reason', 'created', true);
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                           evidence_ids, source_type, model_version, status, visibility, expires_at)
VALUES ('c5a50000-0000-4000-8000-0000000000c5', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0303', 'test.node.mastery', 1, 0.5,
        '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', '{agent,student}', now() + interval '1 day');
SET LOCAL ROLE starbuddy_bff;
SELECT set_config('app.account_id', 'a0000000-0000-4000-8000-00000000000a', true);
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c5a50000-0000-4000-8000-0000000000c5'), '只含 student 的判断，家长看不到');
SELECT set_config('app.account_id', 'a1000000-0000-4000-8000-0000000000a1', true);
SELECT t.ok(EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c5a50000-0000-4000-8000-0000000000c5'), '只含 student 的判断，学生本人看得到');
ROLLBACK;

-- ================= 删除学生档案时判断与历史一起删除 =================
BEGIN;
INSERT INTO platform.account (id, kind) VALUES ('a9000000-0000-4000-8000-0000000000a9', 'student');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region)
VALUES ('c9000000-0000-4000-8000-0000000000c9', 'a9000000-0000-4000-8000-0000000000a9', 'd1000000-0000-4000-8000-0000000000d1', 9, 'nanjing');
SET LOCAL ROLE starbuddy_insight;
SELECT set_config('insight.transition_reason', 'created', true);
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                           source_type, status, visibility, expires_at)
VALUES ('c9a90000-0000-4000-8000-0000000000c9', 'c9000000-0000-4000-8000-0000000000c9', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
        'self', 'hypothesis', '{agent}', now() + interval '1 day');
RESET ROLE;
DELETE FROM platform.student_profile WHERE id = 'c9000000-0000-4000-8000-0000000000c9';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE student_id = 'c9000000-0000-4000-8000-0000000000c9')
            AND NOT EXISTS (SELECT 1 FROM insight.claim_history WHERE claim_id = 'c9a90000-0000-4000-8000-0000000000c9'),
            '删除学生档案时，判断与判断历史一起删除');
ROLLBACK;
SELECT set_config('insight.transition_reason', '', false);
