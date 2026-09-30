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

## H4 壳页面与读路由（2026-09-30）

PR 首行应写 `[NEEDS-HUMAN]`：含契约变更（`learning-internal.v1.yaml` 新增 `POST /internal/v1/plan`，候选版待签字）、画像可见范围（对外模板白名单、
推荐理由编码分类与编码—模板映射）、计量域（只读）、`db/seeds/dev/` 新增合成种子（请人审）。**没有迁移**（需要的库改动见下面“需要的 db 变更”）。

### 交付
- **契约**（必停）：`packages/contracts/openapi/learning-internal.v1.yaml` 新增 `POST /internal/v1/plan`（`PlanQuery` → `PlanDigest`）：请求只有
  `request_id`、`student_ref`、`subjects`（`aya` / `wenli` / `zhixing`，英语不在枚举里）、可选 `time_budget_minutes`；响应每科 `ok` / `unavailable`，
  任务只有 `kind`、`target_node_ids`（知识节点 ID，引用知识服务的 `NodeId`）、`priority`、`estimated_minutes`、`rationale_code`，全部
  `additionalProperties: false`（没有 `p_mastery`、`confidence`、`evidence_ids`、`learner_context`）；`unavailable` 时 `tasks` 必须为空。
  示例 2 个；`check_contract.py` 的 v1 反例 +13 条（夹带掌握概率 / 置信度 / 证据 ID / 画像上下文 / 叙述文字、理由写成句子、目标写成能力点、
  不可用还带任务、英语、调用方自带掌握快照等），全部被拒绝。
- **学习服务**：`learning/plan.py` + `app.py` 路由（服务间令牌、请求体上限、日志只记学科数与各科状态）。读 `platform.mastery_estimate` 当前行组装
  v1.0 `PlanRequest`（非消耗类，不带 `usage_id`），调适配器 `/plan`（单次超时 `plan_timeout` 2 秒，含一次重试总预算 5 秒），结果按插件契约校验、
  `contract_version` 必须 1.0.0；能力点经**已发布**地图版本的 `kge.skill_link`（有效节点、按权重）换成知识节点；摘要显式挑字段、按优先级排序、
  返回前按 `PlanDigest` 校验。不写任何表。候选契约没开时该科 `unavailable`。测试 `tests/test_plan.py`（13 项）。
- **BFF**（`src/http/shell-routes.ts`、`src/shell/`、`config/shell.json`、`openapi/shell.v0.yaml`）：路由表 +9 条——
  `GET enrollments`（session）、`POST enrollments/{subject}` 与 `/pause`（guardian）、`GET today`（session）、`GET balance`（session）、
  `GET/PUT time-guard`（session / guardian，PUT 限流每账号每小时 20）、`GET rest-reminder` 与 `POST rest-reminder/ack`（student）。
  - 选科只经 `enroll_subject` / `pause_subject`，outcome 原样回（只挑 `outcome`、`slot`、`limit`、`max_active_subjects`）；英语与 planned 学科码 404；
  - 今日计划：学习服务客户端 `src/shell/plan-client.ts`（只挑契约字段，任何一条不合格整科按不可用）；HTTP 调用在事务外，前后两段 `withAccount`；
    推荐理由按 `config/shell.json` 分两类（下面待人确认 Q），关于学生类只在目标节点上有该受众可见、成立或假设、未到期、模板在白名单里的判断时，
    调洞察服务 `insight.explain`（`src/shell/insight-client.ts`，caller = bff，带 actor；家长带 `link_id`）取 `RenderedStatement.text`，
    核对陈述确实属于这条判断、是实线的成立或假设；每页最多 10 条、每会话每分钟 30 次（`config/auth.json` 新桶 `insight_explain.session.minute`），
    超时或限频就不显示；
  - 额度与时长：`meter_guard_status`（0030）+ `entitlement_balance`；学生端同一资源合并一行，家长端带分来源明细；`trial_days_left` 按上海自然日；
    `curfew_near`（原名 curfew_soon）阈值（30 分钟）与 `daily_cap_reached` 是显示规则，数值全部来自库；
  - 时长设置只经 `set_time_guard`；GET 不返回设置人；
  - 连续使用提醒 `src/shell/rest.ts`（纯函数 + 进程内存储，键是会话令牌的带密钥 HMAC）；
  - `main.ts`：`BFF_LEARNING_URL` 同时给今日计划用；新增 `BFF_INSIGHT_URL` / `BFF_INSIGHT_TOKEN`、`BFF_SHELL_CONFIG`（只在 dev / test）。
  - 测试：`tests/shell-pages.integration.test.ts`（13 项，真实会话 + 真实 RLS + 可注入计量时钟 + 按契约的假学习服务与假洞察服务）、
    `tests/shell-units.test.ts`（7 项）、`tests/denial-copy.test.ts`（拒绝提示的类型穷举）；`tests/mutations.sh` 新增 7 条 `页面：` 变异，全部被发现。
    `tests/auth.integration.test.ts` 两处按路由表生成路径的夹具补了 `{subject}` 参数的替换（断言不变）。
