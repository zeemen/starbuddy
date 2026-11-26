# 讲评课会话：打开讲评课、课内提问与按节计量（指着元素提问第二阶段 PR ③b） · 规格草稿

- 状态：**已确认（2026-11-26，用户“按建议，默认值都同意，开始实现”）**：第五节五条都按默认；按第二节第 7 条逐个 `[NEEDS-HUMAN]` PR 实现，记录见 lessons.md。
- 来源：`specs/stage-point-to-ask-answer` 第二节第 7 条的 PR ③。实现时 ③ 拆成两半：③a（阿鸦取预制答疑，已合并 zeemen/starbuddy#48）与本规格的 ③b。
  用户 2026-11-26 说“继续 PR ③”。
- 依据：
  - `specs/stage-point-to-ask-answer`：第五节第 3 条，提问不另扣、算在这节课的 `lesson_whiteboard` 里；lessons Q5–Q7；
  - 插件契约 v1.1 草稿：`TeachRequest.question`、`TeachSegment`（`whiteboard_dsl` 片段带 `dsl` 与 `dsl_version`）；
  - 计量迁移 0008、0009、0058：
    - `lesson_whiteboard` 按次计（count），预留 90 分钟过期，只认 `teach`；
    - 基础档 0 次，进阶档 10 次，高级档 40 次；
    - 学习服务写入前按 `platform.learning_meter_check` 核对；
  - 诊断会话的先例：一个会话一笔 `diagnosis_full` 预留，第一道题判分成功后即结算，后续步骤按会话的 `usage_id` 核对 `reserved` / `settled` / `started_in_time`（`services/learning/learning/meter.py`）；
  - 再练票据的先例：学习服务签发、BFF 加密下发、按学生绑定（`services/bff/src/practice/ticket.ts`）；
  - CLAUDE.md 规则 4、9、11、13。

## 一、现状（2026-11-26 核对）
- **讲评课交不到前端**：
  - 阿鸦有讲评课内容包（③a），但没有方法把讲解 DSL 和台词稿交给平台；
  - 契约的 `TeachSegment` 能带 `whiteboard_dsl`（`dsl`、`dsl_version`），**带不了台词稿**。`specs/stage-dsl-v0.2` 第三节已把“`TeachSegment` 携带台词稿”列为契约变更另起；
  - 前端只有样张页在播放合成样例。
- **没有讲评课会话与路由**：
  - BFF 没有 `teach` 路由；学习服务没有讲评课的内部接口；
  - 课内多次提问共用一次扣次，没有现成写法：BFF 的计量是一次请求一笔预留，以幂等键为单位。
- **契约要求每次 `teach` 都带 `usage_id` 与幂等键**（`MeteredContext`），学习服务写入前还要核对计量。所以课内提问必须挂在某一笔已有的计量上。

## 二、默认做法：照诊断会话的样子做“讲评课会话”
1. **打开讲评课**：学生点开一节讲评课，前端带上 `lesson_ref`。
   - BFF 预留一次 `lesson_whiteboard`（数量 1，`teach`），幂等键每次打开生成一个。额度不足、基础档、宵禁等由现有的 `meter_reserve` 判定，前端沿用现有提示。
   - BFF 转给学习服务新的内部接口 `POST /internal/v1/lesson/open`，写在服务本地的 `services/learning/openapi/lesson-internal.v0.yaml`，与 practice-internal 同类，不进 `packages/contracts`。
   - 学习服务建讲评课会话，然后调适配器 `teach(mode: lesson, lesson_ref)`，取回讲解 DSL 与台词稿。
   - 校验：学习服务用 `packages/stage` 的同一套入口校验，DSL 用 `migrateStageDsl`，台词稿用 `migrateStageScript`；不合格就不下发，释放预留。
   - 合格后下发给前端，同时签发讲评课票据（不透明，BFF 加密，绑定学生，有效期与预留一致）。
   - **结算时机**：讲评课内容成功下发即结算这一次。
2. **契约追加**（必停）：讲评课怎么从适配器交给平台：
   - `TeachRequest` 加可选 `lesson_ref`（只在 `mode: lesson`、不带 `question` 时，表示“打开这节讲评课”）；
   - `TeachSegment` 的 `whiteboard_dsl` 片段加可选 `script`（台词稿，结构与 `packages/stage` 的台词稿 schema 一致）；
   - 契约检查照 PR ① 的做法，只放过这几项，并核对与 `packages/stage` 一致。
