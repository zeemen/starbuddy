# 冲刺 4-H：统一壳 MVP（家庭登录、选科、今日计划、星图、四科路由、点点入口、额度可见、休息提醒）

状态：**草稿，等人确认后才开始实现**。确认人、日期：＿＿＿

关联：
- **开发计划 冲刺 4-H**（W9–W10）：“统一壳 MVP：家庭登录、学生选科、今日计划（读 plan）、星图、四科路由挂载、点点入口、额度可见、休息提醒”。负责是前端 + TS 后端，验收是“一名测试学生可从壳内进入四科并回到星图”。
- **同期冲刺**：
  - 4-C 掌握估计 v0：写 `mastery_estimate`，决定星亮；生成复测调度；
  - 4-D 四科补齐方法：阿鸦 `plan` 在这里实现（`specs/adapter-aya-methods-sprint4/spec.md`，另一个会话正在起草，本仓库还没有）；
  - 4-G 发布闸门 v0：四科各发布一个内容版本（`curriculum_version.status = 'published'`）。
- **承接的 spec**：
  - 3-H 星图原型（`specs/star-map-sprint3/`）：lessons 待人确认 #12（删除开发身份桩）、#13（CSP）、#15（日志哈希改带密钥 HMAC）都指向 4-H；
  - 渗透测试第二轮（`specs/household-pentest-round2/spec.md`）：第 7 节的自动化回归“作为 4-H 验收的一部分同步写”；第 2 节 N、O、P、Q 类是 4-H 的攻击面；
  - 渗透测试第一轮（`specs/household-pentest-sprint3/spec.md`）待人确认 #1：BFF 鉴权中间件没有单列，默认归在 4-H。
- **完整方案 v1.0**（`docs/plan/完整方案-v1.0.html`）：
  - 学生端：“今日 20–40 分钟计划、学科星图……不展示其他学生数据；可关闭陪伴功能；休息提醒”；
  - 家长端：“看证据和建议，不看孩子逐句对话与同伴排名；监护操作留痕”；
  - 5.7：“统一学生端壳由平台新建：家庭账号登录、今日计划（跨科）、星图、七科入口、点点常驻入口、额度可见、休息提醒、陪伴开关。各科课堂页面作为路由模块挂入”；
  - 第七章合规设计输入：监护人同意（不满 14 周岁）、AI 身份显著提示、时长控制与夜间限制、未成年人数据最小化。
- **人设与视觉**：点点 V1.2（2.1 身份卡、2.3 职责、4.2 每日开场、继承的 4.7 家长端隔离与 4.8 时长节律、5.2 禁语）；阿鸦 V1.1 §4.11（阿鸦不出现在家长端）；视觉家族规范 v1.2 第六章（家长端不出现任何角色形象）。
- **ADR**：01（BFF 分工，待签；待人确认 #3“统一壳一律经 BFF”）、06（计量）、09（数据驻留）、10（境内托管，待签；选项 B“认证由 BFF 自建，手机号 OTP 用境内短信”）。
- **CLAUDE.md**：第 1、2、4、5、7、8、9、10 条；必停项“migrations”“RLS”“计量与权益”“contracts”“人设”“新增第三方”。

## 目标
一名合成家庭的家长用手机号验证码登录，为孩子建档、完成同意、在这台设备上打开学生模式。学生在壳内看到今日计划、自己的星图和额度，能从壳进入各科路由，再回到星图。整个过程身份只来自真实会话，开发身份桩不再存在。

同时把渗透测试第二轮第 7 节的自动化回归写在 4-H 的路由表和会话上，每次 `make check` 都跑。

## 现状（起草时核实，2026-09-26）

### apps/web（3-H 已合并）
- 只有三类页面：
  - 星图原型 `apps/web/src/app/star-map/[studentId]/page.tsx`；
  - 符号样张 `apps/web/src/app/dev/star-glyphs/page.tsx`；
  - 开发登录 `apps/web/src/app/dev/login/page.tsx` 和写 cookie 的 `apps/web/src/app/dev/session/route.ts`。
- **开发身份桩（前端侧）** `apps/web/src/lib/dev-auth.ts`：
  - 第 7 行 cookie 名 `sb_dev_account`；第 9–10 行开关 `STARBUDDY_DEV_AUTH=1`；
  - 第 13–16 行：cookie 里的账号只要在合成账号列表里就算登录；
  - 星图页第 14 行读这个 cookie；样张页也用 `devAuthEnabled` 做开关（`dev/star-glyphs/page.tsx` 第 4 行）。删除桩时样张页要换一个开关。
- **BFF 地址只能是 loopback**：`apps/web/src/lib/star-map.ts` 第 33–40 行；第 46 行把账号放进请求头 `x-dev-account-id`。
- **安全头**：`apps/web/next.config.ts` 第 11–28 行有 `nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`、`Permissions-Policy`。**没有 CSP**，第 18 行注释写明留给 4-H（需要 nonce）。
- 根布局 `apps/web/src/app/layout.tsx` 用内联 `<style>` 输出主题变量。上 CSP 时这段也要带 nonce，或改成静态 CSS 文件。
- 学科中文名在 `apps/web/src/lib/labels.ts` 第 5–10 行：aya→数学、zhixing→道法、wenli→语文、echo→英语。

### services/bff
- 只有两个模块：计量 `src/metering/`（2-E）和星图读模块 `src/star-map/`（3-H）。`services/bff/README.md` 写明“登录、会话、支付都不在这里”。
- **没有登录、会话、鉴权中间件、限流**，也没有路由表。星图的 HTTP 层是单条正则路由（`src/star-map/server.ts` 第 16 行）。
- **开发身份桩（BFF 侧）**：`src/star-map/server.ts` 第 5–9 行注释、第 39–48 行 `devAccount()`、第 46 行读 `x-dev-account-id`；`src/star-map/main.ts` 第 15 行读开关，第 25 行只监听 127.0.0.1。
- **日志哈希不加密钥**：`src/star-map/server.ts` 第 37 行，`student_id` 取 sha256 前 16 位。知道 UUID 的人能对上号。
- **身份如何进入数据库**：`src/metering/db.ts` 第 5–24 行 `withAccount()`，第 10 行 `set_config('app.account_id', $1, true)`，事务级；出错回滚，回滚失败时销毁连接（第 18、22 行）。
- 计量模块导出 `reserve / settle / release / meteredCall`（`src/metering/index.ts` 第 7 行）。`meteredCall` 目前没有任何 HTTP 入口调用它。
- 依赖只有 `pg`（`services/bff/package.json`），HTTP 用 `node:http`，测试用 `node:test`。

