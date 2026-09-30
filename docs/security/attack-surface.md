# 攻击面清单（家庭隔离 · 第 1 轮）

来源：`specs/household-pentest-sprint3/spec.md` 第 2 节。编号稳定，报告、`findings.csv`、`coverage.md` 都按编号引用。

## 怎么读这张表

- **层**：
  - 1 = 数据库授权层；
  - 2 = 服务间接口；
  - 3 = 用户入口。本轮不测第 3 层，标“第二轮”的条目等 4-H 与 BFF 鉴权实现后再测。
- **已有自动化断言**：写法有三种。`docs/security/check_attack_surface.py`（`make check-fast`）会核对每条引用都真实存在，引用失效时检查失败。
  - `ref:路径#片段`：该文件里含这段文字，通常是断言消息或测试名；
  - `mut:名称`：`db/tests/mutations.sh` 里有这个变异，而且 `make check` 证明它会被测试发现；
  - `mut-bff:名称`：同上，对应 `services/bff/tests/mutations.sh`。
- 没有自动化断言的条目，这一列写“无”，并在“渗透测试要做的”一列标“人工”或“第二轮”。
- `db/tests/mutations.sh` 里名字以“攻击面：”开头的变异，只跑 80（在渗透测试夹具上），而且第一条失败的必须是登记的那条 80 断言，证明是 80 本身发现的，不是别的测试或快照先拦下。
- 渗透测试环境里，80 的可见性矩阵与函数越权矩阵还会在扩展夹具上再跑一遍。扩展夹具有 16 个账号，含兄弟姐妹、禁用账号、已解除的绑定、同号的第二账号（`db/pentest/selftest.sh`，`make pentest-env-test`）。
- **已知遗留**：已登记的风险，报告里单列，不重复计为新发现；执行方认为严重度更高时照常分级。

## A. 家长越权（跨家庭）

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| A1 | 读其他家庭的每一张表和视图：档案、证据、作答、观测、错因、掌握度、判分与维度、同意、选科、订阅、额度桶、预留、流水、余额视图 | 层 1；以任一家长账号为 `app.account_id` | 每张表 ENABLE + FORCE RLS，策略走 `can_read_student` / `is_linked_guardian`；余额视图 `security_invoker` | `ref:db/tests/80_attack_surface.sql#攻击面：可见性矩阵——BFF 可读的每张表`；`ref:db/pentest/selftest.sh#80_attack_surface 在渗透测试夹具上全部通过`；`ref:db/tests/20_rls_household.sql#gA 只看到 S1 的证据`；`ref:db/tests/20_rls_household.sql#gA 只看到 S1 判分的维度`；`mut:攻击面：维度表对 BFF 全开`；`mut:攻击面：新表开了 RLS 却没有策略`；`ref:db/tests/60_metering.sql#家长只能看到本人已授权学生的额度桶、预留与流水`；`mut:学生档案对所有 BFF 请求可读`；`mut:作答全表可读`；`mut:判分全表可读`；`mut:家长可以看所有流水`；`mut:余额视图以属主身份绕过 RLS`；`mut:攻击面：BFF 多了一条 USING (true) 策略` | 人工：按 `db/pentest/accounts.md` 逐个家长账号尝试读别家数据。途径不限于直接 SELECT，还包括 JOIN 到豁免表、函数返回值、报错信息、`EXPLAIN` 与统计信息 | — |
| A2 | 写其他家庭的学生：同意记录、家长自述、选科与暂停、开通订阅 | 层 1；任一家长 | 写入走函数或 WITH CHECK；`enroll_subject`、`pause_subject` 用 `can_read_student`；`meter_open_subscription` 要求 `is_linked_guardian` | `ref:db/tests/20_rls_household.sql#gA 不能给别家的学生写同意记录`；`ref:db/tests/20_rls_household.sql#gB 不能给别家的学生写家长自述`；`ref:db/tests/70_metering_functions.sql#不能给别家的学生暂停选科`；`ref:db/tests/70_metering_functions.sql#不能给别家的学生开通订阅`；`ref:db/tests/80_attack_surface.sql#攻击面：函数越权矩阵——对另一户的对象与不存在的对象结果相同且被拒绝`；`mut:同意记录任意写`；`mut:选科不校验授权`；`mut:暂停选科不校验授权`；`mut:开通不校验家长` | 人工：逐个写入口，用别家 student_id 调用，比较返回与“不存在”是否相同 | — |
| A3 | 家长替孩子发起消耗（预留、结算、释放） | 层 1；已绑定家长 | `meter_reserve` / `meter_settle` / `meter_release` 只认学生本人 | `ref:db/tests/70_metering_functions.sql#家长不能代孩子发起消耗`；`ref:db/tests/70_metering_functions.sql#家长不能替孩子结算`；`ref:db/tests/70_metering_functions.sql#家长不能替孩子释放`；`mut:预留不校验学生本人`；`mut:结算释放不校验学生本人` | 人工：复测；另查有没有别的函数间接触发消耗 | 2-E 待人确认 #1（默认只许学生本人） |
| A4 | 被学生解除绑定的家庭创建者仍能读家庭记录、往家庭里加学生 | 层 1；已解除绑定的创建者 | `can_read_household` 认创建者身份 | `ref:db/tests/20_rls_household.sql#gA 只看到自己的家庭`；`ref:db/tests/80_attack_surface.sql#platform.household`；`mut:攻击面：家庭表对登录用户全开` | 人工：用 gR 之类的账号确认影响范围，包括能不能借新建的学生看到任何已有数据 | schema spec 待人确认 #19 |
| A5 | 家长自述：同一学生的另一位家长、学生本人读不到；已解除绑定的家长仍能删除自己写的自述 | 层 1 | `guardian_report` 只按作者读写 | `ref:db/tests/20_rls_household.sql#同一学生的另一位家长看不到这条家长自述`；`ref:db/tests/20_rls_household.sql#学生读不到家长自述`；`ref:db/tests/20_rls_household.sql#gB 删不掉 gA 写的家长自述`；`mut:学生可以读家长自述`；`mut:家长自述别的家长可读`；`mut:家长自述任意删除` | 人工：确认解除绑定后删除自述是否预期行为，结论写进报告 | `greport_delete` 只看作者（待判断） |