- **前端**（`apps/web`）：
  - 学生壳 `/s/{id}/…` 的布局（`src/app/s/[studentId]/layout.tsx` + `src/shell/StudentShell.tsx`）：顶栏“今日”“星图”“科目”、额度与时长状态条、
    连续使用提醒条（“知道了”回到原页面，路径经 `proxy.ts` 的 `x-sb-path` 请求头）、点点入口占位（`<details>`，系统口吻，无形象）、退出学生模式；
    只有学生本人的会话才套壳、才取状态。页面：`today`、`learn`、`learn/{subject}`；星图沿用 3-H（7.1），套进壳。`/student` 改为转到 `/s/{id}/today`。
  - 科目清单 `src/subjects/registry.ts`（数学 live、道法与语文 coming、英语 hidden）+ 每科目录的入口组件；科目页一步回到星图。
  - 家长端：首页加孩子页入口、记录同意、建档表单；孩子页 `/family/{id}`（额度与分来源、选科开通 / 暂停、今日计划带置信度说明、星图、时长设置、
    完善学习档案入口）；时长设置页 `/family/{id}/time`。家长端没有点点入口、没有角色图片。
  - 信任界面预留：`src/shell/features.ts`（why / dispute / reconcile / weekly / data_rights，默认全关，只在 dev / test 可用 `STARBUDDY_TRUST_UI` 打开）+
    `src/shell/TrustSlot.tsx`（关时 `return null`，开时也只是不可操作的占位，不发请求）；位置：今日计划理由旁、星的详情位、家长孩子页。
  - 拒绝提示文案 `src/shell/denials.ts`（BFF 的 `tests/denial-copy.test.ts` 用 `meter.ts` 的类型做穷举，漏写时 BFF 类型检查失败）。
  - 文案全部在 `src/shell/copy.ts`；BFF 客户端 `src/lib/shell-api.ts`；server action `src/app/family/shell-actions.ts`、`src/app/s/[studentId]/actions.ts`。
  - 测试：`tests/shell.test.ts`（10 项：禁语扫描、禁用推断词表、比较 / 营销 / 白名单外维度词、`{{TODO_COPY}}` 占位、壳 .tsx 里没有中文文案、
    只有 TaskList 渲染 `data-claim-id`、开关默认关且 staging 以上一律关、预留位不发请求、科目清单、家长端无点点与角色资产、不写浏览器存储）；
    端到端 `e2e/shell.spec.ts`（5 项，含开发计划验收）；`e2e/headers.spec.ts` 爬取加上今日、科目与三个科目页；`e2e/session.ts`、`auth.spec.ts`
    的学生落地页改为 `/s/{id}/today`。
- **合成种子**：`db/seeds/dev/shell-synthetic.sql`（审查后从 `services/bff/scripts/` 挪来，库名与开关双重保护保留，**请人审**）——额度配置 `dev-shell-plan@1`（照抄 0015 草稿 v12-pilot@1，只在开发 / 测试库发布）、
  数学与语文的合成已发布课程版本、家庭 HG（家长 gH +99900000106；GT 试用、GR 留存、GP 试点在读数学，都有合成同意）；`apps/web/scripts/e2e.sh` 执行它。