### 数据库（授权完全靠 RLS 与 `app.account_id`）
- **`0002_platform__household.up.sql`**：
  - `account.status` 只有 `active / disabled`（第 18 行）。没有任何函数或授权让 BFF 改它。
  - `account_contact` 第 22 行注释：**v0.9 只有家长账号有联系方式**。学生账号没有手机号，也没有任何登录凭据。
  - 联系方式表没有给任何应用角色的策略（第 297–299 行），只能经函数访问。
  - `signup_guardian(phone)`（第 142 行）：注释写“BFF 在短信验证码通过后调用”，直接写 `verified_at = now()`。它**不检查号码是否已注册**，同号可以建多个家长账号（第一轮 C2）。
  - `lookup_accounts_by_phone(phone)`（第 153 行）：返回有效账号的 ID 与类型，可能多行（第一轮 C3：号码是否注册的查询通道）。
  - `create_student(...)`（第 161 行）：只有有效家长能调，同一事务里建学生账号、档案与创建者绑定。
  - `create_guardian_invite` / `redeem_guardian_invite`（第 190、211 行）。
  - `student_profile` 只存年级、地区、教材版本、出生年月（第 38–47 行），**没有姓名或昵称**。
  - `consent_record` 只追加；只有已绑定的有效家长能写（第 333–334 行策略 `consent_write`）。
  - 这些函数都授予 `starbuddy_bff`（第 278–282 行）。
  - 数据库里**没有会话、验证码、限流相关的表**。
- **`0009_platform__metering_functions.up.sql`**：
  - `enroll_subject(student, subject)`（第 510 行）：学生本人或已绑定家长可调；该学科必须有 `published` 的课程版本，否则返回 `not_launched`；必须有有效主订阅，否则 `no_subscription`；按档位的 `max_active_subjects` 限制；
  - `pause_subject`（第 552 行）；
  - 第 564–565 行收回了 BFF 对 `subject_enrollment` 的直接 INSERT，选科只能经函数；
  - `meter_open_subscription(student, source)`（第 471 行）：只有已绑定家长能开 `trial` 或 `pilot_virtual`；
  - `meter_reserve`（第 155 行）要求 `is_self_student`，即**只有学生本人的身份能消耗额度**。
- **`0008`** 第 497 行 `entitlement_balance` 视图（`security_invoker`），第 515 行授予 BFF；`entitlement` 按 `can_read_student` 可读（第 544 行）。这就是“额度可见”的数据源。
- **`0003`** 第 48 行：BFF 只能读 `published` 的课程版本。阿鸦登记脚本登记的版本是 `draft`（3-H spec 现状）。所以在 4-G 发布之前，**阿鸦不能被选科**，星图也就没有数学星空。

### 契约与学习服务
- 插件 API 有 `/plan`（`packages/contracts/openapi/plugin-api.v0.9.yaml` 第 100 行），非消耗类。`PlanTask` 只有任务类型、能力点、优先级、预计分钟和 `rationale_code`（第 788–810 行），“叙述文字由平台生成”。
- **学习服务的内部接口只有 `grade` 与 `diagnose`**（`packages/contracts/openapi/learning-internal.v0.yaml` 第 37、55 行），没有 `plan`。按 ADR 01 草案第 2 条，适配器由学习服务调用，BFF 不直接调适配器。
- 阿鸦适配器的 `plan`、`teach` 还是 `implemented: false`（`adapters/aya/src/adapter.ts` 第 296–297、412 行），由 4-D 实现。

### 四科现实
- `adapters/` 下**只有 `aya`**。知行、文砺没有适配器，也没有 2-D 的 spec 目录。
- **开口成章已搁置**（`specs/adapter-echo-sprint3/lessons.md`，2026-09-26：“英语课程需要做重大的调整”）。仓库里没有 `adapters/echo`，也没有 `legacy/echo`。
- 原产品只读副本：`legacy/aya`（Next.js 单体，Supabase 登录，`legacy/aya/middleware.ts` 第 2、9–10 行；学习状态以浏览器 localStorage 为准）；`legacy/wenli/初中语文_AI_class/wenli/apps/` 下有 `student`、`parent`、`daofa`（知行寄宿在文砺仓库）。原产品各有自己的登录与数据表，部署在 Vercel（ADR 10 §1.1）。

### ADR 与人设
- **ADR 01**（`docs/adr/01-bff-and-learning-service.md` 第 3 行）与 **ADR 10**（`docs/adr/10-domestic-hosting-selection.md` 第 3 行）都是“草稿，待签字”。ADR 10 建议选项 B：认证由 BFF 实现，手机号 OTP 用境内短信（第 51、69 行）。短信供应商没有定。
- **学生登录方式没有定**（第二轮 spec 待人确认 #5）。
- **点点没有运行时**：仓库里没有点点的 Prompt、对话接口或模型路由代码；点点视觉形象方向待决（点点 V1.2 附录二 #2）。
- **`evals/` 目录不存在**，`make eval-persona` 只打印“尚未建立，跳过”（`Makefile` 第 136–137 行）。3-F 人设治理 spec 还是草稿。
- 点点 V1.2 第 91 行继承 V1.0 的 4.8“时长节律”，但 V1.0 原文不在本仓库，休息提醒的触发阈值也没有定义（附录二 #4 写“平台组定义”）。