## B. 学生越权

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| B1 | 读其他学生，包括兄弟姐妹 | 层 1；任一学生账号 | `is_self_student` 只认本人档案 | `ref:db/tests/20_rls_household.sql#学生 S1 只看到自己的档案`；`ref:db/tests/60_metering.sql#学生看到自己的额度与使用记录，看不到别人的`；`ref:db/pentest/selftest.sh#80_attack_surface 在渗透测试夹具上全部通过` | 人工：以 S3a 账号读 S3b、SX 的数据（同一家庭） | — |
| B2 | 冒充家长：建档、签同意、兑换邀请、开通订阅 | 层 1；学生账号 | 这些函数要求家长身份 | `ref:db/tests/20_rls_household.sql#学生不能自建家庭并把自己绑成家长`；`ref:db/tests/20_rls_household.sql#学生不能伪造 14 岁以下监护人同意`；`ref:db/tests/20_rls_household.sql#学生账号不能兑换邀请`；`ref:db/tests/70_metering_functions.sql#学生不能给自己开通订阅`；`mut:任何账号都算家长`；`ref:db/tests/20_rls_household.sql#BFF 不能调旧注册入口 signup_guardian`；`mut:旧入口：BFF 可调旧注册` | 人工：复测，覆盖 `signup_guardian` 对已有学生账号的行为 | — |
| B3 | 写推断：掌握度、判分、复核结案 | 层 1；学生账号 | BFF 对推断表没有写权限；结案只认 staff 与复核角色 | `ref:db/tests/20_rls_household.sql#学生不能给自己写掌握度`；`ref:db/tests/20_rls_household.sql#学生不能写判分`；`ref:db/tests/40_constraints.sql#非教研账号不能结案`；`mut:学生可以写掌握度`；`mut:结案不校验教研身份` | 人工：复测 | — |
| B4 | 学生可以解除任何家长的绑定，包括家庭创建者 | 层 1；学生账号 | 设计如此 | 无 | 人工：确认影响（被解除的家长预签邀请失效，见 D2），写进已登记风险 | schema spec 待人确认 #7 |
| B5 | 学生本人可以为自己签发家长邀请，任何兑换它的家长账号都成为这个学生的家长 | 层 1；学生账号，加上一个家长账号 | 设计如此（`0002` 注释：学生本人或已绑定的有效家长可签发） | 无 | 人工：以 S3a 账号签发邀请，用 gC 兑换，记录结果；请人判断没有家长参与时是否可以接受 | 待判断（实现时安全审查补充） |

