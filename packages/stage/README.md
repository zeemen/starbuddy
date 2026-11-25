# @starbuddy/stage · 舞台

讲解 DSL 的类型、JSON Schema、确定性校验器、台词稿、版本迁移、播放状态机与最小 SVG 白板（ADR 07；`specs/stage-whiteboard-step1`、`specs/stage-dsl-versioning`、`specs/stage-dsl-v0.2`、`specs/stage-point-to-ask`）。
其他模块只经 `src/index.ts` 与 `src/Whiteboard.tsx` 使用本包；读入讲解 DSL 一律经 `migrateStageDsl`（当前版本直接校验，旧版本沿迁移阶梯升级后再校验）。

## 版本规则
- `DSL_VERSION`（`src/dsl.ts`）只描述**序列化格式**：字段、动作类型、枚举、上限、id 规则、公式命令表。与本包的 `package.json` 版本无关。
- 任何会让“旧校验器拒收新文档”或“新校验器拒收旧文档”的改动都是格式变化，必须升版本（0.x 阶段升 minor），包括收紧或放宽上限。
  只改渲染、播放、注释、不改格式的，版本不动。
- 格式指纹与已发布的 schema 锁在 `schema/versions.lock.json`，由 `tests/version.test.ts` 核对：
  改了格式常量却没升版本、改了已发布版本的 schema，`make stage-test` 都会失败。

## 升版本的步骤（例：0.1.0 → 0.2.0）
1. 改 `src/dsl.ts` 的格式常量与类型，`DSL_VERSION` 改为 `0.2.0`；校验器跟着改。
2. 新增 `schema/stage-dsl-0.2.0.schema.json`（旧版本的 schema 文件**不改**），`package.json` 的 `exports["./schema"]` 指向新文件。
3. 在 `schema/versions.lock.json` 末尾追加一行：`version`、`format_sha256`（`formatSpec()` 的 sha256）、`schema`、`schema_sha256`。已有行不改。
4. 在 `src/version.ts` 的 `DSL_MIGRATIONS` 末尾追加一级 `{ from: '0.1.0', to: '0.2.0', migrate }`：
   - 纯函数、幂等，输入输出都是普通 JSON；`dsl_version` 由执行器盖章，不用自己写；
   - **只改结构，不改任何文字与公式**（标题、`write_text.text`、`write_formula.latex`）；字段改名时用 `textKeys` 写明旧版本的字段名，
     执行器会核对每一级迁移前后文字逐字相同，不同即拒收；需要改文字才能表达的变化不能写成迁移，旧内容进重审。
5. 为这一级迁移补测试（旧文档能升上来、文字逐字保留、幂等、升级后通过完整校验），跑 `make stage-test`、`make stage-mutation`。

## 拒收规则（fail-closed）
- 比当前更新的版本：`future_version`；阶梯上找不到的版本：`unknown_version`；迁移抛错、改了文字、升级后不合格、阶梯不合规：`migration_failed`（升级后不合格时同时带出校验错误）。
- 不降级、不猜测；已审核内容迁移失败或需要改文字时进重审（发布闸门与审核台落地后实现）。

## v0.2：“说 / 画 / 指”与台词稿（`specs/stage-dsl-v0.2`）
- 台词与板书分开存：DSL 的步骤只写 `say: "<台词编号>"`；台词正文在台词稿（`src/script.ts`，`schema/stage-script-0.2.0.schema.json`）里，由人设审核。
  播放前用 `validateStagePair(dsl, script)` 核对：引用的台词都在、没有多余台词、每句的角色等于说这句时持有讲解权的角色、每句 ≤ 60 码点且最多两句。
  字幕只用 `captionAt` 从核对过的稿里取，组件不拼接文字。
- `narrated: true`：带书写或作图的步骤必须有台词（`unpaired_board`）；`point` 必须配台词、一步最多一个。`narrated: false` 是无声板书（v0.1 迁移过来就是它），不得有台词与指向。
- `point`：`spotlight`（目标外接框加粗描边）或 `laser`（目标左侧小三角），只指当步、下一步收起；目标是白板上现存的非高亮元素或 `slots` 里声明的槽位。
- `slots`：题图（`item_figure`）或学生作答切片（`student_answer`）的区域，只渲染空框；内容由调用方运行时在内存里填充，DSL 与台词稿都不含学生数据（ADR 07 决定 4）。

## 指着元素提问（`specs/stage-point-to-ask` 第一阶段）
- 提问对象 `StageQuestion = { dsl_version, cursor, anchor: { kind: element | slot | step, id }, intent }`（`src/question.ts`），
  意图只有四个编码：`why_step`、`what_is`、`where_from`、`again`，不收自由文字；只含 DSL 里的 id、步数与编码，不含学生数据。
- `validateStageQuestion(dsl, q)`：只能指已经出现的东西——此刻白板上的元素（高亮框不能单独被指）、声明过的槽位、已播放过的步；
  错误码 `not_visible`、`unknown_anchor`、`bad_intent`、`bad_version`、`bad_cursor`、`unknown_field`、`not_object`。
- `originsAt(dsl, cursor)`：白板上每个元素是第几步写上去的；`replayCursor(dsl, q)`：“再讲一遍”要 seek 到的步数（之后再 play）。
- 白板 `askable` 属性（缺省关闭，关闭时输出不变）：元素与槽位各一个透明点击区（`role="button"`、可 Tab 聚焦、回车或空格选中），
  选中画虚线细框；读屏标签由调用方给。提问对象**不是** DSL 序列化格式的一部分，不升 `DSL_VERSION`。
- 回答（契约、人设、模型、计量）是第二阶段，必停，另起。
