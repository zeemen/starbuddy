-- 0026 知识服务只读角色 starbuddy_kge_read（specs/kge-03-compile-layer/spec.md 设计要点 1、5）。全部合成数据。
-- 76 号测试之后：kb（数学 0.2.0）已发布，k1 已退役，kx 为草稿。整个文件在一个事务里，结束回滚。
\set kb '''ca200000-0000-4000-8000-00000000ca02'''
\set k1 '''ca100000-0000-4000-8000-00000000ca01'''
BEGIN;

CREATE FUNCTION t.as_role(p_role text, p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_prev text := current_user; v_out text;
BEGIN
  EXECUTE format('SET LOCAL ROLE %I', p_role);
  EXECUTE p_sql INTO v_out;
  EXECUTE format('SET LOCAL ROLE %I', v_prev);
  RETURN v_out;
END $$;
GRANT EXECUTE ON FUNCTION t.as_role(text, text) TO PUBLIC;
CREATE FUNCTION t.reader_count(p_from text) RETURNS bigint LANGUAGE sql AS $$
  SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM ' || p_from)::bigint $$;
GRANT EXECUTE ON FUNCTION t.reader_count(text) TO PUBLIC;

SELECT t.ok((SELECT status FROM kge.map_version WHERE id = :kb) = 'published' AND (SELECT status FROM kge.map_version WHERE id = :k1) = 'retired',
            '前提：kb 已发布、k1 已退役');

-- ================= 补充反例数据 =================
-- 已发布版本上批准标签、发布时政要走审核记录（0019 的守卫）；这里只是造可见性夹具，以超级用户临时关掉触发器直接改
SET LOCAL session_replication_role = replica;
UPDATE kge.item_tag SET tag_review_status = 'approved' WHERE map_version_id = :kb AND item_id = 'synthetic-0301-07';
INSERT INTO kge.current_affair (map_version_id, node_ids, source, valid_from, valid_until, review_status) VALUES
  (:kb, '{MATH.ALG.T.X}', '合成来源：有效', current_date - 1, current_date + 30, 'published'),
  (:kb, '{MATH.ALG.T.X}', '合成来源：已过期', current_date - 60, current_date, 'published'),
  (:kb, '{MATH.ALG.T.X}', '合成来源：未来生效', current_date + 1, current_date + 30, 'published'),
  (:kb, '{MATH.ALG.T.X}', '合成来源：待审', current_date - 1, current_date + 30, 'pending'),
  (:k1, '{MATH.FUNC.QUAD.VERTEX_FORM}', '合成来源：退役版本', current_date - 1, current_date + 30, 'published');
SET LOCAL session_replication_role = origin;

-- ================= 一、可见性：读到的行数 = 按策略谓词算出的行数；并逐条核对反例 =================
SELECT t.ok(t.reader_count('kge.map_version') = (SELECT count(*) FROM kge.map_version WHERE status = 'published'), '只读角色只看已发布的地图版本');
SELECT t.ok(t.reader_count('kge.map_version WHERE id <> ''ca200000-0000-4000-8000-00000000ca02''') =
            (SELECT count(*) FROM kge.map_version WHERE status = 'published' AND id <> :kb), '退役与草稿版本都看不到');
SELECT t.ok(t.reader_count('kge.knowledge_node') =
            (SELECT count(*) FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id WHERE v.status = 'published' AND n.status = 'active'),
            '节点：只看已发布版本里有效的节点');
SELECT t.ok(t.reader_count('kge.knowledge_node WHERE id = ''MATH.ALG.T.DEP''') = 0, '已停用节点看不到');
SELECT t.ok(t.reader_count('kge.knowledge_node WHERE map_version_id = ''ca100000-0000-4000-8000-00000000ca01''') = 0, '退役版本的节点看不到');
SELECT t.ok(t.reader_count('kge.knowledge_edge') =
            (SELECT count(*) FROM kge.knowledge_edge e WHERE e.status = 'active'
               AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = e.map_version_id AND v.status = 'published')
               AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = e.to_map_version_id AND v.status = 'published')),
            '边：两端版本都已发布且边有效');
SELECT t.ok(t.reader_count('kge.knowledge_edge WHERE to_map_version_id <> map_version_id') =
            (SELECT count(*) FROM kge.knowledge_edge e WHERE e.to_map_version_id <> e.map_version_id AND e.status = 'active'
               AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = e.map_version_id AND v.status = 'published')
               AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = e.to_map_version_id AND v.status = 'published')),
            '跨版本边：对端是草稿时看不到');