3. **讲评课会话**（新迁移，必停）：表 `platform.lesson_session`：
   - 字段：学生、学科、`lesson_ref`、预留的 `usage_id` 与幂等键、创建 / 结束时间、已提问次数；
   - **不存讲解文字、提问与回答**；
   - RLS 与诊断会话同一口径：学生本人与授权家长可见，学习服务写；
   - 删除与导出接入现有的个人层清单（0035）。
4. **课内提问**：前端带上讲评课票据与第一阶段的提问对象。
   - BFF 先核票据（学生、讲评课、未过期），**不再预留**，转学习服务 `POST /internal/v1/lesson/ask`。
   - 学习服务依次核对：
     - 会话存在、属于该生、未结束；
     - 计量按会话的 `usage_id` 与预留键、方法 `teach` 核对，放行 `settled` 与 `started_in_time`，与诊断会话同一口径；
     - 提问次数未超上限，超了直接回“不答”，不调适配器。
   - 核对通过后调适配器 `teach(question)`：`context.usage_id` 用会话的那一笔，幂等键按（会话，第几问）派生，重放拿同一结果。
   - 适配器返回的处理：
     - 200：回答文字下发；
     - **422 `MODE_NOT_SUPPORTED` 按“不答”处理**（lessons Q7），前端显示兜底与“再讲一遍”；
     - 其他错误按现有的失败与重试处理。
   - 每问都不扣次，只累计会话里的提问次数。
5. **不作证据**：提问与回答都不进证据、掌握与判断。只有已提问次数是会话上的计数，不进画像。按步骤的聚合统计另起。
6. **前端**：讲评课页面（新路由，开关控制，缺省关闭）：
   - 播放由学习服务下发的 DSL 与台词稿，接上第一阶段的可提问模式；
   - 回答显示在字幕区下方；等待时用过程状态组件；
   - 兜底与菜单文案沿用 `STAGE_ASK` 的占位。
7. **拆成的 PR**（每个都 `[NEEDS-HUMAN]`）：

   | 顺序 | 内容 | 必停原因 |
   |---|---|---|
   | ③b-1 | 契约：`TeachRequest.lesson_ref`、`whiteboard_dsl` 片段的 `script` | 契约 |
   | ③b-2 | 阿鸦：`teach(mode: lesson, lesson_ref)` 下发讲评课 | 适配器方法行为 |
   | ③b-3 | 迁移：`platform.lesson_session`、RLS、删除与导出清单 | 迁移、RLS |
   | ③b-4 | 学习服务 `lesson/open`、`lesson/ask`；BFF 路由、讲评课票据、按节计量 | 计量 |
   | ③b-5 | 讲评课页面（开关缺省关闭） | 面向学生的新页面，文案占位 |

## 三、不做
- 2b 模型回答（规格 `stage-point-to-ask-answer` 第二节第 3 条，另开）。
- 讲评课内容生产与发布闸门、审核台；讲评课目录与推荐（哪节课给谁）。
- 道法、语文的讲评课；家长端的讲评课视图。

## 四、验收
- 打开一节合成讲评课：扣一次 `lesson_whiteboard`；内容不合格时不扣（释放）；重复打开同一幂等键不重扣；额度不足或基础档时打不开，沿用现有提示。
- 课内提问不扣次；超过上限后只剩“再讲一遍”；没有答疑时显示兜底；票据换学生、过期、换讲评课一律拒绝，且不调适配器。
- 会话表不存讲解文字与问答；RLS 有测试；删除与导出覆盖新表。
- 契约、计量、迁移、RLS 各自的检查、变异与幂等测试通过；端到端覆盖打开、提问、兜底、额度不足。

## 五、待人确认（括号里是默认）
1. **扣次单位**：一节讲评课扣一次 `lesson_whiteboard`，课内提问不另扣（默认，`stage-point-to-ask-answer` 第五节第 3 条已定）。另一种做法是每问单独扣一种新资源。
2. **结算时机**：讲评课内容成功下发即结算（默认，与诊断会话“第一题判分成功即结算”同一口径）。另一种做法是学生看完或退出时才结算，中途离开的不扣。
3. **会话有效期与提问上限**：有效期与预留一致，90 分钟内可以继续提问（默认）；每节课最多 20 问（默认；2a 不调模型，上限只防滥用；2b 时改成 10）。
4. **讲评课怎么从适配器交给平台**：契约追加 `TeachRequest.lesson_ref` 与 `whiteboard_dsl` 片段的 `script`（默认）。另一种做法是新增一个只读方法 `present_lesson`，像 `present_item` 那样不计量，由平台另行计量。
5. **会话存哪**：新建 `platform.lesson_session`，迁移由人执行（默认）。另一种做法是复用诊断会话表加类型列，不建议：两者状态机不同。
