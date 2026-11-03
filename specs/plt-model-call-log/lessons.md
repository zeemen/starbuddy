# 模型调用记录落库（platform.model_call_log）· 实现记录

2026-10-26 由 Claude 推进。提交首行 `[NEEDS-HUMAN]`：迁移草稿 0060（新分区表、RLS FORCE、列级授权、两个 SECURITY DEFINER 函数、个人层表清单登记与删除执行身份授权、报表身份获 platform USAGE）、学习服务写库通道、调度器留存删除任务、安全断言的两处例外。不改契约、适配器、前端、人设。

## 交付
- **迁移草稿 0060**：`platform.model_call_log` 按月分区（预建 2026-10 至 2027-12），只存元数据（时间、环境、任务、数据级别、学科、供应商、模型、版本、model_version、提示词模板与版本、结果、尝试、切备、token、估算成本、耗时、`usage_ref` / `idempotency_ref` 截断摘要），无 uuid 列、文本列全有正则约束；表注释写明“不是计量事实，扣次与消耗以 usage_ledger 为准”，`occurred_at` 为调用结束（入队）时刻。RLS ENABLE + FORCE；只 `starbuddy_learning` 列级 INSERT 且限写入时间窗（前 1 天到后 5 分钟）。
  - `model_call_stats(date, date)`：只授 `starbuddy_report`，按北京时间日 × 环境 × 任务 × 供应商 × 结果聚合；**少于 5 次的格子只给次数**，其余置空（与 insight.onboarding_stats 同口径）。
  - `model_call_log_purge(timestamptz, integer)`：只授 `starbuddy_scheduler`，只删 180 天前的行，分批，时钟不得晚于库时间。
  - **登记个人层表清单**（strategy=delete，顺序 53，回执范围 inference）：删除请求按该学生 usage_reservation / practice_log 的 usage_id 重算摘要匹配删除；删除执行身份获该表 DELETE 与相关列 SELECT（计量表仍为豁免、不可删）。
  - 报表身份获 platform USAGE；79zh 断言其在 platform 下可执行的非触发器函数恰为 `model_call_stats`、`current_account_id`、`evidence_payload_ok`，配变异；攻击面清单 J3 登记；down 注释说明回滚会收回。
- **学习服务**：`ledger.py` 的 `to_row`（与列约束同口径）、`DbSink`（队列 1000、每批 100、整段写库限时 5 秒、退出时限时刷写、失败只记行数与异常类名）、`TeeSink`（单个落地出错不影响其他）；提示词模板白名单（提示词登记表三个 prompt_id + `model-smoke`），不在表中的记录 `source_version='invalid'` 且模板列置空——只影响落库记录，路由返回不变；开关 `LEARNING_MODEL_CALL_LOG`（缺省关），打开时启动核对表与 INSERT 权限、自建小池时也做 TLS 核对；三个无标签计数与两条告警。
- **调度器**：`insight.scheduled model-call-purge`（库时钟、批量 1–10000）；合成时间表每天 04:10、批量 1000，注明待签字；staging 只认签字时间表。四个服务的调度任务名枚举同步加 `model-call-purge`。
- **对账草案** `tools/loadtest/sql/model-call-reconcile.draft.sql`：口径声明改为“属主与授权同 cost-usage-export；对账口径不同，消耗事实以 usage_ledger 为准”，复看预留（settled_qty=0）单独计数；落迁移须另起 `[NEEDS-HUMAN]` PR。
- 部署矩阵 `model_call_log`（关，pending migration_0060）、env-inventory、指标清单、告警、首次部署手册、README 同步；证据包样例 `tables_bff_cannot_read` 37→38（手工更新哈希）。

## 测试
子代理逐目标（严格模式）：db-test 3305 断言；db-mutation 调用记录组 47、攻击面组 44、SIE-03 删除覆盖组 66 全发现；attack-surface-check；learning-test 3914；model-router-mutation 99；internal-v1-mutation 39；scheduler 测试与变异；insight 测试与变异 259；deploy-check；staging-mutation；contract-test 通过。严格 `make check` 见提交说明。

## 审查意见的处理
- 安全审查：无阻断。用户决定：调用记录登记清单、删除请求一并删；报表 USAGE 保留并锁定可调函数；聚合加小样本门槛。建议采纳：模板白名单、取连接限时、自建池 TLS 核对。
- 契约审查：无阻断。建议采纳：表注释写明非计量事实、对账口径声明与复看单列、`occurred_at` 口径说明。

## 教训
- “去标识化”不等于“匿名”：截断摘要只要能由保留的表重算，就能关联回个人，删除请求必须覆盖。
- 给报表等低权限身份开 schema USAGE 时，要用断言锁定其可执行函数集合，否则以后任何忘记撤 PUBLIC 的函数都会被它调用。
- 渗透夹具自检（platform 下禁止 `*_name` 列）会让整组攻击面变异在夹具阶段失败，新表命名要先过这条。
- 守卫钩子按命令文本匹配“token”会误报列名，SQL 写进文件再 `psql -f` 执行（用户批准）。
- 子代理违反约束：一次空 heredoc（未执行任何内容）；一次 shell 命令含 token 字样被拦后按约定停下（符合约定）。

## 待人确认
1. 回执范围 `inference` 与删除顺序 53。
2. 删除匹配只覆盖 usage_reservation 与 practice_log；诊断会话、重放封存里的 usage_id 不在范围；调用方传入非规范 uuid 文本时对不上，只能等 180 天留存删除。
3. **79p 与 80 新增的留存删除例外**（只放行 `model_call_log_purge` 删除本表 180 天前的行）——属安全断言放宽，须人复核。
4. 小样本门槛采用置空；按天切窗口后相邻窗口相减仍可能逼近单次调用（与 SIE-01 待人确认 #5 同类）。
5. 模板白名单需随提示词登记表同步（测试兜底）。
6. 只删行不删分区，空分区由人在分区维护时处理；分区预建到 2027-12，续建随 ADR 05 分区维护任务。
7. 默认值待签字：写库参数（队列 1000、每批 100、限时 5 秒、连接池 2）、留存任务 04:10 / 批量 1000 / 上限 10000、两条告警阈值（15 分钟内 > 0）；签字时间表是否加入 `model-call-purge`。
8. 证据包样例哈希为手工更新，未经 `make gate0-evidence` 重生。
9. 其他服务日志若记录幂等键或 usage_id 原文，读日志者可重算摘要关联调用记录（超出本任务范围，建议一并排查）。
