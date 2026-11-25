# 讲解 DSL v0.2 · 实现记录

- 2026-11-25 用户“按建议，默认值都同意，开始实现”：台词单独成稿；旧内容补 `narrated: false` 无损迁移；指向白板元素与两种槽位；每句 ≤ 60 码点、最多两句。

## 做了什么
- `packages/stage/src/dsl.ts`：`DSL_VERSION` 0.2.0；顶层 `narrated`（必填）、`slots`（可选，≤ 4）；步骤 `say`（可选）；新动作 `point`（`spotlight` / `laser`）；
  常量 `BOARD_WRITE_TYPES`、`POINT_MODES`、`SLOT_KINDS`、`TOP_OPTIONAL`、`STEP_OPTIONAL`、`SLOT_FIELDS`。
- `src/validate.ts`：槽位校验（字段、类型、画布内、与元素 id 共用命名空间）；`say` 只在 narrated 时允许、格式同 id、全文唯一；有台词时步骤可以没有动作；
  “说 / 画”配对 `unpaired_board`；`point` 只在 narrated 时允许、只指现存非高亮元素或槽位、一步最多一个、必须配台词（`point_without_say`）。
- `src/script.ts`（新）：台词稿类型与 `validateStagePair`（缺台词 `unknown_line`、多余台词 `unused_line`、角色与讲解权不符 `bad_role`、超长、超过两句
  `too_many_sentences`、安全检查、版本须与 DSL 相同、编号不重复）；`captionAt` 只从登记过的稿里取字幕；`lineProblem`。
- `src/board.ts`：`BoardState.point`（只在当步，下一步收起）。`src/Whiteboard.tsx`：槽位空框、聚光粗描边框、激光笔小三角；没有槽位与指向时输出与 v0.1 逐字相同（原快照不变）。
- `src/version.ts`：第一级真实迁移 `0.1.0 → 0.2.0`（补 `narrated: false`）；格式指纹纳入新常量与台词稿的字段、上限。
- `schema/stage-dsl-0.2.0.schema.json`、`schema/stage-script-0.2.0.schema.json`（新）；锁文件追加 0.2.0（含台词稿 schema 的 sha256）；`stage-dsl-0.1.0.schema.json` 不动；
  `package.json` 的 `./schema` 指向 0.2.0，新增 `./script-schema`。
- 测试：`tests/narration.test.ts`（9 项：配对、无声板书、point、槽位、台词稿、讲解权、字幕、渲染）；`tests/version.test.ts` 加真实迁移用例与“每个已发布旧版本都在阶梯上”，
  合成阶梯改为落在 0.2.0；夹具改为 0.2.0 并新增 `SAMPLE_V01`、`NARRATED`、`NARRATED_SCRIPT`；`schema.test.ts` 核对新字段。
- 变异：新增 18 条（配对、point、槽位、台词稿、字幕、指向收起、聚光线宽、真实迁移），并修正 1 条旧锚点；84 条全部被发现。
- `apps/web`：样张页样例换成 v0.2 有台词版本（7 步，含题图空槽位、聚光与激光笔）与合成台词稿；`StageDemo` 配对核对后显示字幕（`aria-live`）；单测与端到端同步。

## 与 spec 的差异
- **`point` 没有 `id`**：spec 草稿写的是 `{ type, id, target, mode }`；指向只在当步、不留在白板上，也不会被高亮或擦除引用，id 没有用处，去掉以免占用元素命名空间。
- **样张页样例变成 7 步**：原第 6 步“先收回讲解权、再画线”，在 v0.2 里画线必须有台词而台词又必须有人讲，所以拆成“第 6 步画线并说一句、第 7 步收回讲解权”。
- 人设回归：本次台词全是“（合成台词）”占位，没有改任何人设 Prompt、语料与禁语表，所以没有跑 `make eval-persona`；真实台词进来时必须跑（规则 7）。

## 验证
- `make stage-test`、`make stage-mutation`（84 条全部被发现）、`make check-fast` 通过；前端单测 289 项通过；样张页端到端 3 项通过。零新依赖，不改契约。

## 待人确认
1. 台词长度上限（每句 ≤ 60 码点、最多两句）是默认值，请人设与教研定具体数字（改了要升 DSL 版本，见 README 的版本规则）。
