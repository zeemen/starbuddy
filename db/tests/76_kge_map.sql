-- 公共知识层（0011，specs/kge-schema-v0.9/spec.md 第 7 节）：约束、骨架冻结、发布前校验与发布、写入方与读取范围。
-- 合成节点（CLAUDE.md 规则 8；不导入任何真实地图）。留下的数据不含学生键，80_attack_surface 按豁免表处理。

-- 地图版本：k1 = 数学 0.1.0（干净，先发布）；kb = 数学 0.2.0（七类问题，修好后发布）；kw = 语文 0.1.0；kx = 数学 0.3.0（留作草稿）
\set k1 '''b1000000-0000-4000-8000-0000000000b1'''
\set kb '''b2000000-0000-4000-8000-0000000000b2'''
\set kw '''b3000000-0000-4000-8000-0000000000b3'''
\set kx '''b4000000-0000-4000-8000-0000000000b4'''

-- 测试辅助：某版本的阻断问题与警告，排好序的 “检查名:对象” 列表
CREATE FUNCTION t.kge_issues(p_version uuid, p_severity text) RETURNS text[] LANGUAGE sql AS $$
  SELECT coalesce(array_agg(c.check_name || ':' || c.object_id ORDER BY c.check_name, c.object_id), '{}')
    FROM kge.validate_map_version(p_version) c WHERE c.severity = p_severity $$;
GRANT EXECUTE ON FUNCTION t.kge_issues(uuid, text) TO PUBLIC;

-- ================= 版本 =================
SET ROLE starbuddy_kge;
INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES
  (:k1, 'aya', 2022, '0.1.0'), (:kb, 'aya', 2022, '0.2.0'), (:kw, 'wenli', 2022, '0.1.0'), (:kx, 'aya', 2022, '0.3.0');
SELECT t.throws_like($$INSERT INTO kge.map_version (subject, standard_year, version, status, published_at) VALUES ('aya', 2022, '9.0.0', 'published', now())$$,
                     '23514', '只能以草稿新建', '地图版本不能直接以已发布新建');
SELECT t.throws($$INSERT INTO kge.map_version (subject, standard_year, version) VALUES ('aya', 2022, '1.0')$$,
                '23514', '版本号必须是 主.次.修订');
SELECT t.throws($$INSERT INTO kge.map_version (subject, standard_year, version) VALUES ('aya', 2022, '0.1.0')$$,
                '23505', '同一学科的版本号不能重复');
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'published' WHERE id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '42501', '只能经 kge.publish_map_version 发布', '知识服务不能绕过发布函数直接改为已发布');
SELECT t.throws($$UPDATE kge.map_version SET published_at = now() WHERE id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                '42501', '知识服务不能写发布时间');

-- ================= 节点：约束（每条一个反例，正例是下面成功的插入） =================
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels, error_type) VALUES
  (:k1, 'MATH.FUNC', 'domain', NULL, NULL, '函数', '{}', '{}', '{}', '{}', NULL),
  (:k1, 'MATH.FUNC.QUAD', 'topic', 'MATH.FUNC', NULL, '二次函数', '{}', '{}', '{}', '{}', NULL),
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'node', 'MATH.FUNC.QUAD', 'kp', '顶点式', '{配方化顶点式}', '{一般式求根}', '{synthetic-0301-07}', '{understand,apply}', NULL),
  (:k1, 'MATH.FUNC.QUAD.GRAPH', 'node', 'MATH.FUNC.QUAD', 'kp', '图像与性质', '{开口与对称轴}', '{平移作图}', '{synthetic-0301-08}', '{understand}', NULL),
  (:k1, 'MATH.FUNC.QUAD.SIGN_SLIP', 'node', 'MATH.FUNC.QUAD', 'pitfall', '配方时漏变号', '{}', '{}', '{}', '{}', 'computation_or_grammar'),
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM.SIGN', 'detail', 'MATH.FUNC.QUAD.VERTEX_FORM', 'pitfall', '顶点坐标符号', '{}', '{}', '{}', '{}', 'knowledge');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = :k1) = 6, '知识服务可以写节点（四个层级、易错点、细目）');

