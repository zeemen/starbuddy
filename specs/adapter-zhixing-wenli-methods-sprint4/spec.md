# 冲刺 4-D（知行、文砺部分）：各补两个方法到 5/6

状态：**已确认（2026-09-30，用户“按默认建议，开始实现”）**，待人确认全部按默认执行。PR 首行 `[NEEDS-HUMAN]`（契约行为按 v1.0 候选版）。
任务包：开发计划 v2.0 冲刺 4（W9–W10）D 流“四科补齐剩余方法”，验收“阿鸦 6/6、知行与文砺 ≥ 5/6、开口成章 ≥ 4/6”（英语搁置不计，对照文档决定 #1）。阿鸦部分见 `specs/adapter-aya-methods-sprint4/`。
关联：`packages/contracts/openapi/plugin-api.v1.0.yaml`（候选版）、`specs/plugin-api-v1.0-step2/`（已实现）、`specs/adapter-zhixing-sprint2/`、`specs/adapter-wenli-sprint2/`（现有方法与内容包）、知行与文砺盘点的 adapter-design、KGE-03 知识服务（`node.path`、`item.search`、`item.similar`、`rubric.get`）、4-K 道法切片（采分点入原子层）、CLAUDE.md 规则 1、3、11、12。

## 现状
| 适配器 | 已实现 | 未实现 |
|---|---|---|
| 知行（道法） | diagnose、grade（F3 采分点）、generate_variation | plan、teach、report |
| 文砺（语文） | diagnose、plan、grade（五维量表） | teach、generate_variation、report |
- 两科都没有人设 Prompt；teach 依赖人设层（点点主持、七科老师规范）与模型路由，人设 Prompt 属必停项、3-F 治理刚在做。
- report 在 v1.0 候选版里只给字段与证据引用，不写结论句（规则 11），可以确定性实现。

## 设计要点（默认各补“确定性、不调模型”的两个方法）
### 知行：plan + report
- **plan**：读编排层给的 `mastery[]`（`zhixing:` 能力点）、`node_path`（观点间 related 为主，不造前置）、`exam_config`；按“领域权重 × 失分 × 可提升”排序（KGE 第八章：观点之间不硬凑前置，学习路径改用这个排序），输出任务列表（节点、题目族候选、分钟数）；领域权重在 4-K 真实数据前用内容包里的合成权重并标注“待教研校准”。
- **report**：只输出字段与证据引用（逐维采分统计、四类失分归因计数、证据 ID），不写任何结论句与评价；失分归因编码用 `MORAL.*`（与语文 `CHN.*` 区分，KGE 第八章“定位”同名问题）。
### 文砺：generate_variation + report
- **generate_variation**：只从已准入题库选题（与知行一致：候选限定同能力点、同题型、已准入；没有合格候选返回空结果并给原因编码）；不调模型生成新题。
- **report**：五维量表逐维统计与证据引用，主观维带不确定度字段；不写结论句。
### 共同
- 契约 v1.0 候选版的请求 / 响应严格校验；判分相关字段不涉及（plan / report / generate_variation 不产分）；每个响应带 evidence_ids、confidence、source_version、model_version（`algo:*`）、human_review_required。
- 一致性测试（conformance v0.9 / v1.0）覆盖新方法；变异覆盖排序、准入过滤、report 不含自由文本。
- **teach 两科都不做**（等人设 Prompt 与 3-F 治理就绪，另起任务）。

## 范围
### 做
知行 plan、report；文砺 generate_variation、report；契约校验、适配器单测、conformance、变异；lessons。
### 不做
teach（两科）；任何模型调用、人设话术；plan 的真实领域权重（等 4-K 教研数据）；契约变更。

## 验收标准（可测试）
- [ ] 知行 capabilities 声明 5/6、文砺 5/6；conformance v0.9 与 v1.0 候选版通过；契约审查通过。
- [ ] plan 排序在合成数据上与手算一致、确定性；只用 related 边时不产生前置依赖路径。
- [ ] generate_variation 只返回已准入、同能力点、同题型的题；无候选时返回空结果与原因编码。
- [ ] report 响应不含任何自由文本结论（测试扫描字段）；证据 ID 可回溯。
- [ ] 严格 `make check`、安全审查通过。

## 涉及的必停项
- 契约：不改（按 v1.0 候选版实现；签字前以候选版开关运行）。人设：不涉及（teach 不做）。判分：不涉及。PR 首行 `[NEEDS-HUMAN]`。

## 待人确认
1. **补哪两个方法**：默认知行 plan + report、文砺 generate_variation + report（都确定性、不调模型）；teach 两科都等人设 Prompt 与 3-F 就绪。
2. **知行 plan 的排序口径**：默认“领域权重 × 失分 × 可提升”（KGE 第八章），领域权重由道法教研给，签字前用合成值并标注。
3. **report 的字段范围**：默认只给逐维 / 逐类计数与证据 ID，不给等级、趋势描述；趋势与结论随 SIE-02b 经判断账本出。
4. **文砺变式的同题型判定**：默认按内容包里的题型编码严格相等；跨题型变式另议。
