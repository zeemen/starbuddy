-- 0026 kge · 知识服务只读角色（specs/kge-03-compile-layer/spec.md 设计要点 1、5，待人确认 #3 按默认）
-- 状态：草稿。只写文件，由人执行（CLAUDE.md 必停项：迁移、RLS 与权限、新数据库角色）。
-- 知识服务对各科 Agent 与编排层提供只读工具（node.get / node.path / page.get / item.search / item.similar / rubric.get / node.resolve）。
-- 读路径与写路径分开：starbuddy_kge 是 kge.* 的唯一写入方（导入、打标、审核台后端）；对外读工具用本迁移的 starbuddy_kge_read，
-- 只能读已发布地图版本里的公共知识，不能写任何表，没有任何 platform / insight / policy 表权限，也没有函数执行权（规则 1、2、13）。
-- 请以单事务执行（psql -1）。只用标准 Postgres 16 能力。

-- 角色由人在 db/bootstrap/roles.sql 创建；这里只断言它存在、属性安全，且不与任何其他应用角色共存于同一登录角色
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge_read') THEN
    RAISE EXCEPTION '角色 starbuddy_kge_read 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge_read'
               AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
    RAISE EXCEPTION 'starbuddy_kge_read 必须 NOLOGIN，且不能是 SUPERUSER、BYPASSRLS、CREATEROLE、CREATEDB、REPLICATION';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_kge_read', 'MEMBER')
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_review', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_scheduler', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_insight', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_kge', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_report', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_authz', 'MEMBER'))) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_kge_read 与其他应用角色（读服务不能顺带拿到写权限或个人层权限）';
  END IF;
  -- Postgres 预置角色（pg_read_all_data 等）会绕过表权限；只读角色及继承它的登录角色都不能是它们的成员，也不能有建角色、建库权限
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_kge_read', 'MEMBER')
                AND (r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication
                     OR EXISTS (SELECT 1 FROM pg_roles p
                                 WHERE p.rolname IN ('pg_read_all_data', 'pg_write_all_data', 'pg_read_server_files',
                                                     'pg_write_server_files', 'pg_execute_server_program', 'pg_signal_backend',
                                                     'pg_database_owner', 'pg_checkpoint', 'pg_maintain')
                                   AND pg_has_role(r.oid, p.oid, 'MEMBER')))) THEN
    RAISE EXCEPTION '继承 starbuddy_kge_read 的角色不能是 pg_read_all_data 等预置角色的成员，也不能有 CREATEROLE、CREATEDB、BYPASSRLS、REPLICATION';
  END IF;
END $$;

GRANT USAGE ON SCHEMA kge TO starbuddy_kge_read;
-- 列类型（platform.subject 等）要用 platform schema 的类型；不授予任何 platform 表权限
GRANT USAGE ON SCHEMA platform TO starbuddy_kge_read;

-- ---------- 表权限：只读；审核记录、变更提案、打标候选、ID 迁移、核对人都不给 ----------
GRANT SELECT ON kge.map_version, kge.knowledge_node, kge.knowledge_edge, kge.region_projection, kge.curriculum_item,
                kge.skill_link, kge.item_family, kge.item_tag, kge.scoring_point, kge.current_affair
  TO starbuddy_kge_read;
-- 教材投影：与 BFF 相同的列（核对人工号与核对时间不给读服务）
GRANT SELECT (id, map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to, quote_text,
              quote_hash, mapping_state)
  ON kge.textbook_projection TO starbuddy_kge_read;

-- ---------- RLS：只看已发布地图版本；节点、边只看有效的；投影只看已核对的；打标只看已通过的；时政只看已发布且在有效期内的 ----------
CREATE FUNCTION kge.reader_version_published(p_version uuid) RETURNS boolean
  LANGUAGE sql STABLE SET search_path = pg_catalog, kge AS $$
  SELECT EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = p_version AND v.status = 'published') $$;
COMMENT ON FUNCTION kge.reader_version_published(uuid) IS
  '0026：只读角色的 RLS 判定辅助（调用者权限执行，按调用者自己的可见性判断版本是否已发布）';
REVOKE EXECUTE ON FUNCTION kge.reader_version_published(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.reader_version_published(uuid) TO starbuddy_kge_read;

CREATE POLICY map_version_reader ON kge.map_version FOR SELECT TO starbuddy_kge_read USING (status = 'published');
CREATE POLICY node_reader ON kge.knowledge_node FOR SELECT TO starbuddy_kge_read
  USING (status = 'active' AND kge.reader_version_published(map_version_id));
CREATE POLICY edge_reader ON kge.knowledge_edge FOR SELECT TO starbuddy_kge_read
  USING (status = 'active' AND kge.reader_version_published(map_version_id) AND kge.reader_version_published(to_map_version_id));
CREATE POLICY textbook_reader ON kge.textbook_projection FOR SELECT TO starbuddy_kge_read
  USING (mapping_state = 'verified' AND kge.reader_version_published(map_version_id)
         AND EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = textbook_projection.map_version_id
                       AND n.id = textbook_projection.node_id AND n.status = 'active'));
CREATE POLICY region_reader ON kge.region_projection FOR SELECT TO starbuddy_kge_read
  USING (kge.reader_version_published(map_version_id)
         AND EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = region_projection.map_version_id
                       AND n.id = region_projection.node_id AND n.status = 'active'));
-- 课标条目不挂地图版本（按学科与课标年份），是公开文本
CREATE POLICY curriculum_item_reader ON kge.curriculum_item FOR SELECT TO starbuddy_kge_read USING (true);
CREATE POLICY skill_link_reader ON kge.skill_link FOR SELECT TO starbuddy_kge_read USING (kge.reader_version_published(map_version_id));
CREATE POLICY item_family_reader ON kge.item_family FOR SELECT TO starbuddy_kge_read USING (kge.reader_version_published(map_version_id));
CREATE POLICY item_tag_reader ON kge.item_tag FOR SELECT TO starbuddy_kge_read
  USING (tag_review_status = 'approved' AND kge.reader_version_published(map_version_id));
CREATE POLICY scoring_point_reader ON kge.scoring_point FOR SELECT TO starbuddy_kge_read
  USING (kge.reader_version_published(map_version_id));
CREATE POLICY current_affair_reader ON kge.current_affair FOR SELECT TO starbuddy_kge_read
  USING (review_status = 'published' AND kge.reader_version_published(map_version_id)
         AND current_date >= valid_from AND current_date < valid_until);