RESET ROLE;
CREATE FUNCTION t.kge_node(p_id text, p_level text, p_parent text, p_kind text, p_cog text[] DEFAULT '{}', p_err text DEFAULT NULL,
                           p_version uuid DEFAULT 'b1000000-0000-4000-8000-0000000000b1') RETURNS void LANGUAGE sql AS $$
  INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, cognitive_levels, error_type)
  VALUES (p_version, p_id, p_level, p_parent, p_kind, '合成节点', p_cog, p_err::platform.cross_subject_error) $$;
GRANT EXECUTE ON FUNCTION t.kge_node(text, text, text, text, text[], text, uuid) TO PUBLIC;
SET ROLE starbuddy_kge;
SELECT t.throws($$SELECT t.kge_node('math.func.quad.x', 'node', 'MATH.FUNC.QUAD', 'kp')$$, '23514', '节点 ID 必须全大写（正则）');
SELECT t.throws($$SELECT t.kge_node('MATH.FUNC.QUAD.X-Y', 'node', 'MATH.FUNC.QUAD', 'kp')$$, '23514', '节点 ID 段内只能是字母、数字、下划线');
SELECT t.throws_like($$SELECT t.kge_node('CHN.FUNC', 'domain', NULL, NULL)$$, '23514', '学科前缀', '节点 ID 的学科前缀必须与地图版本一致');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD2', 'node', 'MATH.FUNC', 'kp')$$, '23514', 'node_level_depth', '层级与 ID 段数必须一致');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC', 'kp')$$, '23514', 'node_parent_is_prefix', '上一级必须是 ID 去掉最后一段');
SELECT t.throws($$SELECT t.kge_node('MATH.GEOM.TRI', 'topic', 'MATH.GEOM', NULL)$$, '23503', '上一级节点必须存在于同一地图版本');
SELECT t.throws_like($$SELECT t.kge_node('MATH.GEOM', 'domain', NULL, 'kp')$$, '23514', 'node_kind_by_level', '领域与主题不带类型');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC.QUAD', NULL)$$, '23514', 'node_kind_by_level', '知识节点必须有类型');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.GRAPH.AXIS', 'detail', 'MATH.FUNC.QUAD.GRAPH', 'kp')$$, '23514', 'node_detail_kind',
                     '细目只能是易错点或考点');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOT_SLIP', 'node', 'MATH.FUNC.QUAD', 'pitfall')$$, '23514', 'node_pitfall_error_type',
                     '易错点必须写错因大类');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC.QUAD', 'kp', '{}', 'knowledge')$$, '23514', 'node_pitfall_error_type',
                     '非易错点不写错因大类');
SELECT t.throws($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOT_SLIP', 'node', 'MATH.FUNC.QUAD', 'pitfall', '{}', 'careless')$$, '22P02',
                '错因大类只能是七大类之一');
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.COMPLETE', 'node', 'MATH.FUNC.QUAD', 'method', '{apply}')$$, '23514', 'node_cognitive_levels_kind',
                     '方法节点不带认知层次（不参与掌握度计算）');
SELECT t.throws($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC.QUAD', 'kp', '{memorize}')$$, '23514', '认知层次只能取课标四级');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = :k1) = 6, '被拒绝的节点一个都没有写进去');

-- ================= 边 =================
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
  (:kw, 'CHN.READ', 'domain', NULL, NULL, '阅读'), (:kw, 'CHN.READ.NARR', 'topic', 'CHN.READ', NULL, '记叙文');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels) VALUES
  (:kw, 'CHN.READ.NARR.CHARACTER', 'node', 'CHN.READ.NARR', 'kp', '人物形象', '{概括人物特点}', '{修辞手法}', '{synthetic-essay-01}', '{understand}');
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength) VALUES
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', :k1, 'MATH.FUNC.QUAD.GRAPH', 'prerequisite', 'strong'),
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', :k1, 'MATH.FUNC.QUAD.SIGN_SLIP', 'has_pitfall', 'strong'),
  (:kw, 'CHN.READ.NARR.CHARACTER', :k1, 'MATH.FUNC.QUAD.GRAPH', 'related', 'weak');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_edge) = 3, '边：同版本前置边、易错点边、跨学科关联边都能写');
