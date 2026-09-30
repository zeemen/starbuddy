# 冲刺 4-H：统一壳 MVP（家庭登录、选科、今日计划、星图、四科路由、点点入口、额度可见、休息提醒）

状态：**草稿，等人确认后才开始实现**（2026-09-30 按冲刺 2–4 合并后的现状复核，见“2026-09-30 复核”一节；待人确认 #5、#15 的默认随之更新）。确认人、日期：＿＿＿

> **2026-09-28：设计正文已按开发计划 v2.0 改写**（方案见 `specs/sprint4-v2-rewrite/spec.md`）。按 v1.0 起草的原版本见 git 历史（2026-09-27 及之前的提交）；与原版本的差异见下面“相对 v1.0 版本的变化”。改写只动本文件，不改代码、迁移与契约。

任务包：H 流“统一壳 · 星图 · 信任界面”，冲刺 4（W9–W10）统一壳 MVP。冲刺 5 的“为什么”“我不同意”、对账卡界面与跨科周报 v0 不在本 spec，只预留入口。

关联（现行依据）：
- **开发计划 v2.0**（`docs/plan/开发计划-v2.0.html`）：
  - 冲刺 4 H：“统一壳 MVP：家庭登录、选科、今日计划、星图、四科路由、点点入口、额度可见、休息提醒”，验收“测试学生从壳内进入四科并回到星图”；
  - H 流总表：“家庭登录、今日计划、星图（星 = 知识节点，含待验证虚线）、四科路由；‘为什么’‘我不同意’、对账卡；跨科周报 v0”；
  - 冲刺 5 H：“‘为什么’‘我不同意’、对账卡界面；跨科周报 v0（家长版不含逐句对话与同伴比较）”；冲刺 1 L：SIE-01 建档（学生、家长分别填写，两方互不可见）；冲刺 4 L：SIE-02b（判断账本接入掌握状态、`insight.explain`、中圈指标静默计算）。
- **完整方案 v1.1**（`docs/plan/完整方案-v1.1.html`）：
  - 三端：学生端“今日 20–40 分钟计划、学科星图……每颗星可看‘为什么’，可对判断说‘我不同意’；不展示其他学生数据；可关闭陪伴功能；休息提醒”；家长端“各科使用与剩余额度……考试预测对账卡；画像数据查看、导出、更正与删除；看证据和建议，不看孩子逐句对话与同伴排名；监护操作留痕”；
  - 4.2 星图的统一定义；4.5 信任的产品化；5.7 前端壳与舞台（“前端合并壳，不合并页”）；5.8 计量接入；“未成年人、安全与上线前决策”一章的合规设计输入（监护人同意、AI 身份提示、时长控制与夜间限制、数据最小化）。
- **KGE V1.1**（`docs/plan/KGE-知识与成长引擎-V1.1.html`）：知识地图、掌握与星亮；**SIE V1.1**（`docs/plan/SIE-学生洞察引擎-V1.1.html`）：第八章信任界面（学生、家长各看到什么，“我不同意”的三条路）、拍板第 2 项（首发只对外展示数学“知识掌握”“知识迁移”）。
- **ADR**（`docs/adr/`，均为草稿待签）：
  - 11 星图统一定义（星 = 知识节点、能力视图侧栏、虚线以 ADR 13 待人确认 #2 为准、过渡条款）；
  - 13 判断账本的唯一写入方是洞察服务（BFF 只读、前端可见范围）；
  - 01 BFF 与学习服务分工（统一壳一律经 BFF）；06 计量账本一致性；09 数据驻留；10 境内托管（认证由 BFF 自建、手机号验证码用境内短信）。
- **契约**（`packages/contracts/openapi/`）：
  - `insight-service.v0.9.yaml`：`RenderedStatement`（必须带 `claim_id`；`to_verify` 为 `display: dashed`）、`insight.explain`、`insight.dispute`、`insight.reconcile`（v0.9 只给编排层）、`insight.export` / `insight.erase`（只给家长）；
  - `plugin-api.v1.0.yaml`（候选版，待签）：`/plan` 的 `PlanTask`（`rationale_code`，叙述文字由平台生成）；
  - `learning-internal.v1.yaml`（候选版，待签）：BFF → 学习服务，目前只有 grade、diagnose 与重放。
- **计量与权益 v2**：`specs/metering-v2/spec.md` 与 `lessons.md`（已确认并实现，迁移 `0015_platform__metering_v2` 待人执行）；`services/bff/src/metering/meter.ts`（拒绝原因 `retained_no_ai`、`curfew`、`curfew_soon`、`daily_cap_reached`）。
- **用户系统与会员权益规格书 V1.2**（`docs/plan/用户系统与会员权益规格书-V1.2.html`，讨论稿，补充总纲）：§2 角色与权限、§3 试用与留存态、§5 选科、§7 计量规则、§12.2 设备与会话；学生家庭码 + PIN、主 / 协同监护人、设备上限属 PLT-01 修订。
- **对照文档** `specs/plan-v2-alignment/spec.md`：第三节决定记录（#1 英语搁置、#4 星的定义）；第八节 8.1 免费用户决定、#2 核心包决定、#5 单日上限与宵禁、#6 账号与家庭。
- **字段字典** `docs/design/sie/field-dictionary.v0.9.yaml`（草稿）：`dimensions` 的 `launch`、`inner.g3.node_mastery`、`inner.g3.next_retest_at`、`inner.g4.skill_view`、`inner.g4.transfer_gap_math` 的可见范围。
- **承接的 spec**：
  - 3-H 星图（`specs/star-map-sprint3/spec.md`、`lessons.md`，已合并；代码按 ADR 11 过渡条款暂不动）：lessons 待人确认 #12（删除开发身份桩）、#13（CSP）、#15（日志哈希改带密钥 HMAC）指向 4-H；
  - 渗透测试第二轮（`specs/household-pentest-round2/spec.md`）：第 7 节自动化回归作为 4-H 验收的一部分；第 2 节 N、O、P、Q 类是 4-H 的攻击面；
  - 渗透测试第一轮（`specs/household-pentest-sprint3/spec.md`）待人确认 #1：BFF 鉴权中间件归在 4-H。
- **人设与视觉**（只作约束，本 spec 不改）：`docs/personas/点点-规格书-V1.2.md`（家长端隔离、时长节律、禁语）、`docs/personas/阿鸦-规格书-V1.1.md`（阿鸦不出现在家长端）、`docs/personas/角色视觉家族规范-v1.2.html`（家长端不出现角色形象）。
- **CLAUDE.md**：规则 1、2、4、5、6、7、8、9、10、11、12；必停项“migrations”“RLS”“计量与权益”“contracts”“画像可见范围”“人设”“新增第三方”。
- **仅作追溯（已被取代）**：`docs/archive/完整方案-v1.0（已被v1.1取代）.html`、`docs/archive/开发计划-阶段0-1-v1.0（已被v2.0取代）.html`。本 spec 不再以它们为依据。

## 相对 v1.0 版本的变化

| 改写点 | 依据 |
|---|---|
| 星图改为“星 = 知识节点”：星取已发布地图版本的 `kge.knowledge_node`；能力坐标只在侧栏作能力视图、不点亮；待验证虚线等 ADR 13 待人确认 #2 签字后才画；未发布地图的学科显示“地图建设中” | ADR 11 第 3 节；对照文档第三节 #4；迁移 0011 |
| 星图数据源切换有前提（ADR 11 签字、数学首个地图版本发布、4-C 掌握 v0、SIE-02b 判断接入）；前提不满足时壳内挂已合并的 3-H 星图，只换真实会话，数据源不动 | ADR 11 过渡条款；CLAUDE.md 规则 11 过渡条款 |
| 星的亮暗、星数说明、今日计划推荐理由、家长端任何“孩子最近……”都要引用判断账本里一条“成立”或“假设”的判断；没有判断就不出文字 | CLAUDE.md 规则 11；ADR 13；`insight-service.v0.9.yaml` 的 `RenderedStatement` |
| 首发对外维度只有数学“知识掌握”“知识迁移”，其余维度静默，不进壳；BFF 用一张对外模板白名单兜底 | SIE 拍板第 2 项；字段字典 `dimensions` |
| 预留“为什么”“我不同意”、对账卡、数据查看 / 导出 / 删除入口，默认隐藏，实现在冲刺 5 | 开发计划 v2.0 冲刺 5 H、L（SIE-03） |
| 今日计划不再由 BFF 直接读 `mastery_estimate.next_retest_at` 列到期复测；复测只以 `plan` 返回的 `retest` 任务出现，理由按判断显示 | 规则 11；字段字典 `inner.g3.next_retest_at` 经判断展示 |
| 学习服务的 plan 内部接口从 `learning-internal.v0` 改到 `v1`（候选版）；plan 按插件 API v1.0 候选版 | `learning-internal.v1.yaml`、`plugin-api.v1.0.yaml`；`specs/plugin-api-v1.0/spec.md` |
| 额度可见按计量 v2：试用剩余天数、留存态（只看历史 + 每天 5 次自练）、单日上限与今日剩余分钟、宵禁与 `curfew_soon`；新增家长的时长设置页（只能调低上限、只能收紧宵禁） | `specs/metering-v2/`；迁移 0015；`meter.ts` |
| 休息提醒的“夜间提醒”改为显示该生实际宵禁时段（服务端在预留时强制）；连续使用提醒保留，只提醒不锁定 | 计量 v2 第 5 项；规格书 V1.2 §7 |
| 选科按“核心 + 选修槽”（英语搁置期间基本档任选 1 科）与 0015 的新 outcome；默认只有家长能选科、换科 | 计量 v2 第 3 项与 lessons 偏差 3、9；规格书 V1.2 §2、§5；对照文档第八节 #2 |
| 账号：家庭登录（家长短信验证码）留在 4-H；学生家庭码 + PIN、主 / 协同监护人、设备上限与单一活跃会话随 PLT-01，本 spec 只写依赖；“打开学生模式”降为 PLT-01 前的过渡入口 | 规格书 V1.2 §2、§12.2；对照文档第八节 #6 |
| 会话迁移不再叫 `0010`（0010–0015 已被占用），编号合并时取下一个空号 | `db/migrations/` 现状 |
| 登录与选科流程给 SIE-01 建档留位置 | 开发计划 v2.0 冲刺 1 L |
| 待人确认去重：plan 接口归属、四科验收口径、审计表、CSP 实现方式改为引用已决项或移入依赖 | 本 spec“与同期 spec 的协调”“已决事项” |