## 设计要点

### 1. 总体链路
- 浏览器 → `apps/web`（Next.js 服务端组件与 route handler）→ BFF → Postgres（`starbuddy_bff`，RLS 生效）/ 学习服务。
- 浏览器**不直连** BFF、学习服务、适配器与数据库（沿用 3-H，ADR 01 待人确认 #3 的倾向）。
- `apps/web` 只转交会话令牌，不自己判断身份，也不自己判断授权。**身份只在 BFF 一处解析，授权只在数据库一处判断。**

### 2. 认证与会话
**家长登录（短信验证码）**：
1. `POST /bff/v0/auth/otp/request {phone}`：无论号码是否注册，都返回同一个 202 和同一个响应体（第一轮 C3、第二轮 N1）。
2. `POST /bff/v0/auth/otp/verify {phone, code}`：验证通过后，在**同一个数据库函数**里完成“核对验证码 → 查账号（没有就注册）→ 建会话”。
   - 这样 BFF 没有“为任意账号建会话”的函数可调，会话只能从验证码或家长签发产生；
   - 验证码 6 位数字，只存 HMAC；有效期、错误次数上限从配置读（默认按第二轮待人确认 #4：5 分钟，错 5 次作废）。
   - 同号已有多个家长账号时（历史数据），默认拒绝登录并提示联系客服，见待人确认 #6。
3. 短信发送经 `SmsSender` 接口。4-H **只实现捕获桩**：验证码写进只有测试能读的收件箱，不给任何真实号码发短信。真实供应商另起必停 PR（待人确认 #3）。

**学生登录（默认：家长在设备上打开学生模式）**：
- 学生账号没有手机号（`0002` 第 22 行），默认不给未成年人新增凭据。
- 已登录的家长在孩子的档案上点“在这台设备上打开学生模式”。BFF 调数据库函数，要求当前账号 `is_linked_guardian(student)`，为**学生账号**签发一个新会话，并替换本设备的家长会话。
- 学生会话下，`app.account_id` 是学生账号。这样 `meter_reserve` 的“只有学生本人能消耗”（`0009` 第 170 行）自然成立。
- 回到家长模式要重新做短信验证。孩子拿不到家长会话。
- 其他方式（独立账号、设备配对码）见待人确认 #1。

**会话令牌**：
- 256 位随机数，库里只存 sha256。令牌不进 URL、不进日志、不进 Referer。
- cookie：`__Host-sb_session`，`HttpOnly; Secure; SameSite=Lax; Path=/`，不设 Domain。本地 http 开发用不带 `__Host-` 前缀的名字，只在开发构建里允许。
- **轮换**：登录成功、切换到学生模式时都发新令牌，旧令牌立即作废（防会话固定）。
- **失效**：
  - 有效期：绝对 7 天，空闲 24 小时，都从配置读（待人确认 #4）；
  - 登出只废本会话；“退出所有设备”废本账号全部会话；
  - 每次解析会话都核对账号 `status = 'active'`，账号禁用后下一次请求就是 401；
  - 解除绑定不需要废会话：家长会话还在，但 RLS 下已经读不到孩子，返回 404（第二轮验收“401 或 404”）。
- 允许多设备并发会话（第二轮待人确认 #4 的默认）。

**会话存放（默认：Postgres，新迁移）**：
- 新增 `0010_platform__auth_session`：会话、验证码挑战、限流计数三张表。
  - 三张表都 `FORCE ROW LEVEL SECURITY`，不给任何应用角色策略，只能经 `SECURITY DEFINER` 函数访问（属主 `starbuddy_authz`，与 `account_contact` 同一做法）；
  - 函数：`auth_otp_issue`、`auth_otp_login`、`auth_session_resolve`、`auth_session_revoke`、`auth_session_revoke_all`、`auth_issue_student_session`、`auth_rate_hit`；
  - `auth_session_resolve` 只返回账号 ID、账号类型、会话类型，不返回任何学生数据。
- 理由：ADR 10 草案第 5 条倾向“阶段 0–1 优先用 Postgres 自带能力”；BFF 多实例时会话与限流必须共享；不引入 Redis 这类新组件。
- 这是必停项（迁移 + RLS）。另一个选择见待人确认 #2。

### 3. BFF 鉴权中间件与路由表
- 新建 `services/bff/src/http/`：一个 `node:http` 入口、一个**路由表**、一条中间件链。星图模块改为挂在这张表上。
- 路由表每一项声明：`method`、`path` 模板、`auth`（`public` / `session` / `guardian` / `student`）、`rateLimit`（可选）、`csrf`（改变状态的一律开）、`handler`。
- 中间件链固定顺序：请求 ID → 限流 → 解析会话 → 按 `auth` 拦截 → CSRF 校验 → `withAccount(accountId)` → handler → 统一错误与安全头。
- **分发只查路由表**：表里没有的路径一律 404，不存在“忘了挂中间件”的路由。路由表导出给测试，第二轮第 7.1 节的“路由 × 账号”矩阵从它自动生成。
- **身份只来自会话**：handler 拿到的 `accountId` 只能来自 `auth_session_resolve`。请求参数、请求体、请求头里的 `account_id` 一律不读（第二轮 N3）。
- 路径里的 `student_id` 只作为查询条件，授权交给 RLS；未授权与不存在返回同一个 404，响应体与响应头逐字节相同（沿用 3-H）。
- `withAccount` 保持事务级 `set_config(..., true)`；所有查询都在事务里（第二轮 N4）。

**v0 路由**（前缀 `/bff/v0`）：

