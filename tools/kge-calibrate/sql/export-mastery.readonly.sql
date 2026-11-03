-- KGE-09 地图校准：调用只读导出函数（说明见 tools/kge-calibrate/RUNBOOK.md）。
-- 前提（缺一不可）：export-function.draft.sql 已由另起的 [NEEDS-HUMAN] PR 落为迁移并合并、执行；
-- 工具的 synthetic 守卫已由另起任务、经人确认放开。两者之前不得用这份 SQL 做任何真实导出；演练只用 kge_calibrate synth 的合成数据。
-- 本仓库不执行这份 SQL、没有连库代码。整段在 READ ONLY 事务里；不传 salt（salt 在函数里现场生成）。
--
-- psql 变量（-v 传入）：
--   subject    平台学科编码（aya / wenli / zhixing / echo）
--   star_rule  该学科掌握配置 services/learning/config/mastery/<学科>.json 里 star_level_rule 的原文（JSON）

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
  SELECT pseudonym, node_id, gate, p_mastery
    FROM kge.calibration_export_mastery(:'subject'::platform.subject, :'star_rule'::jsonb)
) TO STDOUT WITH (FORMAT csv, HEADER true);

COMMIT;
