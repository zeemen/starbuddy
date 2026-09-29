-- 0024 打标来源与低置信候选队列（specs/kge-02-atoms-tagging/spec.md 设计要点 3）。全部合成：地图版本、节点、题目 ID 都是测试用的。
-- 整个文件在一个事务里，结束回滚。用 76 号测试的 t.as_staff(账号, SQL) 以 staff 身份在 starbuddy_review 下执行。
-- k1 = 76 号测试已发布的数学 0.1.0（节点 MATH.FUNC.QUAD.GRAPH、VERTEX_FORM）；kd = 这里新建的草稿版本。
\set k1 '''ca100000-0000-4000-8000-00000000ca01'''
\set kd '''ca500000-0000-4000-8000-00000000ca05'''
\set sf '''5f000000-0000-4000-8000-00000000005f'''
\set sdis '''5d000000-0000-4000-8000-00000000005d'''
\set gA '''a0000000-0000-4000-8000-00000000000a'''
BEGIN;

-- 以某个应用角色执行一句（79e 里的同名函数随其事务回滚了，这里重建）
CREATE FUNCTION t.as_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.as_role(text, text) TO PUBLIC;
CREATE FUNCTION t.decide(p_staff uuid, p_candidate uuid, p_decision text) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff(p_staff, format('SELECT kge.decide_tag_candidate(%L, %L)', p_candidate, p_decision)) $$;
GRANT EXECUTE ON FUNCTION t.decide(uuid, uuid, text) TO PUBLIC;
-- 为测试新增的合成题目（题目身份只在 platform.item）
INSERT INTO platform.item (subject, item_id, curriculum_version_id, skill_local_id, admission)
SELECT 'aya', x, i.curriculum_version_id, i.skill_local_id, 'source_unverified'
  FROM platform.item i, unnest(ARRAY['synthetic-tag-01', 'synthetic-tag-02', 'synthetic-tag-03']) x
 WHERE i.subject = 'aya' AND i.item_id = 'synthetic-0301-07';

SET ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (:kd, 'aya', 2022, '0.9.0');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
  (:kd, 'MATH.FUNC', 'domain', NULL, NULL, '函数'), (:kd, 'MATH.FUNC.QUAD', 'topic', 'MATH.FUNC', NULL, '合成主题');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
  (:kd, 'MATH.FUNC.QUAD.GRAPH', 'node', 'MATH.FUNC.QUAD', 'kp', '合成节点甲', '{a}', '{b}', '{c}', '{understand}'),
  (:kd, 'MATH.FUNC.QUAD.VERTEX_FORM', 'node', 'MATH.FUNC.QUAD', 'kp', '合成节点乙', '{a}', '{b}', '{c}', '{apply}'),
  (:kd, 'MATH.FUNC.QUAD.AXIS', 'node', 'MATH.FUNC.QUAD', 'kp', '合成节点丙', '{a}', '{b}', '{c}', '{apply}'),
  (:kd, 'MATH.FUNC.QUAD.ROOT', 'node', 'MATH.FUNC.QUAD', 'kp', '合成节点丁', '{a}', '{b}', '{c}', '{apply}');

-- ================= item_tag 的打标来源 =================
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, source_kind, tag_confidence, tag_source, tagger_version)
VALUES (:kd, 'aya', 'synthetic-tag-01', 'MATH.FUNC.QUAD.GRAPH', '{MATH.FUNC.QUAD.GRAPH}', 'original', 0.9, 'rule', 'aya-tagger-rules@0.1.0');
SELECT t.ok((SELECT tag_source = 'rule' AND tag_confidence = 0.9 FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-01'),
            '题目标签记下打标来源、置信度与打标器版本');
SELECT t.throws(format($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind, tag_confidence, tag_source)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'original', 0.9, 'rule')$$, :kd),
                '23514', '有来源就必须有打标器版本');
SELECT t.throws(format($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind, tag_confidence, tag_source, tagger_version)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'original', 0.7, 'model', 'aya-tagger@0.1.0')$$, :kd),
                '23514', '模型层打的标必须带模型版本');
SELECT t.throws(format($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind, tag_confidence, tag_source,
                         tagger_version, tag_model_version) VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'original', 0.9, 'rule',
                         'aya-tagger@0.1.0', 'model:fake-a/fake-tagger@0.0.0')$$, :kd),
                '23514', '规则层打的标不能带模型版本');
SELECT t.throws(format($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind, tag_confidence, tag_source, tagger_version)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'original', 1.2, 'rule', 'aya-tagger@0.1.0')$$, :kd),
                '23514', '置信度在 0–1 之间');