## 目标
一名合成家庭的家长用手机号验证码登录，为孩子建档、完成同意，在这台设备上打开学生模式。学生在壳内看到今日计划、自己的星图、试用或额度状态与今日剩余时长，能从壳进入各科路由再回到星图。壳内任何关于学生的文字都能指向判断账本里的一条判断；没有判断的地方只有中性展示，不出文字。家长能看到孩子的星图与额度，能调低单日上限、收紧宵禁。整个过程身份只来自真实会话，开发身份桩不再存在。

同时把渗透测试第二轮第 7 节的自动化回归写在 4-H 的路由表和会话上，每次 `make check` 都跑。

## 现状（改写时核实，2026-09-28）

### apps/web（3-H 已合并）
- 页面只有星图原型 `apps/web/src/app/star-map/[studentId]/page.tsx`、符号样张 `apps/web/src/app/dev/star-glyphs/page.tsx`、开发登录 `apps/web/src/app/dev/login/page.tsx` 与写 cookie 的 `apps/web/src/app/dev/session/route.ts`。
- **开发身份桩（前端侧）** `apps/web/src/lib/dev-auth.ts`：cookie `sb_dev_account`，开关 `STARBUDDY_DEV_AUTH=1`，cookie 里的账号只要在合成账号列表（`apps/web/src/dev/accounts.ts`）里就算登录；样张页也用 `devAuthEnabled` 做开关。
- `apps/web/src/lib/star-map.ts` 的 `bffBaseUrl()` 只允许 loopback，`fetchStarMap()` 把账号放进请求头 `x-dev-account-id`。
- `apps/web/next.config.ts` 有 `nosniff`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`，**没有 CSP**（注释写明留给 4-H）。根布局 `apps/web/src/app/layout.tsx` 用内联 `<style>` 输出主题变量。
- 学科中文名在 `apps/web/src/lib/labels.ts`（产品命名空间 aya / zhixing / wenli / echo）。

### services/bff
- 只有计量 `src/metering/` 与星图读模块 `src/star-map/`；`services/bff/README.md` 写明“登录、会话、支付都不在这里”。没有登录、会话、鉴权中间件、限流与路由表；星图的 HTTP 层是 `src/star-map/server.ts` 里的单条正则路由。
- **开发身份桩（BFF 侧）**：`src/star-map/server.ts` 的 `devAccount()` 读 `x-dev-account-id`；`src/star-map/main.ts` 读 `STARBUDDY_DEV_AUTH`，只监听 127.0.0.1。
- 日志哈希 `hashId()` 是不加密钥的 sha256 前 16 位。
- 身份进数据库：`src/metering/db.ts` 的 `withAccount()`，事务级 `set_config('app.account_id', $1, true)`。
- 计量模块导出 `reserve / settle / release / meteredCall`；预留结果含计量 v2 的 `retained_no_ai`、`curfew`、`curfew_soon`（带 `minutes_until_curfew`）、`daily_cap_reached`（带 `remaining_today`）。目前没有任何 HTTP 入口调用计量。
- 星图读模块读 `platform.skill_node` 与 `platform.mastery_estimate`（3-H），三态映射在 `src/star-map/display-state.ts`，接口 `services/bff/openapi/star-map.v0.yaml`。

### 数据库（迁移 0001–0015，0010–0015 为草稿待人执行）
- **家庭与同意**（`0002_platform__household`）：`signup_guardian`、`lookup_accounts_by_phone`、`create_student`、`create_guardian_invite` / `redeem_guardian_invite`；只有家长账号有联系方式；`student_profile` 没有姓名或昵称；`consent_record` 只追加、只有已绑定家长能写。**没有会话、验证码、限流表，没有主 / 协同监护人角色，没有学生登录凭据。**（2026-09-29 补：迁移 0020 已有审核台 staff 专用的凭据与会话表 `platform.staff_credential` / `staff_session`，见 `specs/release-gate-sprint4/lessons.md` PR 4；4-H 建家庭会话时按其“待人确认 #6”合并。）
- **计量 v2**（`0015_platform__metering_v2`）：
  - `enroll_subject` 按“核心 + 选修槽”校验，outcome 为 `enrolled`（带 `slot`、`limit`）、`already_active`、`limit_reached`（带 `slot`、`limit`）、`change_next_period`、`no_subscription`、`not_launched`；`pause_subject` 为 `paused`、`not_active`、`change_limit_reached`；学生本人与已绑定家长都能调；
  - `platform.subject_catalog`：九科、上线状态（语文、数学、道法 beta，只对试点订阅开放；英语等 planned）、适用年级、是否核心、对应产品命名空间；
  - `meter_student_state`（trialing / active / grace / retained / none）与 `set_time_guard`（只有已绑定家长；outcome `saved`、`cap_above_plan`、`curfew_must_cover_default`）授予 BFF；
  - 单日上限、今日已用分钟、是否宵禁、距宵禁分钟数的函数（`meter_daily_cap`、`meter_minutes_today`、`meter_in_curfew`、`meter_minutes_until_curfew`）**没有授予 BFF**；BFF 只能读 `student_time_guard` 的设置列、`subscription`、`entitlement_balance`、`practice_log`。
- **额度**：`0008` 的 `platform.entitlement_balance` 视图（`security_invoker`）授予 BFF，按 `can_read_student` 可读。
- **判断账本**（`0010_insight__claim_ledger`）：BFF 可按 RLS（`claim_front_read`）读 `insight.claim`：学生本人只看可见范围含 student 的、有效绑定家长只看含 guardian 的非终态、未过期、模板仍有效的判断；模板表只看 approved。
- **知识地图**（`0011_kge__knowledge_map`）：BFF 可读已发布版本的 `kge.map_version`、`kge.knowledge_node`（有 `name`）、`kge.knowledge_edge` 与投影；**`kge.skill_link` 没有授予 BFF**。
- `0003` 规定 BFF 只能读 `published` 的课程版本；4-G 发布前真实环境没有可选的学科。

### 契约与服务
- `learning-internal.v0.yaml` 与 `v1.yaml` 都**没有 plan**；按 ADR 01，适配器由学习服务调用，BFF 不直接调适配器。
- `plugin-api.v1.0.yaml` 的 `PlanTask` 只有任务类型、`skill_nodes`、优先级、预计分钟与 `rationale_code`，**不带判断 ID**。
- `insight-service.v0.9.yaml`：BFF 能调 `insight.explain`、`insight.dispute`、`insight.record`（学生自述 / 家长观察）、`insight.export` / `insight.erase`（只家长）；`insight.context`、`insight.claims`、`insight.predict`、`insight.reconcile` 只给编排层。**没有给 BFF 的批量陈述读取工具。**洞察服务的部署位置未定（ADR 13 待人确认 #3）。
- 阿鸦适配器 `adapters/aya/src/adapter.ts` 的 `plan`、`teach`、`generate_variation`、`report` 还是 `implemented: false`（4-D 实现）。

### 四科现实
- `adapters/` 下只有 `aya`。知行、文砺的适配器在 v2.0 冲刺 2（ADP-道法-01、ADP-语文-01），本仓库还没有。
- 英语已搁置（`specs/adapter-echo-sprint3/lessons.md`；对照文档决定 #1），`subject_catalog` 里为 planned。
- 原产品只读副本：`legacy/aya`（Supabase 登录，见 `legacy/aya/middleware.ts`；学习状态以浏览器 localStorage 为准）；`legacy/wenli/初中语文_AI_class/wenli/apps/`（含知行）。

### 人设与评测
- 点点没有运行时（没有 Prompt、对话接口、模型路由代码），形象方向待决。
- `evals/` 目录不存在，`make eval-persona` 只打印“尚未建立，跳过”。

## 2026-09-30 复核（冲刺 2–4 合并后的变化，优先于上面“现状”的对应条目）
- **前端已有的页面与组件**：建档页（SIE-01）、复核台 staff 页（2-G、4 PR）、星图（3-H）、“能做对吗”探针组件与诊断组件（2-L、SIE-02a，只在开发构建里的演示页）。统一壳把诊断接成首周“建立学业基线”入口（学生本人、35 分钟可分次），探针随做题页复用；演示页保留、仍按构建时排除。
- **BFF 已有接口**：星图、建档、行为事件与探针、诊断（开始 / 续做 / 取题 / 作答 / 暂停 / 放弃，含宵禁 402 与计量结算）。第 9 节“额度与时长可见”与第 10 节休息提醒可复用诊断里按 0015 口径读 `student_time_guard` 自算宵禁的模块（SIE-02a lessons #15），待人确认 #10 的默认（新增 `meter_guard_status` 只读函数）不变，函数落地后两处一起切换。
- **洞察服务已有**（SIE-02b 第一步，`services/insight`）：`insight.claims / explain / context` 已实现，首份报告由服务内接口生成（每句带判断 ID）。待人确认 #5（BFF 怎么拿陈述文字）现在的默认改为：BFF 调洞察服务的首份报告 / 判断视图接口（SIE-02b 第二步定接口形状，若需新增契约工具则走必停 PR），不再逐条调 explain；洞察服务代表学生 / 家长时的授权再核对缺库函数（SIE-02b lessons C）。
- **题面**：待人确认 #15 与 SIE-02a 的 `present_item` 缺口是同一件事，默认合并为同一个契约变更 PR（插件 API 只读方法 `present_item`，不含答案与解析），签字前统一壳的做题入口只在测试环境用合成题面。
- **迁移编号**：已用到 0028；本 spec 的新迁移从 0029 起按合并顺序编号。
- **计量**：SIE-02a 把诊断改为“首题判分即结算、未答题放弃才退”；统一壳的其他消耗入口沿用计量 v2 既有规则，不另定。

## 设计要点

### 1. 总体链路
- 浏览器 → `apps/web`（Next.js 服务端组件与 route handler）→ BFF → Postgres（`starbuddy_bff`，RLS 生效）/ 学习服务 / 洞察服务。
- 浏览器**不直连** BFF、学习服务、洞察服务、适配器与数据库（沿用 3-H；ADR 01）。
- `apps/web` 只转交会话令牌，不判断身份与授权。**身份只在 BFF 一处解析，授权只在数据库一处判断**；BFF 调洞察服务时按契约带 `actor`，洞察服务再核对一次（规则 5）。
- 4-H 不写任何个人层表，不写判断，不改掌握度（规则 11）。它写的只有：会话与限流（新迁移）、同意记录、选科（经函数）、时长设置（经函数）。

### 2. 认证与会话（家庭登录）
**家长登录（短信验证码）**：
1. `POST /bff/v0/auth/otp/request {phone}`：无论号码是否注册，都返回同一个 202 和同一个响应体（第一轮 C3、第二轮 N1）。
2. `POST /bff/v0/auth/otp/verify {phone, code}`：在**同一个数据库函数**里完成“核对验证码 → 查账号（没有就注册）→ 建会话”。
   - BFF 没有“为任意账号建会话”的函数可调，会话只能从验证码或家长签发产生；
   - 验证码 6 位数字，只存 HMAC；有效期、错误次数上限从配置读（默认 5 分钟、错 5 次作废，待人确认 #4）；
   - 同号已有多个家长账号（历史数据）时默认拒绝登录并提示联系客服（待人确认 #6）；新注册在入口层保证一号一个家长账号（第一轮 C2）。
3. 短信发送经 `SmsSender` 接口。4-H **只实现捕获桩**：验证码写进只有测试能读的收件箱，不给任何真实号码发短信（待人确认 #3）。

**学生进入壳（PLT-01 之前的过渡入口）**：
- 规格书 V1.2 规定学生用“家庭码 + 4 位 PIN”登录（连错 5 次锁定 15 分钟），或由家长扫码授权网页端；这些随 PLT-01 修订实现，本 spec 不做。
- PLT-01 到位前，4-H 用“家长在本设备打开学生模式”：已登录的家长在孩子档案上操作，BFF 调数据库函数，要求当前账号 `is_linked_guardian(student)`，为**学生账号**签发新会话并替换本设备的家长会话。它相当于 V1.2“家长授权登录”的同设备形式。
- 学生会话下 `app.account_id` 是学生账号，`meter_reserve` 与 `meter_practice` 的“只有学生本人能消耗”自然成立。回家长模式要重新短信验证，孩子拿不到家长会话。
- 会话表按 PLT-01 可以在其上加“PIN 凭据”“设备”两类来源设计（会话记录 `origin`：`otp` / `guardian_handoff`，保留扩展），不另建第二套会话（待人确认 #1）。

**会话令牌**：
- 256 位随机数，库里只存 sha256；令牌不进 URL、日志、Referer。
- cookie `__Host-sb_session`：`HttpOnly; Secure; SameSite=Lax; Path=/`，不设 Domain。本地 http 开发用不带前缀的名字，只在开发构建里允许。
- 登录成功、切换到学生模式都发新令牌，旧令牌立即作废（防会话固定）。
- 失效：绝对 7 天、空闲 24 小时（配置，待人确认 #4）；登出废本会话，“退出所有设备”废本账号全部会话；每次解析都核对账号 `status = 'active'`；解除绑定不废会话，但 RLS 下读不到孩子，返回 404。
- 允许多设备并发会话。设备上限（学生 3 台、监护人 5 台）与单一活跃 AI 会话随 PLT-01（规格书 V1.2 §12.2、任务包 U7）。

**会话存放（默认：Postgres，新迁移 `platform__auth_session`，编号合并时取下一个空号）**：
- 会话、验证码挑战、限流计数三张表，都 `FORCE ROW LEVEL SECURITY`、不给任何应用角色策略，只能经 `SECURITY DEFINER` 函数访问（属主 `starbuddy_authz`，与 `account_contact` 同一做法）；
- 函数：`auth_otp_issue`、`auth_otp_login`、`auth_session_resolve`、`auth_session_revoke`、`auth_session_revoke_all`、`auth_issue_student_session`、`auth_rate_hit`；`auth_session_resolve` 只返回账号 ID、账号类型、会话类型；
- 理由：ADR 10 倾向阶段 0–1 优先用 Postgres 自带能力；BFF 多实例要共享会话与限流；不引入新组件。另一个选择见待人确认 #2。

### 3. BFF 鉴权中间件与路由表
- 新建 `services/bff/src/http/`：一个 `node:http` 入口、一张**路由表**、一条中间件链；星图模块改挂在这张表上。
- 每条路由声明 `method`、`path` 模板、`auth`（`public` / `session` / `guardian` / `student`）、`rateLimit`、`csrf`（改变状态的一律开）、`handler`。
- 中间件链固定顺序：请求 ID → 限流 → 解析会话 → 按 `auth` 拦截 → CSRF → `withAccount(accountId)` → handler → 统一错误与安全头。
- **分发只查路由表**：表外路径一律 404。路由表导出给测试，第二轮第 7.1 节的“路由 × 账号”矩阵从它生成。
- **身份只来自会话**：handler 的 `accountId` 只来自 `auth_session_resolve`；请求参数、请求体、请求头里的 `account_id` 一律不读（第二轮 N3）。
- 路径里的 `student_id` 只作查询条件，授权交给 RLS；未授权与不存在返回同一个 404，响应体与响应头逐字节相同。
- `withAccount` 保持事务级 `set_config(..., true)`，所有查询都在事务里（第二轮 N4）。

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
| POST | `/students/{id}/student-session` | guardian，限流 | 在本设备打开学生模式（过渡入口） |
| GET | `/students/{id}/enrollments` | session | 学科目录、选科状态、核心与选修槽口径 |
| POST | `/students/{id}/enrollments/{subject}` | guardian | `enroll_subject`（默认只有家长，待人确认 #7） |
| POST | `/students/{id}/enrollments/{subject}/pause` | guardian | `pause_subject` |
| GET | `/students/{id}/today` | session | 今日计划（第 6 节） |
| GET | `/students/{id}/star-map` | session | 星图（第 7 节） |
| GET | `/students/{id}/balance` | session | 额度与时长状态（第 9 节） |
| GET | `/students/{id}/time-guard` | session | 单日上限与宵禁设置（学生只读） |
| PUT | `/students/{id}/time-guard` | guardian，限流 | `set_time_guard` |

- 邀请第二位家长、解除绑定、订阅开通不在 4-H（见“不做”）。
- 接口定义放新建的 `services/bff/openapi/shell.v0.yaml`，与 `star-map.v0.yaml` 同属 BFF 内部接口，不进 `packages/contracts`（沿用 3-H）。
- 路由表里**没有**调用 `insight.dispute`、`insight.reconcile`、`insight.export`、`insight.erase` 的路由；这些随冲刺 5。

### 4. 删除开发身份桩
- 删除 `apps/web/src/lib/dev-auth.ts`、`/dev/login`、`/dev/session`、`apps/web/src/dev/accounts.ts`；BFF 的 `devAccount()`、`STARBUDDY_DEV_AUTH` 与 `x-dev-account-id`。**是删除，不是关掉**（第二轮 N3）。
- 符号样张页 `/dev/star-glyphs` 只在开发构建里存在，不进 staging 与生产构建（开关改为构建期常量）。
- 开发与测试用短信捕获桩走真实流程登录，端到端测试从捕获收件箱读验证码。
- `bffBaseUrl` 的 loopback 限制改为配置白名单：开发只允许 loopback，staging 与 pilot 只允许内网地址。

### 5. 选科（核心 + 选修槽）
- 只经 `enroll_subject` / `pause_subject`，BFF 不直接写 `subject_enrollment`。BFF 与前端**不自己算**上限，也不自己判断能不能选。
- 学科目录取 `platform.subject_catalog`：只列适用于该生年级的学科；`planned` 的学科不显示为可选（英语搁置期间不出现入口）；`beta` 在非试点订阅下显示“暂未开放”。URL 与界面用目录的学科码（`math`、`politics`、`chinese`），调函数时换成对应的产品命名空间。
- 口径说明只做展示：读当前主订阅配置的 `core_subjects` 与 `elective_slots`（`plan_feature`），显示“核心科目任选 1 科”（`pick_one`，英语搁置期间的基本档）或“已上线核心科目全部可选”（`all_launched`），以及选修槽数；换科规则显示为“每个周期可换 1 科，换上的科目下个周期生效”。
- 函数 outcome 一一映射成界面状态：

| outcome | 界面 |
|---|---|
| `enrolled` | 已开通（显示占用的是核心还是选修槽） |
| `already_active` | 已开通 |
| `limit_reached` | 核心或选修槽已满，显示上限数 |
| `change_next_period` | 本周期已换过，下个周期可开 |
| `no_subscription` | 学生端“需要家长开通”；家长端“尚未开通” |
| `not_launched` | 暂未开放 |
| `paused` / `not_active` / `change_limit_reached` | 已暂停 / 本来没开 / 本周期已换过 1 科 |

- 默认只有家长能选科、换科（规格书 V1.2 §2：学生“提议，家长确认”）；学生端只读。主 / 协同监护人的区分与学生“提议”流程随 PLT-01。数据库目前允许学生本人调函数，BFF 路由收紧为 `guardian`，数据库不改（待人确认 #7）。
- 4-G 发布之前真实环境里没有 `published` 课程版本，一科也选不了；测试用合成的已发布版本。

### 6. 今日计划
**数据链路**：BFF `GET /students/{id}/today` → 学习服务 `POST /internal/v1/plan`（新增，契约变更，必停）→ 各科适配器 `/plan`（插件 API v1.0 候选版）。
- 学习服务组装 `PlanRequest`：必填的 `mastery` 快照（读平台掌握状态，规则 2），`next_retest_at` 按 4-C（KGE-04）的复测调度填；可选的 `learner_context`、`node_path`、`exam_config` 的组装见“与同期 spec 的协调”第 2 条。
- 学习服务给 BFF 的响应只含：学科、任务类型、目标知识节点 ID（由学习服务经 `kge.skill_link` 从 `skill_nodes` 换算；BFF 读不到 `skill_link`）、优先级、预计分钟、`rationale_code`。**不含** `p_mastery`、`confidence`、`evidence_ids`、`learner_context` 的任何条目。
- 4-H 的今日计划**按科并列**，不是跨科编排（跨科编排是阶段 1 的今日计划跨科编排，由点点执行）：每个在读学科一节，按 `priority` 排序。
- 某科适配器 501 或超时：这一科显示“今天暂无安排”，不影响其他科。
- `plan` 非消耗类，不计量；计划不落库，每次现算；响应 `no-store`。
- 到期复测只以 `kind = retest` 的任务出现。BFF 不再单独读 `mastery_estimate.next_retest_at` 列一张“到期复测”表（那是关于学生的推断，字段字典要求经判断展示）。

**推荐理由（规则 11）**：
- `rationale_code` 在一张固定映射表里分两类，表随代码评审：
  - **内容类**：只描述题目或知识点本身、不描述学生（例如 `high_exam_weight` → “考试常考”）。显示固定短句。
  - **关于学生类**：理由取决于该生的数据（例如 `retest_due`、前置薄弱、掌握不足）。只有当该任务的目标节点上有一条该受众可见、状态为“成立”或“假设”、模板在对外白名单里（第 8 节）的判断时，才显示它的 `RenderedStatement.text`，并在响应里带 `claim_id`；否则**不显示理由**，任务照常列出。
  - 映射表里没有的编码按“关于学生类”处理（宁可不显示）。
- 任务类型用固定中文（诊断、练习、复习、微课、复测、模考）；目标节点显示 `kge.knowledge_node.name`；拿不到节点名时显示“数学 · 本周任务”一类的中性标题，不显示编码。

### 7. 星图
**7.1 挂载与会话（H2，无前提）**
- 页面地址改为 `/s/{studentId}/star-map`（学生）与 `/family/{studentId}/star-map`（家长）；旧地址 `/star-map/{id}` 删除。
- 身份来源从开发桩换成真实会话，挂到路由表上。3-H 的授权测试、端到端测试改用真实会话跑，断言不变。

**7.2 数据来源按 ADR 11（H5，有前提）**
- **前提**：ADR 11 签字；数学首个地图版本经 `kge.publish_map_version` 发布；4-C（KGE-04）掌握 v0 按知识节点写入；SIE-02b 判断账本接入掌握状态、首批模板登记（ADR 13 待人确认 #4）。
- **前提不满足时**：壳内挂已合并的 3-H 星图，数据源、三态映射与“原型 · 合成数据”水印都不动（ADR 11 过渡条款、规则 11 过渡条款）；不在旧星图上新增任何关于学生的文字。
- **星的对象**：当前已发布地图版本中 `status = 'active'`、`level = 'node'` 的 `kge.knowledge_node`；领域与主题作为分组，细目不成星（口径见待人确认 #9）。
- **星的状态**：视觉三态（未亮、半亮、亮）与“保持”外圈沿用 3-H 视觉，状态取 KGE-04 给出的节点级掌握状态；BFF 不聚合认知层次、不自己算门槛。
- **判断先于展示**：一颗星只有同时满足下列条件才按掌握状态画，否则画成**中性底星**（不亮、不暗、不带任何文字，与“没有证据”同样外观）：
  1. 该节点上有一条该受众可见（学生看 student、家长看 guardian，RLS `claim_front_read`）、状态为“成立”或“假设”的判断；
  2. 判断的模板在对外白名单里（第 8 节）；
  3. 判断的 `basis_id` 指向该节点当前的掌握记录（掌握已更新、判断还没跟上时不画旧状态）。
- **待验证虚线**：判断状态为 `to_verify` 时，签字前按中性底星处理；ADR 13 待人确认 #2 签字确认“虚线不是陈述”后，按 `RenderedStatement.display = dashed` 画虚线，旁边只显示下一次验证任务，不显示结论文字。虚线由配置开关控制，默认关。
- **星数说明**（例如“已点亮 N 颗、正在攻克 M 颗”）本身是陈述：只显示“知识掌握”维度的一条判断的 `RenderedStatement.text`；没有这条判断就不显示星数说明。“知识迁移”同理（“会做原题”与“换个样子也会”的对比），只在有判断时出现在数学星空的摘要位。
- **能力视图侧栏**：侧栏位置与组件做出来；内容（能力坐标的掌握分布，没有“亮”）只在字段字典 `inner.g4.skill_view` 的可见范围含当前受众时显示。字典 v0.9 目前只给 agent，ADR 11 待人确认 #1 默认学生端与家长端都显示，二者不一致，签字前侧栏为空并显示“能力视图即将开放”（待人确认 #8）。数据由学习服务提供（BFF 读不到 `kge.skill_link`）。
- **范围**：只画已发布地图的学科；地图未发布的在读学科显示“地图建设中”，不显示旧能力坐标星图（ADR 11 待人确认 #4 的默认）；英语不画；非学科维度不画成星。
- 接口：BFF 新增 `GET /students/{id}/star-map` 的 v1 响应形状（新建 `services/bff/openapi/star-map.v1.yaml`），每颗非中性的星带 `claim_id`；v0 形状保留到切换完成。
- 水印：切换到 v1 且 4-C 经教研确认后去掉“原型 · 合成数据”水印。

### 8. 判断的读取与对外白名单（规则 11、12）
- **判断 ID 与状态**：BFF 在 `withAccount` 事务里按 RLS 读 `insight.claim`（`claim_front_read`），只取 `claim_id`、目标、模板键与版本、状态、`basis_id`、到期时间。
- **文字**：面向学生或家长的句子只取洞察服务返回的 `RenderedStatement.text`（模板渲染，带 `claim_id` 与 `confidence_band`）。默认用 `insight.explain` 逐条取（`caller = bff`，带 `actor`；家长带 `link_id`），BFF 只保留 `statement`，丢弃证据链（证据链是冲刺 5“为什么”的内容）；单页最多取 10 条，超时或洞察服务不可用时该处不显示文字。批量读取工具见待人确认 #5。
- **家长端**：`confidence_band` 必须与陈述一起显示（SIE 第八章：家长看不到无置信度的推断）。
- **对外模板白名单**：BFF 配置一张白名单，初值只含数学“知识掌握”“知识迁移”两维的模板（由 SIE-02 提交、人审核登记，ADR 13 待人确认 #4）；白名单外的判断即使 RLS 可见也不展示。中圈、外圈的维度与指标在效度检验通过前不在壳内出现（字段字典 `dimensions` 的 `launch: silent`）。白名单变更属于“画像可见范围”，必停。
- **统一标记**：页面上每一句关于学生的文字都渲染为带 `data-claim-id` 的元素；壳的其余文案（导航、按钮、系统提示）收在一个文案文件里，不含关于学生的描述。
- 4-H 不调 `insight.record`（SIE-01 建档另做）、`insight.dispute`、`insight.reconcile`、`insight.export`、`insight.erase`。

### 9. 额度与时长可见（计量 v2）
**读哪里**：
- 额度：`platform.entitlement_balance`，`withAccount` 下 RLS 生效：学生看自己，家长看已绑定孩子；只显示资源类型、单位、剩余量和周期结束日期；多个来源的同一资源合并成一行，分来源明细只在家长端显示。
- 生命周期状态：`meter_student_state`（已授予 BFF）。
- 时长状态：新增只读函数 `platform.meter_guard_status(student)`（新迁移，计量域，必停），`can_read_student` 才能调，内部复用 `meter_state_of`、`meter_daily_cap`、`meter_plan_daily_cap`、`meter_minutes_today`、`meter_in_curfew`、`meter_minutes_until_curfew` 与自练计数，返回：状态、试用结束时间、档位上限与生效上限、今日已用与剩余分钟、宵禁起止、是否在宵禁内、距宵禁分钟数、留存态今日剩余自练次数。**BFF 不在代码里重算这些规则**，避免第二套口径（另一个选择见待人确认 #10）。
- 只读：不预留、不结算；前端不传、BFF 不收任何数量参数。

**显示**（系统口吻，文案在同一个文案文件里）：

| 状态 | 学生端 | 家长端 |
|---|---|---|
| `trialing` | “试用还剩 N 天”（按上海时区的自然日，由试用订阅的 `period_end` 算） | 同左，另显示试用结束后进入留存态的说明 |
| `retained` | “可以查看星图和历史记录；今天还能做 K 次题库自练”；AI 相关入口显示为不可用 | 同左，另显示开通入口占位（订阅开通不在 4-H） |
| `active` / `grace` | 额度与今日剩余分钟 | 额度分来源明细、今日剩余分钟、当前上限与宵禁 |
| `none` | “需要家长开通” | “尚未开通” |
| 单日上限已到 | “今天的陪练到这里，可以继续做题库自练” | 同左，附“调整上限”入口 |
| 宵禁内 | “现在是休息时间（HH:MM–HH:MM），明天 HH:MM 后可以继续” | 同左，附“调整宵禁”入口 |
| 距宵禁 ≤ 配置分钟数 | “还有 M 分钟到休息时间” | 不显示 |

- 学生端不出现购买、升级或营销类文字（规格书 V1.2 U9“学生端无营销推送”）。

**拒绝提示组件**：
- 为计量拒绝原因做一个共用组件，映射 `retained_no_ai`、`daily_cap_reached`（带今日剩余）、`curfew`、`curfew_soon`（带可用分钟）、`no_entitlement`、`insufficient`；自练的 `daily_limit_reached` 同样映射。
- 4-H 没有消耗类入口，组件由冲刺 5 的诊断、批改、讲评入口使用；4-H 用 `meter.ts` 的类型做穷举检查，新增拒绝原因没有文案时编译失败。

**家长的时长设置页**（`/family/{studentId}/time`）：
- `PUT /students/{id}/time-guard` 调 `set_time_guard`：单日上限只能在 0 到档位上限之间调低；宵禁只能收紧（必须覆盖默认的 22:00–6:00）。界面把档位上限与默认时段显示为边界，outcome `cap_above_plan`、`curfew_must_cover_default` 各有提示。
- 学生端只读显示当前上限与宵禁，不显示是哪位家长改的（0015 已不给 BFF 读 `updated_by`）。
- 主 / 协同监护人都可设置（规格书 V1.2 §2）；多位家长设置的覆盖规则沿用计量 v2 lessons 待人确认 #8，不在本 spec 另定。

### 10. 休息提醒
- **连续使用提醒**：学生会话连续使用 40 分钟，壳顶部出现提醒条，建议休息 10 分钟（数值从配置读，待人确认 #11）。只提醒，不锁定。
- **宵禁**：不再单独做“夜间提醒”。宵禁时段取该生的实际设置（第 9 节），由服务端在 AI 类预留时强制（`meter_reserve`）；壳在宵禁内显示休息时间条，查看星图、历史与题库自练不受影响（计量 v2 待人确认 #6 的默认）。
- 计时以 BFF 为准：学生会话记录开始时间与最近一次“确认休息”的时间，前端只负责显示；不写 localStorage。连续使用计时只用于提醒，不是计量的“有效时长”。
- 提醒文案用**系统口吻**，不以点点或任何老师的身份说话，不配角色形象，按禁语 A–G 类自查（不催促、不评价、不比较、不以连续打卡制造焦虑）。以角色口吻表达时长节律属于人设话术，必停，不在 4-H。

### 11. 点点入口的边界
- 学生壳每页有一个固定位置的“点点”入口按钮，点开是占位面板，系统口吻写“点点正在准备中”。
- 4-H **不做**：点点对话、任何台词、模型调用、点点形象、AI 身份披露流程、陪伴开关。
- 家长端**不出现**点点入口，也不出现任何角色形象。
- 以后接入点点对话时，人设 Prompt 是必停项，要跑 `make eval-persona` 并附前后对比（规则 7）。

### 12. 信任界面的预留入口（冲刺 5 实现）
- **学生端**：星的详情位预留“为什么”（`insight.explain` 的证据链）与“我不同意”（`insight.dispute` 的三条路）两个入口；今日计划理由旁预留“为什么”。
- **家长端**：孩子页预留对账卡（`insight.reconcile`，v0.9 只给编排层，对家长展示由人在 SIE-03 确认）、数据查看、导出、删除（`insight.export` / `insight.erase`，只给家长；规格书 V1.2 §2 只给主监护人）。
- 预留的做法：组件与位置做出来，由一个默认关闭的功能开关控制；开关关闭时不渲染、不发任何请求，路由表里也没有对应路由。

### 13. 家长端（4-H 的最小范围）
- `/family`：已绑定孩子列表、建档、同意、打开学生模式、孩子的星图、选科、额度与时长、时长设置。
- **孩子怎么区分**：档案没有姓名或昵称，默认显示“孩子 1 · 八年级”这样的序号加年级（待人确认 #12）。
- 家长端任何“孩子最近……”的文字都只来自第 8 节的判断陈述（guardian 可见范围），并带置信度说明；没有判断就不显示。4-H 没有周报（冲刺 5）。
- 不显示对话内容、同伴比较、排名、无置信度的推断；外圈家庭数据学生端不显示。
- 监护操作留痕：建档、同意、打开学生模式、选科、暂停、时长设置，BFF 各写一条访问日志（第 17 节格式）。后台审计表随 PLT-01。

### 14. 建档入口（SIE-01）
- 同意完成后、打开学生模式前，家长流程里留一个“完善学习档案”入口；学生首次进入壳时留同样的入口。两者都可跳过、可返回。
- 表单本身属 SIE-01（v2.0 冲刺 1 L：学生、家长分别填写，两方互不可见）。SIE-01 已交付时挂它的路由；未交付时入口隐藏。4-H 不收集任何建档字段。

### 15. 隐私与未成年人
- **同意是打开学生模式的前提**：必须有 `privacy_policy` 的 `granted` 记录；不满 14 周岁（按 `birth_year_month`，为空时按不满 14 处理）还必须有 `guardian_consent_under14`；同意记录带 `document_version`。
- 同意文本与版本由合规轨提供，现在没有。开发与测试用合成版本号 `synthetic-0`；配置禁止在 staging 以外使用 `synthetic-` 前缀，pilot 在合规交付前不能打开学生模式（待人确认 #13）。
- 数据最小化：建档只收年级、地区、教材版本、出生年月，与 `0002` 一致；手机号只用于登录。
- 缓存：所有含学生数据的页面与接口 `no-store`；不注册 Service Worker；不写 localStorage、IndexedDB（沿用 3-H）。
- 不加载境外资源；Next 遥测保持关闭。
- 壳内不出现规则 12 列出的推断；对外模板白名单与“无判断不出文字”是兜底，静态文案另做词表检查（第 18 节）。

### 16. 安全响应头、CSP 与 CSRF
- Next 的请求中间件为每个请求生成 nonce，CSP 默认：
  `default-src 'self'; script-src 'self' 'nonce-<n>' 'strict-dynamic'; style-src 'self' 'nonce-<n>'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'`。
