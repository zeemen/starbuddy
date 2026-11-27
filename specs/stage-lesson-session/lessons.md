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


## ③b-3：迁移 0064 讲评课会话
### 做了什么
- 新迁移 `db/migrations/0064_platform__lesson_session.{up,down}.sql`，草稿，由人在副本演练后执行。
- **表 `platform.lesson_session`**：一行是学生打开的一节讲评课。
  - 存：学生、学科、讲评课引用（`lesson_id`、`lesson_version`，规则与契约 `LessonRef` 相同）、那笔预留的 `usage_id` 与幂等键、状态、提问上限与已提问次数、最近一问的幂等键、创建与过期时间。
  - 不存讲解 DSL、台词、提问与回答：表里没有 json、二进制、数组列，库测试核对列清单。
  - 状态只有三个：`opening`（已建会话、讲评课还没下发）→ `open`（已下发）或 `failed`（不合格或适配器失败）。`open`、`failed` 不再变。
- **新建守卫**（属主 authz）：
  - 学生档案存在且未删除；只能以 `opening`、0 问新建；
  - 必须对得上一笔 BFF 的讲评课预留：`usage_id`、幂等键、学生一致，来源方法 `teach`、资源 `lesson_whiteboard`、状态 `reserved` 且未过期。任一项对不上都回 42501，不说是哪一项；
  - 创建时间取计量时钟，过期时间取这笔预留的过期时间。调用方传的值一律覆盖。所以会话有效期就是预留有效期（第五节第 3 条）。
- **改动守卫**：身份、引用、计量、上限、有效期都不能改；过期以后不能再变成 `open`；提问次数只能在 `open` 且未过期时每次加 1，并且同时换最近一问的键。
- **课内提问放行 `platform.lesson_session_ask(会话, 学生, 这一问的键)`**（属主 authz，只授学习服务）：
  - 锁住会话行后依次判：`none`（会话不存在或不属于该生，同一返回）→ `not_open` → `expired` → `unpaid`（那笔预留既不是未过期的 `reserved`，也不是 `settled`）→ `replay`（与最近一问的键相同，不加次数）→ `limit` → `admitted`（加 1）；
  - 只返回结果编码与第几问，不返回预留状态、额度与时间；
  - 学习服务没有提问次数与最近一问的键的 UPDATE 权限，只能经这个函数加。
- **权限**：学习服务能读、能新建，只能改 `status` 一列；authz 读会话、改两列（放行函数用）；eraser 能删、只读学生键列；BFF 与其他应用角色都没有权限。
- **删除与导出**：登记进 0035 的个人层表清单，`delete`，范围 `evidence`（与诊断会话同组）。导出主题 `lesson_sessions`，只导学科、讲评课引用、状态、已提问次数与创建时间。
- 测试：
  - 新增 `db/tests/79zk_lesson_session.sql`：表结构、新建时核对预留的 8 种对不上、状态迁移、列权限、守卫、放行的 7 种结果与重放、上限、过期、只返回两个键、各角色读不到、清单与家长导出；
  - `79q` 给两名学生各建一个会话（经 BFF 的 `meter_reserve` 预留讲评课，档位没有额度时以超级用户补一笔预留），全库删除覆盖照常；
  - `79p` 的导出列白名单加了 `lesson_id`、`lesson_version`（讲评课对象键）与 `question_count`（计数）；
  - 渗透测试夹具（`db/pentest/fixtures.sql`）给每个学生补一个下发失败的会话（夹具自检要求每张带学生键的表覆盖每个学生）：按“预留 → 建会话 → failed → 释放”写流水，基础档讲评课额度是 0，所以不动额度桶计数，对账仍为零行；
  - 授权与策略快照（`db/tests/expected/`）随之更新：只多学习服务、authz、eraser 三方的授权与一条学习服务策略；
  - `db/tests/mutations.sh` 加了“讲评课会话”一组 40 条，结果见 PR 描述。

