-- 判断账本（0010，specs/sie-00-field-dictionary/spec.md 设计要点 3）：模板、约束、状态机、原因与历史、写入方、可见性、依据、级联删除。
-- 合成数据（CLAUDE.md 规则 8）。留下的 C1（S1）、C2（S2）两条判断给 80_attack_surface 的跨家庭可见性矩阵用。

-- 模板由人登记（超级用户身份模拟人工登记；应用角色都不能写模板）
INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at) VALUES
  ('test.node.mastery', 1, 'node', '{agent,student,guardian}', '60 days', '合成模板：节点掌握', 'approved', 'test-reviewer', now()),
  ('test.metric.agent_only', 1, 'metric', '{agent}', '30 days', NULL, 'approved', 'test-reviewer', now()),
  ('test.node.draft', 1, 'node', '{agent,student}', '30 days', '合成模板：草稿', 'draft', NULL, NULL),
  ('test.node.to_retire', 1, 'node', '{agent,student,guardian}', '30 days', '合成模板：将退役', 'approved', 'test-reviewer', now());
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, status)
                  VALUES ('test.node.visible_no_text', 1, 'node', '{student}', '1 day', 'draft')$$,
                '23514', '对学生或家长可见的模板必须有文本模板');
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, status)
                  VALUES ('test.node.unreviewed', 1, 'node', '{agent}', '1 day', 'approved')$$,
                '23514', '已审核的模板必须写审核人与审核时间');
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, status)
                  VALUES ('test.node.forever', 1, 'node', '{agent}', '10 years', 'draft')$$,
                '23514', '模板最长有效期有上限');

-- 依据：以超级用户身份取两个学生的 platform 推断行 ID（洞察服务读不到 platform 表）
SELECT (SELECT id FROM platform.mastery_estimate WHERE student_id = 'c1000000-0000-4000-8000-0000000000c1' ORDER BY computed_at LIMIT 1) AS m_s1,
       (SELECT id FROM platform.mastery_estimate WHERE student_id = 'c2000000-0000-4000-8000-0000000000c2' ORDER BY computed_at LIMIT 1) AS m_s2 \gset

-- 测试辅助：按原因修改状态、数历史行（以调用者身份执行）
CREATE FUNCTION t.move(p_claim uuid, p_to text, p_reason text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('UPDATE insight.claim SET status = %L, change_reason = %L WHERE id = %L', p_to, p_reason, p_claim);
END $$;
GRANT EXECUTE ON FUNCTION t.move(uuid, text, text) TO PUBLIC;
CREATE FUNCTION t.history_count(p_claim uuid) RETURNS bigint LANGUAGE sql AS $$
  SELECT count(*) FROM insight.claim_history WHERE claim_id = p_claim $$;
GRANT EXECUTE ON FUNCTION t.history_count(uuid) TO PUBLIC;
-- 插入一条最小的节点判断（用于逐条流转测试），返回 ID
CREATE FUNCTION t.new_claim(p_status text, p_basis uuid, p_template text DEFAULT 'test.node.mastery') RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence, evidence_ids,
                             basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at)
  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', p_template, 1, 0.5, '{e1000000-0000-4000-8000-000000000001}',
          'mastery_estimate', p_basis, 'inferred', 'algo:bkt-v0@0.1.0', p_status::insight.claim_status, 'created', '{agent}', now() + interval '10 days')
  RETURNING id INTO v;
  RETURN v;
END $$;
GRANT EXECUTE ON FUNCTION t.new_claim(text, uuid, text) TO PUBLIC;