## C. 同手机号

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| C1 | S2 与 gA 同号：授权判断不能读手机号 | 层 1；gA | 授权只看 `guardian_student_link` | `ref:db/tests/20_rls_household.sql#同手机号不能跨档案：S2 的账号与 gA 同号，gA 仍看不到 S2`；`ref:db/tests/60_metering.sql#同手机号不能跨档案看计量数据`；`ref:db/tests/70_metering_functions.sql#别的学生不能扣这个学生的额度（同手机号也不行）`；`mut:授权改成按手机号关联` | 人工：复测 | — |
| C2 | 同一手机号可注册多个家长账号，同号的第二账号可以绕过“签发人不能兑换自己的邀请” | 层 1；gB2（与 gB 同号） | 只防同一账号 | `ref:db/tests/20_rls_household.sql#签发人不能兑换自己签的邀请`；4-H 起登录拒绝同号多个家长账号：`ref:db/tests/79k_auth_session.sql#同号多个家长账号：拒绝登录，不建会话`；`mut:认证：同号多家长账号不拒绝` | 人工：用 gB2 兑换 gB 签发的邀请，记录结果 | schema spec 待人确认 #15 |
| C3 | `lookup_accounts_by_phone` 在未登录时可被调用，成为“号码是否注册过”的查询通道 | 层 3 | 数据库层只返回账号 ID | `ref:db/tests/20_rls_household.sql#BFF 不能调旧查号入口 lookup_accounts_by_phone`；`mut:旧入口：BFF 可按号码查账号`（0031 收回授权）；4-H 登录入口（0029）不再区分号码是否注册：`ref:db/tests/79k_auth_session.sql#已注册与未注册号码：申请结果相同`；`ref:services/bff/tests/auth.integration.test.ts#发验证码：已注册与未注册号码返回逐字节相同的 202` | 第二轮：BFF 限流与未登录调用 | schema spec 待人确认 #18 |

## D. 邀请兑换

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| D1 | 邀请码：库内生成，64 位十六进制，只存 sha256，最长 7 天，一次性 | 层 1 | `create_guardian_invite` | `ref:db/tests/20_rls_household.sql#邀请码由库内生成（64 位十六进制）`；`ref:db/tests/20_rls_household.sql#邀请有效期不能超过 7 天`；`ref:db/tests/20_rls_household.sql#邀请不能重复使用`；`ref:db/tests/20_rls_household.sql#BFF 不能直接读邀请表`；`mut:邀请有效期不设上限` | 人工：复测；确认原文不出现在任何表、日志、报错里 | — |
| D2 | 兑换校验：签发人仍有权限；签发人不能自兑；过期、已用无效；解除绑定时作废该家长签发的未用邀请 | 层 1 | `redeem_guardian_invite` | `ref:db/tests/20_rls_household.sql#签发人已失去权限时，邀请不能兑换`；`ref:db/tests/20_rls_household.sql#过期邀请无效`；`ref:db/tests/20_rls_household.sql#被解除的家长预签的邀请失效`；`mut:兑换不校验过期`；`mut:兑换不校验签发人是否仍有权限`；`mut:签发人可以兑换自己的邀请`；`mut:解除绑定不再作废邀请、不再锁定 revoked_at` | 人工：用 accounts.md 的未用、已用、过期三张邀请与禁用的 gX 组合测 | 见 C2 |
| D3 | 暴力猜测邀请码 | 层 3 | 数据库层没有次数限制 | 无 | 第二轮：BFF 限流 | — |
| D4 | 错误信息不能区分“不存在”与“已用、过期” | 层 1 | 统一返回 `invite invalid, used or expired` | `ref:db/tests/20_rls_household.sql#错误的邀请码无效` | 人工：逐种失败比较 SQLSTATE、消息、耗时 | — |

## E. 账号状态

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| E1 | 禁用的家长、学生读不到任何数据；已签发的邀请不能再兑换 | 层 1；gX、SX 账号 | 授权函数只认 `status = 'active'` | `ref:db/tests/20_rls_household.sql#禁用的学生账号读不到任何数据`；`ref:db/tests/20_rls_household.sql#签发人已失去权限时，邀请不能兑换`；`ref:db/pentest/selftest.sh#80_attack_surface 在渗透测试夹具上全部通过`；`mut:禁用账号仍可读` | 人工：以 gX（有有效绑定但已禁用）测读、写、兑换 | — |
| E2 | 未设身份、不存在的 UUID、非法字符串 | 层 1 | `current_account_id()` 返回 NULL 或报 22P02 | `ref:db/tests/20_rls_household.sql#未登录：读不到任何学生档案`；`ref:db/tests/80_attack_surface.sql#攻击面：身份不是合法 UUID 时报错，不返回数据`；`ref:db/tests/80_attack_surface.sql#攻击面：可见性矩阵——BFF 可读的每张表` | 人工：检查报错信息是否带出任何数据 | — |
| E3 | staff 账号在 BFF 角色下能看到什么；审核台（starbuddy_review）经复核队列能看到什么、能改什么 | 层 1；staff | 复核队列函数（0023：`take`、`release`、`resolve_review_task`、`review_queue`、`review_task_detail`）只认 active staff，只授 review；审核台不能直接读队列表，读函数只给学生不透明指针、不给作答原文与判断 value；只有领取人能结案，结案码在白名单里 | `ref:db/tests/40_constraints.sql#非教研账号不能结案`；`ref:db/tests/79e_review_queue.sql#审核台不能直接读队列表`；`ref:db/tests/79e_review_queue.sql#队列与详情只给学生的不透明指针`；`ref:db/tests/79e_review_queue.sql#别人领取的不能结案`；`ref:db/tests/79e_review_queue.sql#BFF 不能调用审核台读函数`；`mut:复核队列：审核台可直接读队列表`；`mut:复核队列：详情带回 student_id`；`mut:复核队列：结案不核对领取人`；`mut:攻击面：账号表对登录用户全开`；`ref:db/tests/20_rls_household.sql#BFF 不能读复核队列`；`ref:db/tests/80_attack_surface.sql#攻击面：可见性矩阵——BFF 可读的每张表` | 人工：以 staff 为身份、`bff_app` 登录逐表读；以 `review_app` 逐个调用复核队列函数，核对返回里有没有可识别信息 | 学生指针无密钥（`specs/review-queue-sprint2/lessons.md` 待人确认） |

