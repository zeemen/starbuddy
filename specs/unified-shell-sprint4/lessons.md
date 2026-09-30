# 冲刺 4-H 统一壳：实现记录

## H1 认证与会话（2026-09-30）

PR 首行应写 `[NEEDS-HUMAN]`：含迁移 0029（草稿，只写文件，由人先在副本演练再执行，不在任何共享环境执行）、RLS 与权限、鉴权（安全关键代码）。

### 交付
- **迁移 0029 `platform__auth_session`**（含 down）：
  - 三张表 `auth_otp_challenge`（验证码挑战，只存 BFF 算的 HMAC）、`auth_session`（令牌只存 sha256；`origin` = `otp` / `guardian_handoff`，预留 `device_id`，PLT-01 在同一张表上扩展）、`auth_rate_counter`（固定窗口，键是 HMAC）；都 ENABLE + FORCE RLS，**没有任何策略**，应用角色没有表权限；
  - 七个 SECURITY DEFINER 函数（属主 `starbuddy_authz`、search_path 以 `pg_temp` 结尾、只授 `starbuddy_bff`）：`auth_otp_issue`、`auth_otp_login`、`auth_session_resolve`、`auth_session_revoke`、`auth_session_revoke_all`、`auth_issue_student_session`、`auth_rate_hit`；内部函数（`auth_now` 时钟、`auth_random_bytes`、`auth_token_hash`、`auth_phone_ok`、`auth_new_session`）不授任何应用角色；
  - 没有改 bootstrap 角色；`starbuddy_authz` 新增 `consent_record` 的 SELECT（打开学生模式前查同意）。
- **DB 测试** `db/tests/79k_auth_session.sql`（137 项断言）：权限（三张表对 8 个应用角色读删都拒绝、BFF 不能直接插改、BFF 以外角色不能调用任何 `auth_` 函数）、申请结果不区分注册与否、错码计次、5 分钟过期、用过、错 5 次作废、新申请作废旧码、换号、同号多家长账号、停用家长、号码只挂在学生账号上、令牌只存哈希、空闲 24 小时、绝对 7 天、登出、退出所有设备、账号停用、学生模式（缺同意、不满 14、按月末算年龄、撤回、未绑定、已解除、不存在、停用学生、跨账号用令牌、学生会话再签发）、限流、约束、来源与账号类型不符的会话。
  - 授权快照 `db/tests/expected/privileges.txt` 已更新（+44 行，全部是新对象）；`80_attack_surface` 的函数越权矩阵：`auth_issue_student_session` 进矩阵（第一个参数是学生），其余六个登记豁免理由；`docs/security/pentest/scope.md` 函数表 76 → 83；攻击面清单 C2、C3 补了引用。
  - `db/tests/mutations.sh` 新增 29 条“认证：”变异，`DBMUT_FILTER=认证：` 全部被发现。
- **BFF `src/http/`**：`routes.ts` 路由表（H1 八条）、`server.ts` 中间件链、`config.ts`（`config/auth.json` + 环境 + 密钥）、`secrets.ts`（带密钥 HMAC、常量时间比较）、`sms.ts`（`SmsSender` 接口与捕获桩）、`problems.ts`（预序列化错误体）；接口 `openapi/shell.v0.yaml`。
  - 测试：`tests/auth.integration.test.ts`（19 项：登录、会话、学生模式、CSRF、限流、未登录 401、表外 404、身份注入、连接池 200 次交替并发、日志）、`tests/shell-openapi.test.ts`（路由表与 OpenAPI 一致、路由表不变量）、`tests/shell-startup.test.ts`（生产配置拒绝启动）；`tests/mutations.sh` 新增 13 条“认证：”源码变异。
- **apps/web**：`/login`（手机号 + 验证码两步）、`/family`（孩子列表 + 在本设备打开学生模式 + 退出）、`/student`（学生模式落地页 + 退出）；文案集中在 `src/auth/copy.ts`；cookie 在 `src/lib/session-cookie.ts`；BFF 客户端 `src/lib/shell-auth.ts`；单测 `tests/auth.test.ts`；端到端 `e2e/auth.spec.ts`（捕获收件箱读码、cookie 属性、令牌变化、旧令牌 401、缺同意提示、axe、全程同源）；合成种子 `db/seeds/dev/auth-synthetic.sql`。

