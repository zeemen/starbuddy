-- KGE-02 覆盖矩阵（specs/kge-02-atoms-tagging/spec.md 设计要点 4）：按库里的 kge.item_tag / kge.knowledge_node 算与
-- tools/kge-coverage 报表同样的数字，供人在演练库上核对报表。只读：整段在 READ ONLY 事务里，最后回滚；不写任何表。
-- 本仓库的工具不连库、不执行本文件；由人在演练库上执行（CLAUDE.md 规则 9：不连生产库）：
--   psql -X -v ON_ERROR_STOP=1 -v map_version_id=<地图版本 uuid> -f tools/kge-coverage/sql/coverage.sql
-- 输出分段，每段以一行 “## <段名>” 开头，后接 CSV（Postgres COPY csv，带表头）：
--   matrix               与报表的 coverage-<版本>.csv 逐字节相同（同一批标签时）
--   summary              有主考点题的节点数、矩阵行数、题目数（报表第 1 节）
--   uncovered            没有主考点题的节点（报表第 3 节）
--   node_count           题目节点数分布（报表第 4 节）
--   over_limit           节点数超过 3 个的题（报表第 4 节）
--   item_layer           题目标签的来源层（报表第 5 节“题目标签的来源层”；需要迁移 0024 的 tag_source 列，没有时跳过）
--   candidate_layer      kge.tag_candidate 各层候选条数（需要迁移 0024，没有时跳过）
-- 口径与报表一致：矩阵行 = level 为 node / detail 的有效节点；主考点题按认知层次分列，未标层次记 unlabeled；
-- 关联 = 出现在 node_ids 但不是主考点；题目节点数 = {主考点} ∪ node_ids 去重。
-- 与报表的差别：库里驳回（tag_review_status = 'rejected'）的标签不计（同 kge.validate_map_version 的口径）；
-- 库里没有“无处可标”的题（main_node_id 非空）与 A/B 分级（分级只在打标报告与 kge.tag_candidate.grade 里）。
\set QUIET on
\set ON_ERROR_STOP on
\if :{?map_version_id}
\else
  \echo 'coverage.sql：需要 -v map_version_id=<地图版本 uuid>'
  SELECT 1 / 0 AS "缺少 map_version_id";
\endif
BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog;
-- 地图版本必须存在（输错 uuid 时不要默默输出空表）
SELECT EXISTS (SELECT 1 FROM kge.map_version WHERE id = :'map_version_id') AS mv_exists \gset
\if :mv_exists
\else
  \echo 'coverage.sql：地图版本不存在'
  SELECT 1 / 0 AS "地图版本不存在";
\endif
SELECT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'kge' AND table_name = 'item_tag' AND column_name = 'tag_source') AS has_0024 \gset

\echo '## matrix'
COPY (
  WITH n AS (
    SELECT id, name, kind FROM kge.knowledge_node
     WHERE map_version_id = :'map_version_id' AND level IN ('node', 'detail') AND status = 'active'
  ), t AS (
    SELECT main_node_id, node_ids, cognitive_level FROM kge.item_tag
     WHERE map_version_id = :'map_version_id' AND tag_review_status <> 'rejected'
  )
  SELECT n.id AS node_id, n.name, n.kind,
         count(t.main_node_id) FILTER (WHERE t.cognitive_level = 'know') AS know,
         count(t.main_node_id) FILTER (WHERE t.cognitive_level = 'understand') AS understand,
         count(t.main_node_id) FILTER (WHERE t.cognitive_level = 'apply') AS apply,
         count(t.main_node_id) FILTER (WHERE t.cognitive_level = 'synthesize') AS synthesize,
         count(t.main_node_id) FILTER (WHERE t.cognitive_level IS NULL) AS unlabeled,
         count(t.main_node_id) AS main_total,
         (SELECT count(*) FROM t r WHERE n.id = ANY (r.node_ids) AND r.main_node_id <> n.id) AS related_total,
         CASE WHEN count(t.main_node_id) > 0 THEN 'yes' ELSE 'no' END AS covered
    FROM n LEFT JOIN t ON t.main_node_id = n.id
   GROUP BY n.id, n.name, n.kind
   ORDER BY n.id COLLATE "C"
) TO STDOUT WITH (FORMAT csv, HEADER);