- 根布局的内联 `<style>` 带 nonce，或改成静态 CSS 文件，实现时二选一并记入 lessons；不允许 `unsafe-inline`。
- HSTS 只在 staging 与 pilot 发送（`max-age` ≥ 180 天）。保留 3-H 的 `nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`。
- BFF 响应一律 `no-store`、`nosniff`；错误体只有错误码，不回显堆栈、SQL、内部地址（第二轮 P3）。
- **CSRF**：改变状态的请求同时满足 `SameSite=Lax` cookie、`Origin` 等于壳的来源、带与会话绑定的 CSRF 令牌（双重提交）；BFF 不设 CORS 放行头。

### 17. 限流与日志
**限流**：存在第 2 节的限流表里，多实例共享；阈值全部来自一份配置文件，测试读同一份。

| 接口 | 维度 | 默认 |
|---|---|---|
| 发验证码 | 每号每分钟 / 每号每天 / 每 IP 每小时 | 1 / 10 / 30 |
| 验证验证码 | 每号每 15 分钟 / 每 IP 每小时 | 10 / 60 |
| 建档 | 每家长账号每天 | 5 |
| 打开学生模式 | 每家长账号每小时 | 20 |
| 时长设置 | 每家长账号每小时 | 20 |

- 超过阈值一律返回同一个 429 响应体，不区分原因（第二轮 O1）。
- BFF 调洞察服务按会话限频（`insight-service.v0.9.yaml` 描述里“对 BFF 的调用由 BFF 按会话限频”）：每会话每分钟的 `insight.explain` 次数进同一份配置。