### 与 spec 的偏差（实现时的选择）
1. **推荐理由映射表外的编码不显示理由**：spec 第 6 节写“按关于学生类处理（宁可不显示）”，验收写“映射表外的编码不显示理由”。取更严的一种：
   `config/shell.json` 列出内容类与关于学生类两张表，两张都没有的编码一律不显示（即使目标节点上有判断）。
2. **对外模板白名单初值为空**：spec 的初值是数学“知识掌握”“知识迁移”两维的模板，但它们还没有登记（洞察服务只有 `syn.math.*` 合成占位），
   所以仓库里的白名单为空——壳内目前不显示任何判断陈述；测试注入合成模板。登记后由人改 `config/shell.json`（必停）。
3. **连续使用提醒存在 BFF 进程内**，不在会话表（需要迁移，见下）。两次壳内请求之间停了 ≥ 10 分钟视为休息过、从头计。
4. **选科等业务结果一律 200 + outcome**（与数据库函数一一对应），只有无权 / 不存在 404、学生会话 403、格式不对 422。
5. **路径参数 `{subject}`** 只匹配小写字母（其余参数仍是 UUID）；不认识的学科码与英语、planned 学科同一个 404。
6. **学科目录的 `open`**（“暂未开放”）由 BFF 按展示规则给：beta 且非试点订阅、或没有已发布课程版本。能不能开通仍只由 `enroll_subject` 判断。
7. **科目状态是静态清单**：道法、语文现在固定 `coming`（spec 写“适配器与已发布内容版本都到位时 live”）；到位后改清单一行。
8. **家长端今日计划**：家长孩子页也显示今日计划（判断陈述只取 guardian 可见范围、带置信度说明）；spec 第 13 节的最小范围没写这一项。
9. **同意表单**一次记隐私政策与监护人同意两条；文本版本号取 `STARBUDDY_CONSENT_VERSION`，缺省 `synthetic-0`（BFF 在 pilot / 生产拒收，待人确认 #13）。
10. **`/student` 转到壳的“今日”**，原“学生模式”页的退出按钮移到壳顶栏（测试 ID 不变）。
11. **星图仍是 3-H 的 7.1 版本**（H5 的前提未满足），套进壳后数据源、三态、水印不动，没有新增关于学生的文字。

### 需要的 db 变更（H4 没有写；需人另起必停 PR）
- **连续使用提醒的计时落库（pilot 前必须完成，必停）**：`platform.auth_session` 加“连续使用起点”与“最近确认休息”两列，函数 `auth_session_touch(token)`（返回两个时间，
  只授 BFF）与 `auth_session_ack_rest(token)`；落地后 `src/shell/rest.ts` 换成库实现，多实例、重启都一致。
- **洞察服务核对 BFF 代表的身份**：SIE-02b lessons C 的授权再核对库函数；在它之前真实洞察服务对 BFF 的 `insight.explain` 一律拒绝（壳里只是不显示陈述）。
- （可选）把 `services/bff/scripts/shell-synthetic.sql` 挪进 `db/seeds/dev/`，并按 4-G 发布闸门改成经发布函数产生已发布课程版本（协调第 4 条）。

### 待人确认（新增）
- P. **H4 契约**：`POST /internal/v1/plan` 的请求与响应形状（尤其 `target_node_ids` 可为空、`unavailable` 不带原因码、每科最多 50 条）。
- Q. **推荐理由编码分类**：内容类 `high_exam_weight`、`prerequisite_of_many`；关于学生类 `retest_scheduled`、`retest_due`、`weak_mastery`（阿鸦的四个编码 +
  spec 例子）。道法、语文适配器的编码登记后补进来；分类需产品确认。