-- ================= 洞察服务：写入与约束 =================
SET ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence,
                           evidence_ids, basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at, created_at) VALUES
  ('c1a10000-0000-4000-8000-0000000000c1', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1,
   '{"gate": "execute"}', 0.7, '{e1000000-0000-4000-8000-000000000001}', 'mastery_estimate', :'m_s1', 'inferred', 'algo:bkt-v0@0.1.0',
   'established', 'created', '{agent,student,guardian}', now() + interval '30 days', '2020-01-01'),
  ('c2a20000-0000-4000-8000-0000000000c2', 'c2000000-0000-4000-8000-0000000000c2', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1,
   '{"gate": "understand"}', 0.5, '{e2000000-0000-4000-8000-000000000002}', 'mastery_estimate', :'m_s2', 'inferred', 'algo:bkt-v0@0.1.0',
   'hypothesis', 'created', '{agent,student,guardian}', now() + interval '30 days', now()),
  ('c3a30000-0000-4000-8000-0000000000c3', 'c1000000-0000-4000-8000-0000000000c1', NULL, 'metric', 'hint_dependency', 'test.metric.agent_only', 1,
   '{"value": 0.4}', 0.3, '{e1000000-0000-4000-8000-000000000001}', NULL, NULL, 'inferred', 'algo:metric-v0@0.1.0',
   'hypothesis', 'created', '{agent}', now() + interval '14 days', now()),
  ('c4a40000-0000-4000-8000-0000000000c4', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0302', 'test.node.mastery', 1,
   '{}', 0.6, '{e1000000-0000-4000-8000-000000000001}', 'mastery_estimate', :'m_s1', 'inferred', 'algo:bkt-v0@0.1.0',
   'established', 'created', '{agent,student}', now() + interval '30 days', now());
SELECT t.ok((SELECT count(*) FROM insight.claim) = 4, '洞察服务可以写判断');
SELECT t.ok((SELECT created_at > now() - interval '1 minute' FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'),
            '创建时间由库决定，写入方伪造的时间被覆盖');
SELECT t.ok((SELECT count(*) FROM insight.claim_history WHERE from_status IS NULL AND reason = 'created') = 4,
            '新建判断自动写一行历史（原状态为空、原因 created）');

SELECT t.throws_like($$SELECT t.new_claim('hypothesis', (SELECT basis_id FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'), 'test.node.draft')$$,
                     '23514', '未登记、未审核或已退役', '未审核的模板不能用');
SELECT t.throws_like($$SELECT t.new_claim('hypothesis', (SELECT basis_id FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'), 'test.node.nope')$$,
                     '23514', '未登记、未审核或已退役', '未登记的模板不能用');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'metric', 'hint_dependency', 'test.metric.agent_only', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:m@1', 'hypothesis', 'created', '{agent,student}', now() + interval '1 day')$$,
                     '23514', '超出模板上限', '可见范围不能超出模板上限');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at)
                       SELECT student_id, 'aya', 'skill', 'K-QF-0301', 'test.node.mastery', 1, 0.5, evidence_ids, 'skill_observation', basis_id,
                              'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', 'created', '{agent}', now() + interval '1 day'
                         FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '与模板', '判断对象类型必须与模板一致');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at)
                       SELECT student_id, 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5, basis_kind, basis_id,
                              'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', 'created', '{agent}', now() + interval '1 day'
                         FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'claim_inferred_has_evidence', '推断类判断必须有证据');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, basis_kind, basis_id, source_type, status, change_reason, visibility, expires_at)
                       SELECT student_id, 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5, evidence_ids, basis_kind, basis_id,
                              'inferred', 'hypothesis', 'created', '{agent}', now() + interval '1 day'
                         FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'claim_inferred_has_evidence', '推断类判断必须有模型版本');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       evidence_ids, source_type, model_version, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       '{e1000000-0000-4000-8000-000000000001}', 'inferred', 'algo:bkt-v0@0.1.0', 'hypothesis', 'created', '{agent}', now() + interval '1 day')$$,
                     '23514', 'claim_inferred_has_basis', '节点类推断必须引用 platform 掌握行（真相源）');
SELECT t.throws_like($$SELECT t.new_claim('hypothesis', (SELECT basis_id FROM insight.claim WHERE id = 'c2a20000-0000-4000-8000-0000000000c2'))$$,
                     '23514', '不属于该学生', '判断依据必须属于同一个学生');
SELECT t.throws_like($$SELECT t.new_claim('hypothesis', 'ffffffff-0000-4000-8000-000000000000')$$,
                     '23514', '不属于该学生', '判断依据必须存在');
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  source_type, status, change_reason, visibility)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  'self', 'hypothesis', 'created', '{agent}')$$,
                '23502', '判断必须有到期时间（会过期）');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'hypothesis', 'created', '{agent}', now() - interval '1 day')$$,
                     '23514', 'claim_expires_after_created', '到期时间必须晚于创建时间');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'hypothesis', 'created', '{agent}', now() + interval '61 days')$$,
                     '23514', '最长有效期', '到期时间不能超过模板最长有效期');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'hypothesis', 'created', '{agent}', now() + interval '1 day')$$,
                     '23514', 'claim_subject_required', '节点类判断必须属于某一科');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at, value)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'hypothesis', 'created', '{agent}', now() + interval '1 day',
                       jsonb_build_object('text', repeat('长', 3000)))$$,
                     '23514', 'claim_value_small', 'value 只能放少量模板变量（大小有上限）');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'to_verify', 'created', '{agent}', now() + interval '1 day')$$,
                     '23514', '不允许的状态流转', '新建判断不能直接是待验证');
