-- 公共知识层（0011，specs/kge-schema-v0.9/spec.md 第 7 节）：约束、骨架冻结、发布前校验与发布、写入方与读取范围。
-- 合成节点（CLAUDE.md 规则 8；不导入任何真实地图）。留下的数据不含学生键，80_attack_surface 按豁免表处理。

-- 地图版本：k1 = 数学 0.1.0（干净，先发布）；kb = 数学 0.2.0（七类问题，修好后发布）；kw = 语文 0.1.0；kx = 数学 0.3.0（留作草稿）
\set k1 '''ca100000-0000-4000-8000-00000000ca01'''
\set kb '''ca200000-0000-4000-8000-00000000ca02'''
\set kw '''ca300000-0000-4000-8000-00000000ca03'''
\set kx '''ca400000-0000-4000-8000-00000000ca04'''

-- 测试辅助：某版本的阻断问题与警告，排好序的 “检查名:对象” 列表
CREATE FUNCTION t.kge_issues(p_version uuid, p_severity text) RETURNS text[] LANGUAGE sql AS $$
  SELECT coalesce(array_agg(c.check_name || ':' || c.object_id ORDER BY c.check_name, c.object_id), '{}')
    FROM kge.validate_map_version(p_version) c WHERE c.severity = p_severity $$;
GRANT EXECUTE ON FUNCTION t.kge_issues(uuid, text) TO PUBLIC;

-- 0018 起发布与签字只能由 staff 经 starbuddy_review：两名合成 staff（sf1 来自 10 号夹具，sf2 在这里新建）
INSERT INTO platform.account (id, kind) VALUES ('5e000000-0000-4000-8000-00000000005e', 'staff');
-- 以某个 staff 身份、在 starbuddy_review 角色下执行一段 SQL，结束后回到调用前的角色
CREATE FUNCTION t.as_staff(p_staff uuid, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  PERFORM t.as_account(p_staff);
  SET LOCAL ROLE starbuddy_review;
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  PERFORM t.as_account(NULL);
  RETURN v_out;
END $$;
-- 签字批准并发布（审核台 v0 的正常流程）
-- 两名 staff 各签一次（结构性变更两人确认，0019），再由第一名发布
CREATE FUNCTION t.kge_sign_publish(p_version uuid) RETURNS text LANGUAGE sql AS $$
  SELECT t.as_staff('5e000000-0000-4000-8000-00000000005e',
                    format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''test'')::text', p_version))
      || t.as_staff('5f000000-0000-4000-8000-00000000005f',
                    format('SELECT kge.sign_review(''map_version'', %L, ''approved'', ''high'', ''test'')::text || kge.publish_map_version(%L)::text',
                           p_version, p_version)) $$;
GRANT EXECUTE ON FUNCTION t.as_staff(uuid, text), t.kge_sign_publish(uuid) TO PUBLIC;

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
SELECT t.throws($$UPDATE kge.map_version SET status = 'published' WHERE id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                '42501', '知识服务不能直接改版本状态（0018 收回了状态列的更新权限）');
SELECT t.throws($$UPDATE kge.map_version SET published_at = now() WHERE id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                '42501', '知识服务不能写发布时间');

-- ================= 节点：约束（每条一个反例，正例是下面成功的插入） =================
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels, error_type) VALUES
  (:k1, 'MATH.FUNC', 'domain', NULL, NULL, '函数', '{}', '{}', '{}', '{}', NULL),
  (:k1, 'MATH.FUNC.QUAD', 'topic', 'MATH.FUNC', NULL, '二次函数', '{}', '{}', '{}', '{}', NULL),
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'node', 'MATH.FUNC.QUAD', 'kp', '顶点式', '{配方化顶点式}', '{一般式求根}', '{synthetic-0301-07}', '{understand,apply}', NULL),
  (:k1, 'MATH.FUNC.QUAD.GRAPH', 'node', 'MATH.FUNC.QUAD', 'kp', '图像与性质', '{开口与对称轴}', '{平移作图}', '{synthetic-0301-08}', '{understand}', NULL),
  (:k1, 'MATH.FUNC.QUAD.SIGN_SLIP', 'node', 'MATH.FUNC.QUAD', 'pitfall', '配方时漏变号', '{配方后常数项符号写反}', '{}', '{}', '{}', 'computation_or_grammar'),
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM.SIGN', 'detail', 'MATH.FUNC.QUAD.VERTEX_FORM', 'pitfall', '顶点坐标符号', '{顶点横坐标写成 h 而不是 -h}', '{}', '{}', '{}', 'knowledge');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = :k1) = 6, '知识服务可以写节点（四个层级、易错点、细目）');

