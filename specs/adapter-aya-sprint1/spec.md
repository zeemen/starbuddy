# 阿鸦适配器 · 冲刺 1：diagnose 与 grade 写回平台

状态：**实现中**。用户 2026-09-25 指示按“待人确认”的默认方案实现；实现记录与新增待确认项见 `lessons.md`。spec 本身的确认人、日期：＿＿＿
关联：
- 开发计划 冲刺 1-D（“阿鸦适配器：diagnose 与 grade 写回平台”；验收“一道数学题从作答到能力点写入平台，可查询”）
- `specs/inventory-aya/adapter-design.md` v0.2 与 `decisions.md`（#3、#4、#9、#10、#11）
- 契约 `packages/contracts/openapi/plugin-api.v0.9.yaml`
- 学习者 Schema v0.9（`db/migrations/0001`–`0006`）
- 错因映射表 `docs/mapping/error-taxonomy.v0.9.yaml`
- ADR 01、04、05、09

## 目标
阿鸦的一次数学作答，经适配器的 `grade` 和 `diagnose`，产出**符合契约 v0.9 的结果**，结果可以写进平台学习者模型并查询出来：
- 作答证据 → `evidence_event`、`attempt`
- 判分 → `grade_result`、`grade_dimension`
- 能力观测 → `skill_observation`
- 错因 → `error_attribution`

这是平台的**第一条端到端链路**（开发计划三个排期判断之二：数学先接）。

## 范围

### 做
1. **适配器服务** `adapters/aya/`（TypeScript，Node 22）。实现契约里的三个接口：
   - `GET /capabilities`：声明 grade、diagnose 已实现，其余 4 个方法未实现；验证器 `aya-symbolic-ts@0.1.0`。
   - `POST /grade`：**只做客观题**（单选、多选、填空）。
   - `POST /diagnose`：`mode = intake | gap_test`，输入是一组作答。
2. **包装 legacy 纯函数**。从 `legacy/aya` 复制到 `adapters/aya/src/core/`，文件头注明来源路径和提交号；legacy 本身只读，不改：
   - `lib/grading.ts`（`gradeAnswer`、`normalizeFill`）
   - `lib/symbolic.ts`（`areEquivalent` 等，作为验证器 v0）
   - `content/abilities.ts` 中的 `ERROR_TRAPS`（陷阱码 → 十二根因）
3. **题库内容包**：题干、答案、陷阱、节点都从 legacy `content/chapters/*` 生成成服务端只读的内容包，放在 `adapters/aya/content/`。
   - **答案只在适配器进程里**，任何接口都不返回答案、解析和评分标准（decisions #4）。
   - 在 `aya.question` / `aya.question_key` 表建立之前，用这个内容包过渡（见待人确认 #2）。
4. **内容登记脚本**：把阿鸦的课程版本、能力点（939 个节点）、题目（721 道）登记进 `platform.curriculum_version`、`skill_node`、`item`。
   - 证据表对题目有外键，不登记就写不进去。
   - 准入状态按 adapter-design §3.1 计算：审核等级 ≥ Q2 **且**来源已核，才算 `admitted`。**目前 721 道全部不满足**（Q1、来源待核），全部登记为 `source_unverified`。
5. **字段映射**（详见下一节“映射规则”）。
6. **测试**：
   - 契约测试：用 `packages/contracts` 的 schema 校验适配器的每个响应。按 ClaudeCode 执行方案第六章，测试由契约生成，不手写期望结构。
   - 验证器 v0：正例、等价写法、各类边界原因码。
   - 映射单元测试：陷阱码 → 根因 → 映射表编码 → 跨科大类。
   - **端到端测试**：用合成作答调用适配器，再把响应以 `starbuddy_learning` 身份写进 `make db-test` 用的本地临时库，查询 `skill_observation`、`grade_result`、`error_attribution` 能查到。
7. **接入 `make check`**：新增 `adapter-aya-test` 目标。

### 不做
- 过程题（拍照 OCR 判分，`/api/process-grade` 的替代）：它要调模型，依赖模型路由（冲刺 1-F）与计量（冲刺 2-E），放到后续冲刺。
- `teach`、`plan`、`report`、`generate_variation`：冲刺 4。
- 学习服务 `services/learning` 的写入接口：属于冲刺 1-C，另立 spec。本 spec 的端到端测试用一段测试辅助代码模拟“学习服务落库”，只证明字段映射正确。
- `aya.*` 私有 schema 与迁移（必停项）：见待人确认 #2。
- SymPy：阶段 1（decisions #10）。
- 计量扣费：适配器只原样使用 `usage_id` 与 `idempotency_key`，不扣费（ADR 06）。

## 映射规则