| 方法 | 路径 | auth | 说明 |
|---|---|---|---|
| POST | `/auth/otp/request` | public，限流 | 发验证码 |
| POST | `/auth/otp/verify` | public，限流 | 登录或注册家长 |
| POST | `/auth/logout` | session | 废本会话 |
| POST | `/auth/logout-all` | session | 废本账号全部会话 |
| GET | `/me` | session | 账号类型；家长返回已绑定孩子列表；学生返回自己的 `student_id` |
| POST | `/students` | guardian，限流 | `create_student` 建档 |
| POST | `/students/{id}/consents` | guardian | 写 `consent_record` |
| POST | `/students/{id}/student-session` | guardian | 在本设备打开学生模式 |
| GET | `/students/{id}/enrollments` | session | 选科状态与可选学科 |
| POST | `/students/{id}/enrollments/{subject}` | session | `enroll_subject` |
| POST | `/students/{id}/enrollments/{subject}/pause` | session | `pause_subject` |
| GET | `/students/{id}/today` | session | 今日计划 |
| GET | `/students/{id}/star-map` | session | 星图（3-H 接口不变，换成真实会话） |
| GET | `/students/{id}/balance` | session | 额度 |

- 邀请第二位家长、解除绑定、订阅开通不在 4-H（见“不做”）。
- 接口定义放 `services/bff/openapi/shell.v0.yaml`，与 `star-map.v0.yaml` 同属 BFF 内部接口，不进 `packages/contracts`（沿用 3-H 待人确认 #2 的默认）。

### 4. 删除开发身份桩
- 删除：`apps/web/src/lib/dev-auth.ts`、`/dev/login`、`/dev/session`、`apps/web/src/dev/accounts.ts`；BFF 的 `devAccount()`、`STARBUDDY_DEV_AUTH` 与 `x-dev-account-id`。
- **是删除，不是关掉**（第二轮 N3）。构建产物扫描断言找不到 `x-dev-account-id`、`sb_dev_account`、`/dev/login`。
- 符号样张页 `/dev/star-glyphs` 改为只在开发构建里存在，不进 staging 与生产构建。
- 开发与测试怎么登录：用短信捕获桩走真实流程。端到端测试从捕获收件箱读验证码。
- `bffBaseUrl` 的 loopback 限制改为配置白名单：开发只允许 loopback，staging 与 pilot 只允许内网地址。

### 5. 选科
- 只经 `enroll_subject` / `pause_subject`，BFF 不直接写 `subject_enrollment`（`0009` 已收回 INSERT）。
- 函数返回的 `outcome` 原样映射成界面状态：`enrolled`、`already_active`、`limit_reached`（显示上限数）、`no_subscription`、`not_launched`。BFF 与前端**不自己算**上限。
- 学生本人与已绑定家长都能选（数据库就是这样授权的）。界面上默认学生端和家长端都有入口，见待人确认 #8。
- 没有 `published` 课程版本的学科显示“暂未开放”，不能选。这意味着 4-G 发布之前，真实环境里一科也选不了；测试用合成的已发布版本。
- 没有订阅时显示“需要家长开通”。**4-H 不开订阅**（见“不做”与待人确认 #9）。

### 6. 今日计划
- 数据链路：BFF `GET /students/{id}/today` → 学习服务 `POST /internal/v0/plan` → 各科适配器 `/plan`。学习服务负责组装 `PlanRequest` 的 `mastery` 快照（读平台 `mastery_estimate`，规则 2）。
- **学习服务的内部接口现在没有 `plan`**。默认由 4-H 在 `learning-internal.v0.yaml` 增加 `POST /internal/v0/plan`，并在学习服务实现转发。这是 `packages/contracts` 变更，**必停**。如果 4-D 的 spec 已经包含这一条，4-H 不重复做（待人确认 #5）。
- 4-H 的今日计划是**按科并列**，不是跨科编排：
  - 每个在读学科一节，列出该科 `plan` 返回的任务，按 `priority` 排序；
  - 跨科时间预算与编排是阶段 1 M4 的“今日计划跨科编排”，由点点执行，不在 4-H。
- 另外列出到期复测：`mastery_estimate.next_retest_at` 已到期的能力点（4-C 写入）。只读当前记录（`is_current`）。
- **任务怎么显示**：
  - 任务类型用固定中文（诊断、练习、复习、微课、复测、模考）；
  - `rationale_code` 用一张固定映射表转成短句，例如 `retest_due` → “到期复测”；没有映射的编码不显示理由；
  - 能力点只有 `label_code`，没有中文名（3-H 待人确认 #9 未决），暂显示编码。
- 某科适配器未实现 `plan`（501）或超时：这一科显示“今天暂无安排”，不影响其他科。
- `plan` 不是消耗类方法，不计量。计划不落库，每次请求现算；BFF 响应 `no-store`。

### 7. 星图接入真实会话
- 接口与三态映射不变（`src/star-map/display-state.ts`）。只把身份来源从开发桩换成会话，并挂到路由表上。
- 页面地址改为 `/s/{studentId}/star-map`（学生）和 `/family/{studentId}/star-map`（家长）。旧地址 `/star-map/{id}` 删除。
- 3-H 的“原型 · 合成数据”水印保留到 4-C 上线并经教研确认后再去掉。
- 3-H 的授权测试、端到端测试全部改为用真实会话跑，断言不变。

### 8. 四科路由挂载
完整方案 5.7 的原则是“前端合并壳，不合并页”。三种挂载方式的比较：

| 方式 | 问题 |
|---|---|
| iframe 嵌原产品页 | 原产品有自己的 Supabase 登录，与壳的会话是两套身份；阿鸦的学习状态在 localStorage，会成为第二真相源（规则 2）；原产品部署在 Vercel（ADR 09、10）；要放开 `frame-src` |
| 跳转到原产品 | 离开壳后回不来星图；同样有两套身份和第二真相源 |
| 微前端（运行时加载远端模块） | 构建与 CSP 复杂；阶段 0 只有一科有适配器，收益小 |