SELECT t.throws_like($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'related')$$,
                     '23514', 'edge_no_self_loop', '边不能自环');
SELECT t.throws_like($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                       VALUES ('b3000000-0000-4000-8000-0000000000b3', 'CHN.READ.NARR.CHARACTER', 'b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'prerequisite')$$,
                     '23514', 'edge_same_version', '前置边不能跨学科或跨地图版本');
SELECT t.throws($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.NOPE', 'related')$$,
                '23503', '边的端点必须是已有节点');
SELECT t.throws($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.VERTEX_FORM', 'derives')$$,
                '23514', '边类型只能是七种之一');

-- ================= 投影、能力视图挂接、旧编码迁移 =================
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to,
                                     quote_text, verified_by, verified_at, mapping_state) VALUES
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'bubian-2024', 2024, '2024-1', '九上', '第二十二章', 32, 34, '合成原句：把一般式配方成顶点式。', 'staff-001', now(), 'verified');
SELECT t.ok((SELECT quote_hash = sha256(convert_to('合成原句：把一般式配方成顶点式。', 'UTF8')) FROM kge.textbook_projection WHERE node_id = 'MATH.FUNC.QUAD.VERTEX_FORM'),
            '教材原句的哈希由库计算（UTF-8 字节的 sha256）');
SELECT t.throws($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text, quote_hash)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 35, 36, '合成原句', '\x00')$$,
                '428C9', '写入方不能自己填原句哈希');
SELECT t.throws($$UPDATE kge.textbook_projection SET quote_hash = '\x00'$$, '428C9', '写入方不能改原句哈希');
SELECT t.throws_like($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text, mapping_state)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 35, 36, '合成原句', 'verified')$$,
                     '23514', 'projection_verified_by', '投影标为已核对时必须写核对人与核对时间');
SELECT t.throws_like($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 36, 35)$$,
                     '23514', 'projection_page_range', '投影的止页不能早于起页');
INSERT INTO kge.region_projection (map_version_id, node_id, region, exam_year, weight, typical_slots, source_ref) VALUES
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'nanjing', 2027, 0.300, '{选择题第 5 题}', '合成来源');
INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight) VALUES (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'aya', 'K-QF-0301', 1);
SELECT t.throws_like($$INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                       VALUES ('b3000000-0000-4000-8000-0000000000b3', 'CHN.READ.NARR.CHARACTER', 'aya', 'K-QF-0301', 1)$$,
                     '23514', '学科与地图版本不符', '能力视图挂接的学科必须与地图版本一致');
SELECT t.throws($$INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'MATH.FUNC.QUAD.GRAPH', 'aya', 'K-NOPE-0000', 1)$$,
                '23503', '能力视图挂接必须指向已登记的 platform.skill_node');
INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule) VALUES
  (:k1, 'aya', 'K-QF-0301', 'MATH.FUNC.QUAD.VERTEX_FORM', 'rename'), (:k1, 'aya', 'K-QF-0399', NULL, 'retire');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'K-QF-0302', NULL, 'rename')$$,
                     '23514', 'id_migration_retire', '除撤销外，旧编码迁移必须指向新节点');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'K-QF-0302', 'MATH.FUNC.QUAD.GRAPH', 'retire')$$,
                     '23514', 'id_migration_retire', '撤销的旧编码不能有新节点');
SELECT t.throws($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'K-QF-0399', NULL, 'retire')$$,
                '23505', '同一旧编码的同一去向只登记一次（撤销也算）');

-- ================= 原子：题目标签、采分点、时政 =================
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, prior_difficulty, source_kind) VALUES
  (:k1, 'aya', 'synthetic-0301-07', 'MATH.FUNC.QUAD.VERTEX_FORM', '{MATH.FUNC.QUAD.GRAPH}', 'apply', 0.4, 'original');