-- ================= 候选：只有知识服务能写，只能以待处理写入 =================
INSERT INTO kge.tag_candidate (id, map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, model_version, reason_code, grade)
VALUES ('cc000001-0000-4000-8000-0000000000c1', :kd, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.VERTEX_FORM', 'main', 'rule', 0.6,
        'aya-tagger-rules@0.1.0', NULL, 'rule_chapter_topic', 'B'),
       ('cc000002-0000-4000-8000-0000000000c2', :kd, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.AXIS', 'main', 'model', 0.5,
        'aya-tagger@0.1.0', 'model:fake-a/fake-tagger@0.0.0', 'model_choice', 'B'),
       ('cc000003-0000-4000-8000-0000000000c3', :kd, 'aya', 'synthetic-tag-01', 'MATH.FUNC.QUAD.AXIS', 'secondary', 'vector', 0.55,
        'aya-tagger@0.1.0', NULL, 'vector_similarity', 'B'),
       ('cc000004-0000-4000-8000-0000000000c4', :k1, 'aya', 'synthetic-tag-03', 'MATH.FUNC.QUAD.GRAPH', 'main', 'rule', 0.6,
        'aya-tagger-rules@0.1.0', NULL, 'rule_explicit_ref', 'B'),
       ('cc000005-0000-4000-8000-0000000000c5', :kd, 'aya', 'synthetic-tag-03', 'MATH.FUNC.QUAD.GRAPH', 'main', 'rule', 0.6,
        'aya-tagger-rules@0.1.0', NULL, 'rule_explicit_ref', 'B');
SELECT t.ok((SELECT count(*) FROM kge.tag_candidate WHERE status = 'open') = 5, '知识服务写入候选，默认待处理');
SELECT t.throws(format($$INSERT INTO kge.tag_candidate (map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code,
                         grade, status, decided_at) VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'main', 'rule', 0.6,
                         'aya-tagger-rules@0.2.0', 'x', 'B', 'accepted', now())$$, :kd),
                '42501', '知识服务不能写已处理的候选');
SELECT t.throws($$UPDATE kge.tag_candidate SET status = 'accepted' WHERE id = 'cc000001-0000-4000-8000-0000000000c1'$$,
                '42501', '知识服务不能改候选结论');
SELECT t.throws(format($$INSERT INTO kge.tag_candidate (map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code, grade)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.NOPE', 'main', 'rule', 0.6, 'aya-tagger-rules@0.1.0', 'x', 'B')$$, :kd),
                '23503', '候选节点必须是本版本已有节点');
SELECT t.throws(format($$INSERT INTO kge.tag_candidate (map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code, grade)
                         VALUES (%L, 'aya', 'synthetic-nope', 'MATH.FUNC.QUAD.GRAPH', 'main', 'rule', 0.6, 'aya-tagger-rules@0.1.0', 'x', 'B')$$, :kd),
                '23503', '候选题目必须是 platform.item 已登记的题目');
SELECT t.throws(format($$INSERT INTO kge.tag_candidate (map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code, grade)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'main', 'model', 0.6, 'aya-tagger@0.1.0', 'x', 'B')$$, :kd),
                '23514', '模型层候选必须带模型版本');
SELECT t.throws(format($$INSERT INTO kge.tag_candidate (map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code, grade)
                         VALUES (%L, 'aya', 'synthetic-tag-02', 'MATH.FUNC.QUAD.GRAPH', 'main', 'rule', 0.6, 'aya-tagger-rules@0.1.0', 'Free Text', 'B')$$, :kd),
                '23514', '理由只能是编码，不能是自由文本');
SELECT t.throws($$DELETE FROM kge.tag_candidate$$, '42501', '候选不能删除');
RESET ROLE;

SELECT t.throws($$SELECT t.as_role('starbuddy_learning', 'SELECT count(*)::text FROM kge.tag_candidate')$$, '42501', '学科服务读不到候选');
SELECT t.throws($$SELECT t.as_role('starbuddy_bff', 'SELECT count(*)::text FROM kge.tag_candidate')$$, '42501', 'BFF 读不到候选');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge', 'SELECT kge.decide_tag_candidate(''cc000001-0000-4000-8000-0000000000c1'', ''accepted'')')$$,
                '42501', '知识服务不能处理候选（提交方不能自己签字）');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'kge' AND table_name = 'tag_candidate'
                          AND (column_name LIKE '%student%' OR column_name LIKE '%account%' OR data_type = 'uuid' AND column_name <> 'id'
                               AND column_name <> 'map_version_id')),
            '候选表没有学生键（公共层，规则 13）');