## F. 服务间令牌

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| F1 | 学习服务：没有令牌、令牌错误、前缀或大小写变形、超长头、`Bearer` 后多空格都要 401，而且不读请求体 | 层 2；能访问内网 | 先摘要再定长比较；鉴权在读请求体之前 | `ref:services/learning/tests/test_auth.py#test_bad_token_is_rejected_without_reading_body`；`ref:services/learning/tests/test_auth.py#test_body_read_detector_works_with_valid_token`；`ref:db/pentest/selftest.sh#没有令牌访问` | 人工：在真实启动的服务上复测。尤其是 uvicorn 对头部空白的处理、重复的 authorization 头、HTTP/1.0 | — |
| F2 | 适配器：同 F1；只监听回环地址 | 层 2 | 同 F1；`127.0.0.1` | `ref:adapters/aya/tests/http.test.ts#令牌变形、别的方案、学习服务令牌混用`；`ref:adapters/aya/tests/http.test.ts#鉴权失败时不读请求体`；`ref:adapters/aya/tests/http.test.ts#超长 authorization 头`；`ref:db/pentest/selftest.sh#不在非回环地址` | 人工：复测 | — |
| F3 | 令牌混用：学习服务令牌打适配器、适配器令牌打学习服务 | 层 2；持有其中一个令牌 | 两个令牌不同（环境启动时随机生成、自检互不相同） | `ref:services/learning/tests/test_auth.py#适配器令牌打学习服务（混用）`；`ref:adapters/aya/tests/http.test.ts#学习服务令牌打适配器`；`ref:db/pentest/selftest.sh#适配器令牌打学习服务 → 401`；`ref:db/pentest/selftest.sh#学习服务令牌打适配器 → 401` | 人工：复测；部署时检查两个令牌确实不同。适配器进程内测试里的“学习服务令牌”对适配器只是另一个错误令牌；真正的混用只在 selftest 的完整模式里测（CI 以严格模式运行，必跑） | — |
| F4 | 令牌持有者可以用任意 `student_ref` 写任意学生；可以拿别人的 `idempotency_key` 探测（返回 409） | 层 2；持有学习服务令牌 | 设计如此（服务间令牌是全权限） | `ref:services/learning/tests/test_ingest.py#test_same_key_different_body_conflicts_and_writes_nothing` | 人工：只记录，写进已接受风险 | 已接受风险（2-E lessons 阻断 A） |
| F5 | 恶意适配器：`media_ref`、`prior_evidence`、错误码夹带文本、超大响应 | 层 2；被攻破的适配器 | 1-C 已修复：结果逐项校验，响应上限 5 MB | `ref:services/learning/tests/test_ingest.py#media_ref_injected`；`ref:services/learning/tests/test_ingest.py#test_prior_evidence_must_exist_for_this_student`；`ref:services/learning/tests/test_ingest.py#test_upstream_code_must_look_like_a_code`；`ref:services/learning/tests/test_ingest.py#test_rejected_results_write_nothing` | 人工：复测。超大响应（超过 `MAX_RESPONSE_BYTES`）目前没有自动化断言，要重点测 | — |
| F6 | 令牌轮换、存放位置、是否出现在日志 | 部署 | 启动时校验长度；日志白名单 | `ref:services/learning/tests/test_ingest.py#test_config_refuses_unsafe_settings`；`ref:services/learning/tests/test_leak.py#test_logs_hold_only_whitelisted_fields` | 人工：本轮只审查代码与配置；部署时再测（第二轮） | — |