RESET ROLE;
CREATE FUNCTION t.kge_node(p_id text, p_level text, p_parent text, p_kind text, p_cog text[] DEFAULT '{}', p_err text DEFAULT NULL,
                           p_version uuid DEFAULT 'ca100000-0000-4000-8000-00000000ca01') RETURNS void LANGUAGE sql AS $$
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
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'related')$$,
                     '23514', 'edge_no_self_loop', '边不能自环');
SELECT t.throws_like($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                       VALUES ('ca300000-0000-4000-8000-00000000ca03', 'CHN.READ.NARR.CHARACTER', 'ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'prerequisite')$$,
                     '23514', 'edge_same_version', '前置边不能跨学科或跨地图版本');
SELECT t.throws($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.NOPE', 'related')$$,
                '23503', '边的端点必须是已有节点');
SELECT t.throws($$INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.VERTEX_FORM', 'derives')$$,
                '23514', '边类型只能是七种之一');

-- ================= 投影、能力视图挂接、旧编码迁移 =================
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to,
                                     quote_text, verified_by, verified_at, mapping_state) VALUES
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'bubian-2024', 2024, '2024-1', '九上', '第二十二章', 32, 34, '合成原句：把一般式配方成顶点式。', 'staff-001', now(), 'verified');
SELECT t.ok((SELECT quote_hash = sha256(convert_to('合成原句：把一般式配方成顶点式。', 'UTF8')) FROM kge.textbook_projection WHERE node_id = 'MATH.FUNC.QUAD.VERTEX_FORM'),
            '教材原句的哈希由库计算（UTF-8 字节的 sha256）');
SELECT t.throws($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text, quote_hash)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 35, 36, '合成原句', '\x00')$$,
                '428C9', '写入方不能自己填原句哈希');
SELECT t.throws($$UPDATE kge.textbook_projection SET quote_hash = '\x00'$$, '428C9', '写入方不能改原句哈希');
SELECT t.throws_like($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text, mapping_state)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 35, 36, '合成原句', 'verified')$$,
                     '23514', 'projection_verified_by', '投影标为已核对时必须写核对人与核对时间');
SELECT t.throws_like($$INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'bubian-2024', 2024, '九上', '第二十二章', 36, 35)$$,
                     '23514', 'projection_page_range', '投影的止页不能早于起页');
INSERT INTO kge.region_projection (map_version_id, node_id, region, exam_year, weight, typical_slots, source_ref) VALUES
  (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'nanjing', 2027, 0.300, '{选择题第 5 题}', '合成来源');
INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight) VALUES (:k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'aya', 'K-QF-0301', 1);
SELECT t.throws_like($$INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                       VALUES ('ca300000-0000-4000-8000-00000000ca03', 'CHN.READ.NARR.CHARACTER', 'aya', 'K-QF-0301', 1)$$,
                     '23514', '学科与地图版本不符', '能力视图挂接的学科必须与地图版本一致');
SELECT t.throws($$INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'MATH.FUNC.QUAD.GRAPH', 'aya', 'K-NOPE-0000', 1)$$,
                '23503', '能力视图挂接必须指向已登记的 platform.skill_node');
INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule) VALUES
  (:k1, 'aya', 'K-QF-0301', 'MATH.FUNC.QUAD.VERTEX_FORM', 'rename'), (:k1, 'aya', 'K-QF-0399', NULL, 'retire');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'K-QF-0302', NULL, 'rename')$$,
                     '23514', 'id_migration_retire', '除撤销外，旧编码迁移必须指向新节点');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'K-QF-0302', 'MATH.FUNC.QUAD.GRAPH', 'retire')$$,
                     '23514', 'id_migration_retire', '撤销的旧编码不能有新节点');
SELECT t.throws($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'K-QF-0399', NULL, 'retire')$$,
                '23505', '同一旧编码的同一去向只登记一次（撤销也算）');

-- ================= 原子：题目标签、采分点、时政 =================
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, cognitive_level, prior_difficulty, source_kind) VALUES
  (:k1, 'aya', 'synthetic-0301-07', 'MATH.FUNC.QUAD.VERTEX_FORM', '{MATH.FUNC.QUAD.GRAPH}', 'apply', 0.4, 'original');
SELECT t.throws_like($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'wenli', 'synthetic-essay-01', 'MATH.FUNC.QUAD.GRAPH', 'original')$$,
                     '23514', '学科与地图版本不符', '题目标签的学科必须与地图版本一致');
SELECT t.throws($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-nope', 'MATH.FUNC.QUAD.GRAPH', 'original')$$,
                '23503', '题目标签只能挂在 platform.item 已登记的题目上（不做第二份题目登记）');
SELECT t.throws($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, node_ids, source_kind)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-08', 'MATH.FUNC.QUAD.GRAPH', '{MATH.FUNC.QUAD.NOPE}', 'original')$$,
                '23503', '题目标签的节点数组只能引用本版本已有节点');