### 实现要点
1. **验证码不进库**：BFF 用 CSPRNG 生成 6 位码，经 `SmsSender` 发出，只把 `HMAC(密钥, 号码 + 码)` 交给库。库里比较的是 32 字节 MAC（比较的不是秘密本身，攻击者不知道密钥，逐字节比较的时间差没有用处）；BFF 侧的 CSRF 比较用 `timingSafeEqual`。MAC 与号码绑定，换号使用必然对不上。
2. **会话只能从两处产生**：`auth_otp_login`（核对验证码 → 查家长账号 / 注册 → 建会话，在一个函数里）与 `auth_issue_student_session`（家长签发）。建会话的内部函数 `auth_new_session` 不授 BFF（有测试与变异“BFF 可直接建会话”）。
3. **手机号只登录家长账号**（规则 5）：只按 `kind = 'guardian'` 匹配；同号多个家长账号时返回 `contact_support`、不建会话（待人确认 #6 默认）；停用的家长也计入（不会因“没有有效账号”被重新注册）；只有学生账号有这个号码时新注册一个家长，不会登进学生账号。
4. **学生模式**：令牌必须是当前账号（`app.account_id`，BFF 在 `withAccount` 里设）的有效短信会话，且家长对该学生有有效绑定、学生账号有效；否则一律 `insufficient_privilege 'not allowed'`，与“学生不存在”逐字相同（也在 80 的函数越权矩阵里）。绑定核对之后才查同意：`privacy_policy` 最新一条是 granted；不满 14 周岁（出生年月为空按不满；按该月**最后一天**出生算，保守）另需 `guardian_consent_under14`。成功时家长会话作废、签发学生会话。
5. **续期**：`auth_session_resolve` 每次解析都刷新最近活动时间（空闲超时是滑动的），没有单独的续期函数；**关闭学生模式** = 学生会话登出（`POST /auth/logout`），没有单独的函数或路由；回家长模式要重新短信验证。
6. **时长与阈值**：全部来自 `services/bff/config/auth.json`（测试读同一份）；库函数只卡上下界（验证码 60–900 秒、错误上限 1–10、会话绝对 ≤ 30 天、空闲 ≥ 60 秒且不超过绝对时长），配置写错不会变成“永不过期”；`loadAuthConfig` 按同样的上下界在启动时校验。
7. **CSRF**：改变状态的路由都开；登录前两条只核对 Origin，其余核对 Origin 与与会话绑定的令牌（BFF 用 `HMAC(密钥, 会话令牌)` 派生，不另存）。前端把 CSRF 令牌放在同属性的 HttpOnly cookie 里，服务端组件渲染成表单隐藏字段，server action 核对“表单 = cookie”后连同浏览器的 Origin 转交 BFF（同步令牌 + 双重提交）；Next server action 另有同源校验。BFF 不发任何 CORS 头。
8. **日志**：BFF 访问日志只记请求 ID、路由模板、状态码、耗时、账号类型、`student_id` 的带密钥 HMAC，以及 `outcome`、`sms`、`error_class` 三个枚举字段。旧路由（星图、建档、事件、诊断）的 `hashId` 也改成同一把密钥的 HMAC（spec 范围第 1 条“日志改带密钥 HMAC”）。
9. **短信**：只有捕获桩；只在 `STARBUDDY_ENV` 为 dev / test 能建，只收 +999 合成号码（别的号码拒收、不落盘，请求方照样 202）；staging、pilot、生产没有供应商，BFF 启动即失败（`tests/shell-startup.test.ts`）。

