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