INSERT INTO kge.item_family (id, map_version_id, main_node_id, code) VALUES ('b8000000-0000-4000-8000-0000000000b8', :k1, 'MATH.FUNC.QUAD.VERTEX_FORM', 'quad-vertex');
SELECT t.throws($$INSERT INTO kge.item_family (map_version_id, main_node_id, code) VALUES ('ca200000-0000-4000-8000-00000000ca02', 'MATH.FUNC.QUAD.VERTEX_FORM', 'x')$$,
                '23503', '题目族的主考点必须在同一地图版本');
INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids, required_logic) VALUES
  (:k1, 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'vertex', 2, '{MATH.FUNC.QUAD.VERTEX_FORM}', '合成采分点：写出顶点坐标');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, point_id, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'axis', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23502', '采分点必须带 Rubric 版本');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'no-version', 'axis', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23514', '采分点的 Rubric 版本必须是 名称@版本');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23502', '采分点必须带编号（判分维度据此指回采分点）');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'Axis Point', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23514', '采分点编号与判分维度编号同一写法');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'vertex', 1, '{MATH.FUNC.QUAD.VERTEX_FORM}')$$,
                '23505', '同一题同一 Rubric 版本下采分点编号不能重复');
SELECT t.throws_like($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids)
                       VALUES ('ca200000-0000-4000-8000-00000000ca02', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'axis', 1, '{}')$$,
                     '23505', '已属于另一个地图版本', '一个 Rubric 版本只属于一个地图版本');
SELECT t.throws($$UPDATE kge.scoring_point SET score = 3$$, '42501', '采分点写入后不能修改（改内容发新 Rubric 版本，已有判分可按原版本复算）');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'axis', 1, '{MATH.FUNC.QUAD.NOPE}')$$,
                '23503', '采分点只能引用本版本已有节点');
SELECT t.throws($$INSERT INTO kge.scoring_point (map_version_id, subject, item_id, rubric_version, point_id, score, node_ids, common_errors)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'synthetic-0301-07', 'aya-quad-rubric@0.1.0', 'axis', 1,
                          '{MATH.FUNC.QUAD.VERTEX_FORM}', '[{"attempt_id": "x", "text": "合成"}]')$$,
                '23514', '采分点的常见错误不能带作答等个人层的键');
SELECT t.throws($$INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', '{MATH.FUNC.QUAD.GRAPH}', '合成来源', '2027-03-01', '2027-03-01')$$,
                '23514', '时政的失效日期必须晚于生效日期');
SELECT t.throws($$INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from)
                  VALUES ('ca100000-0000-4000-8000-00000000ca01', '{MATH.FUNC.QUAD.GRAPH}', '合成来源', '2027-03-01')$$,
                '23502', '时政必须写失效日期');

-- ================= 成长治理 =================
INSERT INTO kge.map_change (id, subject, change_type, payload, reviewers, target_version) VALUES
  ('b5000000-0000-4000-8000-0000000000b5', 'aya', 'node_split', '{"from": "MATH.FUNC.QUAD.GRAPH"}', '{staff-001,staff-002}', :kb);
SELECT t.throws_like($$INSERT INTO kge.map_change (subject, change_type, payload, status, reviewers) VALUES ('aya', 'node_merge', '{}', 'approved', '{staff-001,staff-002}')$$,
                     '42501', '待审', '变更单不能直接以已批准新建（0018：只能经签字函数，自报工号不算签字）');
SELECT t.throws($$UPDATE kge.map_change SET status = 'approved' WHERE id = 'b5000000-0000-4000-8000-0000000000b5'$$,
                '42501', '知识服务不能直接改变更单状态');
SELECT t.throws($$SELECT count(*) FROM platform.map_change_signoff$$, '42501', '知识服务读不到签字人表');
-- 规则 13：公共层的 jsonb 在任何层级都不能带个人层的键
INSERT INTO kge.map_change (subject, change_type, payload, evidence_pack) VALUES
  ('aya', 'node_revise', '{"node": "MATH.FUNC.QUAD.GRAPH"}', '{"n": 42, "sample_items": ["synthetic-0301-07"], "error_rate": 0.31}');
SELECT t.throws($$INSERT INTO kge.map_change (subject, change_type, payload, evidence_pack)
                  VALUES ('aya', 'node_revise', '{}', '{"n": 42, "samples": {"student_ids": ["c1000000-0000-4000-8000-0000000000c1"]}}')$$,
                '23514', '变更单的证据包不能带学生 ID（嵌套的键也算）');
SELECT t.throws($$INSERT INTO kge.map_change (subject, change_type, payload) VALUES ('aya', 'node_revise', '{"Attempt_Refs": []}')$$,
                '23514', '变更单的内容不能带作答引用（不区分大小写）');