### 决定与差异
- **新建会话时就在库里核对预留**（规格没写）。学习服务的登录角色读不到计量账本（0058 的理由），而规格第二节第 1 条要求“学习服务建会话”。0058 的 `learning_meter_check` 只认 `grade`、`diagnose`，按 `teach` 一律 `none`（79zf 断言这一点），所以没有改它，另起放行函数。这样“开一节课必须有一笔有效的讲评课预留”由库保证，不靠学习服务自觉。
- **课内提问由一个库函数原子放行**（规格第二节第 4 条写的是学习服务依次核对）：会话、有效期、预留、上限、重放判定与加次数在同一个行锁里做完，避免并发提问超过上限。学习服务（③b-4）调它，按 `outcome` 决定调不调适配器。
- **不用 `started_in_time`**：规格第二节第 4 条说放行 `settled` 与 `started_in_time`、与诊断会话同一口径。这里会话的过期时间就取预留的过期时间，过了就是 `expired`，所以“预留过期后仍放行”的情形不会出现，没有照搬。预留在会话有效期内被释放或被标成 `expired` 时回 `unpaid`。
- **BFF 不读这张表**（规格第二节第 3 条写的是“学生本人与授权家长可见”）：讲评课票据由学习服务签发、BFF 加密下发，BFF 核票据就够，不用读表；家庭经已有的导出拿到这张表。少开一个读口，也不用进 80 的跨户可见性矩阵。以后要在页面上显示讲评课记录，随页面另起，见待人确认 L2。
- **重放只认最近一问的键**：只存最近一问的幂等键，不存每一问。网络重试通常紧跟着原请求，所以够用；更早的键再来会按新的一问计，并占一次上限。学习服务（③b-4）只对“刚发出、没拿到回答”的那一问重放。
- 提问上限存在会话上（`question_limit`，缺省 20，最多 20），2b 改成 10 时不用改迁移。

### 待人确认
- **L1**：清单删除范围归 `evidence`，理由是与诊断会话同组；它其实是学习行为记录，不是作答证据。若要单列，契约的回执范围要先加一项。
- **L2**：BFF 不读会话表，家长只能经导出看到“打开过哪些讲评课、问了几次”。若前端要显示，要定可见范围：家长看不看得到提问次数。这属于画像可见范围，必停。
- **L3**：导出列加了讲评课引用与提问次数。79p 白名单要求导出列是字段字典里对家长可见的字段或通用元数据，这三列按元数据放行。若认为提问次数是关于学生的数据、要进字段字典，就要另起字典变更。
- **L4**：重放只认最近一问的键（见上）。若要求任意一问都能重放，就要另建一张按问记幂等键的子表（仍不存文字）。

## ③b-4a：学习服务的讲评课内部接口
### 拆分
规格第二节第 7 条的 ③b-4 包括学习服务 `lesson/open`、`lesson/ask` 与 BFF 路由、讲评课票据、按节计量。实现时拆成两半：
- ③b-4a：学习服务这一侧，本 PR；
- ③b-4b：BFF 的学生端路由、票据封装、预留与结算，以及路由矩阵与 H6 计量用例。
理由：BFF 那一侧要动路由表、计量入口登记（`METER_CALLERS`）与它的集成用例、限流桶和密钥派生，都是计量必停项，单独审更清楚；学习服务这一半可以独立测试（真实库加假适配器）。

### 做了什么
- 新接口定义 `services/learning/openapi/lesson-internal.v0.yaml`，与诊断、再练的内部接口同一先例，不进 `packages/contracts`。
- 新模块 `services/learning/learning/lesson/`：
  - `ticket.py`：讲评课票据 `lt1`，HMAC 签名、不落库，与再练票据同一套编码；内容是学生、会话、学科、讲评课引用、打开时的节点与题、DSL 版本、会话过期时间；
  - `store.py`：会话的查、建、改状态，调 `platform.lesson_session_ask`，按作答记录判定 `answer_submitted`；
  - `service.py`：打开与提问的流程，见下；
  - `api.py`：两个接口，只认 BFF 的会话令牌，请求体规则与诊断相同，日志不记请求体与文字。