**默认：壳内路由模块**。
- 每科一个目录 `apps/web/src/subjects/<subject>/`，登记在一张清单里：学科、中文名、状态（`live` / `coming` / `hidden`）、入口组件。
- 路由统一为 `/s/{studentId}/learn/{subject}`，共用壳的布局：顶栏有“今日”“星图”“科目”，额度、点点入口、休息提醒。任何科目页一步回到星图。
- 科目页只调 BFF，不调原产品，不读适配器内容包（规则 1）。原产品页面不挂进壳，`frame-src 'none'`。
- **四科现实**：
  - **数学（aya）**：`live`。4-H 只显示阿鸦的今日任务和该科星空入口。作答、诊断、批改入口要走计量，放到冲刺 5 联调（待人确认 #9）；
  - **道法（zhixing）、语文（wenli）**：`coming`。占位页写“即将开放”，可以进入并返回；
  - **英语（echo）**：`hidden`，已搁置，不显示入口。
- 所以开发计划的验收“进入四科”，在 4-H 实际是“进入三个科目路由（一科真实、两科占位），英语不显示”（待人确认 #7）。

### 9. 额度可见
- 读 `platform.entitlement_balance`（`0008` 第 497 行），`withAccount` 下 RLS 生效：学生看自己，家长看已绑定孩子。
- 只显示本人的资源类型、单位、剩余量和周期结束日期。不显示别人，不显示预留明细。
- 多个来源（主订阅、附加包）的同一资源合并成一行显示总剩余；分来源明细只在家长端显示。
- 只读，不预留、不结算。前端不传、BFF 不收任何数量参数。

### 10. 休息提醒
- 默认规则（数值从配置读，待合规与产品确认，见待人确认 #10）：
  - 学生会话连续使用 40 分钟，壳顶部出现提醒条，建议休息 10 分钟；
  - 22:00–次日 6:00 打开学生模式时，显示夜间提醒。
- **只提醒，不强制锁定**。强制时长控制与夜间限制属于合规的“未成年人模式”，4-H 不做。
- 计时以 BFF 为准：学生会话记录开始时间和最近一次“确认休息”的时间，前端只负责显示。不写 localStorage。
- 提醒文案用**系统口吻**，不以点点或任何老师的身份说话，不配角色形象。这样它不是人设文本；文案仍按禁语 A–G 类自查（不催促、不评价、不比较）。
- 以角色口吻表达时长节律（点点继承的 4.8）属于人设话术，是必停项，不在 4-H。

### 11. 点点入口的边界
- 学生壳的每一页有一个固定位置的“点点”入口按钮。点开是占位面板，系统口吻写“点点正在准备中”。
- 4-H **不做**：点点对话、点点的任何台词、模型调用、点点形象（方向待决）、AI 身份披露流程（有对话时才需要）、陪伴开关。
- 家长端**不出现**点点入口，也不出现任何角色形象（视觉规范第六章、点点继承的 4.7、阿鸦 §4.11）。
- 以后接入点点对话时：人设 Prompt 是必停项，要跑 `make eval-persona` 并附前后对比（规则 7）。

### 12. 家长端（4-H 的最小范围）
- `/family`：已绑定孩子列表、建档、同意、打开学生模式、查看某个孩子的星图、选科、额度。
- **孩子怎么区分**：档案没有姓名或昵称（`0002` 第 38–47 行）。默认显示“孩子 1 · 八年级”这样的序号加年级，见待人确认 #11。
- 不显示任何对话内容、同伴比较、排名。
- 监护操作留痕：建档、同意、打开学生模式、选科、暂停，BFF 各写一条访问日志（第 15 节格式）。审计表另议（3-H 待人确认 #12 的延续，见待人确认 #12）。

### 13. 隐私与未成年人
- **同意是打开学生模式的前提**：
  - 必须有 `privacy_policy` 的 `granted` 记录；
  - 孩子不满 14 周岁（按 `birth_year_month`，为空时按不满 14 处理）时，还必须有 `guardian_consent_under14`；
  - 同意记录带 `document_version`。
- **同意文本与版本由合规轨提供，现在没有**。4-H 默认：开发与测试用合成版本号 `synthetic-0`；配置禁止在 staging 以外的环境使用 `synthetic-` 前缀，所以 pilot 在合规交付前无法开学生模式（待人确认 #13）。
- 数据最小化：建档只收年级、地区、教材版本、出生年月，与 `0002` 一致；手机号只用于登录。
- 缓存：所有含学生数据的页面与接口 `no-store`；不注册 Service Worker；不写 localStorage、IndexedDB（沿用 3-H）。
- 不加载境外资源；Next 遥测保持关闭（3-H lessons 偏差 5）。

### 14. 安全响应头与 CSP
- 在 Next 的请求中间件里为每个请求生成 nonce（文件名按锁定的 Next 版本约定），CSP 默认：
  `default-src 'self'; script-src 'self' 'nonce-<n>' 'strict-dynamic'; style-src 'self' 'nonce-<n>'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'`。
- 根布局的内联 `<style>` 带 nonce，或改成静态 CSS 文件（实现时二选一，记入 lessons）。
- HSTS 只在 staging 与 pilot 发送（`max-age` ≥ 180 天），本地 http 不发。
- 保留 3-H 的 `nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`。
- BFF 响应一律 `no-store`、`nosniff`；错误体只有错误码，不回显堆栈、SQL、内部地址（第二轮 P3）。
- **CSRF**：改变状态的请求要同时满足：`SameSite=Lax` cookie；`Origin` 必须等于壳的来源；带一个与会话绑定的 CSRF 令牌（双重提交）。BFF 不设 CORS 放行头。

### 15. 限流
- 存在第 2 节的限流表里，BFF 多实例共享。阈值全部来自一份配置文件，测试读同一份。
- 默认阈值（第二轮待人确认 #4 的常见值）：

| 接口 | 维度 | 默认 |
|---|---|---|
| 发验证码 | 每号每分钟 / 每号每天 / 每 IP 每小时 | 1 / 10 / 30 |
| 验证验证码 | 每号每 15 分钟 / 每 IP 每小时 | 10 / 60 |
| 建档 | 每家长账号每天 | 5 |
| 打开学生模式 | 每家长账号每小时 | 20 |