- R. **对外模板白名单**：登记数学两维模板后写入 `config/shell.json` 的 `external_claim_templates`（`模板键@版本`）。
- S. **`{{TODO_COPY}}` 占位**：家长端置信度说明（三档）、同意正文、信任界面入口文字，需人写人审。
- T. **试用到期当天**（H3 待人确认 K）：剩余 0 天显示“试用今天到期”；续期任务没跑到、周期已过时同样显示这一句。
- U. **每会话每分钟 30 次 `insight.explain`**、每页最多 10 条、单次 1.5 秒：阈值在配置里，需产品确认。
- V. **今日计划超时**：学习服务单科 2 秒（含重试最多 5 秒）、BFF 整体 8 秒；超时的科“今天暂无安排”。
- W. **陈述—判断一致性的全站爬取**：现在由静态检查保证只有 TaskList 渲染 `data-claim-id`，BFF 集成测试覆盖“文字 = 判断陈述、带 claim_id”；
  端到端环境没有洞察服务（合成白名单也为空），全站爬取带判断的页面随 H6 / 冲刺 5 接上假洞察服务后做。

### 测试结果（本机）
- `make contract-test`（用装了 `packages/contracts/requirements-dev.txt` 的临时 venv）：通过，learning-internal v1 反例 36 条全部被拒绝。
- `make learning-test`：2441 passed（含 `tests/test_plan.py` 13 项）。
- `make bff-test`：通过（类型检查 + 全部单测与集成测试，含 H4 的 13 + 7 + 1 项）；H4 的 7 条 `页面：` 变异单独跑，全部被发现（完整 `make bff-mutation` 未跑）。
- `apps/web`：`tsc` 通过；`node --test tests/*.test.ts` 59 项通过。
- `make web-test`：许可证、`next build`、构建产物扫描、单测通过；端到端（`scripts/e2e.sh`，含新增 `e2e/shell.spec.ts` 5 项）最后一次全量 40 passed。
  中间一次全量里 `diagnosis.spec.ts` 的长流程在“提交后换到下一步”的 15 秒轮询上超时一次，单独重跑与再次全量都通过（判断为时序抖动，未改该用例）。
- `make check-fast`：通过（contract-test、taxonomy-check、attack-surface-check、sie-dictionary-check、kge-id-check）；`persona-registry-check` 通过。

### 文案自查（禁语 A–G）
`src/shell/copy.ts` 与 `src/shell/denials.ts` 的全部文案过了 `personas.guard`（platform 口径，0 命中）、`docs/design/sie/sie_lexicon.py`（0 命中，探针命中）、
比较 / 评价 / 营销与白名单外维度词表（`apps/web/tests/shell.test.ts`）。人工按 A–G 类读过：系统口吻，只陈述状态、边界与操作结果；没有评价、比较、
推断、催促，没有以连续打卡制造焦虑；休息提醒是“可以起来活动 M 分钟”（建议，不锁定）；学生端没有购买、升级、营销；关于孩子只有“孩子 N · 某年级”。
点点入口只有“点点正在准备中。”（系统口吻，不是点点的台词）。人设负责人复核待做。

### 审查处理（2026-09-30，两份审查按默认建议）
1. **今日计划限流与陈述并行**：`GET today` 加每会话每分钟限流（`today.session.minute`，`config/auth.json`，20 次；路由表新增 `session` 维度，
   键是会话令牌的带密钥 HMAC）。`insight.explain` 改为并行（`explain.concurrency` 4），整页总时限 `explain.page_deadline_ms` 3 秒，时限内没回来的
   句子不显示（`src/shell/statements.ts` 的 `resolveStatements`）。测试：并发上限与超时句子不显示、限流 429；变异“整页不设总时限”“今日计划不限流”。
   `页面：` 变异现为 11 条（新增映射、时限、限流、默认宵禁、选科周期 5 条；删掉被第 6 条编码—模板映射取代、已无法区分的“映射表外的编码也按判断显示”），
   全部被发现。
2. **休息提醒的进程内计时**：`rest_reminder.single_instance`（缺省 false）；dev / test 以外没声明单实例部署时不启用（响应 `enabled: false`）并在启动时告警
   （`rest_reminder_disabled`）。**pilot 前须完成 `auth_session` 加列迁移（必停）**，见“需要的 db 变更”。