SELECT t.throws($$INSERT INTO kge.content_review (object_type, object_id, risk_level, diff) VALUES ('knowledge_node', 'X', 'low', '{"guardian_note": "合成"}')$$,
                '23514', '审核记录的 diff 不能带家长相关的键');
INSERT INTO kge.content_review (object_type, object_id, risk_level) VALUES ('knowledge_node', 'MATH.FUNC.QUAD.VERTEX_FORM', 'high');
SELECT t.ok(true, '知识服务可以提交待审记录');
SELECT t.throws($$INSERT INTO kge.content_review (object_type, object_id, risk_level, decision, reviewer, decided_at)
                  VALUES ('knowledge_node', 'X', 'high', 'approved', 'staff-003', now())$$,
                '42501', '知识服务不能直接写审核结论（0018：只能经签字函数）');

-- ================= 发布前校验与发布：干净的 k1 =================
SELECT t.ok(t.kge_issues(:k1, 'error') = '{}', '干净的地图版本没有阻断问题');
SELECT t.ok(t.kge_issues(:k1, 'warning') = '{few_items:MATH.FUNC.QUAD.GRAPH,few_items:MATH.FUNC.QUAD.VERTEX_FORM,policy_scheme_missing:nanjing:2027}',
            '主考点少于 3 道题、政策库缺方案只报警告：' || array_to_string(t.kge_issues(:k1, 'warning'), ','));
SELECT t.throws($$SELECT kge.publish_map_version('ca100000-0000-4000-8000-00000000ca01')$$, '42501', '知识服务不能发布（0019 改由签字身份发布）');
SELECT t.ok(t.kge_sign_publish(:k1) IS NOT NULL, '没有阻断问题、签字批准后可以发布');
SELECT t.ok((SELECT status = 'published' AND published_at IS NOT NULL FROM kge.map_version WHERE id = :k1), '发布后状态与发布时间由发布函数写入');
SELECT t.throws_like($$SELECT t.kge_sign_publish('ca100000-0000-4000-8000-00000000ca01')$$, '23514', '只能对草稿', '已发布的版本不能再签字发布');

-- 骨架冻结：已发布版本的节点、边、投影、挂接、旧编码迁移不能新增或修改
SELECT t.throws_like($$SELECT t.kge_node('MATH.FUNC.QUAD.ROOTS', 'node', 'MATH.FUNC.QUAD', 'kp')$$, '23514', '已发布或已撤下', '已发布版本不能加节点');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET name = '改名' WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01' AND id = 'MATH.FUNC.QUAD.GRAPH'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改节点');
SELECT t.throws_like($$UPDATE kge.knowledge_edge SET status = 'deprecated' WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改边');
SELECT t.throws_like($$UPDATE kge.textbook_projection SET page_to = 40 WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改教材投影');
SELECT t.throws_like($$UPDATE kge.region_projection SET weight = 0.9 WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改地区投影');
SELECT t.throws_like($$UPDATE kge.skill_link SET weight = 0.5 WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '已发布或已撤下', '已发布版本不能改能力视图挂接');
SELECT t.throws_like($$INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule)
                       VALUES ('ca100000-0000-4000-8000-00000000ca01', 'aya', 'K-QF-0302', 'MATH.FUNC.QUAD.GRAPH', 'rename')$$,
                     '23514', '已发布或已撤下', '已发布版本不能加旧编码迁移');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET map_version_id = 'ca400000-0000-4000-8000-00000000ca04'
                        WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01' AND id = 'MATH.FUNC.QUAD.VERTEX_FORM.SIGN'$$,
                     '23514', '已发布或已撤下', '已发布版本的节点不能挪到草稿版本');
SELECT t.throws_like($$UPDATE kge.map_version SET version = '0.1.1' WHERE id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '只能改状态', '已发布版本不能改版本号');
-- 原子不随骨架冻结：已发布版本上可以继续加题目标签
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, source_kind) VALUES
  (:k1, 'aya', 'synthetic-0301-08', 'MATH.FUNC.QUAD.GRAPH', 'original');
SELECT t.ok((SELECT count(*) FROM kge.item_tag WHERE map_version_id = :k1) = 2, '已发布版本上可以继续加题目标签（原子不冻结）');