- 超过阈值一律返回同一个 429 响应体，不区分原因，不泄露号码是否注册（第二轮 O1）。
- 同号注册多个家长账号（第一轮 C2）：`auth_otp_login` 在号码已有账号时直接登录，不再调 `signup_guardian` 新建，入口层堵住。
- 邀请码兑换在 4-H 没有路由，限流随邀请功能一起做。

### 16. 日志
- BFF 访问日志只记：请求 ID、路由模板（不是原始路径）、状态码、耗时、账号类型、`student_id` 的 HMAC。
- HMAC 密钥从环境变量读，不进仓库，长度不足拒绝启动；开发与测试启动时随机生成（3-H 待人确认 #15）。密钥存放与轮换随 ADR 10 定（第二轮待人确认 #8）。
- 不记：会话令牌、验证码、手机号原文（最多记手机号的 HMAC）、请求体、星图与计划内容、数据库报错消息。
- 测试断言：日志里找不到令牌、验证码、手机号、UUID 原文。

## 范围

### 做
建议拆成 4 个 PR，每个 PR 只做一件事：

1. **H1 认证与会话**：
   - 迁移 `0010_platform__auth_session`（会话、验证码、限流三张表与函数，含 down）与 RLS 测试；
   - BFF `src/http/`：路由表、中间件链、会话解析、CSRF、限流、统一错误；
   - `SmsSender` 接口与捕获桩；
   - 登录、登出、`/me`、建档、同意、打开学生模式；
   - 日志改带密钥 HMAC。
2. **H2 删除开发身份桩与安全头**：
   - 删除第 4 节列出的全部文件与代码；
   - 星图挂到路由表，换成真实会话；3-H 测试改用真实会话；
   - CSP、HSTS、构建产物扫描。
3. **H3 壳页面**：
   - 登录页、家长页、今日计划、星图、选科、科目路由（清单 + 三个科目页）、额度、休息提醒、点点入口占位；
   - BFF 的选科、今日计划、额度路由；
   - 学习服务 `POST /internal/v0/plan` 与契约（如果待人确认 #5 定由 4-H 做）；
   - 合成种子扩展：一户可登录的合成家庭（`+999` 号码）、合成的已发布课程版本（只在测试库）、合成订阅与额度。
4. **H4 第二轮第 7 节回归**：
   - 路由授权矩阵（从路由表生成）、身份注入、连接池串号、会话、限流、安全头与缓存、计量入口；
   - 每类至少一条变异，接进 `bff-mutation`；
   - `docs/security/attack-surface.md` 的 M1 补真实会话引用，N–Q 类登记 4-H 的断言引用。
5. `specs/unified-shell-sprint4/lessons.md`：实现记录与新增待确认项。

### 不做
- 真实短信供应商接入（必停，另起 PR）。
- 学生独立账号、学生手机号、设备配对码（待人确认 #1 的另一个选择）。
- 邀请第二位家长、解除绑定、家庭切换的界面（数据库函数已有，界面留给家长端 v1，阶段 1 M4）。
- 订阅开通、支付、试点虚拟额度的开通入口（权益逻辑，必停）。
- 任何消耗类请求的入口：诊断、批改、错题分析、讲评课、语音、AI 互动计时（计量，冲刺 5 联调）。
- 跨科编排的今日计划、点点每日开场话术（阶段 1 M4）。
- 点点对话、点点形象、陪伴开关、任何人设文本。
- 原产品页面的 iframe 嵌入或跳转。
- 知行、文砺的适配器与真实内容；英语的任何内容。
- 强制时长控制、夜间锁定、未成年人模式（合规轨定规则后另起）。
- Service Worker 与离线缓存；PWA 安装清单也暂不做。
- 周报（5-H）、星的详情浮层、点亮动效的触发。
- 审计表（见待人确认 #12）。

## 验收标准（可测试）
- [ ] **开发计划验收**（Playwright 端到端，合成数据，全程真实会话）：
  - 合成家长用 `+999` 号码请求验证码，从捕获收件箱读码，登录；
  - 为合成学生打开学生模式；
  - 学生从“今日计划”进入“科目”，依次进入数学、道法、语文三个科目路由，每次从科目页一步回到星图，星图上显示该生在读学科的星空；
  - 英语入口不存在；
  - 全程没有请求离开壳的来源。
- [ ] **开发身份桩已删除**：源码与构建产物（`apps/web/.next/`、BFF）里找不到 `x-dev-account-id`、`sb_dev_account`、`STARBUDDY_DEV_AUTH`、`/dev/login`、`/dev/session`；生产构建里没有 `/dev/star-glyphs`。
- [ ] **登录**：
  - 发验证码对已注册与未注册号码返回逐字节相同的响应；
  - 验证码错误、过期、用过、换号使用都失败，且失败响应相同；
  - 错误次数到上限后验证码作废；
  - 同一号码第二次登录不会新建家长账号（断言账号数不变）。
- [ ] **会话**（第二轮 7.4）：
  - cookie 属性为 `HttpOnly`、`Secure`（非本地）、`SameSite=Lax`、`Path=/`、`__Host-` 前缀；
  - 登录与切换学生模式后令牌变化，旧令牌 401；
  - 登出、退出所有设备、账号禁用后，旧会话的下一次请求 401；
  - 解除绑定后，家长旧会话读孩子的任何路由都是 404；
  - 过期（绝对与空闲）后 401，时间用可注入的时钟测试；
  - 令牌不出现在日志、URL、Referer。
- [ ] **学生模式**：
  - 未绑定的家长、已解除绑定的家长为该学生开学生模式都失败，响应与“学生不存在”相同；
  - 学生会话下访问兄弟姐妹或其他学生的任何路由都是 404；
  - 学生会话不能调 guardian 路由（403 还是 404 按实现定，全表一致）；
  - 缺少必需的同意记录时不能打开学生模式；`birth_year_month` 为空时按不满 14 周岁处理。