3. **计量缺省值单处取值**：`platform.meter_guard_status` 不返回默认宵禁与留存态每天自练次数（写在 0015 / 0030 的函数体里），BFF 暂从
   `config/shell.json` 的 `meter_defaults` 单处读取并下发（`time-guard.default_curfew`、`balance.practice_daily_limit`），前端文案改为参数；
   单测核对与迁移里的值一致，静态检查 BFF 路由与壳前端不写死 22:00 / 06:00 / 5 次。**待人确认 X**：让 `meter_guard_status` 返回这些值（改函数，必停）。
4. **家长孩子页**：只有 404 走“没有找到”，401 回登录，其他错误对应区块显示“暂时无法获取。”（`copy.ts` 的 `BALANCE.unavailable`）。
5. **选科（M1）**：取主订阅时补上 `meter_main_subscription` 的时间窗（计量时钟取 `meter_guard_status` 的 `as_of`，BFF 没有 `meter_now` 的执行权）；
   `open` 只用于“暂未开放”标签，不再隐藏开通按钮，结果以 `enroll_subject` 的 outcome 为准。**长期**：由库函数预判可选状态（新增只读函数，必停），
   BFF 不再自己组合展示口径。
6. **理由编码 → 可用模板映射（M3）**：`config/shell.json` 的 `rationale_templates`（缺省空），关于学生类编码只显示登记给它的模板（且在白名单里）的判断；
   加载时核对编码属于关于学生类、模板在白名单里。**属画像可见范围，改动是必停项**。变异“理由不看编码的模板映射”。
7. **L2**：额度响应与文档里的 `curfew_soon` 改名 `curfew_near`（与计量拒绝原因 `curfew_soon` 区分）。
8. **L3**：学习服务里 `estimated_minutes` 超过 600 时该科按 `unavailable` 处理，不截断（`tests/test_plan.py` 新用例）；契约描述同步。
9. **合成种子**挪到 `db/seeds/dev/shell-synthetic.sql`，`apps/web/scripts/e2e.sh` 路径随之更新；请人审。
10. **L4**：学习服务的 plan 直读 `kge.skill_link` / `kge.map_version` / `kge.knowledge_node`（0011 已授予 `starbuddy_learning`），与“知识服务是 kge.* 的读口”
    的分工是否一致，**待 ADR**（KGE 代码位置与读写边界）。**L5**：`packages/contracts/README.md` 文件表补了 `learning-internal.v1.yaml`；plan 的契约描述
    写明候选开关关闭时返回 200 + 各科 `unavailable`。
11. **不改、登记待人确认**：
    - Y. 家长孩子页的今日计划：任务标题（知识节点名）与任务类型（如“复测”）本身不是判断陈述，但对家长可能暗示孩子的掌握情况，是否越过规则 11 的边界需人定；
    - Z. 同意表单一次记隐私政策与监护人同意两条，不按孩子年龄区分（满 14 周岁不需要监护人同意那一条），需合规轨确认。

### 审查处理后的测试结果（本机）
- `CONTRACT_STRICT=1 make contract-test`（临时 venv）：通过。`make learning-test`：2442 passed。`make bff-test`：通过；`页面：` 变异 11 条全部被发现。
- `make web-test`：许可证、`next build`、构建产物扫描、单测（61 项）、端到端 40 passed。
- `make check-fast`：contract-test、taxonomy-check、sie-dictionary-check、kge-id-check 通过；`attack-surface-check` 在工作区里失败 6 处，
  全部是另一个代理进行中的 SIE-03 改动（`insight.raise_dispute`、`insight.submit_exam_score`、`platform.execute_due_erasures` 的 scope.md 与授权快照不一致），
  换回 HEAD 版本的 scope.md / 授权快照后通过；与 H4 无关。审查处理期间 0032 草稿一度无法执行（另一个代理在改），当时的测试在排除 0032–0035 的临时副本里跑，
  最后一轮全部在工作区里重跑。