-- ================= 发布前校验：kb 构造七类问题 =================
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
  (:kb, 'MATH.ALG', 'domain', NULL, NULL, '代数'), (:kb, 'MATH.ALG.T', 'topic', 'MATH.ALG', NULL, '合成主题');
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels, error_type, status) VALUES
  (:kb, 'MATH.ALG.T.X', 'node', 'MATH.ALG.T', 'kp', 'X', '{a}', '{b}', '{c}', '{understand}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.Y', 'node', 'MATH.ALG.T', 'kp', 'Y', '{a}', '{b}', '{c}', '{understand}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.Z', 'node', 'MATH.ALG.T', 'kp', 'Z', '{a}', '{b}', '{c}', '{understand}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.C1', 'node', 'MATH.ALG.T', 'method', 'C1', '{}', '{}', '{}', '{}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.C2', 'node', 'MATH.ALG.T', 'method', 'C2', '{}', '{}', '{}', '{}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.O', 'node', 'MATH.ALG.T', 'kp', 'O', '{a}', '{b}', '{c}', '{understand}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.M', 'node', 'MATH.ALG.T', 'viewpoint', 'M', '{a}', '{}', '{}', '{}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.DEP', 'node', 'MATH.ALG.T', 'kp', 'DEP', '{a}', '{b}', '{c}', '{understand}', NULL, 'deprecated'),
  (:kb, 'MATH.ALG.T.DOC', 'node', 'MATH.ALG.T', 'material', 'DOC', '{}', '{}', '{}', '{}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.MAT', 'node', 'MATH.ALG.T', 'material', 'MAT', '{}', '{}', '{}', '{}', NULL, 'active'),
  (:kb, 'MATH.ALG.T.PIT', 'node', 'MATH.ALG.T', 'pitfall', 'PIT', '{}', '{}', '{}', '{}', 'knowledge', 'active'),
  (:kb, 'MATH.ALG.T.NL', 'node', 'MATH.ALG.T', 'kp', 'NL', '{a}', '{b}', '{c}', '{}', NULL, 'active');  -- 只缺认知层次
-- kx 里放一个节点，作为跨版本边的对端（kx 始终是草稿）
INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
  (:kx, 'MATH.ALG', 'domain', NULL, NULL, '代数'), (:kx, 'MATH.ALG.T', 'topic', 'MATH.ALG', NULL, '合成主题');
INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type) VALUES
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.Y', 'prerequisite'),
  (:kb, 'MATH.ALG.T.Y', :kb, 'MATH.ALG.T.Z', 'prerequisite'),
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.Z', 'prerequisite'),    -- 传递边
  (:kb, 'MATH.ALG.T.C1', :kb, 'MATH.ALG.T.C2', 'prerequisite'),
  (:kb, 'MATH.ALG.T.C2', :kb, 'MATH.ALG.T.C1', 'prerequisite'),  -- 环
  (:kb, 'MATH.ALG.T.M', :kb, 'MATH.ALG.T.X', 'related'),
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.DEP', 'related'),       -- 指向已停用节点
  (:kb, 'MATH.ALG.T.MAT', :kb, 'MATH.ALG.T.X', 'prerequisite'),  -- 素材进了前置依赖图
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.Y', 'has_pitfall'),     -- 终点不是易错点
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.PIT', 'has_pitfall'),   -- 反例：终点是易错点（但 PIT 缺识别特征）
  (:kb, 'MATH.ALG.T.X', :kb, 'MATH.ALG.T.C1', 'uses_method'),    -- 反例：终点是方法
  (:kb, 'MATH.ALG.T.Y', :kx, 'MATH.ALG.T', 'related'),           -- 跨版本，对端是草稿：只报警告
  (:kb, 'MATH.ALG.T.NL', :kb, 'MATH.ALG.T.X', 'related');
SELECT t.throws($$INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, family_id, source_kind)
                  VALUES ('ca200000-0000-4000-8000-00000000ca02', 'aya', 'synthetic-0301-08', 'MATH.ALG.T.Y', 'b8000000-0000-4000-8000-0000000000b8', 'original')$$,
                '23503', '题目标签不能引用别的地图版本的题目族');
SELECT t.throws_like($$INSERT INTO kge.region_projection (map_version_id, node_id, region, exam_year, weight, source_ref)
                       VALUES ('b9000000-0000-4000-8000-000000000000', 'MATH.ALG.T.X', 'nanjing', 2027, 0.1, '合成')$$,
                     '23514', '不存在或不可见', '引用不存在的地图版本时由冻结检查拒绝（看不到版本不放行）');
-- 题目标签的认知层次不在主考点的层次集合里
INSERT INTO kge.item_tag (map_version_id, subject, item_id, main_node_id, cognitive_level, source_kind) VALUES
  (:kb, 'aya', 'synthetic-0301-07', 'MATH.ALG.T.X', 'synthesize', 'original');
-- DOC 没有边，但有教材投影：不算孤点（反例）
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to) VALUES
  (:kb, 'MATH.ALG.T.DOC', 'bubian-2024', 2024, '八上', '第十四章', 10, 11);