**日志**：
- BFF 访问日志只记请求 ID、路由模板、状态码、耗时、账号类型、`student_id` 的 HMAC。
- HMAC 密钥从环境变量读，不进仓库，长度不足拒绝启动；开发与测试启动时随机生成（3-H 待人确认 #15）。密钥存放与轮换随 ADR 10。
- 不记：会话令牌、验证码、手机号原文、请求体、星图与计划内容、`claim_id`、陈述文字、数据库报错消息。

### 18. 四科路由挂载
- 原则“前端合并壳，不合并页”（完整方案 v1.1 5.7）。iframe、跳转原产品、运行时微前端都不用：原产品有自己的 Supabase 登录（两套身份），阿鸦学习状态在 localStorage（第二真相源，规则 2），原产品部署在境外平台（ADR 09、10）。
- **壳内路由模块**：每科一个目录 `apps/web/src/subjects/<subject>/`，登记在一张清单里：学科码、状态（`live` / `coming` / `hidden`）、入口组件；中文名取 `subject_catalog`。
- 路由统一为 `/s/{studentId}/learn/{subject}`，共用壳布局：顶栏“今日”“星图”“科目”，额度与时长状态、点点入口、休息提醒；任何科目页一步回到星图。
- 科目页只调 BFF，不调原产品，不读适配器内容包（规则 1）；`frame-src 'none'`。
- **状态**：
  - 数学：`live`。4-H 只显示今日任务与该科星空入口；作答、诊断、批改等消耗类入口在冲刺 5 联调；
  - 道法、语文：适配器与已发布内容版本都到位时 `live`，否则 `coming`（占位页“即将开放”，可进入并返回）；
  - 英语：`hidden`（对照文档决定 #1），不显示入口。