SELECT t.throws_like($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'wenli', 'synthetic-essay-01', 'MATH.FUNC.QUAD.GRAPH', 'original')$$,
                     '23514', '学科与地图版本不符', '题目标签的学科必须与地图版本一致');
SELECT t.throws($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'synthetic-nope', 'MATH.FUNC.QUAD.GRAPH', 'original')$$,
                '23503', '题目标签只能挂在 platform.item 已登记的题目上（不做第二份题目登记）');
SELECT t.throws($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, source_kind)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'synthetic-0301-08', 'MATH.FUNC.QUAD.GRAPH', '{MATH.FUNC.QUAD.NOPE}', 'original')$$,
                '23503', '题目标签的节点数组只能引用本版本已有节点');
INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, score, node_ids, required_logic) VALUES
  (:k1, 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 2, '{MATH.FUNC.QUAD.VERTEX_FORM}', '合成采分点：写出顶点坐标');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, score, node_ids)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'synthetic-0301-07', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23502', '采分点必须带 Rubric 版本');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, score, node_ids)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'synthetic-0301-07', 'no-version', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23514', '采分点的 Rubric 版本必须是 名称@版本');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, score, node_ids)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 1, '{MATH.FUNC.QUAD.NOPE}')$$,
                '23503', '采分点只能引用本版本已有节点');
SELECT t.throws($$INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', '{MATH.FUNC.QUAD.GRAPH}', '合成来源', '2027-03-01', '2027-03-01')$$,
                '23514', '时政的失效日期必须晚于生效日期');
SELECT t.throws($$INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from)
                  VALUES ('b1000000-0000-4000-8000-0000000000b1', '{MATH.FUNC.QUAD.GRAPH}', '合成来源', '2027-03-01')$$,
                '23502', '时政必须写失效日期');

-- ================= 成长治理 =================
INSERT INTO kge.map_change (id, subject, change_type, payload, status, reviewers, target_version) VALUES
  ('b5000000-0000-4000-8000-0000000000b5', 'aya', 'node_split', '{"from": "MATH.FUNC.QUAD.GRAPH"}', 'approved', '{staff-001,staff-002}', :kb);
SELECT t.throws_like($$INSERT INTO kge.map_change (subject, change_type, payload, status, reviewers) VALUES ('aya', 'node_merge', '{}', 'approved', '{staff-001}')$$,
                     '23514', 'map_change_two_reviewers', '结构性变更批准时至少两人确认');
SELECT t.throws_like($$INSERT INTO kge.map_change (subject, change_type, payload, status, reviewers) VALUES ('aya', 'node_merge', '{}', 'approved', '{staff-001,staff-001}')$$,
                     '23514', 'map_change_two_reviewers', '两位审核人不能是同一人');
SELECT t.throws_like($$UPDATE kge.map_change SET reviewers = '{staff-001}' WHERE id = 'b5000000-0000-4000-8000-0000000000b5'$$,
                     '23514', 'map_change_two_reviewers', '已批准的变更不能事后减到一人');
INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, reviewer, decided_at) VALUES
  ('knowledge_node', 'MATH.FUNC.QUAD.VERTEX_FORM', 'high', 'approved', 'staff-003', now());
SELECT t.throws_like($$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision) VALUES ('knowledge_node', 'X', 'high', 'approved')$$,
                     '23514', 'content_review_decided', '审核结论必须写审核人与时间');

-- ================= 发布前校验与发布：干净的 k1 =================
SELECT t.ok(t.kge_issues(:k1, 'error') = '{}', '干净的地图版本没有阻断问题');
SELECT t.ok(t.kge_issues(:k1, 'warning') = '{few_items:MATH.FUNC.QUAD.GRAPH,few_items:MATH.FUNC.QUAD.VERTEX_FORM}',
            '主考点少于 3 道题只报警告：' || array_to_string(t.kge_issues(:k1, 'warning'), ','));