### 与 spec 的偏差（实现时的选择）
1. **中间件顺序**：限流拆成两段——按 IP 的在解析会话之前；按号码、按账号的要先有请求体或会话，放在 CSRF 之后。其余顺序与第 3 节一致。
2. **H1 的路由表挂载**：统一壳路由表挂在星图服务同一个端口上（`createStarMapServer({ shell })`），路径不在表里才走旧路由；“表外一律 404”只在独立入口 `createShellServer` 成立。H2 迁移旧路由、删除开发身份桩后改用独立入口。BFF 启动从此**必须设 `STARBUDDY_ENV`**（`apps/web/scripts/e2e.sh` 与 `apps/web/README.md` 已同步）。
3. **错误码**（spec 没写死）：登录失败一律 401 `LOGIN_FAILED`；同号多家长账号 409 `CONTACT_SUPPORT`；缺同意 409 `CONSENT_REQUIRED`；学生会话调家长路由一律 403 `FORBIDDEN`（在任何查询之前判断，与路径里的学生是谁无关，所以不泄露存在性）；CSRF 403 `CSRF`；限流 429 `TOO_MANY_REQUESTS`。
4. **请求 ID 不回响应头**，只进日志：否则“无权”与“不存在”的 404 响应头就不能逐字节相同。
5. **函数签名比 spec 多参数**：会话时长由 BFF 从配置传入；`auth_otp_login` 多一个 `p_prev_token`（登录成功时作废本设备旧会话，防会话固定）；`auth_issue_student_session` 另核对令牌账号 = `app.account_id`。
6. **`auth_session_revoke_all`** 把该账号所有未撤销的会话（含已过期的）标为撤销，返回撤销条数。
7. **前端页面**：`/family`、`/student` 是 H1 的最小过渡页（spec 把家长页、壳页面放在 H4）；建档与同意的**界面**不在 H1（BFF 路由已有），端到端用种子写同意。
8. **CSP、HSTS、删除开发身份桩**仍属 H2；本 PR 没有动 `dev-auth.ts`、`/dev/login`、`/dev/session`。
9. `config/auth.json` 预先写了 H4 要用的 `time_guard.account.hour`；洞察服务按会话限频的阈值随 H4 定。

### 待人确认（新增）
- A. **过期数据的清理**：会话、验证码挑战、限流计数没有定时清理（限流每次命中只清同一个键的旧窗口）。建议随计量定时任务加一个只授 `starbuddy_scheduler` 的清理函数（迁移，必停）。
- B. **收回旧入口**：0002 的 `signup_guardian`、`lookup_accounts_by_phone` 仍授 BFF（H1 已不用；前者能不经验证码注册账号）。建议 H2 一起收回（改授权，必停）。**H2 已按默认收回：迁移 0031 草稿，待人执行。**
- C. **请求体里注入 `account_id`**：BFF 对未知键一律 422（与事件、建档接口一致），与验收“结果与不注入时相同”的字面要求不同；请求头与查询参数注入已按要求忽略。H6 生成注入矩阵时定口径。
- D. **同意的“最新一条”口径**：不区分是哪位家长写的——任一已绑定家长撤回，学生模式就打不开。是否要按家长分别算，需合规轨确认。
- E. **`contact_support` 是否可区分**：只有码对了（持有该号码）才会看到，不对外泄露同号账号数；若要求与普通失败完全相同，改一行即可。
- F. **客户端 IP**：`BFF_TRUST_CLIENT_IP=1` 时 BFF 信 apps/web 转交的 `x-sb-client-ip`，apps/web 取 `x-forwarded-for` 的第一项；这要求部署层的反向代理覆盖（而不是追加）这个头，否则每 IP 限流可被伪造绕过。端到端没开（全部按本机地址计）。随 ADR 10 部署拓扑定。
- G. **数据库日志**：`auth_otp_login` 的参数有手机号原文（验证码只有 MAC）。部署时 BFF 登录角色要关语句与参数日志（同 0020）；`account_contact` 的手机号明文与否仍待 ADR 10。
- H. **学生会话时长**：沿用家长的 7 天 / 24 小时（待人确认 #4 默认）；若按 #4 的另一个选择把学生会话缩短，只改 `config/auth.json` 的 `session.student`。