INSERT INTO kge.current_affair (id, map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
  ('b6000000-0000-4000-8000-0000000000b6', :kb, '{MATH.ALG.T.X}', '合成来源（已过期）', '2020-01-01', '2020-06-01', 'published'),
  ('b7000000-0000-4000-8000-0000000000b7', :kb, '{MATH.ALG.T.Y}', '合成来源（有效）', '2020-01-01', '2100-01-01', 'published');
SELECT array_to_string(t.kge_issues(:kb, 'error'), ',') AS kb_errors \gset
SELECT array_to_string(ARRAY(SELECT x FROM unnest(ARRAY[
          'cycle:MATH.ALG.T.C1', 'cycle:MATH.ALG.T.C2',
          'dangling_ref:' || (SELECT 'knowledge_edge:' || id FROM kge.knowledge_edge WHERE to_id = 'MATH.ALG.T.DEP'),
          'edge_kind_mismatch:' || (SELECT 'knowledge_edge:' || id FROM kge.knowledge_edge WHERE from_id = 'MATH.ALG.T.MAT'),
          'edge_kind_mismatch:' || (SELECT 'knowledge_edge:' || id FROM kge.knowledge_edge WHERE type = 'has_pitfall' AND to_id = 'MATH.ALG.T.Y'),
          'missing_boundary:MATH.ALG.T.M', 'missing_boundary:MATH.ALG.T.NL', 'missing_boundary:MATH.ALG.T.PIT', 'orphan:MATH.ALG.T.O',
          'tag_level_mismatch:item_tag:aya:synthetic-0301-07', 'transitive_edge:MATH.ALG.T.X -> MATH.ALG.T.Z']) x
        ORDER BY split_part(x, ':', 1), substr(x, position(':' IN x) + 1)), ',') AS kb_expected \gset
SELECT t.ok(:'kb_errors' = :'kb_expected',
            '发布前校验：环、传递边、孤点、缺边界与识别特征、悬空引用、边类型不符、标签层次不符各命中且只命中构造的那一处（实际：' || :'kb_errors' || '）');
SELECT t.ok(t.kge_issues(:kb, 'warning') @> ARRAY['expired_current_affair:b6000000-0000-4000-8000-0000000000b6',
                                                  'stale_cross_edge:' || (SELECT 'knowledge_edge:' || id FROM kge.knowledge_edge WHERE to_map_version_id = :kx)]
            AND NOT EXISTS (SELECT 1 FROM kge.validate_map_version(:kb) c WHERE c.object_id = 'b7000000-0000-4000-8000-0000000000b7'),
            '过期时政与指向未发布版本的跨版本边只报警告（有效时政不报）');
SELECT t.ok((SELECT count(*) FROM kge.validate_map_version(:kb) c WHERE c.check_name = 'few_items' AND c.object_id = 'MATH.ALG.T.C1') = 0,
            '方法节点不参与主考点题量检查');
SELECT t.ok((SELECT detail FROM kge.validate_map_version(:kb) c WHERE c.check_name = 'missing_boundary' AND c.object_id = 'MATH.ALG.T.M')
              = '缺不包含、缺典型题例、缺认知层次'
            AND (SELECT detail FROM kge.validate_map_version(:kb) c WHERE c.check_name = 'missing_boundary' AND c.object_id = 'MATH.ALG.T.PIT') = '缺识别特征',
            '缺边界的检查写明缺哪几项（易错点要求识别特征）');
SELECT t.throws_like($$SELECT t.kge_sign_publish('ca200000-0000-4000-8000-00000000ca02')$$, '23514', '阻断问题', '有阻断问题时拒绝发布');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :k1) = 'published' AND (SELECT status FROM kge.map_version WHERE id = :kb) = 'draft',
            '拒绝发布时两个版本的状态都不变');

-- 修好（不删除：边与时政走状态，补齐边界与投影）后可以发布，原已发布版本自动撤下
UPDATE kge.knowledge_edge SET status = 'deprecated'
 WHERE map_version_id = :kb AND ((from_id = 'MATH.ALG.T.X' AND to_id IN ('MATH.ALG.T.Z', 'MATH.ALG.T.DEP')) OR from_id IN ('MATH.ALG.T.C2', 'MATH.ALG.T.MAT')
                                 OR (type = 'has_pitfall' AND to_id = 'MATH.ALG.T.Y'));
UPDATE kge.knowledge_node SET excludes = '{b}', examples = '{c}', cognitive_levels = '{apply}' WHERE map_version_id = :kb AND id = 'MATH.ALG.T.M';
UPDATE kge.knowledge_node SET includes = '{合成识别特征}' WHERE map_version_id = :kb AND id = 'MATH.ALG.T.PIT';
UPDATE kge.knowledge_node SET status = 'deprecated' WHERE map_version_id = :kb AND id = 'MATH.ALG.T.MAT';
UPDATE kge.knowledge_node SET cognitive_levels = '{understand}' WHERE map_version_id = :kb AND id = 'MATH.ALG.T.NL';
UPDATE kge.item_tag SET cognitive_level = 'understand' WHERE map_version_id = :kb;
INSERT INTO kge.textbook_projection (map_version_id, node_id, edition, edition_year, volume, chapter, page_from, page_to, quote_text, verified_by, verified_at, mapping_state) VALUES
  (:kb, 'MATH.ALG.T.O', 'bubian-2024', 2024, '八上', '第十四章', 12, 12, '合成原句', 'staff-001', now(), 'verified');