SELECT t.ok(kge.publish_map_version(:k1) IS NOT NULL, '没有阻断问题时可以发布');
SELECT t.ok((SELECT status = 'published' AND published_at IS NOT NULL FROM kge.map_version WHERE id = :k1), '发布后状态与发布时间由发布函数写入');
SELECT t.throws_like($$SELECT kge.publish_map_version('b1000000-0000-4000-8000-0000000000b1')$$, '23514', '只能发布草稿', '已发布的版本不能再发布');

-- 骨架冻结：已发布版本的节点、边、投影、挂接、旧编码迁移不能新增或修改
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC.QUAD', 'kp')$$, '23514', '已发布或已撤下', '已发布版本不能加节点');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET name = '改名' WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1' AND id = 'MATH.FUNC.QUAD.GRAPH'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改节点');
SELECT t.throws_like($$UPDATE kge.knowledge_edge SET status = 'deprecated' WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改边');
SELECT t.throws_like($$UPDATE kge.textbook_projection SET page_to = 40 WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改教材投影');
SELECT t.throws_like($$UPDATE kge.region_projection SET weight = 0.9 WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改地区投影');
SELECT t.throws_like($$UPDATE kge.skill_link SET weight = 0.5 WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改能力视图挂接');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('b1000000-0000-4000-8000-0000000000b1', 'aya', 'K-QF-0302', 'MATH.FUNC.QUAD.GRAPH', 'rename')$$,
                     '23514', '已发布或已撤下', '已发布版本不能加旧编码迁移');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET map_version_id = 'b4000000-0000-4000-8000-0000000000b4'
                        WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1' AND id = 'MATH.FUNC.QUAD.VERTEX_FORM.SIGN'$$,
                     '23514', '已发布或已撤下', '已发布版本的节点不能挪到草稿版本');
SELECT t.throws_like($$UPDATE kge.map_version SET version = '0.1.1' WHERE id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '只能改状态', '已发布版本不能改版本号');
-- 原子不随骨架冻结：已发布版本上可以继续加题目标签
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind) VALUES
  (:k1, 'aya', 'synthetic-0301-08', 'MATH.FUNC.QUAD.GRAPH', 'original');
SELECT t.ok((SELECT count(*) FROM kge.item_tag WHERE map_version_id = :k1) = 2, '已发布版本上可以继续加题目标签（原子不冻结）');

-- ================= 发布前校验：kb 构造七类问题 =================
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
  (:kb, 'MATH.ALG', 'domain', NULL, NULL, '代数'), (:kb, 'MATH.ALG.T', 'topic', 'MATH.ALG', NULL, '合成主题');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, status) VALUES
  (:kb, 'MATH.ALG.T.X', 'node', 'MATH.ALG.T', 'kp', 'X', '{a}', '{b}', '{c}', 'active'),
  (:kb, 'MATH.ALG.T.Y', 'node', 'MATH.ALG.T', 'kp', 'Y', '{a}', '{b}', '{c}', 'active'),
  (:kb, 'MATH.ALG.T.Z', 'node', 'MATH.ALG.T', 'kp', 'Z', '{a}', '{b}', '{c}', 'active'),
  (:kb, 'MATH.ALG.T.C1', 'node', 'MATH.ALG.T', 'method', 'C1', '{}', '{}', '{}', 'active'),
  (:kb, 'MATH.ALG.T.C2', 'node', 'MATH.ALG.T', 'method', 'C2', '{}', '{}', '{}', 'active'),
  (:kb, 'MATH.ALG.T.O', 'node', 'MATH.ALG.T', 'kp', 'O', '{a}', '{b}', '{c}', 'active'),
  (:kb, 'MATH.ALG.T.M', 'node', 'MATH.ALG.T', 'viewpoint', 'M', '{a}', '{}', '{}', 'active'),
  (:kb, 'MATH.ALG.T.DEP', 'node', 'MATH.ALG.T', 'kp', 'DEP', '{a}', '{b}', '{c}', 'deprecated'),
  (:kb, 'MATH.ALG.T.DOC', 'node', 'MATH.ALG.T', 'material', 'DOC', '{}', '{}', '{}', 'active');
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type) VALUES
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.Y', 'prerequisite'),
  (:kb, 'MATH.ALG.T.Y', :kb, 'MATH.ALG.T.Z', 'prerequisite'),
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.Z', 'prerequisite'),    -- 传递边
  (:kb, 'MATH.ALG.T.C1', :kb, 'MATH.ALG.T.C2', 'prerequisite'),
  (:kb, 'MATH.ALG.T.C2', :kb, 'MATH.ALG.T.C1', 'prerequisite'),  -- 环
  (:kb, 'MATH.ALG.T.M', :kb, 'MATH.ALG.T.X', 'related'),
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.DEP', 'related');       -- 指向已停用节点
-- DOC 没有边，但有教材投影：不算孤点（反例）
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to) VALUES
  (:kb, 'MATH.ALG.T.DOC', 'bubian-2024', 2024, '八上', '第十四章', 10, 11);
INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
  ('b6000000-0000-4000-8000-0000000000b6', :kb, '{MATH.ALG.T.X}', '合成来源（已过期）', '2020-01-01', '2020-06-01', 'published'),
  ('b7000000-0000-4000-8000-0000000000b7', :kb, '{MATH.ALG.T.Y}', '合成来源（有效）', '2020-01-01', '2100-01-01', 'published');
SELECT array_to_string(t.kge_issues(:kb, 'error'), ',') AS kb_errors \gset
SELECT t.ok(:'kb_errors' = 'cycle:MATH.ALG.T.C1,cycle:MATH.ALG.T.C2,dangling_ref:' ||
              (SELECT 'knowledge_edge:' || id FROM kge.knowledge_edge WHERE to_id = 'MATH.ALG.T.DEP') ||
              ',expired_current_affair:b6000000-0000-4000-8000-0000000000b6,missing_boundary:MATH.ALG.T.M,orphan:MATH.ALG.T.O,' ||
              'transitive_edge:MATH.ALG.T.X -> MATH.ALG.T.Z',
            '发布前校验：环、传递边、孤点、缺边界、悬空引用、过期时政各命中且只命中构造的那一处（实际：' || :'kb_errors' || '）');
SELECT t.ok((SELECT count(*) FROM kge.validate_map_version(:kb) c WHERE c.check_name = 'few_items' AND c.object_id = 'MATH.ALG.T.C1') = 0,
            '方法节点不参与主考点题量检查');
SELECT t.ok((SELECT detail FROM kge.validate_map_version(:kb) c WHERE c.check_name = 'missing_boundary') = '缺不包含、缺典型题例',
            '缺边界的检查写明缺哪几项');
SELECT t.throws_like($$SELECT kge.publish_map_version('b2000000-0000-4000-8000-0000000000b2')$$, '23514', '阻断问题', '有阻断问题时拒绝发布');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :k1) = 'published' AND (SELECT status FROM kge.map_version WHERE id = :kb) = 'draft',
            '拒绝发布时两个版本的状态都不变');

-- 修好（不删除：边与时政走状态，补齐边界与投影）后可以发布，原已发布版本自动撤下
UPDATE kge.knowledge_edge SET status = 'deprecated'
 WHERE map_version_id = :kb AND ((from_id = 'MATH.ALG.T.X' AND to_id IN ('MATH.ALG.T.Z', 'MATH.ALG.T.DEP')) OR from_id = 'MATH.ALG.T.C2');
UPDATE kge.knowledge_node SET excludes = '{b}', examples = '{c}' WHERE map_version_id = :kb AND id = 'MATH.ALG.T.M';
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to) VALUES
  (:kb, 'MATH.ALG.T.O', 'bubian-2024', 2024, '八上', '第十四章', 12, 12);
