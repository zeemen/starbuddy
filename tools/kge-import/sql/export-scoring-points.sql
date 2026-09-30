-- 冲刺 4-K：采分点原子层导出（make zhixing-rubric-check 的比对输入；specs/kge-daofa-slice-sprint4/spec.md 设计要点 3）。
-- 只读：READ ONLY 事务，最后回滚。输出一行 JSON（format = kge-scoring-point-export/1），按（题目, rubric_version, 采分点）排序。
-- 用法：psql -X -q -At -v ON_ERROR_STOP=1 -v subject=zhixing -f tools/kge-import/sql/export-scoring-points.sql > export.json
-- 只导出比对需要的列；不含任何学生数据（kge 是公共知识层）。
\set QUIET on
\set ON_ERROR_STOP on
\if :{?subject}
\else
  \echo 'export-scoring-points.sql: 缺 -v subject=<学科>'
  SELECT 1 / 0 AS "缺少 subject";
\endif
BEGIN READ ONLY;
SELECT jsonb_build_object(
  'format', 'kge-scoring-point-export/1',
  'subject', :'subject',
  'points', coalesce((
    SELECT jsonb_agg(jsonb_build_object(
             'item_id', s.item_id, 'rubric_version', s.rubric_version, 'point_id', s.point_id, 'score', s.score,
             'node_ids', to_jsonb(s.node_ids::text[]), 'subject_dimension', s.subject_dimension, 'keyword_family', s.keyword_family,
             'required_logic', s.required_logic, 'material_linkage', s.material_linkage, 'partial_credit_rule', s.partial_credit_rule,
             'common_errors', s.common_errors)
           ORDER BY s.item_id COLLATE "C", s.rubric_version COLLATE "C", s.point_id COLLATE "C")
      FROM kge.scoring_point s
     WHERE s.subject = (:'subject')::platform.subject), '[]'::jsonb))::text;
ROLLBACK;