### 文案自查（禁语 A–G）
`src/auth/copy.ts` 的全部文案（登录、结果提示、家长首页、学生模式）过了 `python -m personas.guard scan --persona platform --stdin-json`（0 命中）与比较 / 评价 / 营销词表检查（`apps/web/tests/auth.test.ts`）。人工按 A–G 类逐条读过一遍：系统口吻、只陈述操作与结果，没有评价、比较、推断、催促或诱导；关于孩子只出现“孩子 N · 某年级”。人设负责人复核待做。

## H3 计量只读函数（2026-09-30）

PR 首行应写 `[NEEDS-HUMAN]`：含迁移 0030（草稿，只写文件，由人先在副本演练再执行，不在任何共享环境执行）、权限、计量域（只读）。

### 交付
- **迁移 0030 `platform__meter_guard_status`**（含 down）：一个只读 SECURITY DEFINER 函数 `platform.meter_guard_status(p_student) → jsonb`
  （STABLE，属主 `starbuddy_authz`，search_path 以 `pg_temp` 结尾，只授 `starbuddy_bff`）。授权只认 `can_read_student`（学生本人或按
  `guardian_student_link` 有效绑定的家长，规则 5），其他人、未登录、学生不存在、参数为空一律同一个 `42501 'not permitted'`（进 80 的函数越权矩阵）。
  返回键固定：`state`、`trial_ends_at`、`plan_daily_cap`、`daily_cap`、`minutes_used_today`、`minutes_remaining_today`、`curfew_start`、
  `curfew_end`、`in_curfew`、`minutes_until_curfew`、`practice_remaining_today`、`as_of`；全部由 0015 的推导函数按同一个 `meter_now()` 组合，
  不预留、不结算、不回滚，不改 0015 任何规则与数值；不返回设置人。
- **DB 测试** `db/tests/79l_meter_guard.sql`（42 项断言，整文件一个事务、结束回滚）：函数属性与授权（其他 7 个应用角色与 PUBLIC 不能执行）；
  本人、已绑定家长（结果与本人相同）、未登录、另一户家长、另一户学生、兄妹、已解除绑定的家长、停用的学生账号、staff、不存在与参数为空（逐字相同）；
  与 `meter_minutes_today` / `meter_daily_cap` / `meter_plan_daily_cap` / `meter_in_curfew` / `meter_minutes_until_curfew` 一致；试用结束时间；
  已过期未回收的预留不计入；家长调低后生效上限变、档位上限不变；上限低于已用时剩余为 0 且 `meter_reserve` 同时拒绝；调用前后账本、预留、自练不变；
  宵禁边界 21:59 / 22:00 / 23:30 / 05:59 / 06:00（上海时间，与 UTC 不同的换算点）、家长收紧到 21:00–07:00、距宵禁 30 分钟、兄妹设置分开；
  留存态自练 5 → 2 → 0 且此时 `meter_practice` 同样拒绝、按上海时间换日（跨 UTC 零点仍是同一天）；试用到期（续期任务跑过）后转留存态。
- 授权快照（`db/tests/expected/privileges.txt` +4 行）、`docs/security/pentest/scope.md` 函数表 83 → 84、攻击面 G1、G5 补了引用。
- `db/tests/mutations.sh` 新增 14 条 `计量只读：`（去掉授权检查、只认本人、只认家长、用档位上限代替生效上限、剩余可为负、时钟用系统时间、
  宵禁起止不取家长设置、自练按 UTC 换日、每天 6 次、非留存态也给次数、试用结束取开始时间、返回设置人、学习服务可调用、对 PUBLIC 开放），全部被发现。