## G. 计量函数

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| G1 | 预留、结算、释放只能由学生本人发起；拿别人的 `usage_id` 结算或释放，要与“不存在”返回同样的 `not permitted` | 层 1 | `meter_authorize_usage` | `ref:db/tests/70_metering_functions.sql#不存在的 usage_id 与无权限同样拒绝（不泄露是否存在）`；`ref:db/tests/80_attack_surface.sql#攻击面：函数越权矩阵——对另一户的对象与不存在的对象结果相同且被拒绝`；`ref:db/tests/80_attack_surface.sql#GET STACKED DIAGNOSTICS v_detail = PG_EXCEPTION_DETAIL`；`mut:结算释放不校验学生本人`；`mut:攻击面：函数经 DETAIL 泄露学生是否存在`；计量只读函数 `meter_guard_status`（0030）：`ref:db/tests/79l_meter_guard.sql#计量只读：学生不存在、参数为空与无权逐字相同（不泄露存在性）`；`ref:db/tests/79l_meter_guard.sql#计量只读：已解除绑定的家长被拒`；`mut:计量只读：去掉授权检查` | 人工：从 key 推出别人的 `usage_id` 后调用，比较返回、SQLSTATE、耗时 | — |
| G2 | 同 key 冲突不泄露已有记录；复看的 `dedup_key` 含学生，不能跨学生探测 | 层 1 | `meter_same_key`；去重键含学生 | `ref:db/tests/70_metering_functions.sql#别的学生撞上同一个 key → 冲突，而且不透露已有记录的任何信息`；`ref:db/tests/70_metering_functions.sql#另一个学生看同一道题：各扣各的（按学生去重）`；`ref:db/tests/60_metering.sql#复看只能引用同一学生的首次`；`mut:去重键不含学生`；`mut:同 key 不比学生`；`mut:复看可以跨学生引用` | 人工：复测 | 1-E 待人确认 #3 |
| G3 | 开通：只允许 `trial`、`pilot_virtual`；档位由来源决定；试点必须在名单里 | 层 1；家长 | `meter_open_subscription` | `ref:db/tests/70_metering_functions.sql#付费订阅不能经这个函数开通`；`ref:db/tests/70_metering_functions.sql#不在试点名单里的家庭开不了试点额度`；`ref:db/tests/70_metering_functions.sql#试点固定 PRO`；`mut:试点不校验名单`；`mut:试点档位不固定` | 人工：以 g4（试点家庭）与非试点家长各测一遍 | — |
| G4 | 结算数量由 BFF 决定，数据库只校验 0 到预留数 | 层 1 / 层 3 | `meter_settle` 校验上限 | `ref:db/tests/70_metering_functions.sql#结算数量不能超过预留`；`mut:结算可以超过预留` | 第二轮：BFF 接口不能让前端决定这个数 | 2-E 待人确认 #9 |
| G5 | BFF 不能直接写计量表；学习服务、复核角色不能调用计量函数 | 层 1 | 只授 SELECT（部分列）；函数 EXECUTE 按角色授予 | `ref:db/tests/60_metering.sql#BFF、学习服务、复核、定时任务角色对计量表都没有直接写权限`；`ref:db/tests/70_metering_functions.sql#学习服务、复核角色不能调用任何计量或选科函数`；`mut:BFF 可以直接写流水`；`mut:学习服务可以预留`；`ref:db/tests/79l_meter_guard.sql#计量只读：只授 starbuddy_bff（其他应用角色与 PUBLIC 都不能执行）`；`mut:计量只读：学习服务可调用` | 人工：以 `learning_app`、`review_app` 登录复测 | — |

## H. 定时任务角色

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| H1 | `starbuddy_scheduler` 只能调用 `meter_expire_due`、`meter_renew_due` 与抽检 `review_sample_spot_checks`（0023），没有表权限；BFF 不能调用这些函数 | 层 1 | 按角色授 EXECUTE | `ref:db/tests/70_metering_functions.sql#定时任务角色只能调用过期、续期与抽检`；`ref:db/tests/70_metering_functions.sql#定时任务角色读不到任何计量表`；`ref:db/tests/70_metering_functions.sql#BFF 不能调用过期任务`；`ref:db/tests/70_metering_functions.sql#BFF 不能调用续期任务`；`mut:BFF 可以调用过期任务` | 人工：以 `scheduler_app` 登录逐表、逐函数试 | — |
| H2 | 一个登录角色不能同时继承 scheduler 和其他应用角色 | 部署 | `0009` 执行时检查 | `ref:db/migrations/0009_platform__metering_functions.up.sql#同一个角色不能同时继承 starbuddy_scheduler`；`ref:db/tests/80_attack_surface.sql#攻击面：没有角色同时继承两个应用角色`；`ref:db/tests/80_attack_surface.sql#攻击面：应用角色没有危险属性`；`mut:攻击面：登录角色同时继承定时任务与 BFF`；`mut:攻击面：应用角色带 BYPASSRLS` | 人工：80 只在跑测试的库上检查；部署后授予的继承要靠部署清单或定期巡检 | 部署环境的角色只能靠部署检查 |
| H3 | 过期任务在没有设身份时运行，不能借此以别人的身份写入 | 层 1 | 过期任务只写 `ttl_elapsed` 流水 | `ref:db/tests/60_metering.sql#过期流水只能由定时任务写`；`ref:db/tests/70_metering_functions.sql#再跑一次没有可过期的（幂等）` | 人工：代码审查 `meter_expire_due`、`meter_renew_due` | — |

