# PLT-11 今日计划跨科编排 · 第一步（读时汇总，不落库）

2026-10-09 起由 Claude 按用户授权自主推进。编号 PLT-11 由本任务首次使用（开发计划 v2.0 只定了编号规则，见 lessons 待人确认）。
依据：开发计划 v2.0 第 439 行“寒假开始今日计划跨科编排”；完整方案 v1.1 第 174 行“今日 20–40 分钟计划”、第 532 行显式状态流程；
SIE 第 484 行“节点到复习时间、验证题待做 → 放入今日计划，不单独推送”；specs/practice-loop-stage1/spec.md 待确认 #9。

## 背景
4-H 的今日计划按科并列（`/internal/v1/plan` 各科一节，现算、不落库、不计量）；错题再练（practice/queue）与复测（retest/queue，KGE-05）各有独立入口，
与 plan 的 retest 任务同节点重复；任务列表没有“去做”入口。

## 范围
1. **学习服务**新增读时汇总模块 `learning/today/`，接口 `POST /internal/v1/today`（BFF 服务令牌；服务内 OpenAPI 放
   `services/learning/openapi/today-internal.v0.yaml`，不改 packages/contracts）：
   - 输入：student_ref、subjects（在读科目）、可选 time_budget_minutes（缺省取配置，合成值 30，允许 20–40）。
   - 来源：各科 plan、practice/queue 的 due 条目、retest/queue 的 due 条目；任一来源未启用或失败时跳过该来源并在结果里标 `source_unavailable`，不整体失败。
   - 去重：同一知识节点同时出现在 retest/queue 与 plan 的 retest 任务时只保留 retest/queue 条目（可直接去做）；再练与复测同节点时保留复测。
   - 排序与截断：按配置的来源优先级（合成：retest/verify > practice > plan）→ 各来源内原有顺序 → 学科轮转；按 estimated_minutes 累加到预算截断，截掉的计数返回。
   - 每条输出：subject、kind（retest / verify / practice / plan 原 kind）、node_id、estimated_minutes、source、可去做的引用（entry_id 或 null）、plan 原有 rationale_code。
   - 只读：不写任何表、不计量、不调模型；受 `LEARNING_INTERNAL_V1` 与新配置 `LEARNING_TODAY_CONFIG`（缺省不设 = 接口 501）约束。
2. **BFF**：`GET /bff/v0/students/{id}/today` 在开关 `STARBUDDY_TODAY_ORCH=today_orch`（只在 dev/test 允许，缺省关）打开时，额外返回跨科有序列表 `ordered`（保留原按科 `sections` 不变）；
   推荐理由沿用现有两类机制与白名单，不新增关于学生的陈述；本人核对、限流同现有 today。`services/bff/openapi/shell.v0.yaml` 同步。
3. **前端**：今日页在开关打开时显示跨科列表，每条带“去做”链接到已有的再练、复测页（引用为空的 plan 任务链接到科目页）；文案 `{{TODO_COPY:today.*}}`；
   不显示掌握、置信度、证据 ID、截断原因之外的内部编码。

## 不做
- 落库（计划完成率、学生调整计划、ADR 03 任务表）——需迁移，另起。
- 改 packages/contracts（`PlanDigest` 加字段）、改计量口径、改复测调度与作答用途、读 insight.intervention_log、点点话术与任何 Prompt。
- 家长端跨科列表（可见范围待人确认）。

## 验收
- 开关关闭时 today 响应与现状逐字段一致；学习服务未配 `LEARNING_TODAY_CONFIG` 时 501。
- 合成数据上：去重、优先级、预算截断、来源失败降级各有测试；接口前后 platform.* 全表行数不变；响应无掌握、置信度、证据 ID（DOM 泄露检查）。
- 变异测试：去重失效、截断失效、来源失败整体报错、泄露字段 —— 均被发现。
- 严格 `make check` 通过。