-- ================= 教研处理 =================
SELECT t.throws(format('SELECT t.decide(%L, %L, %L)', :gA, 'cc000001-0000-4000-8000-0000000000c1', 'accepted'), '42501', '非 staff 不能处理候选');
SELECT t.throws(format('SELECT t.decide(%L, %L, %L)', :sdis, 'cc000001-0000-4000-8000-0000000000c1', 'accepted'), '42501', '已停用的 staff 不能处理候选');
SELECT t.throws(format('SELECT t.decide(%L, %L, %L)', :sf, 'cc000001-0000-4000-8000-0000000000c1', 'maybe'), '22023', '结论只能是接受或驳回');
SELECT t.throws(format('SELECT t.decide(%L, gen_random_uuid(), %L)', :sf, 'accepted'), 'P0002', '候选不存在');
-- 草稿版本：接受主考点候选，该题还没有打标 → 写回 item_tag（来源 human、置信度 1）
SELECT t.ok(t.decide(:sf, 'cc000001-0000-4000-8000-0000000000c1', 'accepted') = 'written', '草稿版本上接受主考点候选写回打标');
SELECT t.ok((SELECT main_node_id = 'MATH.FUNC.QUAD.VERTEX_FORM' AND tag_source = 'human' AND tag_confidence = 1
                    AND tagger_version = 'aya-tagger-rules@0.1.0' AND tag_review_status = 'pending'
               FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-02'), '写回的打标来源是 human、仍待版本审核');
SELECT t.ok((SELECT status = 'accepted' AND decided_at IS NOT NULL FROM kge.tag_candidate WHERE id = 'cc000001-0000-4000-8000-0000000000c1')
            AND (SELECT account_id = :sf::uuid AND decision = 'accepted' FROM platform.tag_candidate_signoff
                  WHERE tag_candidate_id = 'cc000001-0000-4000-8000-0000000000c1'),
            '记下结论与时间；处理人记在平台层签字表（公共层不挂账号 ID）');
SELECT t.throws($$SELECT t.as_role('starbuddy_review', 'SELECT count(*)::text FROM platform.tag_candidate_signoff')$$, '42501', '审核台读不到签字表');
SELECT t.throws($$UPDATE platform.tag_candidate_signoff SET decision = 'rejected'$$, '42501', '签字记录只追加，不能改');
SELECT t.throws(format('SELECT t.decide(%L, %L, %L)', :sf, 'cc000001-0000-4000-8000-0000000000c1', 'rejected'), '23514', '已处理的候选不能再处理');
-- 同一题的另一个主考点候选：已有打标 → 只记结论，不覆盖
SELECT t.ok(t.decide(:sf, 'cc000002-0000-4000-8000-0000000000c2', 'accepted') = 'recorded'
            AND (SELECT main_node_id FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-02') = 'MATH.FUNC.QUAD.VERTEX_FORM',
            '已有打标时接受另一个主考点候选只记结论，不覆盖');
-- 次要节点：追加到已有打标的节点数组
SELECT t.decide(:sf, 'cc000003-0000-4000-8000-0000000000c3', 'accepted') AS dec3 \gset
SELECT t.ok(:'dec3' = 'written', '接受次要节点候选返回 written（实际 ' || :'dec3' || '）');
SELECT t.ok((SELECT node_ids::text FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-01')
                = '{MATH.FUNC.QUAD.GRAPH,MATH.FUNC.QUAD.AXIS}',
            '接受次要节点候选时追加到已有打标（实际 ' || (SELECT node_ids::text FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-01') || '）');
-- 节点数上限 3（小题 1–3 个节点，KGE V1.1 :745）：第三个追加，第四个只记结论
SET ROLE starbuddy_kge;
INSERT INTO kge.tag_candidate (id, map_version_id, subject, item_id, node_id, role, layer, confidence, tagger_version, reason_code, grade) VALUES
  ('cc000006-0000-4000-8000-0000000000c6', :kd, 'aya', 'synthetic-tag-01', 'MATH.FUNC.QUAD.VERTEX_FORM', 'secondary', 'rule', 0.6, 'aya-tagger-rules@0.1.0', 'rule_chapter_topic', 'B'),
  ('cc000007-0000-4000-8000-0000000000c7', :kd, 'aya', 'synthetic-tag-01', 'MATH.FUNC.QUAD.ROOT', 'secondary', 'rule', 0.6, 'aya-tagger-rules@0.1.0', 'rule_chapter_topic', 'B');
RESET ROLE;
-- 分开执行：同一条语句里多次调用的求值顺序不确定
SELECT t.ok(t.decide(:sf, 'cc000006-0000-4000-8000-0000000000c6', 'accepted') = 'written', '第三个节点追加');
SELECT t.ok(t.decide(:sf, 'cc000007-0000-4000-8000-0000000000c7', 'accepted') = 'recorded'
            AND (SELECT cardinality(node_ids) FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-01') = 3,
            '一道题最多 3 个节点：超出时接受只记结论');
-- 已发布版本：只记结论（打标变更走 0019 的审核记录）
SELECT t.ok(t.decide(:sf, 'cc000004-0000-4000-8000-0000000000c4', 'accepted') = 'recorded'
            AND NOT EXISTS (SELECT 1 FROM kge.item_tag WHERE map_version_id = :k1 AND item_id = 'synthetic-tag-03'),
            '已发布版本上接受候选只记结论，不写回');
-- 驳回：只记结论
SELECT t.ok(t.decide(:sf, 'cc000005-0000-4000-8000-0000000000c5', 'rejected') = 'recorded'
            AND NOT EXISTS (SELECT 1 FROM kge.item_tag WHERE map_version_id = :kd AND item_id = 'synthetic-tag-03'),
            '驳回候选不写打标');
SELECT t.throws($$SELECT t.as_role('starbuddy_review', 'UPDATE kge.tag_candidate SET status = ''open''')$$, '42501', '审核台不能直接改候选');

ROLLBACK;
