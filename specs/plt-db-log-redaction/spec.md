# PLT 数据库服务端日志不带标识原文（RDS 日志参数）

2026-10-27 起由 Claude 推进。来源：specs/plt-model-call-log/lessons.md 待人确认第 9 条（读日志者若能拿到 usage_id 或幂等键原文，就能重算摘要关联调用记录），以及 LT-06 待人确认第 10 条（RDS 错误日志可能含 CHECK 失败的整行）。不改迁移、契约、计量、人设。IaC 改动须人在 plan / apply 时核对，提交首行 `[NEEDS-HUMAN]`。

## 排查结论
- 应用日志已有防线：BFF `observability/log.ts` 与 Python `obs.py` 顶层字段白名单不含 `usage_id`、`idempotency_key`、学生 / 账号 ID，字符串里的 UUID 一律替换为 `[uuid]`；异常只记类名；uvicorn 访问日志关闭；适配器访问日志只有方法、状态、编码、request_id、耗时；调度器只打印配置核对信息。未发现原文外泄。
- **缺口在数据库服务端日志**：`deploy/iac/alicloud/rds.tf` 没设任何日志参数。RDS 默认开慢查询日志，按 PostgreSQL 缺省 `log_parameter_max_length = -1` 会把绑定参数（学生 ID、usage_id、幂等键）整段写进日志；约束冲突的 `DETAIL` 行带键值（例如唯一约束的 `Key (idempotency_key, student_id)=(…)`、CHECK 失败的整行）。两个驱动（asyncpg、node-postgres）都走扩展协议，`STATEMENT` 行只有占位符。
- **残余（安全审查 B1）**：报错的主消息不受 `terse` 影响。现行触发器把标识拼进主消息：0007 `guard_ingest_request`（幂等键原文）、0008 计量账本与预留的三处 RAISE（usage_id 原文）；0005 另有三处行 ID。改这些须改迁移（必停），本次不做，另起 `[NEEDS-HUMAN]` 任务（改为不带标识的主消息，标识放 `USING DETAIL`，客户端仍可收到、服务端日志被 terse 去掉）。

## 范围
1. `rds.tf` 用 `parameters` 块固定：`log_parameter_max_length = 0`、`log_parameter_max_length_on_error = 0`、`log_error_verbosity = terse`（不写 DETAIL / HINT / QUERY / CONTEXT；客户端仍收到完整错误）、`log_statement = none`、`log_duration = off`、`log_min_duration_sample = -1`、`log_transaction_sample_rate = 0`。慢查询日志本身保留（只剩语句与耗时）。
2. `check_iac.py` 新规则 R22：上述参数名字面小写、各恰一条、取值固定，不用 `dynamic` 块；`sql_collector_status` 不写或为字面值 `Disabled`；只读实例、参数模板、SLS 采 RDS 审计列入禁建类型。配变异测试（含大小写、引用、dynamic 三种绕过）。
3. README 写明理由与待人核实项。

## 不做
- 不改应用日志白名单（已覆盖）；不改 RDS 慢查询阈值；不处理已产生的日志（staging 尚未部署）。

## 验收
- 目标限定为：绑定参数、DETAIL、全部语句与采样日志不进数据库服务端日志（主消息残余见上）。
- deploy-check 通过，R22 每条变异都被发现；严格 `make check` 中相关目标通过。

## 待人确认
1. RDS 控制台是否允许修改这七个参数（不允许的须改为提工单或接受风险）；参数修改是否需要重启实例。
2. 关闭 DETAIL 后排障只能看客户端错误（应用日志只记 SQLSTATE 与异常类名），是否可接受。

## 决定记录（2026-10-30，用户；LT-03 阶段 B apply）
1. RDS PostgreSQL 16 不开放修改 `log_statement` 与 `log_error_verbosity`（参数模板里没有，apply 报 `InvalidParameters.Prohibited`）。按用户选择：从 `rds.tf` 去掉这两项，R22 改为“这两项不得写进 parameters”（`RDS_LOG_UNMODIFIABLE`）；其余 5 项照旧固定。
   补偿：`deploy/runbooks/staging-first-deploy.md` 3.1 迁移前人工 `SHOW` 核对，`log_statement` 只能是 `none` / `ddl`，否则停下提工单。
   残余风险：`log_error_verbosity` 不是 `terse` 时，约束冲突等报错日志可能带 DETAIL（含字段值）；不买 KMS 期间 staging 只放合成数据（LT-03 决定记录第 15 条），风险较低，导入真实数据前须复核（可提工单改为 terse）。
