-- KGE-09 地图校准第二步：调用只读导出函数 kge.calibration_export_responses（说明见 tools/kge-calibrate/RUNBOOK.md 第 6 节）。
-- 前提（缺一不可）：export-function.draft.sql 里的两个函数已由另起的 [NEEDS-HUMAN] PR 落为迁移并合并、执行；
-- 工具的 synthetic 守卫已由另起任务、经人确认放开。两者之前不得用这份 SQL 做任何真实导出；演练只用 kge_calibrate synth 的合成数据。
-- 本仓库不执行这份 SQL、没有连库代码。整段在 READ ONLY 事务里；不传 salt（salt 在函数里现场生成）。
-- 输出一份 CSV，record_kind 为 attempt（作答聚合行）或 error（错因行），由执行人按 RUNBOOK 拆成工具的两个输入。
--
-- psql 变量（-v 传入）：
--   subject       平台学科编码（aya / wenli / zhixing / echo）
--   usable_codes  错因分类表里状态“明确”的学科编码（PostgreSQL 数组字面量，如 {gainian_cuowu,yunsuan_cuowu}），
--                 由 python -m kge_calibrate codes --subject <学科> 打印；待定与敏感编码不得列入
--   min_grade_confidence  判分置信度下限（如 0.8；待人确认）

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '10min';

DO $$
BEGIN
  IF current_setting('transaction_read_only') <> 'on' THEN
    RAISE EXCEPTION '会话不是只读：停止';
  END IF;
END
$$;

COPY (
  SELECT record_kind, pseudonym, item_id, node_id, correct, cognitive_level, subject_category, cross_subject_category, confidence
    FROM kge.calibration_export_responses(:'subject'::platform.subject, :'usable_codes'::text[], :'min_grade_confidence'::numeric)
) TO STDOUT WITH (FORMAT csv, HEADER true);

COMMIT;