- **文案检查**：壳内全部界面文案收在一个文件里，静态检查不含比较词表（“别人”“同学都”“排名”“最差”等）与禁用推断词表（`docs/design/sie/sie_lexicon.py`）的命中。

## 范围

### 做
建议拆成 6 个 PR，每个只做一件事：
1. **H1 认证与会话**：迁移 `platform__auth_session`（含 down）与 RLS 测试；BFF `src/http/`（路由表、中间件链、会话解析、CSRF、限流、统一错误）；`SmsSender` 接口与捕获桩；登录、登出、`/me`、建档、同意、打开学生模式；日志改带密钥 HMAC。
2. **H2 删除开发身份桩与安全头**：删除第 4 节列出的全部文件与代码；3-H 星图挂到路由表、换成真实会话（第 7.1 节）；CSP、HSTS、构建产物扫描。
3. **H3 计量只读函数**：迁移 `platform__meter_guard_status`（含 down）与测试、变异；权限快照与攻击面清单同步。
4. **H4 壳页面与读路由**：登录页、家长页、今日计划、选科、科目路由（清单 + 三个科目页）、额度与时长、时长设置页、休息提醒、点点入口占位、信任界面预留位（开关关闭）；BFF 的选科、今日计划、额度、时长路由与洞察服务客户端；学习服务 `POST /internal/v1/plan` 与契约；合成种子（一户可登录的合成家庭、合成的已发布课程版本、合成订阅：试用中、留存态、试点各一名学生）。
5. **H5 星图 v1（知识节点）**：第 7.2 节，前提满足后开工；不满足时本 PR 顺延到冲刺 5，4-H 验收用 7.1 的星图。
6. **H6 第二轮第 7 节回归**：路由授权矩阵（从路由表生成）、身份注入、连接池串号、会话、限流、安全头与缓存、计量入口；每类至少一条变异，接进 `bff-mutation`；`docs/security/attack-surface.md` 的 M1 补真实会话引用，N–Q 类登记断言引用。
7. `specs/unified-shell-sprint4/lessons.md`：实现记录与新增待确认项。