### grade
| 契约字段 | 取值 |
|---|---|
| `result.verdict` | 选择题：key 集合比对，得出 correct 或 incorrect。填空题：先按 legacy `normalizeFill` 比对，不一致再用验证器 v0 判等价。**边界情况**（见下）返回 `needs_review`，不下对错结论 |
| `result.dimensions` | 一个维度 `answer`，`judged_by = verifier`，`rule_ref = aya-symbolic-ts@0.1.0`，`subjective = false` |
| `confidence` | 选择题 key 比对 = 1.0；填空题 = 采样一致点占比（一致点少于 4 个算边界）；needs_review 时 ≤ 0.5 |
| `result.verifier.reason_codes` | 三角或多字母函数、方程或不等式求解、证明或多答案、解析失败、采样不足、没有可判等价的标准答案。与契约枚举一一对应 |
| `result.error_attributions` | 答错且命中 `wrongAnswerTraps` 时：陷阱码 → `ERROR_TRAPS.rootCause`（中文根因）→ 映射表阿鸦编码（例如“运算错误”对应 `yunsuan_cuowu`）→ `cross_subject_category`。`algorithm_version = aya-trap-attr@0.1.0`；置信度：命中预标注陷阱给 0.8，其他情况不产出错因 |
| `evidence` | 作答本身（填空题 `response_text`，选择题 `response_choice`），外加一条 `verifier_trace`（采样点数、一致点数、原因码） |
| `admission` / `semantics` | 题目未准入（目前全部如此）：证据 `admission = source_unverified`，`semantics = practice`，`human_review_required = true`，原因含 `content_not_admitted` |
| `model_version` | `algo:aya-symbolic-ts@0.1.0`（不调用模型） |
| `source_version` | `verifier:aya-symbolic-ts@0.1.0;items:<内容包版本>` |

### diagnose
| 契约字段 | 取值 |
|---|---|
| `result.observations` | 每道作答的每个主测节点（`mainNodes`）产出一条观测：答对为 demonstrated，答错为 not_demonstrated，needs_review 为 inconclusive。`algorithm_version = aya-diag@0.1.0` |
| `confidence` | 答对且自评为“猜的”时降权，置信度 ≤ 0.3（adapter-design §1）；使用了 4 级及以上提示时 ≤ 0.5 |
| `result.error_attributions` | 规则同 grade |
| 不输出 | 掌握度、θ、能力均分：这些只由平台 `mastery_estimate` 计算（CLAUDE.md 规则 2） |

### 标识
- 能力点：`aya:<节点 ID>`，例如 `aya:K-QF-0301`。题目：`aya:<题 ID>`，例如 `aya:Q-EQU-U01-D-001`。两者都符合契约的格式。
- `data_class`：请求里声明为 L2（作答内容关联到学生）。适配器不调用任何模型，不涉及出境。

## 验收标准（可测试）
- [ ] 一道合成的填空作答经 `grade` 后，端到端测试能在本地库查到对应的 `evidence_event`、`attempt`、`grade_result`（含维度）、`error_attribution`（答错且命中陷阱时），并且和响应里的 evidence_id 一致。
- [ ] 同一组作答经 `diagnose` 后，能查到 `skill_observation`，能力点是 `aya:<节点>`。
- [ ] 适配器的每个响应都通过契约 schema 校验；契约测试从 `packages/contracts` 生成。
- [ ] 所有 grade 响应都带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required`（规则 3）。
- [ ] 响应和日志里都不出现答案、解析、评分标准（decisions #4）；有测试断言这一点。
- [ ] 未准入题目的结果一律是 `practice` 并要求复核（decisions #11）；有测试断言这一点。
- [ ] 验证器 v0 的六类边界原因码都有测试用例，边界情况返回 `needs_review` 而不是对或错。
- [ ] 同一个 `idempotency_key` 重试时，返回同一批 evidence_id（ADR 05 第 2 条）。
- [ ] 测试数据全部是合成数据（规则 8）；题目内容来自 legacy 内容包，不含学生数据。
- [ ] `make check` 通过；新增依赖的许可证已核（规则 10）。

## 涉及的必停项（见 CLAUDE.md）
- 默认方案**不涉及**：不改契约、不写迁移、不改人设。
- 以下情况会变成必停项，届时 PR 首行写 `[NEEDS-HUMAN]`：
  - 选择待人确认 #2 的方案 (b)，新增 `aya.*` 迁移；
  - 实现中发现契约需要改动。

## 依赖与输入
- 已有：契约 v0.9、学习者 Schema v0.9、错因映射表 v0.9（待教研签字，签字前按草稿映射，签字后只需更新表）。
- legacy 阿鸦仓库只读访问（`legacy/aya`，本会话里在 `/home/user/ayaheshui`）。
- 冲刺 1-C 学习服务写入接口：本 spec 不依赖它完成，但真正的端到端（适配器 → 学习服务 → 平台库）要等 1-C。

## 待人确认
1. **运行时依赖**：草案用 Node 22 自带的 `node:http` 加 `ajv`（MIT，用于按契约校验请求和响应），测试用 Node 自带的 `node:test`，尽量少引入框架。是否同意？还是统一用 Fastify 之类的框架（许可证需要另核）？
2. **题目与答案放在哪里**：
   - (a) 冲刺 1 用服务端内容包过渡（本 spec 默认）；
   - (b) 现在就建 `aya.question` / `aya.question_key` 表。这需要写迁移，属于必停项，由人执行。
3. **复核队列会被刷满**：721 道题全部未准入，按现有规则每一次阿鸦判分都会带 `human_review_required = true`。如果学习服务把每条都放进复核队列，队列会被刷满。建议：原因只有 `content_not_admitted` 的结果不进人工复核队列，只标记为练习语义。这需要平台侧定一个规则。
4. **陷阱归因的置信度**：命中预标注陷阱给 0.8，这是否合适？没有命中陷阱的错题不产出错因，而不是按“信心交叉推断”（legacy `mistakes.ts:37`）去猜。
5. **内容登记脚本由谁执行**：它写的是平台的内容表（`starbuddy_learning` 角色）。在开发库里由我执行，pilot 和生产由人执行。是否同意？
6. **适配器语言**：adapter-design 疑点 1 问的是长期保持 TS 还是移植到 Python。本 spec 按 TS 做，不影响以后移植。