- **打开**（`POST /internal/v1/lesson/open`）：
  1. 同一学生、同一预留键已有会话就按幂等重放；没有就新建，库里的守卫核对预留，对不上回 409 `IDEMPOTENCY_KEY_SEALED`，不调适配器；
  2. 调适配器 `teach(mode: lesson, lesson_ref)`：契约 1.1.0，`usage_id` 是那笔预留，幂等键由（会话，是否已交题）派生，`allowed_hint_level = 1`，人设 `aya@1.1.0`；
  3. 校验：契约 `TeachResponse`；恰好一段 `whiteboard_dsl`、版本 0.3.0；讲解 DSL 与台词稿各按 `packages/stage` 冻结的 0.3.0 schema 校验，再做配对核对：每个 `say` 有台词、每句台词都被引用、角色与讲话人一致、步骤 id 不重复、不带 `answers`；
  4. 合格 → 会话 `open`，签发票据，返回讲解 DSL、台词稿、票据、过期时间、提问上限与次数；
  5. 适配器确定拒绝（400 / 404 / 409 / 422）→ 会话 `failed`、回 422 `LESSON_NOT_AVAILABLE`；内容不合格 → `failed`、回 502；暂时失败 → 留在 `opening`、回 503，同一个键重试。
- **提问**（`POST /internal/v1/lesson/ask`）：
  1. 验票（学生一致、未过期），核对提问对象的结构（不合格 400，不占次数）；
  2. 库里放行：`none` 404、`not_open` 409、`expired` 410、`unpaid` 409 `IDEMPOTENCY_KEY_SEALED`、`limit` 200 `no_answer`（不调适配器）；
  3. `admitted` / `replay` → 调适配器 `teach(question)`，`usage_id` 用会话那一笔，幂等键由（会话，第几问）派生，重放拿同一结果；
  4. 适配器 422 `MODE_NOT_SUPPORTED` → 200 `no_answer`（lessons Q7）；其他确定拒绝 → 422 `QUESTION_NOT_ANSWERABLE`；暂时失败 → 503。
- **`answer_submitted` 由平台判定**：这道题学生有作答记录就是 true，请求里不收这个字段（前端自报会被用来提前拿答案）。这回答了 ③b-2 的 R1 后半句“由平台在打开前判断”。
- 配置：`LEARNING_LESSON_TICKET_SECRET`（≥ 32 字符，与其他令牌和密钥各不相同），设了即开讲评课接口；签字前需同时设候选契约开关，所以只在 dev / test。启动时核对迁移 0064 已执行。已登记进 `deploy/config/env-inventory.yaml`。
- 镜像：`services/learning/Dockerfile` 与它的 `.dockerignore` 加上两份 0.3.0 schema。
- 测试：
  - 新增 `tests/test_lesson_session.py` 34 条：打开、重放与换课、四种预留状态、预留对不上、请求不合格、适配器确定拒绝与暂时失败、8 种内容不合格、`answer_submitted`；
  - 提问：回答与计数不扣次、重放、没有答疑、到上限不调适配器、票据（换学生、伪造、换前缀、过期）、不合格提问不占次数、预留已释放、已结算照样放行；
  - 日志不含文字与学生 ID、没配置 501、写入令牌 401、纯函数、配置开关、接口文档与源码和响应一致；
  - `tests/fixtures/meter_provision.sql` 的代建预留支持 `teach → lesson_whiteboard`。

### 决定与差异
- **讲解校验用同一份 schema 加同一组配对规则，不是 `migrateStageDsl` 本身**（规格第二节第 1 条写的是用 `packages/stage` 的同一套入口）。学习服务是 Python，读不了 TS 的入口。没有覆盖的是 `packages/stage` 的文字安全检查（`src/text.ts`）；台词文字另有契约 `StageScriptLine.text` 的字符规则把关，前端播放前（③b-5）还会用 `migrateStageDsl` / `migrateStageScript` 再读一次。见待人确认 L5。
- **节点与题放在票据里**：会话表（0064，已合并）不存节点与题，提问时适配器又要它们，所以随票据签名下发、提问时原样带回。票据由学习服务签名，前端改不了。
- **不合格的提问在库里放行之前就拒绝**，不占次数；适配器拒收的提问（例如指着还没出现的东西）已经占了一次，因为学习服务没有讲解 DSL，判断不了。见待人确认 L6。

### 待人确认
- **L5**：讲解 DSL 与台词稿的文字安全检查只在前端（③b-5）做一次。若要求平台下发前就做，学习服务要用 Python 重写一份 `src/text.ts` 的规则，并加一致性检查。
- **L6**：适配器拒收的提问也算一次。2a 上限 20 问，影响不大；2b 改成 10 问时可能要改成不算。