- **SIE-02a 诊断切到这个函数**（SIE-02a lessons #15 已更新状态）：`services/bff/src/diagnosis/service.ts` 的 `inCurfew` 改为调
  `meter_guard_status` 取 `in_curfew`（42501 时回与“不是本人”相同的 403）；删掉 BFF 里的宵禁 SQL、`deps.now`、`STARBUDDY_DEV_METER_CLOCK`
  （端到端只把库里的 `meter_now()` 挪到白天）。行为不变的证据：原宵禁集成测试原样通过；新增“家长把宵禁提前到 21:00 后 20:59 能开始、21:30 拒绝，
  与函数返回一致”和“诊断源码里没有宵禁 / 时区计算”两条。BFF 变异“宵禁口径不跨午夜”换成“宵禁不看计量只读函数的结果”。

### 与 spec 的偏差
1. **返回 jsonb**（与 0015 的计量函数一致），不是表；多一个 `as_of`（计量时钟），BFF 显示“试用还剩 N 天”等时用同一时钟。
2. **`curfew_soon` 不在库里判断**：库只给 `minutes_until_curfew`（已在宵禁内为 0，与 0015 相同）；“距宵禁 ≤ 配置分钟数”的阈值是显示规则，随 H4 放 BFF 配置。
3. `curfew_start` / `curfew_end` 以 `HH:MM` 文本返回（家长设定或缺省 22:00–06:00，跨午夜）。

### 待人确认（新增）
- I. **“每天 5 次自练”在库里有两处**：0015 的 `meter_practice` 把 5 写在函数体里，0030 为了只读也写了一次（测试断言两处在第 5 次同时到 0）。
  要消除重复需改 0015 的 `meter_practice`（抽成共用函数），属计量逻辑变更，必停，未做。
- J. **上线顺序**：诊断的宵禁判断现在依赖 0030；0030 执行前 BFF 的续做、取题、探针、作答会 500。0030 须先于（或与）BFF 这次改动一起上线。
- K. `trial_ends_at` 取 `meter_state_of` 同一条订阅的 `period_end`；试用周期已过、续期任务还没跑到时仍显示 trialing 与一个过去的时间（与 0015
  “按订阅状态判断”一致）。界面是否要在这段空档显示“今天到期”，随 H4 文案定。

## H2 删除开发身份桩与安全头（2026-09-30）

PR 首行应写 `[NEEDS-HUMAN]`：含迁移 0031（草稿，收回授权）、鉴权（安全关键代码）。建议与 H3 分两个提交。

### 交付
- **删除开发身份桩（是删除，不是关掉）**：`apps/web/src/lib/dev-auth.ts`、`src/dev/accounts.ts`、`/dev/login`、`/dev/session`、旧星图页
  `src/app/star-map/[studentId]/page.tsx`；BFF 的 `src/star-map/server.ts`（`devAccount()`、`x-dev-account-id`、正则分发）与 `src/star-map/main.ts`
  （`STARBUDDY_DEV_AUTH`、`STARBUDDY_DEV_METER_CLOCK`）。BFF 入口改为 `src/main.ts`（`npm start`），只有 `createShellServer`，表外一律 404。
- **旧路由全部迁进路由表**（`services/bff/src/http/feature-routes.ts`，路由表 8 → 23 条）：星图（session）、建档 5 条（session）、事件与探针 3 条、
  诊断 6 条（student）。复用原查询模块，handler 自己开事务（路由新属性 `ownTx`，中间件不另开事务、`ctx.q` 不可用）。接口定义仍在各自的
  `openapi/*.v0.yaml`（开发桩参数换成 `x-sb-session`）；`tests/shell-openapi.test.ts` 改为“路由表 = 五份 yaml 的并集”，另加“源码与接口定义里
  没有开发桩痕迹”。
- **3-H 星图挂到真实会话**（第 7.1 节）：`/s/{studentId}/star-map`（学生）、`/family/{studentId}/star-map`（家长），共用 `src/star/StarMapPage.tsx`；
  没会话去 `/login`，学生会话开家长地址（或反之）转到自己那侧；旧地址 404。数据源、三态、水印不动；家长首页与学生页各加一个“星图”链接（文案在 `src/auth/copy.ts`）。