SELECT t.throws_like($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                       source_type, status, change_reason, visibility, expires_at)
                       VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                       'self', 'hypothesis', 'revised', '{agent}', now() + interval '1 day')$$,
                     '23514', '不允许的状态流转', '新建判断的原因只能是 created');

-- ================= 状态机：逐条枚举全部 (原状态, 新状态, 原因) =================
-- 合法组合来自 change_allowed 的白名单（新建以外共 18 种：12 种状态流转 + 6 种不改状态的修改）；其余全部组合（含终态出发、原因不对）都必须被拒绝
DO $$
DECLARE
  st text; tg text; rs text;
  v_id uuid;
  v_basis uuid := (SELECT basis_id FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1');
  v_ok int := 0; v_rejected int := 0;
  v_reasons text[] := ARRAY['created', 'evidence_threshold', 'expired', 'dispute', 'exam_deviation', 'verification_supported',
                            'verification_refuted', 'fact_corrected', 'revised', 'visibility_changed', ''];
  v_path jsonb := '{"hypothesis": [], "established": [], "to_verify": ["expired"], "refuted": ["expired", "verification_refuted"],
                   "withdrawn": ["fact_corrected"]}';
  v_expect boolean;
BEGIN
  FOREACH st IN ARRAY ARRAY['hypothesis', 'established', 'to_verify', 'refuted', 'withdrawn'] LOOP
    FOREACH tg IN ARRAY ARRAY['hypothesis', 'established', 'to_verify', 'refuted', 'withdrawn'] LOOP
      FOREACH rs IN ARRAY v_reasons LOOP
        -- 准备一条处于 st 状态的判断：新建为 hypothesis / established，再按固定路径走到 to_verify、refuted、withdrawn
        v_id := t.new_claim(CASE WHEN st = 'established' THEN 'established' ELSE 'hypothesis' END, v_basis);
        IF st IN ('to_verify', 'refuted') THEN PERFORM t.move(v_id, 'to_verify', 'expired'); END IF;
        IF st = 'refuted' THEN PERFORM t.move(v_id, 'refuted', 'verification_refuted'); END IF;
        IF st = 'withdrawn' THEN PERFORM t.move(v_id, 'withdrawn', 'fact_corrected'); END IF;
        v_expect := st NOT IN ('refuted', 'withdrawn') AND insight.change_allowed(st::insight.claim_status, tg::insight.claim_status, rs);
        BEGIN
          PERFORM t.move(v_id, tg, rs);
          IF NOT v_expect THEN RAISE EXCEPTION 'FAIL: 状态机——% → %（原因 %）应被拒绝，却成功了', st, tg, rs; END IF;
          IF (SELECT reason FROM insight.claim_history WHERE claim_id = v_id ORDER BY at DESC LIMIT 1) <> rs THEN
            RAISE EXCEPTION 'FAIL: 状态机——% → % 成功后历史里的原因不是 %', st, tg, rs;
          END IF;
          v_ok := v_ok + 1;
        EXCEPTION WHEN check_violation THEN
          IF v_expect THEN RAISE EXCEPTION 'FAIL: 状态机——% → %（原因 %）应被允许，却被拒绝：%', st, tg, rs, SQLERRM; END IF;
          v_rejected := v_rejected + 1;
        END;
      END LOOP;
    END LOOP;
  END LOOP;
  PERFORM t.ok(v_ok = 18 AND v_rejected = 5 * 5 * 11 - 18,
               format('状态机：全部 %s 种组合逐条核对，%s 种合法、%s 种被拒（含终态出发与原因不符）', 5 * 5 * 11, v_ok, v_rejected));
END $$;

SELECT t.throws_like($$SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'refuted', 'verification_refuted')$$,
                     '23514', '不允许的状态流转', '假设不能直接推翻（待人确认 #1 默认）');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', 'evidence_threshold');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'to_verify', 'exam_deviation');