### 不做
- 真实短信供应商接入（必停，另起 PR）。
- 学生家庭码 + PIN、学生手机号、设备表与设备上限、单一活跃 AI 会话、主 / 协同监护人角色、后台审计表（PLT-01 修订）。
- 邀请第二位家长、解除绑定、家庭切换的界面（家长端 v1，阶段 1）。
- 订阅开通、试用领取、支付、升级、试点虚拟额度的开通入口（权益逻辑，必停；待人确认 #14）。
- 任何消耗类请求的入口：诊断、批改、错题分析、上传、讲评课、语音、题库自练、AI 互动计时（冲刺 5 联调）。
- “为什么”“我不同意”、对账卡、数据查看 / 导出 / 删除的实现，跨科周报（冲刺 5，SIE-03）。
- SIE-01 建档表单；任何判断的写入、模板登记、白名单以外的维度。
- 跨科编排的今日计划、点点每日开场话术（阶段 1）。
- 点点对话、点点形象、陪伴开关、任何人设文本。
- 原产品页面的 iframe 嵌入或跳转；知行、文砺、英语的内容。
- 改星亮门槛、掌握计算、复测调度（4-C / KGE-04）。
- 强制锁定学生模式、未成年人模式的其余规则（合规轨定规则后另起）。
- Service Worker、离线缓存、PWA 安装清单；手机 APP（规格书 V1.2 U10，另起 spec）。