UPDATE kge.current_affair SET review_status = 'retired' WHERE id = 'b6000000-0000-4000-8000-0000000000b6';
SELECT t.ok(t.kge_issues(:kb, 'error') = '{}', '修好后没有阻断问题（实际：' || array_to_string(t.kge_issues(:kb, 'error'), ',') || '）');
SELECT t.throws_like($$SELECT t.kge_sign_publish('ca200000-0000-4000-8000-00000000ca02')$$, '23514', '变更单未全部批准', '结构性变更单未签字时拒绝发布');
SELECT t.ok(t.as_staff('5f000000-0000-4000-8000-00000000005f', $$SELECT kge.sign_map_change('b5000000-0000-4000-8000-0000000000b5', 'approved')$$) = 'proposed'
            AND t.as_staff('5e000000-0000-4000-8000-00000000005e', $$SELECT kge.sign_map_change('b5000000-0000-4000-8000-0000000000b5', 'approved')$$) = 'approved',
            '两名不同 staff 签字后变更单转为已批准');
SELECT t.ok(t.kge_sign_publish(:kb) IS NOT NULL, '修好、变更单批准后可以发布');
SELECT t.ok((SELECT status FROM kge.map_change WHERE id = 'b5000000-0000-4000-8000-0000000000b5') = 'applied', '发布后变更单转为已应用');
SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :k1) = 'retired' AND (SELECT status FROM kge.map_version WHERE id = :kb) = 'published',
            '发布新版本时同一学科原已发布版本自动撤下');
SELECT t.ok((SELECT count(*) FROM kge.map_version WHERE subject = 'aya' AND status = 'published') = 1, '同一学科同一时刻只有一个已发布版本');
SELECT t.throws_like($$INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until)
                       VALUES ('ca200000-0000-4000-8000-00000000ca02', '{MATH.ALG.T.DEP}', '合成来源', '2027-03-01', '2027-06-01')$$,
                     '23514', '引用了已停用的节点', '发布后新增的原子不能引用已停用节点（发布时的校验结论保持成立）');
INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until)
  VALUES (:kb, '{MATH.ALG.T.X}', '合成来源（发布后新增）', '2027-03-01', '2027-06-01');
SELECT t.throws($$UPDATE kge.map_version SET status = 'draft' WHERE id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                '42501', '知识服务不能改版本状态（0018）');
SELECT t.throws_like($$UPDATE kge.knowledge_node SET name = '改名' WHERE map_version_id = 'ca100000-0000-4000-8000-00000000ca01' AND id = 'MATH.FUNC.QUAD.GRAPH'$$,
                     '23514', '已发布或已撤下', '已撤下版本同样不能改骨架');
SELECT t.throws($$SELECT kge.validate_map_version('b0000000-0000-4000-8000-000000000000')$$, 'P0002', '校验不存在的版本时报错');
RESET ROLE;

-- 唯一已发布：即使绕过发布函数（以函数属主身份直接改），同一学科也不能有第二个已发布版本
SET ROLE starbuddy_authz;
SELECT t.throws($$UPDATE kge.map_version SET status = 'published', published_at = now() WHERE id = 'ca400000-0000-4000-8000-00000000ca04'$$,
                '23505', '同一学科同一时刻只能有一个已发布版本（唯一索引兜底）');
RESET ROLE;
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'published', published_at = now() WHERE id = 'ca300000-0000-4000-8000-00000000ca03'$$,
                     '42501', '只能经 kge.publish_map_version 发布', '超级用户直接改也不能绕过发布函数');
-- 状态机由守卫触发器兜底（连超级用户也拦）
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'draft' WHERE id = 'ca100000-0000-4000-8000-00000000ca01'$$,
                     '23514', '不允许的地图版本状态变化', '已撤下的版本不能回到草稿');
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'draft' WHERE id = 'ca200000-0000-4000-8000-00000000ca02'$$,
                     '23514', '不允许的地图版本状态变化', '已发布的版本不能回到草稿');
SELECT t.throws_like($$UPDATE kge.map_version SET status = 'retired' WHERE id = 'ca400000-0000-4000-8000-00000000ca04'$$,
                     '42501', '只能经 kge.retire_map_version 撤下', '超级用户直接改也不能绕过撤下函数（0018）');

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
SELECT t.throws($$SELECT t.kge_node('MATH.GEOM', 'domain', NULL, NULL, '{}', NULL, 'ca400000-0000-4000-8000-00000000ca04')$$, '42501', '学科服务不能写节点');
SELECT t.throws($$SELECT kge.publish_map_version('ca400000-0000-4000-8000-00000000ca04')$$, '42501', '学科服务不能发布地图');
SELECT t.ok((SELECT count(*) FROM kge.map_version) = 4 AND (SELECT count(*) FROM kge.map_change) = 2 AND (SELECT count(*) FROM kge.scoring_point) = 1,
            '学科服务读得到全部版本（含草稿与已撤下）、变更单与采分点');