UPDATE kge.current_affair SET review_status = 'retired' WHERE id = 'b6000000-0000-4000-8000-0000000000b6';
SELECT t.ok(t.kge_issues(:kb, 'error') = '{}', '修好后没有阻断问题（实际：' || array_to_string(t.kge_issues(:kb, 'error'), ',') || '）');
SELECT t.ok(kge.publish_map_version(:kb) IS NOT NULL, '修好后可以发布');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :k1) = 'retired' AND (SELECT status FROM kge.map_version WHERE id = :kb) = 'published',
            '发布新版本时同一学科原已发布版本自动撤下');
SELECT t.ok((SELECT count(*) FROM kge.map_version WHERE subject = 'aya' AND status = 'published') = 1, '同一学科同一时刻只有一个已发布版本');
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'draft' WHERE id = 'b1000000-0000-4000-8000-0000000000b1'$$,
                     '23514', '不允许的地图版本状态变化', '已撤下的版本不能回到草稿');
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'draft' WHERE id = 'b2000000-0000-4000-8000-0000000000b2'$$,
                     '23514', '不允许的地图版本状态变化', '已发布的版本不能回到草稿');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET name = '改名' WHERE map_version_id = 'b1000000-0000-4000-8000-0000000000b1' AND id = 'MATH.FUNC.QUAD.GRAPH'$$,
                     '23514', '已发布或已撤下', '已撤下版本同样不能改骨架');
SELECT t.throws($$SELECT kge.validate_map_version('b0000000-0000-4000-8000-000000000000')$$, 'P0002', '校验不存在的版本时报错');
RESET ROLE;

-- 唯一已发布：即使绕过发布函数（以函数属主身份直接改），同一学科也不能有第二个已发布版本
SET ROLE starbuddy_authz;
SELECT t.throws($$UPDATE kge.map_version SET status = 'published', published_at = now() WHERE id = 'b4000000-0000-4000-8000-0000000000b4'$$,
                '23505', '同一学科同一时刻只能有一个已发布版本（唯一索引兜底）');
RESET ROLE;
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'published', published_at = now() WHERE id = 'b3000000-0000-4000-8000-0000000000b3'$$,
                     '42501', '只能经 kge.publish_map_version 发布', '超级用户直接改也不能绕过发布函数');

-- ================= 写入方与读取范围 =================
-- 只有知识服务能写，任何应用角色都没有 DELETE / TRUNCATE（撤下走状态）
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c CROSS JOIN unnest(ARRAY['starbuddy_bff', 'starbuddy_learning', 'starbuddy_review', 'starbuddy_scheduler',
                                                   'starbuddy_insight', 'starbuddy_kge']) r(role)
   WHERE c.relnamespace = 'kge'::regnamespace AND c.relkind = 'r'
     AND (has_table_privilege(r.role, c.oid, 'DELETE') OR has_table_privilege(r.role, c.oid, 'TRUNCATE')
          OR (r.role <> 'starbuddy_kge' AND (has_any_column_privilege(r.role, c.oid, 'INSERT') OR has_any_column_privilege(r.role, c.oid, 'UPDATE'))))),
  '公共知识层：只有知识服务能写，任何应用角色都不能删除或清空');
SELECT t.ok((SELECT count(*) FROM pg_class c WHERE c.relnamespace = 'kge'::regnamespace AND c.relkind = 'r'
               AND has_table_privilege('starbuddy_kge', c.oid, 'INSERT')) = 14, '知识服务能写全部 14 张 kge 表');
SET ROLE starbuddy_kge;
SELECT t.throws($$DELETE FROM kge.knowledge_edge WHERE status = 'deprecated'$$, '42501', '知识服务也不能删除边');
RESET ROLE;
SET ROLE starbuddy_learning;
SELECT t.throws($$SELECT t.kge_node('MATH.GEOM', 'domain', NULL, NULL, '{}', NULL, 'b4000000-0000-4000-8000-0000000000b4')$$, '42501', '学科服务不能写节点');
SELECT t.throws($$SELECT kge.publish_map_version('b4000000-0000-4000-8000-0000000000b4')$$, '42501', '学科服务不能发布地图');
SELECT t.ok((SELECT count(*) FROM kge.map_version) = 4 AND (SELECT count(*) FROM kge.map_change) = 1 AND (SELECT count(*) FROM kge.scoring_point) = 1,
            '学科服务读得到全部版本（含草稿与已撤下）、变更单与采分点');
