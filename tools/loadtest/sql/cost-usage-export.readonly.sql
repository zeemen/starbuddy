-- PLT-17 成本仪表盘：调用只读导出函数（说明见 tools/loadtest/RUNBOOK.md 第 4 节）。
-- 前提（缺一不可）：cost-usage-export.draft.sql 已由另起的 [NEEDS-HUMAN] PR 落为迁移并合并、执行；
-- 工具的 synthetic 守卫已由另起任务、经人确认放开。两者之前不得用这份 SQL 做任何真实导出；演练只用 dashboard-synth 的合成数据。
-- 本仓库不执行这份 SQL、没有连库代码。整段在 READ ONLY 事务里；不传 salt（salt 在函数里现场生成）。
-- 窗口固定为每名学生导出时主订阅的当前计费周期（不收日期参数）；档位为导出时档位，期内升降档未拆分。
--
-- psql 变量（-v 传入）：
--   config_version    额度配置版本（须与 tools/loadtest/config/cost_profile.yaml 的 quota_config 相同，如 v12-pilot@1）
--   consent_versions  试点参与同意文档版本白名单，Postgres 数组字面量（如 {pilot-consent@1}）

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
  SELECT pseudonym, month, tier, subject, resource, quantity
    FROM platform.cost_export_usage(:'config_version', :'consent_versions'::text[])
) TO STDOUT WITH (FORMAT csv, HEADER true);

COMMIT;
