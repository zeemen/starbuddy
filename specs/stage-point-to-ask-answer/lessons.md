# 指着元素提问 · 第二阶段 · 实现记录（lessons）

2026-11-26，用户确认第五节五条都按默认（先 2a 预制答疑；答疑写进台词稿；提问不另扣、2b 每节最多 10 问；只送键盘作答；问答文字不落库）。
按第二节第 7 条拆成五个 `[NEEDS-HUMAN]` PR，本文件逐个记。

## PR ①：契约（本 PR）
### 做了什么
- 在 `plugin-api.v1.1.yaml` 里追加，v1.1 仍是待签字的候选版：
  - `TeachRequest` 新增可选字段 `question`（`TeachQuestion`）与 `answer_submitted`（布尔）；
  - 新增三个 schema：`TeachQuestion`、`LessonRef`、`StageAnchor`；
  - 新增一条条件规则：带 `question` 时只能是 `mode: lesson`，且 `conversation` 为空；
  - `/teach` 挂上新示例 `TeachRequestAyaQuestion`；
  - 契约说明里加了一节“v1.1 草稿内追加”。
- `TeachQuestion = { lesson_ref, dsl_version, cursor (1..100), anchor { kind, id }, intent }`：
  - 意图只有 `why_step`、`what_is`、`where_from` 三种。“再讲一遍”在前端本地处理，不进契约；
  - 不收自由文字、讲解内容与学生作答，各层都是 `additionalProperties: false`。
- 契约检查（`scripts/check_contract.py`）：
  - `check_v11_additive`：先把这次追加的内容剥掉，再与 v1.0 逐项比对；同时核对追加内容本身一字不差：
    - 两个字段的形状；
    - 不能改成必填；
    - 恰好一条条件规则；
    - 三个新 schema；
    - `/teach` 只多这一个示例。
  - 新增 `check_teach_question_alignment`：契约的锚点类型、意图（去掉 `again`）、id 规则、步数上限，与 `packages/stage` 的 `ANCHOR_KINDS`、`QUESTION_INTENTS`、`ID_RE`、`LIMITS.maxSteps` 一致。直接读 TS 源码比对，不手抄。
- 反例 +17 条（`tests/counterexamples_v11.py`）：
  - 提示模式带提问；提问同时带对话；
  - 夹带自由文字、讲解 DSL、学生作答；
  - 意图是 `again` 或任意文字；
  - 锚点类型、id、坐标；
  - 步数 0 与 101；
  - 讲评课引用缺失、版本格式、夹带内容；
  - 缺 DSL 版本；`answer_submitted` 不是布尔。
- 变异 +10 条（`tests/test_mutations.py` 的 `V11_MUTATIONS`），v1.1 的变异循环也跑一致性检查。
- `packages/contracts/README.md` 的 v1.1 行补了说明。

### 决定与差异
- **追加在 v1.1 候选版里，不另开 v1.2**。理由：
  - v1.1 还没签字；
  - 仓库已有“草稿内追加”的先例（learning-internal 1.1.0、洞察服务 `insight.report`）；
  - 另开 v1.2 要改三处契约版本常量、平台侧版本协商、适配器的版本表与一致性测试，影响面大很多。
  代价是：v1.1 的签字范围从“只多 present_item”变成“多 present_item 与 teach 结构化提问”。评审时请一并看，见待人确认 P1。
- **`TeachResult` 不加字段**。“带提问时回答只用 `text` 片段”跨请求与响应，JSON Schema 表达不了，由适配器自检（PR ③）。
- **适配器行为暂时不变**：阿鸦按 1.1.0 收到带 `question` 的请求会照常校验通过，但 v0 不读它，按讲评课页面直出处理。
  平台现在也不会发这种请求（BFF 还没有 `teach` 路由），PR ③ 才接上。

### 验证
- `make contract-test`、`make contract-mutation`、`make adapter-aya-test`、`make conformance` 通过（结果见 PR 描述）。

### 待人确认
- **P1**：v1.1 的签字范围加上 teach 结构化提问。若评审要求 v1.1 只含 present_item，就把这次追加挪到新的 v1.2 候选版，那会是一个更大的 PR。
- **P2**：`cursor` 上限直接取讲解 DSL 的最大步数（100）。将来 DSL 上限改了，契约要跟着改；一致性检查会报出来。
