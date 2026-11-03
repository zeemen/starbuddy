# KGE-05 复测与验证题交付（让“延迟复测”真正发生）

2026-10-09 起由 Claude 按用户授权自主推进；触及计量口径的部分提交首行 `[NEEDS-HUMAN]`，开关默认关。

## 背景
KGE-04 第二步已能排出 `retest_task`（reason：scheduled / early / decay / hint_followup），SIE-03 的“我其实会”会产生验证任务，但学生端没有入口，复测从不发生，Gate 1“8 周延迟复测改善”拿不到数据（`specs/kge-04-mastery-step2/lessons.md:188,203,209`；`specs/sie-03-dispute-reconcile/lessons.md:141`）。

## 范围
1. **学习服务**：复用错题再练的选题、票据、作答组件（`services/learning/learning/practice/{select,ticket,answer}.py`），新增复测模块：
   - 列出学生到期的复测与验证任务（读 `retest_task` 与验证任务，只读）；
   - 取题：按任务的节点与题目族选题，排除原题与近期做过的题；题面走 PLT-12 的题面来源（开关缺省为合成题面）；
   - 作答：经现有 grade 路径落证据，`purpose` 由服务端写成 `retest` 或 `transfer_check`，不接受客户端传入；作答后触发掌握重算，任务按 KGE-04 规则关闭；
   - 服务内部 OpenAPI 放 `services/learning/openapi/retest-internal.v0.yaml`（不进 packages/contracts）。
2. **计量**：每次作答带 `usage_id` 与 `idempotency_key`，默认沿用错题再练的计次口径（`meter_practice`，只计次不扣额度）；复测与验证是否扣额度未定（`specs/mastery-estimate-sprint4/spec.md` #21），写进 lessons 待人确认。
3. **调度**：Gate 1 要的“8 周”复测点不在现有 0/3/14 天，只加可配置项，默认关，数值等教研签字；不改掌握计算、不加 `retest_task.reason`。
4. **BFF 与前端**：学生端“复测”入口与页面（开关 `STARBUDDY_RETEST_UI`，默认关）；BFF 票据沿用再练的不透明票据做法；宵禁、限流同再练。
5. **可见内容**：返回给前端的内容不含掌握、置信度、证据 ID；任务说明只用固定文案占位（`{{TODO_COPY}}`），不出现关于学生的陈述（规则 11）。

## 不做
- 改掌握计算、星亮门槛、复测调度的现有数值；新迁移（若发现必须新表或新列，停下另起草稿）。
- 真实题面与真实内容准入（教研、合规）。

## 验收
- 到期复测与验证任务能列出、取题、作答，作答经 grade 落库并触发重算；同一组合成证据上，有无本模块时掌握记录与复测任务逐字段一致（对拍）。
- 票据防伪造、防过期、防跨学生；同 key 重放不写新行；幂等测试全绿。
- 前端响应无掌握、置信度、证据 ID，DOM 泄露检查通过；变异能发现漏改。
- 开关关闭时现有行为不变；严格 `make check` 通过。