SELECT t.ok((SELECT count(*) FROM kge.validate_map_version('ca400000-0000-4000-8000-00000000ca04')) = 0, '学科服务可以跑发布前校验');
RESET ROLE;
SET ROLE starbuddy_insight;
SELECT t.ok((SELECT count(DISTINCT map_version_id) FROM kge.knowledge_node) = 4, '洞察服务读得到各版本的节点（核对判断对象）');
SELECT t.throws($$SELECT count(*) FROM kge.item_tag$$, '42501', '洞察服务不能读题目标签');
SELECT t.throws($$SELECT count(*) FROM kge.textbook_projection$$, '42501', '洞察服务不能读教材投影');
SELECT t.throws($$SELECT t.kge_node('MATH.GEOM', 'domain', NULL, NULL, '{}', NULL, 'ca400000-0000-4000-8000-00000000ca04')$$, '42501', '洞察服务不能写节点');
RESET ROLE;
SET ROLE starbuddy_bff;
SELECT t.ok((SELECT array_agg(id ORDER BY id) FROM kge.map_version) = ARRAY['ca200000-0000-4000-8000-00000000ca02'::uuid],
            'BFF 只看得到已发布的地图版本');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_node WHERE map_version_id <> :kb) = 0
            AND (SELECT count(*) FROM kge.knowledge_node) = (SELECT count(*) FROM kge.knowledge_node WHERE map_version_id = :kb AND status = 'active')
            AND NOT EXISTS (SELECT 1 FROM kge.knowledge_node WHERE id = 'MATH.ALG.T.DEP'),
            'BFF 只看得到已发布版本里的有效节点（草稿、已撤下版本与已停用节点都看不到）');
SELECT t.ok((SELECT count(*) FROM kge.knowledge_edge) = 7 AND NOT EXISTS (SELECT 1 FROM kge.knowledge_edge WHERE status <> 'active'),
            'BFF 只看得到已发布版本里的有效边');
SELECT t.ok(NOT EXISTS (SELECT 1 FROM kge.knowledge_edge WHERE to_map_version_id <> map_version_id),
            'BFF 看不到从已发布版本指向草稿版本的跨版本边');
SELECT t.ok((SELECT array_agg(node_id::text) FROM kge.textbook_projection) = ARRAY['MATH.ALG.T.O']
            AND (SELECT count(*) FROM kge.region_projection) = 0,
            'BFF 只看得到已发布版本里、有效节点上、已核对的投影（草稿、已撤下版本与未核对的都看不到）');
SELECT t.throws($$SELECT verified_by FROM kge.textbook_projection$$, '42501', 'BFF 看不到投影的核对人');
SELECT t.throws($$SELECT count(*) FROM kge.map_change$$, '42501', 'BFF 看不到变更单');
SELECT t.throws($$SELECT count(*) FROM kge.content_review$$, '42501', 'BFF 看不到审核记录');
SELECT t.throws($$SELECT count(*) FROM kge.scoring_point$$, '42501', 'BFF 看不到采分点（spec 待人确认 #4，按默认）');
SELECT t.throws($$SELECT count(*) FROM kge.item_tag$$, '42501', 'BFF 看不到题目标签');
SELECT t.throws($$SELECT kge.publish_map_version('ca400000-0000-4000-8000-00000000ca04')$$, '42501', 'BFF 不能发布地图');
SELECT t.throws($$SELECT kge.validate_map_version('ca400000-0000-4000-8000-00000000ca04')$$, '42501', 'BFF 不能跑发布前校验');
RESET ROLE;

-- ================= 对 platform 的最小改动 =================
SET ROLE starbuddy_learning;
UPDATE platform.curriculum_version SET map_version_id = :kb WHERE id = 'e0000000-0000-4000-8000-0000000000e0';
SELECT t.ok((SELECT map_version_id FROM platform.curriculum_version WHERE id = 'e0000000-0000-4000-8000-0000000000e0') = :kb,
            '内容版本可以引用同学科的地图版本');
SELECT t.throws($$UPDATE platform.curriculum_version SET map_version_id = 'ca200000-0000-4000-8000-00000000ca02' WHERE id = 'e9000000-0000-4000-8000-0000000000e9'$$,
                '23503', '内容版本不能引用别的学科的地图版本');
SELECT t.ok((SELECT count(*) FROM platform.curriculum_version WHERE map_version_id IS NULL) >= 1, '地图版本引用可空（现有内容版本不受影响）');
RESET ROLE;