SELECT t.ok(t.reader_count('kge.textbook_projection') =
            (SELECT count(*) FROM kge.textbook_projection p JOIN kge.map_version v ON v.id = p.map_version_id
               JOIN kge.knowledge_node n ON n.map_version_id = p.map_version_id AND n.id = p.node_id
              WHERE v.status = 'published' AND p.mapping_state = 'verified' AND n.status = 'active'),
            '教材投影：只看已发布版本里已核对、节点有效的');
SELECT t.ok(t.reader_count('kge.textbook_projection WHERE node_id = ''MATH.ALG.T.DOC''') = 0, '未核对的投影看不到');
SELECT t.ok(t.reader_count('kge.region_projection') =
            (SELECT count(*) FROM kge.region_projection p JOIN kge.map_version v ON v.id = p.map_version_id
               JOIN kge.knowledge_node n ON n.map_version_id = p.map_version_id AND n.id = p.node_id
              WHERE v.status = 'published' AND n.status = 'active'),
            '地区投影：只看已发布版本、节点有效的');
SELECT t.ok(t.reader_count('kge.curriculum_item') = (SELECT count(*) FROM kge.curriculum_item), '课标条目是公开文本，全部可读');
SELECT t.ok(t.reader_count('kge.skill_link') =
            (SELECT count(*) FROM kge.skill_link s JOIN kge.map_version v ON v.id = s.map_version_id WHERE v.status = 'published'),
            '能力视图关联：只看已发布版本');
SELECT t.ok(t.reader_count('kge.item_family') =
            (SELECT count(*) FROM kge.item_family s JOIN kge.map_version v ON v.id = s.map_version_id WHERE v.status = 'published'),
            '题目族：只看已发布版本');
SELECT t.ok(t.reader_count('kge.item_tag') =
            (SELECT count(*) FROM kge.item_tag s JOIN kge.map_version v ON v.id = s.map_version_id
              WHERE v.status = 'published' AND s.tag_review_status = 'approved'),
            '题目标签：只看已发布版本里审核通过的');
SELECT t.ok(t.reader_count('kge.item_tag') >= 1, '前提：至少有一条可见的题目标签');
SELECT t.ok((SELECT count(*) FROM kge.item_tag s JOIN kge.map_version v ON v.id = s.map_version_id
              WHERE v.status = 'published' AND s.tag_review_status <> 'approved') >= 0
            AND t.reader_count('kge.item_tag WHERE tag_review_status <> ''approved''') = 0, '待审、驳回的标签看不到');
SELECT t.ok(t.reader_count('kge.scoring_point') =
            (SELECT count(*) FROM kge.scoring_point s JOIN kge.map_version v ON v.id = s.map_version_id WHERE v.status = 'published'),
            '采分点：只看已发布版本');
SELECT t.ok(t.reader_count('kge.current_affair') =
            (SELECT count(*) FROM kge.current_affair a JOIN kge.map_version v ON v.id = a.map_version_id
              WHERE v.status = 'published' AND a.review_status = 'published' AND current_date >= a.valid_from AND current_date < a.valid_until),
            '时政：读到的行数 = 已发布版本里已发布且在有效期内的行数');
SELECT t.ok(t.as_role('starbuddy_kge_read', 'SELECT string_agg(source, ''|'' ORDER BY source) FROM kge.current_affair WHERE source LIKE ''合成来源：%''') = '合成来源：有效',
            '时政：只看已发布版本里已发布、今天在有效期内的（过期、未生效、待审、退役版本都看不到）');

-- ================= 二、不能写：每张可读表的 INSERT / UPDATE / DELETE 都被拒绝 =================
DO $$
DECLARE r text; v_state text;
BEGIN
  FOREACH r IN ARRAY ARRAY['map_version', 'knowledge_node', 'knowledge_edge', 'textbook_projection', 'region_projection', 'curriculum_item',
                           'skill_link', 'item_family', 'item_tag', 'scoring_point', 'current_affair'] LOOP
    FOREACH v_state IN ARRAY ARRAY['UPDATE kge.%I SET map_version_id = map_version_id', 'DELETE FROM kge.%I', 'TRUNCATE kge.%I'] LOOP
      IF v_state LIKE 'UPDATE%' AND r IN ('map_version', 'curriculum_item') THEN v_state := 'UPDATE kge.%I SET subject = subject'; END IF;
      BEGIN
        SET LOCAL ROLE starbuddy_kge_read;
        EXECUTE format(v_state, r);
        RESET ROLE;
        RAISE EXCEPTION 'kge_read 可以执行：%', format(v_state, r);
      EXCEPTION WHEN insufficient_privilege THEN RESET ROLE;
      END;
    END LOOP;
  END LOOP;