- **前端全部走真实会话**：`src/lib/bff.ts`（地址白名单 + 请求头）、`src/lib/session.ts`（`pageSession()`、`actionSession()`：表单或组件带回的
  CSRF 令牌必须等于 CSRF cookie）；建档、探针与诊断演示页、星图都改用它，写入带 Origin 与 CSRF 令牌。演示页的 server action 多一个 `csrf` 参数。
- **开发专用页面改为构建期常量**：`STARBUDDY_DEV_PAGES=1` 且 dev / test 构建才把 `page.dev.tsx` 当页面；`/dev/star-glyphs` 也改成 `page.dev.tsx`，
  生产构建里没有任何 `/dev/*` 路由；运行时再核对环境与本机 Host（`src/lib/dev-pages.ts`）。
- **BFF 地址白名单**（第 4 节）：dev / test（或没设）只允许 loopback 的 http；staging、pilot、生产只允许 RFC 1918 内网 IPv4 字面量；不许带路径与凭据。
- **收回旧入口**（H1 待人确认 B 的默认）：迁移 0031 草稿 `platform__revoke_legacy_signup`（含 down）收回 0002 的 `signup_guardian`、
  `lookup_accounts_by_phone` 对 BFF 的 EXECUTE（函数与数据不动，属主仍可执行）。`20_rls_household` 改为断言 BFF 调用 42501、函数行为不变；
  80 的豁免表删掉这两条；快照 −2 行；scope.md 两行改为“无（仅属主）”；攻击面 B2、C3 补引用；DB 变异 2 条 `旧入口：` 被发现。
- **安全头**（第 16 节）：`src/proxy.ts`（Next 16 的 proxy）每个请求生成 16 字节 nonce，CSP 同时进请求头（Next 给自己的脚本加 nonce）与响应头：
  `default-src 'self'; script-src 'self' 'nonce-…' 'strict-dynamic'; style-src 'self' 'nonce-…'; img-src 'self' data:; connect-src 'self';
  font-src 'self'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'`（`next dev` 另加
  `unsafe-eval`，生产与测试构建没有）；HSTS `max-age=31536000; includeSubDomains` 只在 staging、pilot、生产；`next.config.ts` 保留 nosniff、
  `X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`，页面一律 `no-store`（带哈希的静态资源除外）。根布局 `await headers()`，
  所有页面动态渲染（静态预渲染的页面拿不到 nonce）。
- **内联样式二选一：选静态文件**。主题变量从根布局的内联 `<style>` 移到 `src/app/theme.css`（单测核对与 `THEME_CSS` 一致）；组件里两处
  `style` 属性也去掉：半亮裁剪区的 `<svg>` 用 `.star-defs`，星空宽度用 `data-cols`（1–64 列的规则预先写在 `globals.css`，由
  `layout.ts` 的 `skyColumnsCss()` 生成，单测核对）。静态检查：`.tsx` 里没有 `style={`、内联 `<style>`、`dangerouslySetInnerHTML`。
- **构建产物扫描**：`make web-test` 在生产构建后 grep `apps/web/.next` 里的 `x-dev-account-id`、`sb_dev_account`、`STARBUDDY_DEV_AUTH`、`/dev/login`、
  `/dev/session`、`/dev/star-glyphs`、`SYNTHETIC_ACCOUNTS`；`tests/build-output.test.ts` 另查路由表没有 `/dev/*`、`/star-map/*` 与合成账号 ID。