SELECT t.move('c2a20000-0000-4000-8000-0000000000c2', 'established', 'verification_supported');
SELECT t.ok((SELECT array_agg(reason ORDER BY at) FROM insight.claim_history WHERE claim_id = 'c2a20000-0000-4000-8000-0000000000c2')
            = '{created,evidence_threshold,exam_deviation,verification_supported}', '历史按顺序记录每次流转的原因');

SELECT t.move('c3a30000-0000-4000-8000-0000000000c3', 'to_verify', 'dispute');
SELECT t.move('c3a30000-0000-4000-8000-0000000000c3', 'refuted', 'verification_refuted');
SELECT t.throws_like($$UPDATE insight.claim SET confidence = 0.9, change_reason = 'revised' WHERE id = 'c3a30000-0000-4000-8000-0000000000c3'$$,
                     '23514', '终态判断', '终态判断的任何字段都不能改');
SELECT t.move('c4a40000-0000-4000-8000-0000000000c4', 'withdrawn', 'fact_corrected');

-- 不改状态的修改：必须写原因，并且每次都留痕（含快照）
SELECT t.throws_like($$UPDATE insight.claim SET confidence = 0.75 WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '不允许的状态流转或修改', '不改状态的修改也必须写原因（沿用上次的 created 被拒）');
UPDATE insight.claim SET confidence = 0.75, change_reason = 'revised' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT confidence = 0.75 AND reason = 'revised' FROM insight.claim_history
              WHERE claim_id = 'c1a10000-0000-4000-8000-0000000000c1' ORDER BY at DESC LIMIT 1),
            '修改置信度写一行历史（原因 revised，带快照）');
SELECT t.throws_like($$UPDATE insight.claim SET visibility = '{agent,student}', change_reason = 'revised' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', 'visibility_changed', '改可见范围必须用原因 visibility_changed');
UPDATE insight.claim SET visibility = '{agent,student,guardian}', change_reason = 'visibility_changed' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1';
SELECT t.ok((SELECT visibility = '{agent,student,guardian}' AND reason = 'visibility_changed' FROM insight.claim_history
              WHERE claim_id = 'c1a10000-0000-4000-8000-0000000000c1' ORDER BY at DESC LIMIT 1),
            '可见范围的变化记入历史快照');
SELECT t.throws_like($$UPDATE insight.claim SET expires_at = now() + interval '100 years', change_reason = 'revised' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '最长有效期', '续期不能超过模板最长有效期（不能靠延长到期时间绕开待验证）');
-- 原因写在行里、由写入前触发器校验：语句结束前改会话变量也影响不了历史（安全审查 A-1）
SELECT t.throws_like($$UPDATE insight.claim SET change_reason = 'forged_reason_x', confidence = 0.7 WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '不允许的状态流转或修改', '任意自由文本不能作为原因写入');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim_history WHERE reason NOT IN ('created', 'evidence_threshold', 'expired', 'dispute',
            'exam_deviation', 'verification_supported', 'verification_refuted', 'fact_corrected', 'revised', 'visibility_changed')),
            '历史里的原因全部来自白名单');

SELECT t.throws($$UPDATE insight.claim SET target_id = 'K-QF-0399', change_reason = 'revised' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                '42501', '洞察服务也不能改判断的对象（没有列权限）');
SELECT t.throws($$DELETE FROM insight.claim$$, '42501', '洞察服务不能删除判断');
SELECT t.throws($$INSERT INTO insight.claim_history (claim_id, to_status, reason, visibility, value, evidence_ids, confidence, expires_at)
                  VALUES ('c1a10000-0000-4000-8000-0000000000c1', 'established', 'revised', '{agent}', '{}', '{}', 0.1, now() + interval '1 day')$$,
                '42501', '洞察服务不能自己写历史（防止伪造）');
SELECT t.throws($$UPDATE insight.claim_history SET reason = 'revised'$$, '42501', '洞察服务不能改历史');
SELECT t.throws($$DELETE FROM insight.claim_history$$, '42501', '洞察服务不能删历史');
SELECT t.throws($$INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl) VALUES ('test.x.y', 1, 'node', '{agent}', '1 day')$$,
                '42501', '洞察服务不能登记模板');
SELECT t.throws($$SELECT count(*) FROM platform.mastery_estimate$$, '42501', '洞察服务读不到 platform 推断表（依据只经 authz 函数核对）');
RESET ROLE;

