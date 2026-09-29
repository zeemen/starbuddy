# G 流冲刺 1 · PR 4：审核台 staff 登录（设计细化）

状态：**草稿，等人确认后才开始实现**。确认人、日期：＿＿＿
上位：`spec.md` 范围“PR 4（`[NEEDS-HUMAN]`：权限）：staff 登录（按待人确认 #3 的决定）”。#3 已按默认确认：审核台后端单独实现 staff 登录，
账号由人建，口令 + 第二因素，会话只在审核台后端，只部署在 dev / staging；pilot 前随 PLT-01 修订的“后台人员按岗位 RBAC 与审计”统一。
本文件只细化 PR 4 的做法，不改 spec 的范围。

## 目标
一名教研用自己的 staff 账号（登录名 + 口令 + 验证器 App 的 6 位码）登录审核台，独立完成 v2.0 验收的“一次节点审核发布”；
身份只来自真实会话，删除 PR 3 的开发 staff 身份桩（前端 `src/dev/staff.ts`、`sb_dev_staff`、后端 `devauth.py`）。

## 现状
- 没有任何 staff 凭据、会话、限流表；staff 账号只能由人用 SQL 建（`platform.account.kind = 'staff'`）。
- 审核台后端的认证器可注入（`review_console/app.py` 的 `StaffAuthenticator`），默认 `deny_all`；签字、发布、撤下在库里再核对 active staff。
- pgcrypto 已由 0009 装在 `starbuddy_ext`（`crypt` / `gen_salt('bf')`、`hmac`、`gen_random_bytes` 都可用），不需要新依赖。
- 4-H（家庭登录，草稿）的做法：凭据核对与建会话在同一个数据库函数里，令牌只存哈希，应用只转交令牌。本设计沿用。

## 设计（默认做法，待人确认的点标在第 8 节）

### 1. 迁移 `0020_platform__staff_auth`（只改 platform，`[NEEDS-HUMAN]`）
- `platform.staff_credential`：`account_id`（PK，FK account，触发器要求 kind = staff）、`login_name`（唯一，`^[a-z][a-z0-9_.-]{2,31}$`）、
  `password_hash`（bcrypt，cost 12）、`totp_secret`（bytea，20 字节）、`failed_count`、`locked_until`、`updated_at`。
  **没有任何应用角色能读这张表**（RLS 开启且强制，无策略；只有函数属主 `starbuddy_authz` 能读写）。
- `platform.staff_session`：`id`、`token_hash`（sha256，唯一）、`account_id`、`created_at`、`last_seen_at`、`expires_at`、`revoked_at`。
  令牌本身不入库。同样只有函数属主能读写。
- 函数（SECURITY DEFINER，属主 authz，`search_path` 固定，REVOKE PUBLIC，只授 `starbuddy_review`）：
  - `platform.staff_login(login_name, password, totp_code) → text`：在一个函数里完成“查凭据 → 锁定检查 → 口令核对（bcrypt）→
    6 位码核对（RFC 6238，30 秒步长，前后各容 1 步，同一步的码不能重复使用）→ 建会话 → 返回令牌”。
    失败一律同一个错误（不区分“没有这个人”“口令错”“码错”“已锁定”），连错 5 次锁 15 分钟；账号 status 不是 active 时拒绝。
  - `platform.staff_session_resolve(token) → uuid`：令牌哈希查会话，未撤销、未过期（绝对 12 小时、空闲 30 分钟）、账号仍是 active staff 才返回账号，并刷新 `last_seen_at`。
  - `platform.staff_logout(token)`：撤销本会话。
- 凭据只能由人建：`platform.staff_enroll(account_id, login_name, password, totp_secret)` 与 `platform.staff_reset(...)`
  **不授给任何应用角色**，只能由迁移执行者（人）在库里调用；口令长度至少 12 位。
- DB 测试（`db/tests/79c_staff_auth.sql`）：锁定、统一错误、码重放、过期与空闲、停用账号、非 staff、应用角色读不到凭据与会话、
  review 以外的角色调不了登录；变异测试覆盖每条检查；授权快照与攻击面清单登记新函数。