- **测试全部走真实会话**：
  - BFF：`tests/shell-harness.ts`（起 `createShellServer`，家长经 OTP + 捕获桩登录，学生经家长打开学生模式；需要的同意由超级用户补合成记录；
    限流阈值放宽 1 万倍——限流本身仍在 `auth.integration.test.ts` 按原配置测）。星图、建档、事件、诊断四个集成测试改用它，断言不变（偏差见下）；
    新增：星图“开发身份头一律 401 / 带会话时被忽略”、“表外 404、旧地址 404”，建档“写入缺 CSRF、令牌属于别的会话、Origin 不符都 403”，
    诊断“会话已结束时其他学生读仍 403”（H3 让宵禁关卡也会拒绝别人，原变异“取当前题不核对会话归属”因此一度漏检，补这条后被发现）。
  - 端到端：`e2e/session.ts`（登录页 + 捕获收件箱读码；学生经家长首页“打开学生模式”，同意经 BFF 的同意路由补上）；星图、建档、探针、诊断、
    审核台、视觉回归的用例全部改用它；新增 `e2e/headers.spec.ts`（登录、404、样张、学生页、星图、建档：带本次 nonce 的 CSP、no-store、nosniff、
    no-referrer、无 HSTS；HTML 里每个 `<script>` / `<style>` 都带本次 nonce、没有 style 属性；每个请求 nonce 不同；浏览器没有 CSP 违规）。
    `auth-synthetic.sql` 补了 gO（+99900000102）、gD（+99900000105）的号码。
- BFF 变异：删掉已不存在的 3 条“开发身份桩不看…”与 2 条重复的日志变异，其余 8 条改指新位置；新增 4 条 `壳：`（身份改从 x-dev-account-id 读、
  星图不需要会话、建档与事件写入不开 CSRF、表外路径不回 404）。

### 与 spec 的偏差（实现时的选择）
1. **路径参数不是 UUID 时一律 404**（统一壳路由表的规则）；原来事件、探针、诊断对非法 ID 回 403。错误体与“不存在”相同。
2. **只认学生本人的接口**（事件、探针、诊断）auth = student：家长会话在任何查询之前就 403，响应体与原来“不是本人”的 403 逐字相同。
3. **停用的学生账号**：原来开发桩下读自己的星图是 404；真实会话下账号停用后会话本身失效，是 401（更早拦下）。3-H 这一条断言按此改。
4. **写入上限的 429**（0025 的 24 小时写入上限，不是限流）保留原来的 `TOO_MANY` / `too many writes`，与限流的 `TOO_MANY_REQUESTS` 不同。
5. **HSTS 也在生产发送**（spec 写 staging 与 pilot；生产同样是 https，按同一规则处理）；没有加 `preload`。
6. **`BFF_AUTH_CONFIG`**：新增一个只在 dev / test 生效的阈值配置覆盖（端到端一次要让同一个合成家长登录几十次，每号每分钟 1 次会 429）；
   staging、pilot、生产设了就拒绝启动（`tests/shell-startup.test.ts`）。`scripts/e2e.sh` 生成放宽 1000 倍的临时副本。
7. **演示页**：家长会话也能打开（与原来一样，提交时 BFF 按“只认学生本人”拒绝，页面只提示没记下）；没登录去 `/login`（原来去开发登录页）。
8. **家长首页、学生页的“星图”链接**是为了能从壳进到星图的最小入口；壳的顶栏、今日计划等仍随 H4。

### 待人确认（新增）
- L. **内网地址白名单只认 RFC 1918 IPv4 字面量**：staging / pilot 若用内网 DNS 名或 IPv6，需要随 ADR 10 部署拓扑扩白名单。
- M. **视觉回归基线**：星空宽度从 style 属性换成 `data-cols` 类，像素应不变，但 `make web-visual` 不在本次要求的检查里、没有重跑；
  基线需要在固定容器里由视觉负责人确认（3-H 待人确认 #11 的流程）。
- N. **0031 的上线顺序**：收回授权前确认没有别的调用方（仓库里 BFF 自 H1 起已不调用；运维脚本若有，改用 `auth_otp_login` 或以属主身份执行）。
- O. **CSP 的 `img-src data:`** 按 spec 保留（目前页面没有 data: 图片，SVG 都是内联元素）；若 H4 也用不到，可收紧为 `'self'`。

### 文案自查（禁语 A–G）
新增文案只有“星图”与“孩子 N · 某年级的星图”（`src/auth/copy.ts`，过了 `apps/web/tests/auth.test.ts` 的禁语扫描与比较 / 评价 / 营销词表检查）：
系统口吻、只是导航，没有评价、比较、推断、催促。人设负责人复核待做。
