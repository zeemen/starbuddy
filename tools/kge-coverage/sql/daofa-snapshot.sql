-- 冲刺 4-K：道法覆盖报表的库上输入（specs/kge-daofa-slice-sprint4/spec.md 设计要点 6）。
-- 只读：READ ONLY 事务，最后回滚；不写任何表。本仓库的工具不连库；由人在演练库上执行（CLAUDE.md 规则 9）：
--   psql -X -q -At -v ON_ERROR_STOP=1 -v map_version_id=<道法地图版本 uuid> -f tools/kge-coverage/sql/daofa-snapshot.sql > snapshot.json
--   cd tools/kge-coverage && node src/main.ts --subject zhixing --snapshot snapshot.json --out report
-- 输出一行 JSON（format = kge-daofa-snapshot/1），字段与排序与导入器的离线快照一致（tools/kge-import/src/daofa/snapshot.ts；
-- tools/kge-import/tests/daofa-db.sh 核对两边逐项相同）。节点含停用的（报表只算有效的）；时政含全部审核状态（报表不算已下架的）。
-- 不含教材原句（本来就不入库）、核对人工号、时政来源、采分点的关键词与给分规则；不含任何学生数据。
\set QUIET on
\set ON_ERROR_STOP on
\if :{?map_version_id}
\else
  \echo 'daofa-snapshot.sql：需要 -v map_version_id=<地图版本 uuid>'
  SELECT 1 / 0 AS "缺少 map_version_id";
\endif
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
SELECT EXISTS (SELECT 1 FROM kge.map_version WHERE id = :'map_version_id' AND subject = 'zhixing') AS mv_exists \gset
\if :mv_exists
\else
  \echo 'daofa-snapshot.sql：道法地图版本不存在'
  SELECT 1 / 0 AS "地图版本不存在";
\endif
SELECT jsonb_build_object(
  'format', 'kge-daofa-snapshot/1',
  'subject', 'zhixing',
  'map_version_id', v.id,
  'version', v.version,
  'nodes', coalesce((SELECT jsonb_agg(jsonb_build_object('id', n.id, 'level', n.level, 'parent_id', n.parent_id, 'kind', n.kind, 'name', n.name,
                                                          'cognitive_levels', to_jsonb(n.cognitive_levels), 'status', n.status) ORDER BY n.id COLLATE "C")
                       FROM kge.knowledge_node n WHERE n.map_version_id = v.id), '[]'::jsonb),
  'edges', coalesce((SELECT jsonb_agg(jsonb_build_object('from_id', e.from_id, 'to_id', e.to_id, 'type', e.type)
                                      ORDER BY e.from_id COLLATE "C", e.to_id COLLATE "C", e.type COLLATE "C")
                       FROM kge.knowledge_edge e WHERE e.map_version_id = v.id AND e.to_map_version_id = v.id AND e.status = 'active'), '[]'::jsonb),
  'projections', coalesce((SELECT jsonb_agg(jsonb_build_object('id', p.id, 'node_id', p.node_id, 'edition', p.edition, 'edition_year', p.edition_year,
                                                                'volume', p.volume, 'chapter', p.chapter, 'page_from', p.page_from, 'page_to', p.page_to,
                                                                'mapping_state', p.mapping_state) ORDER BY p.id::text COLLATE "C")
                             FROM kge.textbook_projection p WHERE p.map_version_id = v.id), '[]'::jsonb),
  'scoring_points', coalesce((SELECT jsonb_agg(jsonb_build_object('item_id', s.item_id, 'rubric_version', s.rubric_version, 'point_id', s.point_id,
                                                                   'score', s.score, 'node_ids', to_jsonb(s.node_ids::text[]))
                                               ORDER BY s.item_id COLLATE "C", s.rubric_version COLLATE "C", s.point_id COLLATE "C")
                                FROM kge.scoring_point s WHERE s.map_version_id = v.id), '[]'::jsonb),
  'current_affairs', coalesce((SELECT jsonb_agg(jsonb_build_object('id', c.id, 'node_ids', to_jsonb(c.node_ids::text[]),
                                                                    'valid_from', to_char(c.valid_from, 'YYYY-MM-DD'),
                                                                    'valid_until', to_char(c.valid_until, 'YYYY-MM-DD'), 'review_status', c.review_status)
                                                ORDER BY c.id::text COLLATE "C")
                                 FROM kge.current_affair c WHERE c.map_version_id = v.id), '[]'::jsonb)
)::text
FROM kge.map_version v WHERE v.id = :'map_version_id';
ROLLBACK;
