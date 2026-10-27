# 数据库报错主消息不带个人行标识（迁移草稿 0061）· 实现记录

2026-10-27 由 Claude 推进。来源：specs/plt-db-log-redaction 待人确认第 3 条。提交首行 `[NEEDS-HUMAN]`（迁移；含四个计量函数，只改报错文本）。不改判定逻辑、错误码、契约、应用代码。

## 交付
- **迁移草稿 0061**：9 个函数 `CREATE OR REPLACE`，函数体逐字复制现行最新定义，只改 RAISE——主消息去掉标识，标识移到 `USING DETAIL`（客户端仍可收到；RDS `log_error_verbosity = terse` 后不进服务端日志）。SQLSTATE、条件、`SECURITY`、`search_path`、属主与授权不变；down 逐字恢复。
  - `guard_ingest_request`（0007，幂等键与写入方法）
  - 计量：`guard_subscription`、`guard_usage_reservation`、`guard_ledger_insert`（0008）、`meter_check_entitlement`（0009）
  - `grade_dimension_requires_review`、`guard_supersede`（0005）、`check_supersede_target`（最新定义在 0037）
  - `review_sample_weekly_reports`（0036，周报 ID）
  - 测试依赖的子串保留（`already has a reserve entry`、`invalid supersede target`、`取不到复核任务的学科` 等）。
- **79zi**：每处 RAISE 触发一次（主消息不含标识、DETAIL 含之、SQLSTATE 不变）；静态扫描 `t.raise_hits` 覆盖 platform / insight / kge / policy 下的 plpgsql 函数：记录变量的 `x.id` / `x.*_id` / `x.*_key`、名字含标识词的 `p_*` / `v_*`、整行 NEW / OLD、`row_to_json` 类、`USING MESSAGE =` / `:=`；豁免三条公共层标识（判断模板键、干预规则键、知识节点 ID），另断言豁免不过期；自检用合成源码防空转。
- **变异**：“报错脱敏”组 7 条（三类主消息带回标识、函数参数、记录变量、DETAIL 丢标识、只有静态扫描能发现的新函数）。

## 测试
- 子代理（严格模式，逐个）：db-test（61 个迁移 up → down → up、0061 单独 down → up，3318 项断言）、分组 db-mutation（报错脱敏、周报、计量核对、计量只读、0009 计量函数相关）全发现、deploy-check（迁移演练）、attack-surface-check。
- 全量 db-mutation（严格模式，独立工作区）：1250 个变异全部被发现；采纳复核建议后 db-test 3319 项断言通过、报错脱敏组 7 个变异全发现。

## 审查意见的处理
- 安全审查第一轮阻断：0009 `meter_check_entitlement`、0036 `review_sample_weekly_reports` 主消息带个人行 ID，扫描规则只看 NEW. / OLD. 而漏掉——已补入 0061 并放宽扫描、配变异。复核无阻断；建议 `MESSAGE :=`、记录变量变异、`x.*_key` 自检样例已采纳。
- 契约审查：无阻断。应用代码一律按 SQLSTATE 映射，客户端可见内容不变。

## 教训
- 静态扫描只按最初发现的写法（NEW. / OLD.）建规则，会漏掉同类的函数参数与记录变量；规则要按“参数里出现什么”而不是“已知的几处长什么样”来写，并用全库扫描结果反向核对。
- 改报错文本前先查所有子串断言，保留被依赖的片段，避免无关测试失败。

## 待人确认
1. 豁免清单的范围（三条公共层标识）。
2. 0061 与 0010–0060 一起由人在副本演练后执行；四个计量函数只改文本，是否仍需计量负责人单独签字。
3. 写入幂等的主消息不再带写入方法（grade / diagnose 也移到 DETAIL），排障是否需要保留。
4. 标识进了 DETAIL：目前应用代码不读取、不记录数据库错误的 detail；以后若把整个错误对象写进应用日志，标识会经应用日志带出（BFF 日志白名单允许 `detail` 字段，UUID 会被遮盖，非 UUID 幂等键不会）。建议约定应用日志只记 SQLSTATE 与异常类名。
5. 审核台（`services/learning/review_console/store.py`）会把 23514 的主消息透传给客户端；现在不调用这些守卫，以后扩展时须复核。
6. RDS 未设 `log_min_error_statement`（缺省 ERROR，会记出错语句）；两个驱动走扩展协议，语句里只有占位符，但若有调用方把标识拼成字面量仍会进日志（plt-db-log-redaction 待人确认 5）。
7. 扫描局限：参数里带分号的字符串字面量会截断；`$$` 格式串、经别的函数间接拼进主消息、名字不含标识词的变量扫不到。
8. 全量 db-mutation 单次约 5 小时（本机 4 核），CI 若有时限需分组运行。