## I. SECURITY DEFINER 与目录权限

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| I1 | 28 个 SECURITY DEFINER 函数：属主 `authz`；`search_path` 固定且以 `pg_temp` 结尾；PUBLIC 不能执行；不接受外部传入的“要授权的账号” | 层 1 | 迁移统一写法 | `ref:db/tests/80_attack_surface.sql#攻击面：每个 SECURITY DEFINER 函数属主是 authz`；`ref:db/tests/80_attack_surface.sql#攻击面：授权快照与 db/tests/expected/privileges.txt 一致`；`ref:db/tests/70_metering_functions.sql#写入函数都是 SECURITY DEFINER、属主 starbuddy_authz、固定 search_path`；`mut:攻击面：SECURITY DEFINER 函数不固定 pg_temp`；`mut:攻击面：计量函数对 PUBLIC 开放`；`mut:计量函数不固定 search_path`；`ref:db/tests/80_attack_surface.sql#攻击面：BFF 可执行的 SECURITY DEFINER 函数要么在函数越权矩阵里`；`mut:攻击面：新函数的学生参数换个名字躲过越权矩阵` | 人工：按 `docs/security/pentest/scope.md` 的函数表逐个审查参数与动态行为 | — |
| I2 | 应用角色在任何 schema 上都没有 CREATE，包括 `public`、`platform`、`starbuddy_ext` | 层 1 | 迁移收回 CREATE | `ref:db/tests/80_attack_surface.sql#攻击面：应用角色在任何 schema`；`mut:攻击面：BFF 能在 platform 上建对象` | 人工：复测；第二轮在真实部署拓扑（托管 Postgres 另有 `anon`、`authenticated` 等角色）上复测 | 本地环境与托管环境有差异 |
| I3 | 动态 SQL：只有 `ensure_monthly_partitions` 用了 `format(%I, %L)`，它不是 SECURITY DEFINER，PUBLIC 不能执行 | 层 1 | 属主为迁移执行者 | `ref:db/tests/80_attack_surface.sql#攻击面：授权快照与 db/tests/expected/privileges.txt 一致` | 人工：全仓搜索 `EXECUTE`、`format(` 确认没有新的动态 SQL | — |
| I4 | 触发器函数以调用者身份执行，只有 `guard_link_update`、`check_supersede_target` 是 SECURITY DEFINER | 层 1 | 迁移统一写法 | `ref:db/tests/expected/privileges.txt#secdef\|platform.guard_link_update()`；`ref:db/tests/expected/privileges.txt#secdef\|platform.check_supersede_target()`；`ref:db/tests/60_metering.sql#计量的保护函数都不是 SECURITY DEFINER`；`mut:保护触发器改成 SECURITY DEFINER` | 人工：审查这两个触发器函数 | — |
| I5 | 没有让新建对象自动授权给应用角色的默认授权；授权快照覆盖授权人与可转授（WITH GRANT OPTION） | 层 1 | 不使用 `ALTER DEFAULT PRIVILEGES` | `ref:db/tests/80_attack_surface.sql#攻击面：没有让新建对象自动授权给应用角色或 PUBLIC 的默认授权`；`mut:攻击面：默认授权给 BFF`；`mut:攻击面：可转授的授权` | 人工：复测 | — |

## J. 分区与视图

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| J1 | 直接读分区：`evidence_event_*`、`attempt_*`、`usage_ledger_*` 不开 RLS，任何应用角色都不能有权限，以后新建的分区也一样 | 层 1 | 分区不授权 | `ref:db/tests/80_attack_surface.sql#攻击面：子分区对任何应用角色都没有权限`；`ref:db/tests/20_rls_household.sql#不能绕过父表直接读证据分区`；`ref:db/tests/60_metering.sql#流水分区没有授权给任何应用角色`；`mut:攻击面：新分区授权给 BFF`；`mut:分区授权给 BFF`；`mut:流水分区授权给 BFF` | 人工：复测；第二轮在真实部署拓扑上复测 | 本地环境与托管环境有差异 |
| J2 | `entitlement_balance` 必须是 `security_invoker`；两个对账视图不授权给 BFF | 层 1 | 视图选项与授权 | `ref:db/tests/60_metering.sql#余额视图随调用者的 RLS`；`ref:db/tests/60_metering.sql#对账视图只给运维，不授权给 BFF`；`ref:db/tests/60_metering.sql#预留对账视图只给运维，不授权给 BFF`；`mut:余额视图以属主身份绕过 RLS`；`mut:预留对账视图授权给 BFF`；`ref:db/tests/80_attack_surface.sql#攻击面：物化视图与外部表对任何应用角色都没有权限`；`mut:攻击面：物化视图授权给 BFF` | 人工：复测 | — |
| J3 | 列级授权：预留的幂等键与指纹、流水的来源 key 都不给 BFF 读 | 层 1 | 列级 GRANT | `ref:db/tests/60_metering.sql#BFF 读不到预留的幂等键`；`ref:db/tests/60_metering.sql#BFF 读不到内容指纹与去重键`；`ref:db/tests/70_metering_functions.sql#BFF 读不到流水的来源 key`；`mut:BFF 可以读幂等键`；`mut:BFF 可以读流水来源 key` | 人工：试 `SELECT *`、`RETURNING`、`COPY`、`row_to_json` 等绕过 | — |

