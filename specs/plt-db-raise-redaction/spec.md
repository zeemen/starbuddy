# PLT 数据库报错主消息不带个人行标识（迁移草稿 0061）

2026-10-27 起由 Claude 推进。来源：specs/plt-db-log-redaction/lessons.md 待人确认第 3 条。`log_error_verbosity = terse` 只去掉 DETAIL，主消息照写进 RDS 服务端日志；下列触发器函数把 usage_id、幂等键或个人层行 ID 拼进了主消息。提交首行 `[NEEDS-HUMAN]`（迁移；其中三个函数属计量触发器，只改报错文本，不改判定逻辑）。

## 范围
1. 迁移草稿 0061（up / down）对下列函数 `CREATE OR REPLACE`，函数体以**现行最新定义**为准逐字复制，只改 RAISE：主消息去掉标识，标识移到 `USING DETAIL`（客户端仍可收到，服务端日志被 terse 去掉）；SQLSTATE、判定条件、属主、`SECURITY` 属性、`search_path`、授权全部不变。
   - `platform.guard_ingest_request`（0007，幂等键）
   - `platform.guard_usage_reservation`、`platform.guard_ledger_insert`（0008，usage_id 三处）、`platform.guard_subscription`（0008，订阅 ID）
   - `platform.grade_dimension_requires_review`、`platform.guard_supersede`（0005，行 ID）、`platform.check_supersede_target`（最新定义在 0037）
   - 主消息保留现有测试依赖的子串（如 `already has a reserve entry`、`already has a terminal entry`）。
   - down 恢复为迁移前的现行定义。
2. 库测试：每个函数触发一次，断言主消息不含该行标识、DETAIL 含之、SQLSTATE 不变；新增一条静态断言：platform / insight / kge 下现行函数体里 `RAISE … '%…'` 的参数不得是 `*_id`、`id`、`*_key`（模板键、规则键、知识节点等公共层标识列入显式豁免清单）。配变异（把任一函数改回带标识，断言必须失败）。
3. 迁移演练（up → down → up）通过。

## 不做
- 不改判定逻辑、错误码、契约、应用代码；公共层标识（判断模板键、规则键、知识节点 ID、届别年级）不动。

## 验收
- 严格模式 db-test、db-mutation、deploy-check（迁移演练）通过；新断言与变异全发现。

## 待人确认
1. 豁免清单的范围。
2. 0061 与 0010–0060 一起由人执行；计量触发器仅改文本，是否仍需计量负责人单独签字。