SELECT t.ok((SELECT count(*) FROM kge.validate_map_version('b4000000-0000-4000-8000-0000000000b4')) = 0, '学科服务可以跑发布前校验');
RESET ROLE;
SET ROLE starbuddy_insight;
SELECT t.ok((SELECT count(DISTINCT map_version_id) FROM kge.knowledge_node) = 3, '洞察服务读得到各版本的节点（核对判断对象）');
SELECT t.throws($$SELECT count(*) FROM kge.item_tag$$, '42501', '洞察服务不能读题目标签');
SELECT t.throws($$SELECT count(*) FROM kge.textbook_projection$$, '42501', '洞察服务不能读教材投影');
SELECT t.throws($$SELECT t.kge_node('MATH.GEOM', 'domain', NULL, NULL, '{}', NULL, 'b4000000-0000-4000-8000-0000000000b4')$$, '42501', '洞察服务不能写节点');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM kge.map_version) = ARRAY['b2000000-0000-4000-8000-0000000000b2'::uuid],
            'BFF 只看得到已发布的地图版本');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_node WHERE map_version_id <> :kb) = 0
            AND (SELECT count(*) FROM kge.knowledge_node) = (SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = :kb AND status = 'active')
            AND NOT EXISTS (SELECT 1 FROM kge.knowledge_node WHERE id = 'MATH.ALG.T.DEP'),
            'BFF 只看得到已发布版本里的有效节点（草稿、已撤下版本与已停用节点都看不到）');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_edge) = 4 AND NOT EXISTS (SELECT 1 FROM kge.knowledge_edge WHERE status <> 'active'),
            'BFF 只看得到已发布版本里的有效边');
SELECT t.ok((SELECT count(*) FROM kge.textbook_projection WHERE map_version_id = :kb) = 2
            AND (SELECT count(*) FROM kge.region_projection) = 0, 'BFF 只看得到已发布版本的投影');
SELECT t.throws($$SELECT verified_by FROM kge.textbook_projection$$, '42501', 'BFF 看不到投影的核对人');
SELECT t.throws($$SELECT count(*) FROM kge.map_change$$, '42501', 'BFF 看不到变更单');
SELECT t.throws($$SELECT count(*) FROM kge.content_review$$, '42501', 'BFF 看不到审核记录');
SELECT t.throws($$SELECT count(*) FROM kge.scoring_point$$, '42501', 'BFF 看不到采分点（spec 待人确认 #4，按默认）');
SELECT t.throws($$SELECT count(*) FROM kge.item_tag$$, '42501', 'BFF 看不到题目标签');
SELECT t.throws($$SELECT kge.publish_map_version('b4000000-0000-4000-8000-0000000000b4')$$, '42501', 'BFF 不能发布地图');
SELECT t.throws($$SELECT kge.validate_map_version('b4000000-0000-4000-8000-0000000000b4')$$, '42501', 'BFF 不能跑发布前校验');
RESET ROLE;

-- ================= 对 platform 的最小改动 =================
SET ROLE starbuddy_learning;
UPDATE platform.curriculum_version SET map_version_id = :kb WHERE id = 'e0000000-0000-4000-8000-0000000000e0';
SELECT t.ok((SELECT map_version_id FROM platform.curriculum_version WHERE id = 'e0000000-0000-4000-8000-0000000000e0') = :kb,
            '内容版本可以引用同学科的地图版本');
SELECT t.throws($$UPDATE platform.curriculum_version SET map_version_id = 'b2000000-0000-4000-8000-0000000000b2' WHERE id = 'e9000000-0000-4000-8000-0000000000e9'$$,
                '23503', '内容版本不能引用别的学科的地图版本');
SELECT t.ok((SELECT count(*) FROM platform.curriculum_version WHERE map_version_id IS NULL) >= 1, '地图版本引用可空（现有内容版本不受影响）');
RESET ROLE;