## K. 信息泄露

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| K1 | 数据库约束报错带 “Failing row contains (...)”；学习服务不转发，但 Postgres 服务端日志会记下 | 层 1 / 部署 | 学习服务只回错误码 | `ref:services/learning/tests/test_ingest.py#db_constraint` | 人工：检查服务端日志配置（部署时，第二轮） | 1-C 待人确认 #14 |
| K2 | 学习服务与适配器的日志只记方法、状态码、错误码、request_id、耗时 | 层 2 | 日志白名单 | `ref:services/learning/tests/test_leak.py#test_logs_hold_only_whitelisted_fields`；`ref:adapters/aya/tests/leak.test.ts#日志只含方法、状态码、错误码、request_id、耗时` | 人工：在渗透测试环境里看 `learning.log`、`aya.log` | — |

## L. 资源与滥用

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| L1 | 注册与建档没有数量和频率上限 | 层 3 | 无 | 无 | 第二轮：BFF 限流 | schema spec 待人确认 #17 |
| L2 | 请求体上限：学习服务 16 MB，适配器 1 MB | 层 2 | 读请求体时计数 | `ref:services/learning/tests/test_ingest.py#test_request_body_limit`；`ref:adapters/aya/tests/http.test.ts#请求体过大 413` | 人工：慢速发送、分块编码、压缩体 | — |

## M. 同冲刺新增的攻击面

3-H、3-D 与本冲刺并行。测试窗口开始前已合并的，一并纳入本轮；没合并的登记到第二轮。M1（3-H 星图）已实现，补上了断言引用；M2（3-D）已搁置（英语课程调整，2026-09-26），两轮都不纳入；英语方案调整后按新 spec 重新登记。

