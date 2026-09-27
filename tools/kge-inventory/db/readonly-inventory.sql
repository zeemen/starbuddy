-- KGE-00 库模式：各科 legacy 数据库的只读内容盘点（specs/kge-00-asset-inventory/spec.md 设计要点 4）。
--
-- 由有只读权限的人运行，本仓库不自动执行、不保存任何连接信息（CLAUDE.md 规则 9）：
--   psql "<只读连接串>" -X -At -F '|' -f tools/kge-inventory/db/readonly-inventory.sql > db-output-<库名>.txt
-- 同一份 SQL 对阿鸦库与文砺库都能跑；库里没有的表输出 absent，没有的列输出 column absent。
--
-- 只查白名单里的内容表（知识点、题目、篇目、原文、Rubric），不查任何学生个人表，
-- 只输出计数、分组计数与列名，不输出任何行内容。整段在只读事务里执行，最后回滚。
\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';

WITH spec(section, tbl, col) AS (
  VALUES
    -- 阿鸦库：schema.sql 建了但代码不用的内容表
    ('rows',    'knowledge_nodes',   NULL),
    ('group',   'knowledge_nodes',   'kind'),
    ('rows',    'questions',         NULL),
    -- 文砺 / 知行库
    ('rows',    'knowledge_point',   NULL),
    ('group',   'knowledge_point',   'domain'),
    ('group',   'knowledge_point',   'level'),
    ('group',   'knowledge_point',   'status'),
    ('notnull', 'knowledge_point',   'catalog_ref'),
    ('rows',    'item_bank',         NULL),
    ('group',   'item_bank',         'domain'),
    ('group',   'item_bank',         'review_status'),
    ('group',   'item_bank',         'source'),
    ('notnull', 'item_bank',         'textbook_anchor'),
    ('rows',    'textbook_catalog',  NULL),
    ('true',    'textbook_catalog',  'must_recite'),
    ('group',   'textbook_catalog',  'grade'),
    ('rows',    'recitation_corpus', NULL),
    ('true',    'recitation_corpus', 'must_recite'),
    ('rows',    'classical_text',    NULL),
    ('group',   'classical_text',    'status'),
    ('rows',    'daofa_item',        NULL),
    ('group',   'daofa_item',        'status'),
    ('notnull', 'daofa_item',        'rubric'),
    ('rows',    'doc_chunks',        NULL),
    ('group',   'doc_chunks',        'source')
),
resolved AS (
  SELECT s.section, s.tbl, s.col, t.table_schema,
         (c.column_name IS NOT NULL) AS col_ok
  FROM spec s
  LEFT JOIN information_schema.tables t
         ON t.table_schema = 'public' AND t.table_name = s.tbl
  LEFT JOIN information_schema.columns c
         ON c.table_schema = 'public' AND c.table_name = s.tbl AND c.column_name = s.col
)
SELECT section, tbl || COALESCE('.' || col, '') AS key,
       CASE
         WHEN table_schema IS NULL THEN 'absent'
         WHEN col IS NOT NULL AND NOT col_ok THEN 'column absent'
         ELSE (xpath('/row/v/text()', query_to_xml(
                CASE section
                  WHEN 'rows'    THEN format('SELECT count(*)::text AS v FROM %I.%I', table_schema, tbl)
                  WHEN 'true'    THEN format('SELECT count(*)::text AS v FROM %I.%I WHERE %I', table_schema, tbl, col)
                  WHEN 'notnull' THEN format('SELECT count(*)::text AS v FROM %I.%I WHERE %I IS NOT NULL', table_schema, tbl, col)
                  WHEN 'group'   THEN format('SELECT COALESCE(json_object_agg(k, n ORDER BY k), ''{}'')::text AS v FROM (SELECT COALESCE(%I::text, ''(null)'') AS k, count(*) AS n FROM %I.%I GROUP BY 1) g', col, table_schema, tbl)
                END, false, true, '')))[1]::text
       END AS value
FROM resolved
UNION ALL
SELECT 'columns', c.table_name, string_agg(c.column_name, ',' ORDER BY c.ordinal_position)
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN ('knowledge_nodes', 'questions', 'knowledge_point', 'item_bank', 'textbook_catalog',
                       'recitation_corpus', 'classical_text', 'daofa_item', 'doc_chunks')
GROUP BY c.table_name
ORDER BY 1, 2;

ROLLBACK;