- [ ] **路由授权矩阵**（第二轮 7.1）：从路由表生成“路由 × 账号（未登录、家长本人、学生本人、兄妹、另一户家长、禁用账号、解除绑定的家长、同号第二账号）”矩阵；未登录一律 401；越权与不存在同样 404，响应体与响应头逐字节相同；服务器能响应但不在路由表里的路径数为 0。
- [ ] **身份注入**（第二轮 7.2）：每条路由在查询参数、请求体、请求头（含 `x-dev-account-id`、`x-account-id`）里注入别人的 `account_id` / `student_id`，结果与不注入时相同。
- [ ] **连接池串号**（第二轮 7.3）：两个账号 × 至少 200 次交替并发请求，没有一次串号；故意让事务出错后，同一连接上 `current_setting('app.account_id', true)` 为空。
- [ ] **CSRF**：改变状态的路由缺 CSRF 令牌、令牌不属于本会话、`Origin` 不符时都拒绝；响应没有 `Access-Control-Allow-Origin`。
- [ ] **限流**（第二轮 7.5）：发验证码、验证验证码、建档、打开学生模式超过阈值后返回逐字节相同的 429；阈值来自配置文件，测试读取同一份。
- [ ] **安全头与缓存**（第二轮 7.6）：爬遍壳内全部路由，每个响应都有带 nonce 的 CSP（不含 `unsafe-inline`、`unsafe-eval`）、`frame-ancestors 'none'`、`no-store`；页面上每个内联脚本与样式都带本次响应的 nonce；没有外部资源。
- [ ] **选科**：五种 `outcome` 各有集成测试，界面状态与之一一对应；BFF 源码里没有对 `subject_enrollment` 的 INSERT 或 UPDATE。
- [ ] **今日计划**：适配器返回的任务按科分组、按 `priority` 排序；适配器 501 或超时时该科显示“今天暂无安排”，其他科正常；到期复测只取 `is_current` 记录；响应没有 `p_mastery`、`confidence`、`evidence_ids`。
- [ ] **额度可见**：学生只看到自己的额度；家长只看到已绑定孩子的；解除绑定后 404；数值等于 `entitlement_balance.remaining` 的合计。
- [ ] **计量入口**（第二轮 7.7）：4-H 没有消耗类路由。测试断言路由表里没有任何路由调用 `reserve` / `settle` / `meteredCall`；以后新增消耗类路由时，这条测试改为“前端传入的数量、资源类型、`usage_id` 被忽略”。
- [ ] **休息提醒**：用可注入的时钟，学生会话连续 40 分钟后出现提醒条，夜间时段打开学生模式出现夜间提醒；家长端不出现；阈值来自配置。
- [ ] **点点入口与家长端**：学生壳每页都有点点入口；家长端页面没有点点入口，没有任何角色图片（静态检查家长端组件不引用角色资产目录）。
- [ ] **日志**：`student_id` 以带密钥 HMAC 记录，换密钥后值变化；日志里找不到令牌、验证码、手机号、UUID 原文。
- [ ] **变异**（第二轮 7.8）：至少包括“身份改从请求参数读”“`set_config` 第三个参数改为 `false`”“登出不废会话”“限流按原因返回不同错误”“新路由绕过鉴权中间件”“验证码不检查过期”，全部被指定的断言发现。
- [ ] **RLS 测试**（规则 5）：新表对 `starbuddy_bff`、`starbuddy_learning` 直接 SELECT / INSERT / UPDATE / DELETE 全部被拒；函数只授予 `starbuddy_bff`；迁移可回滚。
- [ ] **文案自查**：壳内全部界面文案收在一个文件里，静态检查不含比较词表（“别人”“同学都”“排名”“最差”等）；人工按禁语 A–G 类复核一遍，结果记入 lessons。
- [ ] **许可证**：没有新增运行时依赖；如有，按规则 10 更新 `apps/web/LICENSES.md` 或 BFF 的清单并通过检查。
- [ ] 严格模式 `make check` 通过。

## 涉及的必停项（见 CLAUDE.md）
默认方案下逐条判断：

| 必停项 | 是否涉及 | 说明 |
|---|---|---|
| `db/migrations` | **是** | 新增 `0010_platform__auth_session`：会话、验证码、限流三张表与函数。另一个选择（待人确认 #2）也要迁移或引入新组件 |
| RLS 策略 | **是** | 新表 `FORCE ROW LEVEL SECURITY` 且不给策略；新增 `SECURITY DEFINER` 函数与授权。附 RLS 测试 |
| 计量与权益逻辑 | 否（默认） | 只读 `entitlement_balance`；选科调用现有 `enroll_subject`，不改函数，不自己算上限；不开订阅、不预留、不结算。如果待人确认 #9 选择在 4-H 开订阅或接入消耗类入口，就变成“是” |
| `packages/contracts` | **是**（默认） | 在 `learning-internal.v0.yaml` 增加 `POST /internal/v0/plan`。如果 4-D 已包含（待人确认 #5），4-H 不改契约 |
| `evals/` 金标准 | 否 | 不读不写 |
| 人设 Prompt、禁语表、转介话术 | 否（默认） | 点点入口只是占位，休息提醒用系统口吻，没有任何角色台词。UI 文案不是人设文本，但按禁语 A–G 类自查。如果要让点点说开场或提醒休息，就变成“是”，需跑 `make eval-persona` |
| 新增第三方服务 / 供应商 / 出境路径 | 否（4-H 内） | 4-H 只做短信接口和捕获桩。**真实短信供应商是必停项**，要随 ADR 10 选定境内供应商，另起 PR（待人确认 #3） |

**实现 PR 首行写 `[NEEDS-HUMAN]`**。理由：迁移与 RLS；契约变更；认证与会话属于安全关键代码，要做安全审查；同意流程与休息提醒的规则需要合规与产品确认。H2、H4 不含迁移与契约，但涉及鉴权，也建议写 `[NEEDS-HUMAN]`。

