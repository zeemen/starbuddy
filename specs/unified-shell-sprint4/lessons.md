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
- B. **收回旧入口**：0002 的 `signup_guardian`、`lookup_accounts_by_phone` 仍授 BFF（H1 已不用；前者能不经验证码注册账号）。建议 H2 一起收回（改授权，必停）。
- C. **请求体里注入 `account_id`**：BFF 对未知键一律 422（与事件、建档接口一致），与验收“结果与不注入时相同”的字面要求不同；请求头与查询参数注入已按要求忽略。H6 生成注入矩阵时定口径。
- D. **同意的“最新一条”口径**：不区分是哪位家长写的——任一已绑定家长撤回，学生模式就打不开。是否要按家长分别算，需合规轨确认。
- E. **`contact_support` 是否可区分**：只有码对了（持有该号码）才会看到，不对外泄露同号账号数；若要求与普通失败完全相同，改一行即可。
- F. **客户端 IP**：`BFF_TRUST_CLIENT_IP=1` 时 BFF 信 apps/web 转交的 `x-sb-client-ip`，apps/web 取 `x-forwarded-for` 的第一项；这要求部署层的反向代理覆盖（而不是追加）这个头，否则每 IP 限流可被伪造绕过。端到端没开（全部按本机地址计）。随 ADR 10 部署拓扑定。
- G. **数据库日志**：`auth_otp_login` 的参数有手机号原文（验证码只有 MAC）。部署时 BFF 登录角色要关语句与参数日志（同 0020）；`account_contact` 的手机号明文与否仍待 ADR 10。
- H. **学生会话时长**：沿用家长的 7 天 / 24 小时（待人确认 #4 默认）；若按 #4 的另一个选择把学生会话缩短，只改 `config/auth.json` 的 `session.student`。

### 文案自查（禁语 A–G）
`src/auth/copy.ts` 的全部文案（登录、结果提示、家长首页、学生模式）过了 `python -m personas.guard scan --persona platform --stdin-json`（0 命中）与比较 / 评价 / 营销词表检查（`apps/web/tests/auth.test.ts`）。人工按 A–G 类逐条读过一遍：系统口吻、只陈述操作与结果，没有评价、比较、推断、催促或诱导；关于孩子只出现“孩子 N · 某年级”。人设负责人复核待做。
