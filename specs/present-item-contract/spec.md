# PLT-12 题面接口 `present_item`（契约草案 + 两科实现，开关隔离）

2026-10-09 用户授权“加快、自主推进”，本 spec 由 Claude 拟定后直接实施；涉及 packages/contracts，提交首行 `[NEEDS-HUMAN]`，只进分支，签字前不在任何环境启用。

## 背景
诊断与错题再练至今只能用合成题面（`services/learning/learning/diagnosis/presenter.py`、`practice/presenter.py`；`specs/sie-02a-math-diagnosis/spec.md:42,76`、`specs/unified-shell-sprint4/spec.md:110`）。插件 API 没有只读的取题面方法。

## 范围
1. **契约**：新开候选文件 `packages/contracts/openapi/plugin-api.v1.1.yaml`（以 v1.0 候选为底，不改 v1.0 正在表决的 61 项），新增方法 `present_item`：
   - 入参：`context`（同其他方法，含 request_id、contract_version、subject、data_class；只读、非消耗类，不带 usage_id）、`item_ref`（`<subject>:<item_id>`）、`content_version`。
   - 出参：题干（受限富文本：段落、行内公式 LaTeX、图片引用）、题型、选项（单选/多选时）、作答格式约束（数字/表达式/选项），`license_state`、`item_version`。
   - **不得**含答案、解析、采分点、rubric；契约 schema 层 `additionalProperties: false`，并加“答案类字段名”反例。
   - 错误：沿用 Problem；新增编码只用已有的 `ITEM_NOT_FOUND`、`RATE_LIMITED` 等，若需新编码在 spec 偏差里写明。
2. **契约测试与一致性**：正例、反例（含答案字段泄露）、变异；一致性夹具与覆盖率报告纳入 1.1.0 候选。
3. **适配器**：阿鸦（`adapters/aya`）、知行（`adapters/zhixing`）实现 `present_item`，读各自现有题库结构，输出前做白名单投影；单测含泄露检查。文砺不在本任务（作文无题面）。
4. **学习服务**：诊断与再练的 presenter 增加“从适配器取题面”分支，由新开关 `LEARNING_PRESENT_ITEM_SOURCE=adapter|synthetic`（默认 synthetic）控制，且只有在候选契约开关 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1` 时才允许 adapter；适配器题面在返回 BFF 前再过一次白名单。开关关闭时行为与现在逐字节一致（对拍测试）。
5. **BFF / 前端**：渲染受限富文本（公式用现有 KaTeX 或等价、无 HTML 注入），DOM 泄露检查覆盖答案类字段。

## 不做
- 版权核对与真实题干上线（合规、教研）；判分方式与 `rubric_version` 来源（practice lessons 待人确认 2、3）。
- 计量：取题面非消耗类，不计量。

## 验收
- 契约正反例与变异全部通过；1.0 候选与 0.9 的现有测试不变。
- 两科适配器 `present_item` 单测与泄露检查通过；适配器变异发现。
- 学习服务开关关闭对拍一致；开关打开时诊断、再练端到端走适配器题面。
- 严格 `make check` 通过。
