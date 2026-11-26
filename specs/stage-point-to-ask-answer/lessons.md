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

## PR ②：DSL 与台词稿 0.3.0（预制答疑）
### 做了什么
- 按 `specs/stage-dsl-versioning` 的五步升版本：
  1. `DSL_VERSION = '0.3.0'`；
  2. 新增冻结的 `schema/stage-dsl-0.3.0.schema.json` 与 `schema/stage-script-0.3.0.schema.json`，0.1.0、0.2.0 的 schema 不改；
  3. `versions.lock.json` 追加 0.3.0 一行；
  4. 迁移阶梯追加第二级 `0.2.0 → 0.3.0`：DSL 结构不变，只升版本号；
  5. `package.json` 的 `exports` 指向 0.3.0。
- 台词稿（`src/script.ts`）：
  - 可选的 `answers: [{ step, intent, line }]`，问法 `ANSWER_INTENTS` 由第一阶段的 `QUESTION_INTENTS` 去掉 `again` 得到，不另写一份；
  - `validateStagePair` 的核对见 README“v0.3”一节；
  - `answerFor` 只从登记过的稿里取。
- 新增台词稿的读入入口 `migrateStageScript(dsl, raw)` 与 `SCRIPT_MIGRATIONS`（`0.2.0 → 0.3.0` 只升版本号）：
  - 旧稿沿阶梯升级，每级核对台词逐字不变，再配对核对；
  - 未来版本、未知版本、迁移失败一律拒收。
  - 规格里没写这一条：DSL 升了版本，旧台词稿的 `dsl_version` 就对不上了，没有这个入口，旧稿读不进来。
- 样张页 `/dev/stage`：
  - 合成样例与台词稿升到 0.3.0，台词稿加了两条合成答疑；
  - 选了“为什么 / 是什么 / 从哪来”后显示台词稿里这一步的答疑。槽位算当前这一步；元素算它写上去的那一步；
  - 没有答疑时显示兜底文案（`STAGE_ASK.no_answer`，占位）加“再讲一遍”按钮；
  - 原来的“已收到”占位 `STAGE_ASK.received` 删掉了。
- 测试：
  - `tests/answers.test.ts` 8 条：合格与取用、问法、结构、台词归属与角色、文字检查、无声板书、台词稿迁移、schema 与常量一致；
  - 版本测试补了第二级真实迁移，合成阶梯的终点改为 0.3.0；
  - 变异加了 14 条，共 115 条，全部被发现。
    - 有两条旧变异要找的原文在 `version.ts` 里出现了两次（DSL 与台词稿两处入口），改成两处一起变异（`'all'`）；
    - “answerFor 对‘再讲一遍’也取”是等价变异，没有列入：校验器不收 `again` 的答疑，稿里不可能有这一条，`answerFor` 里那句判断只是多一层保险。
  - web 单测与样张页端到端更新：显示答疑，兜底后可以再讲一遍。

### 待人确认
- **P3**：答疑台词是人设话术，现在全是“（合成答疑）”占位。真实答疑随讲评课内容由人设写、教研核对内容，跑人设回归。
- **P4**：兜底文案 `STAGE_ASK.no_answer` 是占位，已在 W6 的 `STAGE_ASK` 范围内。
