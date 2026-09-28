# 学科插件 API v1.0 冻结（第一步）· 记录

2026-09-28。用户确认“按默认建议，开始实现”：spec 待人确认 #1–#6 全部按默认执行。v1.0 文件是**候选版**，评审会逐项签字后才算冻结。

## 交付
- `packages/contracts/openapi/plugin-api.v1.0.yaml`：由 v0.9 逐处修改而来（生成脚本不进仓库，每处替换都要求恰好命中一次），`info.description` 列出全部破坏性变更与新约定。
- `packages/contracts/openapi/learning-internal.v1.yaml`：请求体改指 v1.0；新增只查询、不执行的重放接口（N17）与错误码 `REPLAY_NOT_FOUND`；写明 5xx 一律结果未知（N18）。
- `insight-service.v0.9.yaml`：`ExamTarget.full_score` 由枚举放宽为 1–300 的整数（pol-#2，非破坏）。
- 检查脚本 `check_contract.py`：不带参数时同时检查 v0.9 与 v1.0；v1.0 的跨文件引用按文件 URI 解析；新增版本号一致性检查、v1.0 结构检查（衔接字段都在且可选、新错误码在枚举里、报告没有预测），以及“破坏性变更 v0.9 接受 / v1.0 拒绝”的比对。
- 反例：`tests/counterexamples_v1.py`，14 条破坏性变更用例、10 个 v1.0 反例；变异：v1.0 新增 20 个，全部被发现（契约变异合计 152 个）。
- 共享库 `starbuddy_contract.contract`：按 `STARBUDDY_CONTRACT_VERSION` 选版本，缺省 0.9.0（学习服务、阿鸦不受影响）；参考适配器与一致性测试支持 v1.0（作答带 `attempt_id`、作答类证据指回作答），`make conformance` 加两条按 1.0.0 跑参考适配器（同步与异步）。
- 评审清单 `v1.0-review.md`：61 项每项最后一列写“v1.0 默认结论（待签字）”；README 写明迁移期。

## 与 spec 的偏差
1. **sie-#2 改为移除 `forecast`**：spec 写“`forecast` 的 audience 去掉 student、parent”。报告响应看不到请求的受众，schema 无法按受众约束，所以 v1.0 直接移除，预测只由洞察服务 `insight.predict` 提供（只给编排层）。
2. **N4 随 N3 改为必填**：spec 写“描述写明由平台生成”。证据要指回作答，作答就必须带 ID；学习服务早已这样传，实际影响只有异步英语示例。
3. **适配器路径不变**：v1.0 仍用 `/plugin/v0` 路径，以 `contract_version` 协商，迁移期同一路径可接两个版本，避免阿鸦与学习服务同时改路由。
4. **`student_ref` 加小写 UUID pattern**（N13“是否强制”）：`format: uuid` 在缺少 format 插件时不校验，与 N10 同理用 pattern 兜底；列为破坏性变更。
5. **N7 的前缀用例**：请求级规则放在四类请求（grade、diagnose、teach、generate_variation）上；道法只约束题目引用，不约束能力点（待人确认 #5）。
6. **作图语义的结构**：`semantic` 只约束为“操作名 + 最多 8 个参数”，具体操作表随输入通道 spec 定。

## 第二步（另起任务）
- 学习服务同时接受 v0.9 与 v1.0：按 `context.contract_version` 选校验版本；实现重放接口；提交阶段错误改为可重试（N18 b）。
- 阿鸦适配器切到 v1.0：作答类证据带 `attempt_id`、`verified_claims` 改名（阿鸦 teach 未实现，影响为零）、时间与前缀已符合。
- BFF 预留过期后改用重放接口。
- 迁移期结束（W6）删除 v0.9 与 learning-internal v0。

## 待人确认
1. 评审会签字（61 项）；签字前 v1.0 按候选版对待。
2. 迁移期 W6 是否合适。
3. v1.0 衔接字段直接引用两份服务契约 v0.9 的 schema：服务契约升级时插件契约要随之改引用。
4. 作图语义的操作表由谁定、何时定。