END $$;
SELECT t.ok(true, '只读角色对每张可读表执行 UPDATE、DELETE、TRUNCATE 都报 42501');
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c WHERE c.relnamespace IN (SELECT oid FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'information_schema', 't', 'pg_toast') AND nspname NOT LIKE 'pg_temp%') AND c.relkind IN ('r', 'p', 'v', 'm')
     AND (has_any_column_privilege('starbuddy_kge_read', c.oid, 'INSERT') OR has_any_column_privilege('starbuddy_kge_read', c.oid, 'UPDATE')
          OR has_table_privilege('starbuddy_kge_read', c.oid, 'DELETE') OR has_table_privilege('starbuddy_kge_read', c.oid, 'TRUNCATE'))),
  '只读角色在任何 schema 的任何表上都没有 INSERT、UPDATE、DELETE、TRUNCATE');

-- ================= 三、个人层与其他 schema：没有任何表权限 =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname NOT IN ('kge', 'pg_catalog', 'information_schema', 't') AND n.nspname NOT LIKE 'pg_%'
     AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND has_any_column_privilege('starbuddy_kge_read', c.oid, 'SELECT')),
  '只读角色读不到 kge 以外任何 schema 的表（platform、insight、policy、public）');
SELECT t.ok(NOT has_schema_privilege('starbuddy_kge_read', 'insight', 'USAGE') AND NOT has_schema_privilege('starbuddy_kge_read', 'policy', 'USAGE'),
            '只读角色没有 insight、policy schema 的使用权');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM platform.item')$$, '42501', '读不到题目登记表');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM platform.attempt')$$, '42501', '读不到作答');

-- ================= 四、kge 里的治理与审核对象：读不到 =================
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM kge.content_review')$$, '42501', '读不到审核记录');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM kge.map_change')$$, '42501', '读不到地图变更提案');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM kge.tag_candidate')$$, '42501', '读不到打标候选');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT count(*) FROM kge.id_migration')$$, '42501', '读不到 ID 迁移表');
SELECT t.throws($$SELECT t.as_role('starbuddy_kge_read', 'SELECT verified_by FROM kge.textbook_projection LIMIT 1')$$, '42501', '读不到投影的核对人工号');
SELECT t.ok(t.as_role('starbuddy_kge_read', 'SELECT count(quote_text)::text FROM kge.textbook_projection')::int >= 1, '能读已核对投影的原句');

-- ================= 五、函数：除 RLS 辅助函数外不能执行 kge、platform、insight、policy 的任何函数 =================
SELECT t.ok(NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname IN ('kge', 'platform', 'insight', 'policy') AND p.prosecdef
     AND has_function_privilege('starbuddy_kge_read', p.oid, 'EXECUTE')),
  '只读角色不能执行任何 SECURITY DEFINER 函数（发布、签字、打标决定等）');
SELECT t.ok((SELECT NOT prosecdef FROM pg_proc WHERE oid = 'kge.reader_version_published(uuid)'::regprocedure),
            'RLS 辅助函数以调用者权限执行（不能借它越过版本可见性）');
SELECT t.ok(NOT has_function_privilege('starbuddy_bff', 'kge.reader_version_published(uuid)', 'EXECUTE')
            AND NOT has_function_privilege('starbuddy_learning', 'kge.reader_version_published(uuid)', 'EXECUTE'),
            'RLS 辅助函数只授给只读角色');

-- ================= 六、角色属性 =================
SELECT t.ok((SELECT NOT (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolreplication)
               FROM pg_roles WHERE rolname = 'starbuddy_kge_read'), '只读角色 NOLOGIN、非超级用户、不绕过 RLS');
SELECT t.ok(NOT pg_has_role('starbuddy_kge_read', 'starbuddy_kge', 'MEMBER') AND NOT pg_has_role('starbuddy_kge', 'starbuddy_kge_read', 'MEMBER'),
            '只读角色与写入角色互不继承');

ROLLBACK;