## 验收标准（可测试）
- [ ] **开发计划验收**（Playwright 端到端，合成数据，全程真实会话）：合成家长用 `+999` 号码请求验证码，从捕获收件箱读码登录；为合成学生打开学生模式；学生从“今日”进入“科目”，依次进入数学、道法、语文三个科目路由，每次从科目页一步回到星图；英语入口不存在；全程没有请求离开壳的来源。
- [ ] **开发身份桩已删除**：源码与构建产物（`apps/web/.next/`、BFF）里找不到 `x-dev-account-id`、`sb_dev_account`、`STARBUDDY_DEV_AUTH`、`/dev/login`、`/dev/session`；生产构建里没有 `/dev/star-glyphs`。
- [ ] **登录**：发验证码对已注册与未注册号码返回逐字节相同的响应；验证码错误、过期、用过、换号使用都失败且失败响应相同；错误次数到上限后作废；同一号码第二次登录不新建家长账号（断言账号数不变）。
- [ ] **会话**（第二轮 7.4）：cookie 属性为 `HttpOnly`、`Secure`（非本地）、`SameSite=Lax`、`Path=/`、`__Host-` 前缀；登录与切换学生模式后令牌变化、旧令牌 401；登出、退出所有设备、账号禁用后旧会话下一次请求 401；解除绑定后家长旧会话读孩子的任何路由都是 404；绝对与空闲过期后 401（可注入时钟）；令牌不出现在日志、URL、Referer。
- [ ] **学生模式**：未绑定、已解除绑定的家长为该学生开学生模式都失败，响应与“学生不存在”相同；学生会话访问兄弟姐妹或其他学生的任何路由都是 404；学生会话调 guardian 路由全表一致地拒绝；缺少必需的同意记录时不能打开；`birth_year_month` 为空按不满 14 处理。
- [ ] **路由授权矩阵**（第二轮 7.1）：从路由表生成“路由 × 账号（未登录、家长本人、学生本人、兄妹、另一户家长、禁用账号、解除绑定的家长、同号第二账号）”矩阵；未登录一律 401；越权与不存在同样 404，响应体与响应头逐字节相同；服务器能响应但不在路由表里的路径数为 0。
- [ ] **身份注入**（第二轮 7.2）：每条路由在查询参数、请求体、请求头（含 `x-dev-account-id`、`x-account-id`）里注入别人的 `account_id` / `student_id`，结果与不注入时相同。
- [ ] **连接池串号**（第二轮 7.3）：两个账号 × 至少 200 次交替并发请求没有一次串号；故意让事务出错后，同一连接上 `current_setting('app.account_id', true)` 为空。
- [ ] **CSRF**：改变状态的路由缺 CSRF 令牌、令牌不属于本会话、`Origin` 不符时都拒绝；响应没有 `Access-Control-Allow-Origin`。
- [ ] **限流**（第二轮 7.5）：发验证码、验证验证码、建档、打开学生模式、时长设置超过阈值后返回逐字节相同的 429；阈值与测试读同一份配置。
- [ ] **安全头与缓存**（第二轮 7.6）：爬遍壳内全部路由，每个响应都有带 nonce 的 CSP（不含 `unsafe-inline`、`unsafe-eval`）、`frame-ancestors 'none'`、`no-store`；每个内联脚本与样式都带本次响应的 nonce；没有外部资源。
- [ ] **选科**：第 5 节表里每个 outcome 各有集成测试，界面状态一一对应；`pick_one` 配置下基本档开第二个核心科目得到 `limit_reached`；`planned` 学科（英语）不出现在可选列表；学生会话调选科、暂停路由被拒；BFF 源码里没有对 `subject_enrollment` 的 INSERT 或 UPDATE。
- [ ] **今日计划**：任务按科分组、按 `priority` 排序；某科 501 或超时显示“今天暂无安排”，其他科正常；响应没有 `p_mastery`、`confidence`、`evidence_ids`、`learner_context`；BFF 源码不读 `mastery_estimate.next_retest_at`。
- [ ] **推荐理由引用判断**：夹具覆盖三种情形——内容类编码显示固定短句且不带 `claim_id`；关于学生类编码在有可见、成立或假设、白名单内判断时显示该判断的陈述并带 `claim_id`；同一编码没有判断、判断已推翻、已过期、模板不在白名单或洞察服务超时时都不显示理由。映射表外的编码不显示理由。
- [ ] **星图 7.1**：3-H 的授权与端到端测试改用真实会话后全部通过，断言不变；旧地址 `/star-map/{id}` 404。
- [ ] **星图 7.2**（H5 合并时）：夹具覆盖——掌握为亮且有匹配判断 → 画亮并带 `claim_id`；掌握为亮但没有判断 → 中性底星；判断 `basis_id` 不是当前掌握记录 → 中性底星；判断为 `to_verify` 且虚线开关关 → 中性底星，开关开 → 虚线且不带结论文字；家长会话只按 guardian 可见范围取判断；地图未发布的在读学科显示“地图建设中”；英语不出现；能力视图侧栏在字典可见范围不含当前受众时没有数据请求；星数说明与迁移摘要只在有判断时出现。
- [ ] **陈述—判断一致性**：爬遍学生端与家长端全部页面（合成数据），每个带学生描述的元素都有 `data-claim-id`，其值是该受众 RLS 下可见、状态为成立或假设（虚线开关开时另含待验证）的判断；文案文件里没有关于学生的描述（词表检查）；页面里不出现白名单外维度的名称（学习策略、自主学习、表达应用、学习持续性）。
- [ ] **家长端置信度**：家长端每条判断陈述旁都显示 `confidence_band` 的说明；家长端没有对话内容、排名、同伴比较。
- [ ] **额度与时长可见**：用 `meter_now` 可注入时钟与合成订阅——试用第 1 天与第 14 天显示的剩余天数正确；试用到期后显示留存态，第 6 次自练后剩余次数为 0；单日上限已到时显示上限提示；23:30 与 05:59（上海时区）显示宵禁，06:00 不显示；距宵禁 M 分钟时显示 `curfew_soon` 提示；学生只看到自己的额度，家长只看到已绑定孩子的，解除绑定后 404；额度数值等于 `entitlement_balance.remaining` 的合计；时长数值等于 `meter_guard_status` 的返回值（BFF 源码里没有单日上限、宵禁、已用分钟的计算）。
- [ ] **时长设置**：家长把上限调到档位上限以上得到 `cap_above_plan` 提示，数据库值不变；把宵禁改成不覆盖 22:00–6:00 得到 `curfew_must_cover_default`；调低、收紧后学生端下一次请求显示新值；学生会话调 `PUT /time-guard` 被拒；学生端响应里没有设置人。
- [ ] **拒绝提示组件**：`meter.ts` 的 `ReserveResult` / `MeteredCallResult` 每个拒绝 outcome 与自练的 `daily_limit_reached` 都有文案（类型穷举，漏写时 `make check` 失败）；学生端文案不含购买、升级、营销类词。
- [ ] **计量入口**（第二轮 7.7）：路由表里没有任何路由调用 `reserve` / `settle` / `meteredCall` / `meter_practice`；以后新增消耗类路由时，这条测试改为“前端传入的数量、资源类型、`usage_id` 被忽略”。
- [ ] **计量只读函数**（H3）：`meter_guard_status` 对无权学生报 `insufficient_privilege`；返回值与分别调用内部函数的结果一致（含跨午夜宵禁、家长调低后的上限、已过期未回收的预留不计入）；只授予 `starbuddy_bff`；迁移 up → down → up 通过；变异（去掉授权检查、改用档位上限代替生效上限）被发现。
- [ ] **信任界面预留**：功能开关关闭时，页面没有“为什么”“我不同意”、对账卡、导出删除的可见元素，网络请求里没有对应调用，路由表没有调用 `insight.dispute` / `insight.reconcile` / `insight.export` / `insight.erase` 的路由。
- [ ] **休息提醒**：可注入时钟下学生会话连续 40 分钟出现提醒条；家长端不出现；阈值来自配置。
- [ ] **点点入口与家长端**：学生壳每页都有点点入口；家长端页面没有点点入口、没有任何角色图片（静态检查家长端组件不引用角色资产目录）。
- [ ] **日志**：`student_id` 以带密钥 HMAC 记录，换密钥后值变化；日志里找不到令牌、验证码、手机号、UUID 原文、`claim_id`、陈述文字。
- [ ] **变异**（第二轮 7.8）：至少包括“身份改从请求参数读”“`set_config` 第三个参数改为 `false`”“登出不废会话”“限流按原因返回不同错误”“新路由绕过鉴权中间件”“验证码不检查过期”“无判断也显示推荐理由”“星图不核对 `basis_id`”“白名单检查去掉”，全部被指定的断言发现。
- [ ] **RLS 测试**（规则 5）：新表对 `starbuddy_bff`、`starbuddy_learning` 直接 SELECT / INSERT / UPDATE / DELETE 全部被拒；函数只授予 `starbuddy_bff`；迁移可回滚；权限快照（`db/tests/expected/`）与 `docs/security/pentest/scope.md` 函数表同步。
- [ ] **契约**：`learning-internal.v1.yaml` 新增的 plan 接口通过契约检查与一致性测试；响应形状里没有 `p_mastery`、`confidence`、`evidence_ids`、`learner_context`。
- [ ] **文案自查**：壳内全部界面文案在一个文件里，静态检查不含比较词表与禁用推断词表的命中；人工按禁语 A–G 类复核一遍，结果记入 lessons。
- [ ] **许可证**：没有新增运行时依赖；如有，按规则 10 更新清单并通过检查。
- [ ] 严格模式 `make check` 通过。

## 涉及的必停项（见 CLAUDE.md）
默认方案下逐条判断：

| 必停项 | 是否涉及 | 说明 |
|---|---|---|
| `db/migrations` | **是** | 新增 `platform__auth_session`（会话、验证码、限流）与 `platform__meter_guard_status`（只读函数）。编号合并时取下一个空号 |
| RLS 与权限策略 | **是** | 新表 `FORCE ROW LEVEL SECURITY` 且不给策略；新增 `SECURITY DEFINER` 函数与授权；附 RLS 测试 |
| 计量与权益逻辑 | **是（只读）** | 新增只读函数复用 0015 的规则，不改预留、结算、回滚、扣次；选科与时长设置调用现有函数。若待人确认 #14 选择在 4-H 开订阅或接入消耗类入口，范围扩大 |
| `packages/contracts` | **是** | `learning-internal.v1.yaml` 新增 `POST /internal/v1/plan`；若待人确认 #5 选择批量陈述工具，还要改 `insight-service` 契约（归 SIE-02b） |
| 判分与掌握计算 | 否 | 只读掌握状态，不改门槛、权重、复测调度 |
| 判断模板、禁用推断词表、画像可见范围 | **涉及（只读取）** | 不登记模板、不改字典；对外模板白名单的初值与能力视图的可见范围由人确认（待人确认 #8、ADR 13 待人确认 #4） |
| `evals/` 金标准 | 否 | 不读不写 |
| 人设 Prompt、禁语表、转介话术 | 否 | 点点入口只是占位，提醒与拒绝提示用系统口吻；UI 文案不是人设文本，但按禁语 A–G 类自查。要让点点说开场或提醒休息就变成“是” |
| 新增第三方服务 / 供应商 / 出境路径 | 否（4-H 内） | 只做短信接口与捕获桩；真实短信供应商是必停项，随 ADR 10 选境内厂商另起 PR |

**实现 PR 首行写 `[NEEDS-HUMAN]`**：H1、H3 含迁移与 RLS；H4 含契约变更；H1、H2、H6 涉及鉴权，属安全关键代码；H5 改星图的数据来源与画像展示。

## 依赖与输入
- **已合并**：3-H 星图与开发身份桩；2-E 计量模块与 `0008`、`0009`；计量 v2 的 BFF 改动与 `0015` 草稿；第一轮渗透测试夹具与攻击面清单；洞察服务与知识服务 OpenAPI v0.9；插件 API v1.0 与 `learning-internal.v1` 候选版。
- **要人执行或签字的**：迁移 0010、0011、0015 由人执行后，4-H 的判断读取、地图读取、时长状态才有真实数据源；ADR 01、10、11、13 签字；插件 API v1.0 与 `learning-internal.v1` 评审签字。
- **同期、会卡住验收的**（4-H 用假服务按契约完成验收，不以它们为门禁）：
  - 4-G 发布闸门：没有 `published` 课程版本就一科也选不了；4-H 用测试库里的合成已发布版本。
  - 4-D 阿鸦 `plan`：没有它，数学一节只能显示“今天暂无安排”；4-H 用按契约返回合成任务的假适配器。
  - 4-C（KGE-04）掌握 v0 与 SIE-02b 判断接入：没有它们，星图停在 7.1、推荐理由不显示；测试用合成掌握与合成判断（按 0010 的模板与状态机写入测试库）。
  - 洞察服务：部署位置未定（ADR 13 待人确认 #3）；4-H 用按 `insight-service.v0.9.yaml` 返回的假洞察服务。