## 依赖与输入
- **已合并**：3-H 星图与开发身份桩；2-E 计量模块与 `0008`、`0009`；第一轮渗透测试的夹具与攻击面清单；第二轮 spec（草稿，未确认）。
- **同期、会卡住验收的**：
  - **4-G 发布闸门**：真实环境里没有 `published` 课程版本就一科也选不了。4-H 的验收用测试库里的合成已发布版本完成，不以 4-G 为门禁。
  - **4-D 阿鸦 `plan`**：没有它，今日计划的数学一节只能显示“今天暂无安排”。4-H 用假适配器（按契约返回合成任务）完成验收。
  - **4-C 掌握估计**：没有它，星图与到期复测只有合成数据，保留“原型 · 合成数据”水印。
- **需要人签字或提供的**：
  - ADR 01（统一壳一律经 BFF）、ADR 10（认证由 BFF 自建、会话放 Postgres、短信供应商、密钥管理）；
  - 合规轨：隐私政策与监护人同意的文本和版本号、时长与夜间规则；
  - 产品：学生登录方式、会话与限流参数、休息提醒阈值；
  - 视觉：统一壳视觉（开发计划角色轨 W5–W8“统一壳视觉”），没有交付时按 3-H 令牌与系统字体实现。
- **下游**：第二轮渗透测试的进入条件（W10 末）要求 4-H 合并、开发身份桩不存在；冲刺 5 端到端联调要在壳上加消耗类入口。

## 待人确认
1. **学生怎么登录**（第二轮待人确认 #5）：
   - 默认：家长登录后在设备上“打开学生模式”，为学生账号签发会话；学生没有自己的凭据；回家长模式要重新短信验证。
   - 另一个选择：学生独立账号（学生手机号验证码）。要给未成年人存手机号，改 `account_contact` 的注释约定，需合规确认。
   - 再一个选择：家长生成一次性设备配对码，孩子在自己的设备上输入。多一个可暴力猜测的码，要限流。
2. **会话存放在哪里**：
   - 默认：Postgres 新迁移 `0010`，表不给策略，只经函数访问；限流计数同表共存。必停。
   - 另一个选择：无状态签名令牌加一张“已吊销”表。仍要迁移，而且“退出所有设备”和空闲超时更难做。
   - 再一个选择：Redis 一类组件。新增组件，要随 ADR 10 评估。
3. **短信供应商**：
   - 默认 4-H 只做接口与捕获桩，不接任何真实供应商；供应商随 ADR 10 选境内厂商，另起必停 PR，并登记 ADR 09 数据流。
   - 另一个选择：4-H 内同时接入选定的供应商沙箱。需要先定供应商，并确认沙箱不出境。
4. **会话、验证码、限流参数**（第二轮待人确认 #4）：
   - 默认：验证码 5 分钟、错 5 次作废；会话绝对 7 天、空闲 24 小时；允许多设备；限流阈值见第 15 节。
   - 另一个选择：学生会话更短（例如绝对 12 小时），家长会话保持 7 天。
5. **学习服务的 `plan` 内部接口由谁加**：
   - 默认：4-H 在 `learning-internal.v0.yaml` 加 `POST /internal/v0/plan`，学习服务组装 `mastery` 快照后转发给适配器。契约变更，必停。
   - 另一个选择：放进 4-D（阿鸦 `plan` 的 spec 一起定），4-H 只做消费方。两个会话要对齐，避免重复改契约。
6. **同一号码已有多个家长账号**（历史数据，第一轮 C2）：
   - 默认：拒绝登录，提示联系客服；新注册在入口层保证一号一个家长账号。
   - 另一个选择：列出账号让用户选。会把“同号有几个账号”暴露给持号人。
7. **“进入四科”的验收口径**：
   - 默认：数学真实、道法与语文占位、英语不显示（已搁置）；验收改为“进入三个科目路由并回到星图”。
   - 另一个选择：保持“四科”，英语也放一个“调整中”的占位入口。需要产品确认英语入口是否对学生可见。
8. **谁能选科**：
   - 默认：学生端与家长端都有选科入口（数据库两者都允许）。
   - 另一个选择：只有家长能选科或暂停，学生端只读。数据库不用改，只是 BFF 路由改成 `guardian`。
9. **订阅开通与消耗类入口**：
   - 默认：4-H 都不做。开发与测试用合成订阅；pilot 的开通方式另定。
   - 另一个选择：建档后由 BFF 自动调 `meter_open_subscription('trial')`，或在 4-H 接入阿鸦诊断的计量入口。都属于计量与权益，必停。
10. **休息提醒规则**：
    - 默认：连续 40 分钟提醒、建议休息 10 分钟；22:00–6:00 夜间提醒；只提醒不锁定；系统口吻。
    - 另一个选择：按合规意见做强制限制（例如夜间不能打开学生模式）。需要合规轨给出规则。
11. **家长端怎么区分孩子**：
    - 默认：序号加年级（“孩子 1 · 八年级”），不收姓名。
    - 另一个选择：档案加一个家长自填的昵称字段。要改迁移（必停），并按数据最小化评估。
12. **监护操作留痕**：
    - 默认：4-H 只写访问日志（HMAC 后的 `student_id`、操作类型、账号类型）。
    - 另一个选择：新增只追加的审计表。要改迁移，建议与家长端 v1 一起做。
13. **同意文本未到位时的环境策略**：
    - 默认：开发与测试用 `synthetic-0` 版本号；staging 以外禁止 `synthetic-` 前缀，pilot 在合规交付前不能打开学生模式。
    - 另一个选择：pilot 先放行，事后补签。不建议，涉及未成年人个人信息。
14. **CSP 与内联样式**：
    - 默认：内联 `<style>` 带 nonce；不允许 `unsafe-inline`。
    - 另一个选择：主题变量改成静态 CSS 文件，CSP 的 `style-src` 只留 `'self'`。
15. **今日计划的能力点显示名**（3-H 待人确认 #9 的延续）：
    - 默认：暂显示 `label_code`。
    - 另一个选择：显示名经契约从适配器取，或平台加名称表（后者要迁移）。
