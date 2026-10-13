-- KGE-07 第一步：语文覆盖报表的库上输入（specs/kge07-chinese-slice/spec.md 范围 2）。
-- 只读：READ ONLY 事务，最后回滚；不写任何表。本仓库的工具不连库；由人在演练库上执行（CLAUDE.md 规则 9）：
--   psql -X -q -At -v ON_ERROR_STOP=1 -v map_version_id=<语文地图版本 uuid> -f tools/kge-coverage/sql/chinese-snapshot.sql > snapshot.json
--   cd tools/kge-coverage && node src/main.ts --subject wenli --snapshot snapshot.json --out report
-- 输出一行 JSON（format = kge-chinese-snapshot/1），字段与排序与导入器的离线快照一致（tools/kge-import/src/chinese/seed.ts；
-- tools/kge-import/tests/chinese/chinese-db.sh 核对两边逐项相同）。节点含停用的（报表只算有效的）；边只取本版本内、有效的。
-- 不含教材原句（本来就不入库）、核对人工号、边的依据文字；不含任何学生数据。
\set QUIET on
\set ON_ERROR_STOP on
\if :{?map_version_id}
\else
  \echo 'chinese-snapshot.sql：需要 -v map_version_id=<地图版本 uuid>'
  SELECT 1 / 0 AS "缺少 map_version_id";
\endif
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SELECT EXISTS (SELECT 1 FROM kge.map_version WHERE id = :'map_version_id' AND subject = 'wenli') AS mv_exists \gset
\if :mv_exists
\else
  \echo 'chinese-snapshot.sql：语文地图版本不存在'
  SELECT 1 / 0 AS "地图版本不存在";
\endif
SELECT jsonb_build_object(
  'format', 'kge-chinese-snapshot/1',
  'subject', 'wenli',
  'map_version_id', v.id,
  'version', v.version,
  'nodes', coalesce((SELECT jsonb_agg(jsonb_build_object('id', n.id, 'level', n.level, 'parent_id', n.parent_id, 'kind', n.kind, 'name', n.name,
                                                          'scope', n.scope, 'cognitive_levels', to_jsonb(n.cognitive_levels), 'status', n.status)
                                      ORDER BY n.id COLLATE "C")
                       FROM kge.knowledge_node n WHERE n.map_version_id = v.id), '[]'::jsonb),
  'edges', coalesce((SELECT jsonb_agg(jsonb_build_object('from_id', e.from_id, 'to_id', e.to_id, 'type', e.type, 'strength', e.strength)
                                      ORDER BY e.from_id COLLATE "C", e.to_id COLLATE "C", e.type COLLATE "C")
                       FROM kge.knowledge_edge e WHERE e.map_version_id = v.id AND e.to_map_version_id = v.id AND e.status = 'active'), '[]'::jsonb),
  'projections', coalesce((SELECT jsonb_agg(jsonb_build_object('id', p.id, 'node_id', p.node_id, 'edition', p.edition, 'edition_year', p.edition_year,
                                                                'volume', p.volume, 'chapter', p.chapter, 'page_from', p.page_from, 'page_to', p.page_to,
                                                                'mapping_state', p.mapping_state) ORDER BY p.id::text COLLATE "C")
                             FROM kge.textbook_projection p WHERE p.map_version_id = v.id), '[]'::jsonb)
)::text
FROM kge.map_version v WHERE v.id = :'map_version_id';
ROLLBACK;