- **PLT-01 修订**（规格书 V1.2 U1、U7）：学生家庭码 + PIN、主 / 协同监护人与权限表、设备表与上限、单一活跃 AI 会话、后台审计。4-H 的会话表与路由表要能被它扩展，不在 4-H 实现。
- **SIE-01**：建档表单与两方互不可见（第 14 节只留入口）。
- **需要人提供的**：合规轨（隐私政策与监护人同意文本及版本号）；产品（会话与限流参数、连续使用提醒阈值、推荐理由编码的分类）；视觉（统一壳视觉、星图三态与待验证虚线，开发计划 v2.0 角色轨；没有交付时按 3-H 令牌与系统字体实现）。
- **下游**：第二轮渗透测试的进入条件要求 4-H 合并、开发身份桩不存在；冲刺 5 在壳上接消耗类入口与信任界面。

## 与同期 spec 的协调（冲刺 4 四份 spec 复核时统一，以本节为准）
四份 spec：4-H `specs/unified-shell-sprint4/`、4-C `specs/mastery-estimate-sprint4/`、4-G `specs/release-gate-sprint4/`、4-D `specs/adapter-aya-methods-sprint4/`。
1. **迁移编号**：0010–0015 已被 insight、kge、课程地图版本、policy、届别、计量 v2 占用。四份 spec 的新迁移一律不预占编号，合并时按顺序取下一个空号；4-H 的两份记为 `platform__auth_session`、`platform__meter_guard_status`。
2. **plan 内部接口**：4-H 负责在 `learning-internal.v1.yaml` 新增 `POST /internal/v1/plan` 与学习服务的转发（必填的 `mastery` 快照）；4-D 只实现适配器侧 `plan`，不新增平台接口。`plan` 可选的 `learner_context`、`node_path`、`exam_config` 由编排层组装，写在 4-D 的 spec 里（它是这些字段的消费方），代码放在同一个学习服务模块。
3. **复测的唯一来源**：4-C（KGE-04）的复测调度。学习服务调 `plan` 时按它填 `mastery[].next_retest_at`；适配器只按它排序，不自己排复测；BFF 不直接读复测时间。
4. **已发布课程版本的测试数据**：4-G 上线后已发布版本与已准入题目只能经发布函数产生；4-G 的 PR 在同一个 PR 里改掉 `db/tests/10_fixtures.sql`、3-H 的 `db/seeds/dev/star-map-synthetic.sql` 与 4-H 的测试种子。
5. **作答要带提示等级**：统一壳提交作答时必须带 `hint_level_used`（冲刺 5 接入作答时生效）。
6. **复测作答扣不扣额度**：4-H 本冲刺不做消耗类入口；接入时如果要不扣，属于计量逻辑变更，必停。
7. **星图的节点与判断**：4-C 给出节点级掌握状态与当前记录 ID；SIE-02b 的判断以 `basis_id` 指向这条记录；4-H 只核对两者一致，不聚合、不重算。
8. **没有 spec 负责的缺口**（交人决定，见待人确认 #14、#15）：pilot 家庭的订阅开通入口；阿鸦题干（统一壳显示不了题目）。

## 待人确认
1. **PLT-01 之前学生怎么进壳**：默认 4-H 用“家长在本设备打开学生模式”作过渡，会话表为 PIN 与设备扩展留字段；PLT-01 上线家庭码 + PIN 后保留还是删除过渡入口，随 PLT-01 定。另一个选择：4-H 不做学生入口，等 PLT-01（4-H 验收会推迟）。
2. **会话存放**：默认 Postgres 新迁移、表不给策略、只经函数访问，限流计数同库。另一个选择：无状态签名令牌加吊销表（仍要迁移，“退出所有设备”与空闲超时更难做）；或 Redis 一类组件（新增组件，随 ADR 10 评估）。
3. **短信供应商**：默认 4-H 只做接口与捕获桩；供应商随 ADR 10 选境内厂商，另起必停 PR 并登记 ADR 09 数据流。另一个选择：4-H 内接入选定供应商的沙箱（需先定供应商并确认不出境）。
4. **会话、验证码、限流参数**（第二轮待人确认 #4）：默认验证码 5 分钟、错 5 次作废；会话绝对 7 天、空闲 24 小时；限流阈值见第 17 节。另一个选择：学生会话更短（例如绝对 12 小时）。
5. **BFF 怎么拿陈述文字**：`insight-service.v0.9` 没有给 BFF 的批量陈述读取工具。默认逐条调 `insight.explain` 并丢弃证据链，单页最多 10 条。另一个选择：SIE-02b 在洞察服务契约里加一个只给 BFF、按目标批量返回 `RenderedStatement` 的只读工具（契约变更，必停，归 SIE-02b）。
6. **同一号码已有多个家长账号**（历史数据，第一轮 C2）：默认拒绝登录、提示联系客服。另一个选择：列出账号让用户选（会暴露同号账号数）。
7. **谁能选科、换科**：默认按规格书 V1.2 §2，只有家长（BFF 路由为 `guardian`），学生端只读；学生“提议、家长确认”与主 / 协同区分随 PLT-01。另一个选择：沿用数据库现状，学生本人也能选。
8. **能力视图侧栏与对外模板白名单的可见范围**：字段字典 v0.9 的 `inner.g4.skill_view` 只给 agent，ADR 11 待人确认 #1 默认学生与家长都显示；默认签字前侧栏为空。白名单初值（数学“知识掌握”“知识迁移”两维的模板）随 ADR 13 待人确认 #4 由人登记。两者都属“画像可见范围”。
9. **星的粒度**：默认星 = 已发布地图中 `level = 'node'` 的有效知识节点，领域与主题只作分组（3-H 待人确认 #7“959 颗星”的延续）。另一个选择：只取参与掌握计算的类型（`kp`、`ability_kp`、`viewpoint`）。需数学教研与产品回答。
10. **时长状态的读取**：默认新增只读函数 `meter_guard_status`（迁移、计量域，必停）。另一个选择：BFF 用已授权的表自己算今日已用与宵禁（不用迁移，但与 `meter_reserve` 形成两套口径，不建议）。
11. **连续使用提醒**：默认连续 40 分钟提醒、建议休息 10 分钟，只提醒不锁定，系统口吻。另一个选择：按合规意见改为强制休息（需合规轨给规则）。
12. **家长端怎么区分孩子**：默认序号加年级，不收姓名。另一个选择：档案加家长自填昵称（改迁移，按数据最小化评估）。
13. **同意文本未到位时的环境策略**：默认开发与测试用 `synthetic-0`，staging 以外禁止 `synthetic-` 前缀，pilot 在合规交付前不能打开学生模式。另一个选择：pilot 先放行、事后补签（不建议）。
14. **订阅开通与消耗类入口**：计量 v2 没有对外接口，任何 spec 都没有 pilot 家庭或试用的开通入口。默认 4-H 都不做，开发与测试用合成订阅；开通入口随家长端或 PLT spec。另一个选择：建档后由 BFF 调 `meter_open_subscription('trial')`（规格书 V1.2“注册即得”），属计量与权益，必停。
15. **阿鸦题干**：平台和内容包里都没有题干，`plan` 与变式题只返回题目 ID，统一壳显示不了题目。由 4-D / 4-G 还是另起任务补，需人定（冲刺 5 接作答前必须解决）。

## 已决事项（引用，不再列为待确认）
- **英语**：继续搁置，入口 `hidden`、`subject_catalog` 为 planned；“进入四科”在 4-H 实际是数学真实、道法与语文按到位情况 `live` 或占位；Gate 0 里与英语相关的口径由 Gate 0 评审定（对照文档第三节决定 #1）。
- **星 = 知识节点、能力坐标为能力视图、3-H 代码暂不动**：对照文档第三节 #4 默认已确认；ADR 11 草稿待签。
- **核心包**：英语搁置期间基本档 = 已上线核心科目任选 1 科，Plus 及以上开放全部已上线核心科目（对照文档第八节 #2 用户决定；`specs/metering-v2/lessons.md` 待人确认 #1 的实现）。
- **免费用户**：试用 = 基本档功能、14 天一次性；到期转留存态（可登录、看星图与历史、每天 5 次自练、不调用 AI）（对照文档第八节 8.1 决定记录）。
- **单日上限与宵禁**：服务端在预留时强制，家长只能调低与收紧；题库自练不受限（`specs/metering-v2/spec.md` 已确认，待人确认 #6 按默认）。
- **plan 内部接口由 4-H 加**：本 spec“与同期 spec 的协调”第 2 条（原待人确认 #5）。
- **CSP 内联样式**：实现时二选一并记入 lessons，不单列确认（原待人确认 #14）。
- **监护操作审计表**：随 PLT-01 后台审计（规格书 V1.2 U1），4-H 只写访问日志（原待人确认 #12）。
- **首发对外维度**：只展示数学“知识掌握”“知识迁移”（SIE 拍板第 2 项，已按默认确认）。