### 2. 审核台后端
- 新增未认证路由 `POST /review/v0/auth/login {login_name, password, code}` → 调 `staff_login`（review 连接），成功返回令牌；
  失败一律 401 `LOGIN_FAILED`；请求体大小与字段格式先校验。`POST /review/v0/auth/logout` 撤销当前会话。
- 认证器 `session_auth`：读请求头 `x-review-session`（前端服务端转交），调 `staff_session_resolve`，得到 staff 账号。
  替换 `dev_staff_auth`；`devauth.py` 删除。
- 日志不记令牌、口令、码与登录名（只记路由、状态码、错误码）。

### 3. 前端（apps/web）
- `/review/login`：登录名、口令、6 位码三个字段，server action 调后端登录，成功后写 cookie：
  `__Host-sb_staff`（`HttpOnly; Secure; SameSite=Strict; Path=/`；本地 http 开发用不带前缀的名字，只在开发构建里允许），
  有效期与会话一致；页面与 server action 从 cookie 取令牌转交后端，前端不判断身份。
- 页头显示当前登录名与“退出”；未登录进 `/review*` 跳 `/review/login`。
- 删除 `/dev/login` 的“合成教研”一栏、`/dev/session?staff=`、`src/dev/staff.ts`、`devStaffFromCookie`、`review-session.ts` 的开发分支。
- 保留 PR 3 的 Host 检查与 server action 同源校验；登录表单同样走 server action（挡登录 CSRF）。

### 4. 测试与种子
- 合成种子 `review-synthetic.sql` 改为经 `staff_enroll` 给 T1、T2 建凭据（合成口令与合成 TOTP 密钥写在种子与测试里，只用于一次性库）。
- 端到端：用登录页登录（测试里按合成密钥算 6 位码），走完 PR 3 的全流程；另测错口令、错码、锁定、退出后回到登录页、会话过期。
- 后端单测：登录失败统一 401、令牌不进日志、认证器拒绝缺失与伪造的令牌。

### 5. 自审修改人（PR 2 安全审查 A-3，可选，见待人确认 #5）
有了真实会话后，可以记“谁改了草稿”：改节点时在同一事务里经 authz 函数写 `platform.draft_edit`（staff、版本、摘要前后）；
`kge.sign_review` 拒绝“最近修改人 = 签字人”。默认**本 PR 不做**，只在 lessons 记录，等人决定。

### 6. 不做
- 岗位 RBAC（教研 / 运营 / 客服 / 财务）与查看学生数据的审计：PLT-01 修订统一做；审核台只有公共层内容。
- 口令找回、自助改口令、设备管理；短信或邮件验证码（需要第三方服务）。
- pilot 与生产部署（CLAUDE.md 规则 9）。

## 7. 验收
- [ ] 应用角色（含 review、kge、bff、learning）读不到 `staff_credential`、`staff_session`；登录与会话函数只授 review；凭据建立与重置函数不授任何应用角色。
- [ ] 错口令、错码、不存在的登录名、锁定中、停用账号：同一个错误；连错 5 次后正确凭据也被拒 15 分钟；同一个 6 位码不能用两次。
- [ ] 会话：绝对 12 小时、空闲 30 分钟后失效；退出后令牌失效；账号停用后已有会话立即失效。
- [ ] 前后端找不到开发 staff 身份桩（源码与构建产物）；端到端经真实登录走完审核发布全流程。
- [ ] 严格 `make check` 通过；安全审查、契约审查意见处理完。

## 8. 待人确认
1. **第二因素用验证器 App（TOTP）**（默认；不需要第三方服务）。另一个选择：staging 只用口令、pilot 前再加第二因素。
2. **会话时长**：绝对 12 小时、空闲 30 分钟（默认）。
3. **锁定策略**：连错 5 次锁 15 分钟（默认，与规格书 V1.2 学生 PIN 一致）。
4. **凭据由人用 SQL 建**（默认，staging 上由工程负责人执行 `staff_enroll`，把 TOTP 密钥当面交给教研）；是否需要一个命令行工具生成密钥与二维码（只在本机运行）。
5. **自审修改人**（第 5 节）：本 PR 做还是以后做；做的话“改的人不能签”是否只限结构性变更。
6. TOTP 密钥明文存在只有函数属主能读的表里（默认）；另一个选择是应用层加密（要引入加密库与密钥管理，改动更大）。