-- 不可变列即使由表属主改也被触发器拦下；历史表的检查约束拦住非白名单原因
SELECT t.throws_like($$UPDATE insight.claim SET target_id = 'K-QF-0399', change_reason = 'revised' WHERE id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '23514', '不能修改', '判断的对象不可变（触发器）');
SELECT t.throws_like($$UPDATE insight.claim_history SET reason = 'x'$$, '42501', '只能追加', '历史不能修改（触发器）');
SELECT t.throws_like($$DELETE FROM insight.claim_history WHERE claim_id = 'c1a10000-0000-4000-8000-0000000000c1'$$,
                     '42501', '不能单独删除', '判断还在时历史不能单独删除（触发器）');
SELECT t.throws_like($$INSERT INTO insight.claim_history (claim_id, from_status, to_status, reason, visibility, value, evidence_ids, confidence, expires_at)
                       VALUES ('c1a10000-0000-4000-8000-0000000000c1', 'established', 'refuted', 'verification_refuted', '{agent}', '{}', '{}', 0.1, now())$$,
                     '23514', 'history_reason_allowed', '历史行的流转与原因也必须在白名单里（约束兜底）');

-- ================= 模板退役：使用它的判断仍能撤下，前端与学科服务不再看到 =================
SET ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, evidence_ids,
                           basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at)
SELECT 'c6a60000-0000-4000-8000-0000000000c6', student_id, 'aya', 'node', 'K-QF-0304', 'test.node.to_retire', 1, 0.6, evidence_ids,
       basis_kind, basis_id, 'inferred', 'algo:bkt-v0@0.1.0', 'established', 'created', '{agent,student,guardian}', now() + interval '10 days'
  FROM insight.claim WHERE id = 'c1a10000-0000-4000-8000-0000000000c1';
RESET ROLE;
UPDATE insight.claim_template SET status = 'retired' WHERE template_key = 'test.node.to_retire';
SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c6a60000-0000-4000-8000-0000000000c6'), '模板退役后，家长看不到用它的判断');
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c6a60000-0000-4000-8000-0000000000c6'), '模板退役后，学科服务看不到用它的判断');
RESET ROLE;
SET ROLE starbuddy_insight;
SELECT t.throws_like($$UPDATE insight.claim SET confidence = 0.1, change_reason = 'revised' WHERE id = 'c6a60000-0000-4000-8000-0000000000c6'$$,
                     '23514', '已退役', '模板退役后不能再修改为可展示的内容');
SELECT t.move('c6a60000-0000-4000-8000-0000000000c6', 'withdrawn', 'fact_corrected');
SELECT t.ok((SELECT status FROM insight.claim WHERE id = 'c6a60000-0000-4000-8000-0000000000c6') = 'withdrawn',
            '模板退役后，用它的判断仍能撤回（事实更正后能撤下）');
RESET ROLE;

-- ================= 其他应用角色不能写：先把行本身准备合法（原因 created），权限是唯一的拦截点 =================
SET ROLE starbuddy_learning;
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  source_type, status, change_reason, visibility, expires_at)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  'self', 'hypothesis', 'created', '{agent}', now() + interval '1 day')$$,
                '42501', '学科服务不能写判断（行本身合法，只被权限拦下）');
SELECT t.throws($$UPDATE insight.claim SET confidence = 0.1, change_reason = 'revised'$$, '42501', '学科服务不能改判断');
SELECT t.throws($$SELECT * FROM insight.claim_history$$, '42501', '学科服务读不到判断历史');
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM insight.claim WHERE id::text LIKE 'c_a_0000-%')
            = '{c1a10000-0000-4000-8000-0000000000c1,c2a20000-0000-4000-8000-0000000000c2}',
            'Agent 视图只看到可见范围含 agent 的非终态判断（推翻、撤回、模板退役的看不到）');
RESET ROLE;

SET ROLE starbuddy_bff;
SELECT t.as_account('a0000000-0000-4000-8000-00000000000a');
SELECT t.throws($$INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                  source_type, status, change_reason, visibility, expires_at)
                  VALUES ('c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
                  'self', 'hypothesis', 'created', '{agent}', now() + interval '1 day')$$,
                '42501', 'BFF 不能写判断（行本身合法，只被权限拦下）');
