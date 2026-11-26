# 讲评课会话 · 实现记录（lessons）

2026-11-26 用户确认第五节五条都按默认：
1. 一节讲评课扣一次，课内提问不另扣；
2. 内容成功下发即结算；
3. 会话 90 分钟有效，每节最多 20 问；
4. 契约追加 `lesson_ref` 与台词稿 `script`；
5. 新建表 `platform.lesson_session`。

按第二节第 7 条拆成 ③b-1 至 ③b-5，逐个记。

## ③b-1：契约（本 PR）
### 做了什么
- 在 `plugin-api.v1.1.yaml` 追加，v1.1 仍是待签字候选版：
  - `TeachRequest.lesson_ref`（可选，`LessonRef`），加一条规则：带它时只能是 `mode: lesson`，不带 `question`，`conversation` 为空；
  - `TeachSegment.script`（可选，`StageScript`），加一条规则：只能在 `whiteboard_dsl` 片段上，且同时带 `dsl` 与 `dsl_version`；
  - 新 schema：`StageScript`、`StageScriptLine`、`StageScriptAnswer`；
  - 新示例：`TeachRequestAyaLessonOpen`、`TeachResponseAyaLessonOpen`，分别挂在 `/teach` 的请求与 200 响应上；
  - 契约说明“v1.1 草稿内追加（续）”一节。
- **台词正文加了 pattern**：与题面文字片段（`RichTextRun.text`）同一口径，拒收尖括号、控制字符、双向控制符、零宽字符、行段分隔符，不收换行，1–60 字。
  - 契约的“响应无约束文本扫描”要求响应里的字符串都有约束，所以台词正文必须带 pattern；
  - `check_present_item` 的探针表加了 `StageScriptLine.text`，同时核对拒收与放行的样例。
  - 这比 `packages/stage` 的台词稿 schema 严：那份只有长度，安全检查在 `src/text.ts` 里做。所以契约不会放进 `packages/stage` 会拒的台词。
- 契约检查（`scripts/check_contract.py`）：
  - `check_v11_additive`：把这次追加的字段、规则、schema 与示例剥掉之后，再与 v1.0 逐项比对；同时核对追加内容本身一字不差，`TeachSegment` 只多 `script` 与一条规则；
  - `check_teach_question_alignment`：另外核对台词稿的字段（`SCRIPT_FIELDS`、`SCRIPT_OPTIONAL`、`LINE_FIELDS`、`ANSWER_FIELDS`）、角色（`ROLES`）、问法、条数与长度上限（`SCRIPT_LIMITS`）、各编号的 id 规则（`ID_RE`）与 `packages/stage` 一致，直接读 TS 源码。
- 反例 +17 条：
  - 打开讲评课用在提示模式、同时带提问、带对话、引用夹带内容；
  - 台词稿挂在 text 片段上、缺 DSL、多余字段、缺 lines；
  - 台词超长、夹带 HTML、夹带零宽字符；角色、编号不合规；夹带姓名；
  - 答疑是 again、夹带正文、步骤编号不合规。
- 变异 +10 条。

### 待人确认
- 无新增，沿用规格第五节。v1.1 的签字范围又多了这一项，与 `specs/stage-point-to-ask-answer` lessons P1 同一个问题。

## ③b-2：阿鸦下发讲评课
### 做了什么
- `teach` 带 `lesson_ref` 时（`src/methods/teach.ts` 的 `teachLessonOpen`，算法 `aya-lesson-open@0.1.0`）：
  - 从讲评课内容包（③a 的 `src/lessons.ts`）取这一版，下发一段 `whiteboard_dsl`，含 `dsl_version`、`dsl`、`script`；
  - 讲评课不在内容包里 → 404 `CONTENT_VERSION_NOT_FOUND`；`skill_node`、`item_ref` 与讲评课不一致 → 422 `INVALID_REQUEST`；
  - 记 `hint_level_used = 0`：讲解是讲内容，不算提示；`source_version` 带 `items:aya-lessons@<内容包版本>`。
- **预制答疑不随讲评课下发**（规格没写，实现时发现）：
  - 台词稿原样下发的话，前端能直接拿到整份答疑，阿鸦在 `teachQuestion` 里做的“提交前不露答案”守门就被绕过了。
  - 所以下发的台词稿只留字幕台词（被某一步 `say` 引用的），去掉 `answers` 与只给答疑用的台词。答疑只能经结构化提问一条一条取。
  - 平台用 `packages/stage` 读回时仍能配对通过：没有多余台词，也没有缺台词，单测核对过。
- **作答提交前不露答案**：作答提交前（`answer_submitted` 不是 true），讲解板书或字幕里出现这道填空题的标准答案原文，就整节不下发，回 422 `MODE_NOT_SUPPORTED`。
  - 判断口径与提示阶梯、预制答疑相同（`hintRevealsAnswer`）。
  - 板书文字用 `packages/stage` 的 `dslTexts` 取，包括标题、`write_text`、`write_formula` 的原文。
- 测试：
  - `tests/lesson-open.test.ts` 6 条：
    - 下发形状与契约自检，平台能用 `packages/stage` 读回；
    - 不带答疑；提交前后的答案守门；
    - 讲评课引用、节点、题的核对与没有内容包；
    - 契约挡住的四种请求；日志不含文字、幂等重放、不带 `lesson_ref` 照旧。
  - 变异加了 7 条。

### 待人确认
- **R1**：讲评课一般在学生做完题之后上，所以提交前讲解含答案时不下发，可能挡掉“先讲后做”的用法。若教研要先讲后做，讲评课就不能把答案写在板书上，或者由平台在打开前判断这道题提交过没有。现在按“提交前不露答案”从严处理。
- **R2**：讲评课内容包还没有构建脚本与禁语扫描，同 `specs/stage-point-to-ask-answer` lessons Q6。

