# 数据库服务端日志不带标识原文 · 实现记录

2026-10-27 由 Claude 推进。来源：plt-model-call-log 待人确认第 9 条、LT-06 待人确认第 10 条。提交首行 `[NEEDS-HUMAN]`（IaC 日志策略，apply 前须人核对）。不改迁移、契约、计量、人设、应用代码。

## 排查
- 应用日志（BFF log.ts、三个 Python obs.py、适配器访问日志、调度器）已由字段白名单与 UUID 遮盖覆盖，未发现 usage_id、幂等键、学生 / 账号 ID 原文外泄。
- 缺口在 RDS 服务端日志：慢查询日志按缺省会写全部绑定参数；报错 DETAIL 带唯一约束键值与 CHECK 失败整行。

## 交付
- `deploy/iac/alicloud/rds.tf`：七个日志参数固定（见 spec 范围 1）。
- `deploy/iac/check_iac.py` R22 与禁建类型三项；`deploy/tests/test_iac.py` 新增 14 条变异；README 规则数 R01–R22。

## 测试
- 严格模式 deploy-check（含 test_iac 107 项、迁移演练）、staging-mutation（89 + 9）通过。只动 deploy/ 与 specs/，未跑全量 `make check`。

## 审查意见的处理
- 安全审查 B2（R22 可被大小写、引用、dynamic 绕过）：已修，配变异。建议 3（采样与耗时日志）、4（sql_collector_status 改白名单、禁建参数模板与 SLS 采 RDS 审计）、5（只读实例）、7（注释位置）已采纳。
- B1（主消息带标识）：涉及迁移，按默认做法如实记录、另起任务；验收目标相应限定。

## 待人确认
1. 控制台能否修改这七个参数、是否需重启；不开放的参数由谁接受残余风险。
2. 关闭 DETAIL 后服务端排障只剩主消息与 SQLSTATE，是否可接受。
3. 另起迁移任务：0007 `guard_ingest_request`、0008 三处 RAISE 去掉主消息里的幂等键与 usage_id（0005 三处行 ID 一并评估）。
4. RDS 是否预加载 auto_explain：若有，须固定 `auto_explain.log_min_duration = -1` 或 `auto_explain.log_parameter_max_length = 0`（未加入 R22，参数在未加载时可能不可设）。
5. `log_min_error_statement` 是否改为 panic（当前驱动走扩展协议，STATEMENT 行只有占位符；属纵深防御，代价是少一行语句文本）。
6. 迁移 lint 是否禁止 `ALTER ROLE / DATABASE … SET log_*` 放宽实例级设置；`pg_read_all_stats` 可见 pg_stat_statements 语句文本（已归一化为 `$n`）。

## 教训
- `terse` 只去 DETAIL 等附加字段，主消息照写；排查日志外泄要连同数据库函数里的 RAISE 文本一起看。
- 静态规则按字面键匹配时，要先归一化并拒绝非字面值与 dynamic 块，否则加一条变体就能覆盖。