SELECT t.throws($$SELECT * FROM insight.claim_history$$, '42501', 'BFF 读不到判断历史');
SELECT t.ok((SELECT array_agg(template_key ORDER BY template_key) FROM insight.claim_template)
            = '{test.metric.agent_only,test.node.mastery}', 'BFF 只看得到已审核的模板');
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

-- 只含 student 的判断家长看不到；已过期的判断前端与学科服务都看不到
BEGIN;
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                           source_type, status, change_reason, visibility, expires_at)
VALUES ('c5a50000-0000-4000-8000-0000000000c5', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0303', 'test.node.mastery', 1, 0.5,
        'self', 'hypothesis', 'created', '{agent,student}', now() + interval '1 day'),
       ('c7a70000-0000-4000-8000-0000000000c7', 'c1000000-0000-4000-8000-0000000000c1', 'aya', 'node', 'K-QF-0305', 'test.node.mastery', 1, 0.5,
        'self', 'hypothesis', 'created', '{agent,student,guardian}', now() + interval '1 day');
RESET ROLE;
-- 模拟已过期（以超级用户关掉触发器改到期时间，只为造数据）
ALTER TABLE insight.claim DISABLE TRIGGER USER;
UPDATE insight.claim SET created_at = now() - interval '3 days', expires_at = now() - interval '1 day' WHERE id = 'c7a70000-0000-4000-8000-0000000000c7';
ALTER TABLE insight.claim ENABLE TRIGGER USER;
SET LOCAL ROLE starbuddy_bff;
SELECT set_config('app.account_id', 'a0000000-0000-4000-8000-00000000000a', true);
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c5a50000-0000-4000-8000-0000000000c5'), '只含 student 的判断，家长看不到');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c7a70000-0000-4000-8000-0000000000c7'), '已过期的判断，家长看不到');
SELECT set_config('app.account_id', 'a1000000-0000-4000-8000-0000000000a1', true);
SELECT t.ok(EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c5a50000-0000-4000-8000-0000000000c5'), '只含 student 的判断，学生本人看得到');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c7a70000-0000-4000-8000-0000000000c7'), '已过期的判断，学生本人也看不到');
SET LOCAL ROLE starbuddy_learning;
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE id = 'c7a70000-0000-4000-8000-0000000000c7'), '已过期的判断，学科服务看不到');
ROLLBACK;

-- ================= 删除学生档案时判断与历史一起删除 =================
BEGIN;
INSERT INTO platform.account (id, kind) VALUES ('a9000000-0000-4000-8000-0000000000a9', 'student');
INSERT INTO platform.student_profile (id, account_id, household_id, grade, region)
VALUES ('c9000000-0000-4000-8000-0000000000c9', 'a9000000-0000-4000-8000-0000000000a9', 'd1000000-0000-4000-8000-0000000000d1', 9, 'nanjing');
SET LOCAL ROLE starbuddy_insight;
INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                           source_type, status, change_reason, visibility, expires_at)
VALUES ('c9a90000-0000-4000-8000-0000000000c9', 'c9000000-0000-4000-8000-0000000000c9', 'aya', 'node', 'K-QF-0301', 'test.node.mastery', 1, 0.5,
        'self', 'hypothesis', 'created', '{agent}', now() + interval '1 day');
RESET ROLE;
DELETE FROM platform.student_profile WHERE id = 'c9000000-0000-4000-8000-0000000000c9';
SELECT t.ok(NOT EXISTS (SELECT 1 FROM insight.claim WHERE student_id = 'c9000000-0000-4000-8000-0000000000c9')
            AND NOT EXISTS (SELECT 1 FROM insight.claim_history WHERE claim_id = 'c9a90000-0000-4000-8000-0000000000c9'),
            '删除学生档案时，判断与判断历史一起删除');
ROLLBACK;

-- 状态机矩阵留下的测试判断（只含 agent）清掉：以超级用户级联删除，不影响 80 的可见性矩阵
ALTER TABLE insight.claim_history DISABLE TRIGGER USER;
DELETE FROM insight.claim_history WHERE claim_id IN (SELECT id FROM insight.claim WHERE id::text NOT LIKE 'c_a_0000-%');
ALTER TABLE insight.claim_history ENABLE TRIGGER USER;
DELETE FROM insight.claim WHERE id::text NOT LIKE 'c_a_0000-%';