\echo '## summary'
COPY (
  WITH n AS (
    SELECT id FROM kge.knowledge_node
     WHERE map_version_id = :'map_version_id' AND level IN ('node', 'detail') AND status = 'active'
  ), t AS (
    SELECT main_node_id FROM kge.item_tag WHERE map_version_id = :'map_version_id' AND tag_review_status <> 'rejected'
  )
  SELECT (SELECT count(*) FROM n WHERE EXISTS (SELECT 1 FROM t WHERE t.main_node_id = n.id)) AS covered_nodes,
         (SELECT count(*) FROM n) AS total_nodes,
         (SELECT count(*) FROM t) AS items
) TO STDOUT WITH (FORMAT csv, HEADER);

\echo '## uncovered'
COPY (
  SELECT n.id AS node_id, n.name, n.kind
    FROM kge.knowledge_node n
   WHERE n.map_version_id = :'map_version_id' AND n.level IN ('node', 'detail') AND n.status = 'active'
     AND NOT EXISTS (SELECT 1 FROM kge.item_tag t WHERE t.map_version_id = n.map_version_id AND t.main_node_id = n.id
                        AND t.tag_review_status <> 'rejected')
   ORDER BY n.id COLLATE "C"
) TO STDOUT WITH (FORMAT csv, HEADER);

\echo '## node_count'
COPY (
  SELECT k AS nodes, count(*) AS items
    FROM (SELECT cardinality(ARRAY(SELECT DISTINCT x FROM unnest(t.node_ids || t.main_node_id) x)) AS k
            FROM kge.item_tag t WHERE t.map_version_id = :'map_version_id' AND t.tag_review_status <> 'rejected') s
   GROUP BY k ORDER BY k
) TO STDOUT WITH (FORMAT csv, HEADER);

\echo '## over_limit'
COPY (
  SELECT item_id, k AS nodes
    FROM (SELECT t.item_id, cardinality(ARRAY(SELECT DISTINCT x FROM unnest(t.node_ids || t.main_node_id) x)) AS k
            FROM kge.item_tag t WHERE t.map_version_id = :'map_version_id' AND t.tag_review_status <> 'rejected') s
   WHERE k > 3
   ORDER BY item_id COLLATE "C"
) TO STDOUT WITH (FORMAT csv, HEADER);

\if :has_0024
\echo '## item_layer'
COPY (
  SELECT coalesce(tag_source, 'none') AS layer, count(*) AS items
    FROM kge.item_tag WHERE map_version_id = :'map_version_id' AND tag_review_status <> 'rejected'
   GROUP BY 1
   ORDER BY array_position(ARRAY['rule', 'vector', 'model', 'human', 'none'], coalesce(tag_source, 'none')), coalesce(tag_source, 'none') COLLATE "C"
) TO STDOUT WITH (FORMAT csv, HEADER);

\echo '## candidate_layer'
COPY (
  SELECT layer, count(*) AS candidates
    FROM kge.tag_candidate WHERE map_version_id = :'map_version_id'
   GROUP BY 1
   ORDER BY array_position(ARRAY['rule', 'vector', 'model'], layer), layer COLLATE "C"
) TO STDOUT WITH (FORMAT csv, HEADER);
\else
\echo '## item_layer'
\echo '（跳过：库里还没有迁移 0024 的 tag_source 列）'
\echo '## candidate_layer'
\echo '（跳过：库里还没有迁移 0024 的 kge.tag_candidate 表）'
\endif
ROLLBACK;