| 编号 | 攻击面 | 攻击者 / 前提 | 现有防护 | 已有自动化断言 | 渗透测试要做的 | 已知遗留 |
|---|---|---|---|---|---|---|
| M1 | 星图读接口 `GET /bff/v0/students/{id}/star-map`：跨家庭、跨学生读；解除绑定后能否读；未授权与“学生不存在”是否同样 404；响应是否带出 `p_mastery`、`confidence`、`evidence_ids`；身份能否被请求头伪造 | 层 3（真实会话） | 授权完全交给 RLS（`can_read_student`）；预先序列化的同一个 404；只返回三态展示字段；4-H H2 起挂在统一壳路由表上，身份只来自会话（`auth_session_resolve`），开发身份桩已删除 | `ref:services/bff/tests/star-map.integration.test.ts#兄妹互看、另一户家长、不存在、非法 ID：都是 404，响应体逐字节相同`；`ref:services/bff/tests/star-map.integration.test.ts#绑定撤销后立刻看不到；停用的学生账号的会话失效`；`ref:services/bff/tests/star-map.integration.test.ts#响应只含展示字段`；`ref:services/bff/tests/star-map.integration.test.ts#开发身份桩已删除`；`ref:services/bff/tests/shell-openapi.test.ts#开发身份桩已删除：源码与接口定义里没有 x-dev-account-id`；`ref:services/bff/tests/mutations.sh#未授权与不存在的响应不同`；`ref:services/bff/tests/mutations.sh#壳：身份改从 x-dev-account-id 读`；`ref:apps/web/e2e/star-map.spec.ts#兄妹互看、另一户家长、不存在的学生` | 第二轮：在真实部署拓扑上以真实会话复测（apps/web → BFF） | 部署拓扑随 ADR 10；星图数据来源切换（7.2）随 H5 |
| M3 | 审核台 staff 登录（G 流 PR 4）：口令爆破、6 位码重放、锁定他人账号（DoS）、会话固定与过期、凭据与会话表被应用角色读到、DNS 重绑定页面直接调登录接口 | 层 3；知道登录名的外部人员，或同机浏览器里的恶意页面 | 核对与建会话在同一个 SECURITY DEFINER 函数（迁移 0020）；失败统一返回、连错 5 次锁 15 分钟、失败计次只看 15 分钟；码按时间步防重放；令牌只存 sha256；一账号一会话；绝对 12 小时、空闲 30 分钟；凭据与会话表强制 RLS 无策略；后端只认本机 Host；登录限流在部署层 | `ref:db/tests/79c_staff_auth.sql#同一个 6 位码不能用两次`；`ref:db/tests/79c_staff_auth.sql#连错 5 次锁 15 分钟`；`ref:db/tests/79c_staff_auth.sql#锁定期间正确凭据也被拒`；`ref:db/tests/79c_staff_auth.sql#签字身份读不到凭据表`；`ref:db/tests/79c_staff_auth.sql#空闲超过 30 分钟失效`；`ref:db/tests/79c_staff_auth.sql#满 12 小时失效（绝对时长）`；`ref:db/tests/79c_staff_auth.sql#退出后令牌失效`；`ref:db/tests/79c_staff_auth.sql#新登录撤销同一账号的旧会话`；`ref:services/learning/tests/test_review_console.py#test_session_login_flow`；`mut:staff 登录：不锁定`；`mut:staff 登录：6 位码可重放`；`mut:staff 会话：不查空闲`；`mut:staff 凭据：签字身份可读`；`mut:staff 登录：一账号多会话` | 人工：在 staging 复测部署层限流（包括向其他 /review 路径提交登录 action）；按登录名轮流试探的计时差；锁定他人账号的影响 | 锁定 DoS 与登录名枚举的计时差靠部署层限流兜住（release-gate lessons PR 4 待人确认 #4）；过期会话不清理 |
| M4 | 建档（SIE-01）`/bff/v0/students/{id}/onboarding[/answers\|exams\|textbooks\|progress]`：跨家庭或兄妹写入、学生冒充家长（或反之）、两方互看对方填写的原文、同一学生的两位家长互看、学生读排名区间、字典以外的字段或超长自由文本、直接读事实表、覆盖历史答案、报表身份读明细 | 层 3；已登录的学生、家长或兄妹账号 | 写入与读取全在 SECURITY DEFINER 函数里按当前账号核对（迁移 0021、0022）；BFF 对 `insight.*` 无表授权，只经 `insight.onboarding_view` 按身份裁剪；字段白名单与字典一致、取值按形状校验、自由文本 ≤ 200 字；改答案只追加（触发器）；排名区间与难度说明只收家长录入（表约束）；教材版本按填写账号各存一行；报表身份只能调聚合函数，不足 5 人不出数；步骤白名单与写入限量；请求体 16 KB 上限 | `ref:db/tests/79d_onboarding.sql#学生不能给别的学生写自述`；`ref:db/tests/79d_onboarding.sql#学生不能冒充家长写家长观察`；`ref:db/tests/79d_onboarding.sql#学生看不到家长说的 120 分钟`；`ref:db/tests/79d_onboarding.sql#家长只看到自己写的观察，看不到学生自述原文`；`ref:db/tests/79d_onboarding.sql#两位家长互相看不到对方写的观察`；`ref:db/tests/79d_onboarding.sql#学生只看到自己录的，且看不到排名区间与难度说明`；`ref:db/tests/79d_onboarding.sql#字典以外的字段一律拒绝`；`ref:db/tests/79d_onboarding.sql#解除绑定后家长读不到建档内容`；`ref:db/tests/79d_onboarding.sql#统计：不足 5 人时只给开始人数`；`ref:db/tests/79d_onboarding.sql#BFF 不能直接读自述表`；`ref:db/tests/79d_onboarding.sql#改答案：旧行保留（标为已替换），新行为当前`；`ref:services/bff/tests/onboarding.integration.test.ts#兄妹、另一户家长、不存在、非法 ID：同一个 404`；`ref:apps/web/e2e/onboarding.spec.ts#两方互不可见`；`mut:建档：家长能读学生自述`；`mut:建档：家长能看到别的家长写的观察`；`mut:建档：学生能看到家长录的成绩`；`mut:建档：学生可录排名区间`；`mut:建档：BFF 可直接读自述`；`mut:建档：解除绑定的家长仍算已绑定`；`mut:建档：统计不设人数门槛`；`mut:建档：教材版本两边共用一行`；`mut:建档：报表身份可读进度明细`；`mut:建档：改答案覆盖旧行` | 人工：用两户合成家庭逐个接口做越权与互看；改包提交字典外字段、超长文本、学生带排名区间；报表身份能否拼出个人明细（小样本时段的统计） | 统计有 5 人门槛与 1 天窗口，但相邻窗口相减仍可能逼近少数学生（SIE-01 lessons 待人确认 #5）；考试记录纠错与删除另做 |
| M2 | 开口成章异步批改：`/jobs/{job_id}` 能否被猜出或跨学生读取；对象存储读取器的路径穿越；`transcript_ref` 指向别人的转写；`audio/` 前缀引用一律拒绝；伪造转写能否作为学生原话写入平台 | 层 2 | 见 `specs/adapter-echo-sprint3/spec.md` | 无 | 第二轮：搁置，不纳入（3-D 开口成章已搁置，英语课程调整） | 搁置（`specs/adapter-echo-sprint3/lessons.md`） |
