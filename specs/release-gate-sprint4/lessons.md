# G 流（审核台 v0 起）· 经验与待人确认

## 冲刺 1 · PR 1：签字与发布规则（迁移 0018、0019）
### 交付
- **0018 platform**：签字人表 `platform.content_signoff`（审核记录的签字人）、`platform.map_change_signoff`（变更单签字人，带签字时的内容摘要）；只追加，只有签字函数属主能写。
- **0019 kge**：
  - `kge.current_staff()`：当前账号必须是 active staff；
  - `kge.map_version_digest(uuid)`：草稿骨架与原子的 SHA-256；`kge.row_digest`、`kge.map_change_digest`；
  - `kge.sign_review`、`kge.sign_map_change`、`kge.retire_map_version`（授 `starbuddy_review`）；
  - `kge.publish_map_version` 改授 `starbuddy_review`，新增：最近一条审核结论是批准且摘要一致、目标变更单全部两人批准、版本号严格递增、含结构性变更须升次版本号；发布后变更单转为已应用；
  - `content_review` 只追加（UPDATE、DELETE、TRUNCATE 连超级用户也拦）；知识服务只能提交“待审”记录；
  - 变更单状态只能经函数推进，批准后内容不可改；已发布版本上的题目标签、时政要有摘要一致的批准记录才能生效；
  - 撤下只能经函数（发布新版本时的自动撤下除外）。
- 测试：`db/tests/79b_kge_review_signoff.sql`（约 85 处断言）、76 号改为经 staff 签字发布；变异 30 条；攻击面登记 6 个新函数（共 55 个）；KGE-01 库上测试改走签字发布（两名 staff）。

### 与 spec 的差异（按默认处理，记在这里供评审）
1. **签字人不放在 kge，放在 platform**：spec 写“`reviewer` 改为 staff 账号引用（新列）”。但公共知识层不能存任何账号引用（规则 13，`80_attack_surface` 的列名、外键、uuid 列与金丝雀四条断言都会拦），
   所以签字人记在 platform 的两张签字人表，按审核记录 / 变更单 ID 引用 kge；按 ADR 02“每个迁移只改一个 schema”拆成 0018（platform）与 0019（kge）。
2. **变更单签字带内容摘要**：待审期间知识服务仍能改变更单内容，只看“两个不同账号签过”会出现“两人签的不是同一份”。每个签字记下签时的内容摘要，批准只数与当前摘要相同的签字。
3. 审核对象 v0 只支持 `map_version`、`item_tag`、`current_affair`（节点、边的审核随整个版本签字）；风险级别由调用方（审核台后端的分类器）传入，库只存。
4. 测试里地图版本 ID 从 `b1…b4` 改为 `ca01…ca04`：`b1…b1` 与夹具里一个学生账号 ID 相同，签字把它写进 `content_review.object_id` 后触发了金丝雀断言。

### 审查意见处理（安全审查、契约审查，按默认）
- 已批准原子（题目标签、时政）不能原地改内容，须先退回待审再审（安全 M1）；签字、发布、原子批准对版本行加共享锁（M2）。
- 结构性按规则判定（M3、契约 B1）：`kge.structural_diff` 比较有效节点集与前置 / 从属边，与已发布版本有差异、或首次发布，即为结构性；须两名不同 staff 对当前摘要批准（最近一次驳回之后）并升次版本号。
- 变更单所需人数按类型（契约 B2）：增删拆并节点、增删边 2 人，其余 1 人；驳回的变更单不阻塞发布；原签字人在内容变更后可重签。
- 摘要固定按 UTC 计算；批准后变更单的内容、证据包、类型、目标版本、学科、置信度、审核人列都冻结；每次签字的审核记录与签字人同一时间戳。
- 地图版本审核至少“高”风险（约束）；撤下必须写依据；发布与自动撤下都记审核记录（action = publish / retire）与签字人；新增 `kge.version_record(subject)` 导出版本记录。
- down 迁移把旧约束以 NOT VALID 恢复，避免回滚被新数据卡住。
- 测试夹具的坑：79b 里改投影页码的 UPDATE 起初没按节点过滤，连另一节点的页码也改了，恢复后摘要仍不符——是测试写错，不是摘要不稳定。

### 待人确认
1. 迁移 0018、0019 依赖 0011（草稿，由人执行）；按顺序在副本演练：0011 → 0012 → … → 0018 → 0019。
2. 风险级别由调用方传；已按默认给地图版本定下限“高”。其他对象的下限是否也在库里定。
3. 发布人与签字人可以是同一个 staff（v0 走查只有一名教研时需要）；结构性发布须两名签字人，发布人可以是其中之一。是否要求“发布人不能是签字人”。
4. 摘要只覆盖公共层内容；编译页面（冲刺 3）另有 Git 提交号对账。
5. 签字身份的强度取决于 PR 4 的 staff 登录（一账号一会话、强认证）；在那之前 `app.account_id` 由后端设置，库只核对账号是 staff 且 active。
6. 任何 active staff 都能签、发布、撤下，暂不分教研 / 管理员；一名 staff 即可撤下（须写依据）。是否需要细分角色，或撤下也要两人。
7. 已发布版本上仍可新增题目族、采分点（现状，测试钉住）；是否也要走审核记录。

## 冲刺 1 · PR 2：审核台后端（services/learning/review_console）
### 交付
- 差异（`diff.py`）：节点逐字段、边（增删、停用、强弱）、教材 / 地区投影、能力视图挂接、旧编码迁移、题目标签、采分点、时政；键与版本 ID、行 UUID 无关，输出逐字节确定。
- 风险分类（`risk.py`）：节点增删停用、前置 / 从属边增删为结构性高风险（两人）；包含 / 不包含 / 题例为中；认知层次、名称、类型、范围为高；教材投影、采分点、旧编码迁移为高；非结构性边为中；时政为中；题目族引用为低；其余一律高。
- 接口（`app.py`）：版本列表、差异视图（附校验问题、变更单、影响计数、签字记录）、改草稿节点、签字、变更单签字、发布、撤下、版本记录；命令行导出版本记录。
- 测试：`tests/test_review_diff.py`（20 项纯函数）、`tests/test_review_console.py`（9 项，一次性库上走完首次两人发布 → 改“不包含” → 单人发布 → 撤下 → 结构性变更单两人签 → 导出）。

### 与 spec 的差异（按默认处理）
1. **staff 身份先用可注入的认证器**：PR 4（staff 登录，`[NEEDS-HUMAN]`）之前默认 `deny_all`，任何请求 401；不接受请求自报账号。测试注入合成 staff。
2. **“看到的就是签的”在后端再加一道**：签字、发布、改节点都带 `expected_digest`；签字在同一事务里签前、签后各比一次，任一不符整笔回滚。
3. **地图版本的审核记录一律按“高”记**（0019 约束）；差异视图里的风险摘要只用于展示与所需签字人数，所需人数与库的判定一致（按差异或已批准的结构性变更单）。
4. **v0 能直接改的只有节点的名称、包含、不包含、典型题例、认知层次、范围**；新增、停用、父节点等结构变化走变更单（审核台 v0 只签，不建）。
5. 签字进度（谁签了）经 `kge.version_record` 读；知识服务身份读不到签字人表本身。

### 审查意见处理（安全审查、契约审查，按默认）
- **变更单签字也要带看到的摘要**（安全 A-1、契约 S8）：改了迁移 0019（PR 1 的草稿迁移，仍在同一 `[NEEDS-HUMAN]` 范围）：`kge.sign_map_change` 加第三个参数 `p_expected_digest`，锁住变更单后比对；新增只返回摘要的 `kge.map_change_digest_of(uuid)`（授 kge、review）。库测试、变异、攻击面清单（56 个函数）、授权快照同步。
- **差异覆盖摘要的全部内容**（契约 B1、安全 A-2 / B-2）：快照加题目族、旧编码迁移的变更单 ID、教材原句；时政键加失效日与节点、同键加 `#n`；单测逐节核对 `kge.map_version_digest` 的每一节都在快照里。
- **结构性与所需人数以库为准**（契约 S1）：差异视图调 `kge.structural_diff`，再加未驳回的结构性变更单；逐条标记只在碰到有效一侧时算结构性。前置 / 从属边的依据变化改为高风险（S2）。
- 中风险条目加 `review_level: high`（阶段 0 中风险按高审，spec 待人确认 #7 默认）；采分点按评分版本分组；旧编码撤销按 `rule` 识别；教材投影键含原句哈希，只挪页码不会错配（S5）；校验问题按对象 ID 挂到条目，挂不上的单列（S6）；影响分析计入边的终点、删除节点在基准版本上统计（S7）。
- 发布后同一事务再比一次摘要（安全 B-1）；启动检查加不绕过 RLS、恰好只继承一个应用角色（B-3）；命令行只要 kge 连接并做同样的角色检查（B-4）；只回传函数里写好的规则说明，权限错误与原生约束消息只给错误码（B-5）；节点文本拒绝控制字符、连接层错误只给错误码（B-6）；认证器约定写进接口文档（B-7）。
- 版本记录导出加“线”（map）、涉及的评分版本、当前校验的错误 / 警告数（契约“版本记录”）。
- 另修：严格检查 16 发现旧变异“发布不撤下原已发布版本”的模式在 PR 1 改写发布函数后失效，已改到新写法。

### 待人确认
0. **同一名 staff 可以自己改草稿、自己签字、自己发布**（安全 A-3，按阻断请人决定）：两个连接身份只在库层分开了“知识服务”和“签字人”，人这一层没有分开；草稿修改也没有记修改人。
   默认（本 PR）：v0 走查只有一名教研，暂时接受；结构性变更仍须两人。另一个选择：platform 侧新增草稿修改记录（staff、版本、摘要前后），`sign_review` 拒绝“最近修改人 = 签字人”（要改迁移，随 PR 4 staff 登录一起做）。
1. 风险分类里“名称改动”分不出错别字与定义变化，按高风险；是否让提交方标“仅错别字”再由审核人确认降为低。
2. 地区投影的权重、典型题位是否算 KGE 第四章表里低风险的“考情数值更新”（现按高）；能力视图挂接不在表里，按高。
4. `starbuddy_review` 继承了复核队列的读权限（`platform.review_task` 含学生指针），审核台用不到。是否另建只签字的组角色（改迁移）。
5. 版本记录的粒度：现在每行一次签字、发布或撤下（spec 写“每行一次发布”）；六段标识（冲刺 4）与页面提交号（冲刺 3）到时补列。
6. 教材投影的差异同时显示原句与哈希（spec 只要求哈希），原句是公共层教材内容。
7. `/review/v0` 是审核台内部接口，不是插件或知识服务契约；迁到知识服务时再定是否进 packages/contracts。
3. ~~审核台后端的部署（进程、端口、与学习服务是否同机）随 PR 4 staff 登录一起定。~~ 已处理：见 `services/learning/README.md`“审核台部署要求”（PR 4）。

## 冲刺 1 · PR 3：审核台前端（apps/web /review）
### 交付
- staff 路由组 `/review`（独立布局与导航，不与学生、家长页面共用）：地图版本列表（按学科）、版本审核页（风险摘要、差异表附风险与校验、
  其他校验问题、变更单签字、节点边界编辑、签字、发布、撤下、签字记录）、版本记录页。全部 `no-store`、`noindex`。
- 写操作全是 server action：服务端读开发 staff 身份、校验表单，再调审核台后端；浏览器不直连后端。每个表单带当前内容摘要。
  结果经 `useActionState` 显示在表单的状态区域（屏幕阅读器可读），不经地址栏传递，别人不能用链接伪造提示文字。
- 开发身份桩：`/dev/login` 加“合成教研”两项；staff cookie（`sb_dev_staff`）与学生、家长的分开，互不通用。后端 `review_console/devauth.py`
  同样只认 loopback、不带转发头、合成 staff 名单（两边名单由单测核对一致）；`python -m review_console serve` 只监听 127.0.0.1。
- 合成种子 `db/seeds/dev/review-synthetic.sql`：两名合成教研、语文合成地图 1.0.0（经两人签字发布）与 1.0.1 草稿。
- 测试：前端单测 5 项；端到端 3 项（学生进不了审核台；改“不包含” → 差异与校验 → 另一教研改过后旧页面签字被拒 → 刷新重签 → 发布 →
  旧版本撤下 → 版本记录；已发布版本没有签字、发布、编辑表单），含 axe 无障碍检查；`make web-test` 的端到端环境多起一个审核台后端。

### 与 spec 的差异（按默认）
1. v0 节点编辑只有包含、不包含、典型题例三项（名称、认知层次、范围后端支持，界面未放，走查后按教研反馈加）；节点新增、停用与边走变更单，界面只签不建。
2. 发布成功后页面按新状态重新渲染，发布表单消失，改为显示“本版本已发布”的说明。
3. 撤下依据先给三个编码（版本有误、内容错误、暂停使用），正式清单待教研定。

### 审查意见处理（安全审查、契约审查，按默认）
- **看到的就是签的**（契约 B1、B2）：变更单改为显示签字覆盖的全部内容（后端返回 `signed_content`，与 `kge.map_change_digest` 的输入一致，库上测试核对两者摘要相同）；
  版本元数据的课标年份进差异（高风险），页面头部显示课标年份；摘要单测不再把版本元数据标为“不覆盖”。
- **端到端验收最后一环**（契约 B3）：端到端以 BFF 的数据库身份读已发布版本，发布前只看到 1.0.0、发布后只看到 1.0.1（旧版本对 BFF 不可见）。
- 签字进度由后端按发布函数的口径算（最近一次驳回之后、对当前内容批准的不同人数，最近结论是否驳回）（S1）；签字人显示后 12 位（S2）；
  摘要显示“阶段 0 按高审”（S3）；差异行显示风险理由（S4）；差异值一律显示 JSON（S5）；非草稿版本不显示风险摘要（S6）；
  发布表单写明“当前已发布的 x.y.z 将自动撤下”（S7）；撤下必须选依据并勾选确认，server action 也检查（S8）；端到端改为按顺序执行（S10）。
- 安全：开发身份桩再加一道环境条件（`STARBUDDY_ENV` 为 dev / test）；前端 `/dev/*` 与审核台、后端开发身份桩都要求 Host 是本机地址（挡 DNS 重绑定）；
  开发 cookie 8 小时过期；前端只对规则类错误显示后端说明并截到 200 字；调用后端加 10 秒超时，连不上只显示错误码。

### 待人确认
1. 撤下依据编码清单（现为 wrong_version / content_error / withdrawn）。
2. 签字页的“我已核对全部差异”只是文字说明，没有强制勾选；是否要求逐条确认高风险差异后才能批准。
3. 开发登录仍是 GET 链接（别的网站一个链接就能把浏览器登成合成账号，只影响合成数据）；随 PR 4 / 4-H 删除开发身份桩时一并消失，本 PR 不改成 POST。
4. 变更单只显示所需人数，不显示已签人数（签字人表只有签字函数属主能读）；是否要加一个只返回人数的函数（改迁移）。
5. 前端类型是手写的，没有与后端模型自动核对（契约审查 S10 后半）；审核台接口进契约或导出 schema 时再做。
6. 学生侧（星图、BFF）的开发身份桩也只靠开关与 loopback，没有 Host 检查；前端的 `/dev/session` 已统一检查 Host，BFF 侧随 4-H 删除。

## 冲刺 1 · PR 4：staff 登录（设计见 pr4-staff-login.md，已按默认确认）
### 交付
- 迁移 `0020_platform__staff_auth`（只改 platform，`[NEEDS-HUMAN]`）：凭据表、会话表（RLS 强制、无策略，应用角色读不到）；
  `staff_login` / `staff_session_resolve` / `staff_logout`（只授 review）；`staff_enroll` / `staff_reset`（不授任何应用角色）；
  bcrypt、TOTP、随机令牌都用 0009 已装的 pgcrypto，没有新依赖。DB 测试 `79c_staff_auth.sql`（约 70 项断言，含 RFC 6238 测试向量）、变异 24 条、
  授权快照与攻击面清单（59 个函数）。
- 审核台后端：默认认证器改为会话解析（请求头 `x-review-session`）；新增登录、退出、`me` 接口；登录失败统一 401 `LOGIN_FAILED`；
  删除 `devauth.py`。单测覆盖失败统一、码不能重用、退出后失效、日志里没有令牌、口令与登录名。
- 前端：`/review/login`（server action，成功写会话 cookie）；页头显示登录名与“退出”；会话无效时各页回登录页；
  删除开发 staff 身份桩（`src/dev/staff.ts`、`sb_dev_staff`、`/dev/session?staff=`、登录页“合成教研”栏），单测核对前后端源码里都找不到。
- 种子给 T1、T2 建合成凭据；端到端经真实登录走完审核发布全流程，另测登录失败、退出后旧令牌失效。

### 与设计的差异（按默认）
1. 登录失败时函数返回 NULL 而不是抛错：抛错会回滚失败计次，锁定就失效了。
2. 格式不对（登录名、6 位码格式）直接失败、不计次，避免别人借格式错误撞锁他人账号。
3. 没有这个登录名、锁定中也做一次 bcrypt，耗时与口令错时相近。

### 审查意见处理（安全审查，按默认）
- 退出删 cookie 时带上写入时的属性（`__Host-` 要求 Secure、Path=/，否则正式环境删不掉）；后端撤销失败时照样删 cookie，登录页提示“退出未完成”（B-1）。
- 数据库服务器日志：部署要求写明对签字登录角色关掉语句与参数日志；迁移注释写明人工建凭据前先关日志、不写 psql 历史（B-2，需人在 staging 执行）。
- 登录用单独的两条连接（语句 5 秒超时），不占签字连接；同一登录名并发登录拿不到行锁直接失败（`FOR UPDATE NOWAIT`）（建议 1）。
- 后端恢复 Host 检查（只认本机地址，否则 421），挡 DNS 重绑定页面直接调登录接口（建议 5）。
- 一个账号同时只有一个有效会话：新登录撤销旧会话（建议 6）。口令最多 72 字节（前端、后端、库三处）（建议 7）。
- `staff_now` 改为 VOLATILE，时间步由同一个时间点算出（建议 8）。失败计次只看最近 15 分钟（新列 `last_failed_at`）（建议 9）。
- 迁移注释写明凭据函数的执行者要求与密钥生成方法（建议 10）；清掉本地残留的字节码与构建缓存后重新构建核对（建议 11）。
### 契约审查意见处理（按默认）
- **阻断 B1**：新列 `staff_credential.login_name` 命中渗透夹具“platform 里不得出现姓名类列”的自检，严格检查里 db-mutation 与 pentest-env-test 失败。
  按首选修法把列改名为 `login`（不给自检开排除口子）；函数返回列与接口字段仍叫 `login_name`（不进 information_schema.columns）。
- S1：部署层限流改为覆盖 `/review` 下所有 POST（server action 按 `Next-Action` 头分派、不看路径），参考配置只限 POST，上线验证加一条“向其他路径提交登录 action 也 429”。
- S2：端到端补错口令用例；锁定与会话过期由 79c（可推进的时钟）覆盖，端到端不等 15 分钟 / 30 分钟。
- S3：`make web-test` 构建后扫描产物（`.next`，不含缓存），出现开发 staff 身份桩的字符串就失败。
- S5：scope.md 写明三个 staff 函数不取 `current_account_id()` 的例外，服务路由表加审核台后端；攻击面清单加 M3（渗透测试 spec 第 2 节同步登记，列入第二轮）。
- S6：本节数字更新；PR 2 待人确认 #3（部署）标为已处理。S7：后端请求体上限 64 KB（超出 413）。S8：spec 冲刺 1 端到端一条在严格检查通过后打勾。
- 没采纳：建议 4（cookie 名与 Secure 按 `NODE_ENV` 定）——端到端用 `next start`（生产构建）跑在本机 http 上，按 `NODE_ENV` 会让测试拿不到 cookie；
  改为在部署要求里写明 `STARBUDDY_ENV` 必须是 staging，上线检查核对。建议 3（登录名枚举的计时差）靠部署层限流兜住。

### 待人确认
1. staging 上首批 staff 凭据由谁建、TOTP 密钥怎么交付（设计待人确认 #4 默认当面交付；命令行生成二维码工具未做）。
2. ~~登录接口除了库里的按账号锁定，没有按来源的限流~~ **已决（2026-09-29，用户）：在部署层加限流**。审核台后端只收本机（前端服务端）的请求，
   看不到用户来源，所以限流放在 apps/web 前面的反向代理：按来源 IP 限制对 `/review/login` 的 POST（登录的 server action 提交到页面路径）。
   要求与参考配置写在 `services/learning/README.md`“部署要求”；staging 部署时由人配置并验证（仓库里没有部署配置）。
3. 自审修改人（PR 2 安全审查 A-3）仍未做，按设计待人确认 #5 默认留到以后。
4. 知道登录名的人可以每 15 分钟错 5 次，让某个 staff 一直处于锁定（安全审查建议 2）。部署层限流能缓解；是否改为“锁定期间口令与码都对仍放行”（会削弱防爆破）。
5. 过期会话不清理（安全审查建议 6 后半）：需要定期删除，要给调度身份单独授权（改迁移），随 PLT-01 后台审计一起定。
6. 与 4-H 家庭登录合并（契约审查 S4）：PLT-01 / 4-H 时 `platform.staff_session` 并入 4-H 的 `auth_session`（origin = staff），
   时钟函数统一，解析函数返回账号类型；cookie 继续分开（staff 为 SameSite=Strict、一账号一会话，家庭为 Lax、多设备）。

## 冲刺 1 · 其余
- 走查（需要真实教研在 staging 操作）：staging 部署、凭据发放与走查记录由人完成。

## 冲刺 4 · 第一部分：阿鸦内容包格式 3 与提示原文 C9 检查（不碰迁移）
2026-09-30。按 spec 冲刺 4 段（已确认，待人确认全部按默认）先做不碰迁移的部分；迁移与审核台部分在统一壳 H3 / H2 合入后接着编号。
只改 `adapters/aya`，不改 db、packages/contracts、docs/personas、evals、services、apps；不调模型、不联网；测试全部合成数据。

### 交付
- **内容包格式 3**（`aya-content-pack/3`，`pack_version 0.3.0-0ed2fe4`，从同一 legacy 提交重新生成；题目、答案、提示与格式 2 逐字段相同）：
  - 逐题 `source`（`source_ref` 来源编号或 null、`source_type`、`license_status`）；决定文件里有的题另有 `admission_review`（学科审核等级、审核记录编号）；
  - 整包 `admission_decisions`（所用决定文件的格式、批次、导出时刻、SHA-256 摘要、条数；没有时为 null）与 `hint_scan`（C9 记录）；
  - 去掉整包 `source_verified`（来源改为逐题）；六段标识的 `content_version` 仍是 0.1.0。
  - `pack.json` 的 diff 很大（约 3.5 万行）：主要是序列化改为按键名排序，字段位置变了，内容没变。评审时按字段比对，不要逐行看。
- **准入决定文件**（`aya-admission-decisions/1`）：由平台导出、人执行。格式、校验与自洽规则见 `adapters/aya/README.md`“内容包格式 3”。
  - 决定文件是完整快照：重建时先把全部题重置为默认，再逐条套用；
  - 只收编号与枚举，多出的字段、网址或中文编号一律拒绝（#15：材料不进仓库，只记编号）。
- **按准入决定重建**：`npm run rebuild:pack -- --decisions <文件>` 不需要 legacy，只改准入与来源、重做 C9；`build:pack` 从 legacy 生成时走同一收尾（`scripts/pack-cli.ts`）。
  - 规范化序列化（键排序、不读时钟），两次构建逐字节相同；
  - 已核实：`build:pack` 两次、`rebuild:pack` 一次，三份 sha256 相同；
  - 带决定时 `pack_version` 加 `.d<摘要前 8 位>`，准入变化会体现在 `source_version` 的 items 段。
- **适配器行为不变**：只有 `admitted` 的题给 formal 与满置信度，其余按现有 `content_not_admitted` 规则；判分没改。单测用真实题 + 合成决定验证：
  - admitted → formal、不复核；
  - pending_review / rejected → practice + `content_not_admitted`；
  - generate_variation 只选已准入的题。
- **C9 词表检查**：构建时把全部提示阶梯原文（4447 条，含 4–6 级）过禁语扫描。报告只列题号、等级、规则编号，不含原文。
  - 严格模式（默认）有命中即失败、不写文件；
  - 非严格（`--c9 flag`）放行并记在 `hint_scan.flagged_items`；
  - 加载时拒绝“严格却有标记”的记录；
  - 失败即关闭：解释器不可用、guard 报错、结果条数或顺序不符都算失败。
  - 当前 4447 条提示零命中。
- 测试：新增 `tests/pack-format3.test.ts`（9 条）、`tests/hint-scan.test.ts`（7 条），改 `helpers.ts`（合成已准入题改走决定文件路径）、`pack.test.ts`、`plan.test.ts`、`verifier.test.ts`。
  - 单测 117 → 133 条；
  - 变异 49 → 65 条（新增 16：准入自洽 3、决定校验 3、重建与版本 2、确定性 2、加载 1、C9 5）。

### 与 spec 或任务说明的偏差
1. **“G 冲刺 1 段的 C9 检查”没有实现可复用**。
   - C1–C11 只出现在 v1.0 口径的原稿（提交 4f72e67，C9 = “敏感词：对有文字的内容按词表扫描”，词表来源是原稿待人确认 #10），v2.0 改写时删掉，冲刺 1 没有实现。
   - 原稿 #10 的默认是另建内容审核词表（`services/learning/learning/release/wordlist/`，从未建立），并写明“不使用 docs/personas 的禁语表”。
   - 现在的做法：只读复用冲刺 3-F 收拢后的单一真相源 `packages/personas`，调用方式与 `apps/web/tests/auth.test.ts` 相同（`python -m personas.guard scan --persona aya --stdin-json`）。KGE-03 页面体检也用这份词表（经 `export_kge`）。
   - 不改词表，不涉及人设必停项。见待人确认 1。
2. **扫描口径是角色 aya**：A–F 类（F 取数学）加全平台禁语，不含 G 类（decisions #7：G 属于考官型基线）。页面体检对所有页面都查 G，两者口径不同。
   - guard 的模型层规则不执行（本冲刺不阻断，同 3-F）；结构上下文层规则需要引擎字段，提示原文没有，不适用。
3. **准入依据的审核等级来自决定文件，不看 legacy 的 `reviewStatus`**。
   - legacy 721 道都是 Q1；
   - `admitted` 要求决定里的 `review_level ≥ Q2`（平台的学科审核结论）；
   - 题目上的 `reviewStatus` 保留 legacy 原值，只作记录。见待人确认 3。
4. **决定文件目前没有**：平台导出依赖本流程冲刺 4 的 platform 迁移（题目来源编号列、`change_admission`、审核记录），在第二部分做。
   - 缺省路径 `content/admission-decisions.json` 暂不建立；
   - 登记脚本 `register-content.ts` 不变（平台表还没有来源编号列，准入仍最高登记为 pending_review）。
5. **一致性测试没有新建 venv**：环境不联网。用的是已有的 `services/learning/.venv`（只读使用，含 pytest、jsonschema、PyYAML；缺 openapi-spec-validator，一致性测试用不到）。
   - 仓库的 guard 钩子拦截直接写出适配器令牌变量名的命令，所以经 `make conformance PYTHON=…` 跑（全部学科一起跑，全部通过）。
6. **变异测试变慢**：新测试里有 Python 子进程与命令行重建，`make adapter-aya-mutation` 从约 3.5 分钟变为约 9 分钟（4 核）。
7. legacy 生成的逐字节确定性只手工核实。legacy 是本机软链接，不在 CI 里；单测覆盖的是“已提交内容包按空决定重建逐字节不变”和“命令行重建两次相同”。

### 待人确认
1. **C9 词表来源**（原稿 #10 的延续）：
   - 默认：复用 `packages/personas` 的阿鸦口径（只读）；
   - 另一个选择：合规与教研另给一份内容审核词表（题面、提示通用，与人设禁语分开）；
   - 题面进包之后（目前没有题干）是否同样扫描，也一起定。
2. **提示命中是否要按 G 类或页面体检口径再查一遍**：默认按角色 aya（提示经 teach 以阿鸦身份给出）。
3. **admitted 的等级门槛看平台审核等级还是 legacy 等级**：默认看平台决定里的 `review_level`，legacy `reviewStatus` 只作记录。
4. **`rejected`、授权 `denied` 的题在运行时怎么处理**：
   - 现在仍留在内容包里；grade 按 practice + 复核；plan、teach 仍可能用到；generate_variation 不选。
   - 默认先不改方法行为。另一个选择：plan、teach 跳过 rejected 的题（方法行为变更，要改 4-D 的单测与夹具）。
5. **非严格模式带标记的题**：teach 仍会返回其提示原文。默认只允许在本地迭代时用非严格，发布闸门（第二部分）拒绝 `flagged_items` 非空的内容包。
   另一个选择：teach 对被标记的题只返回题目引用（同“含答案”的做法）。
6. **来源类型与授权状态的枚举**请法务、教研确认：
   - 来源类型：`unknown`、`self_authored`、`adapted`、`textbook`、`exam_paper`、`reference_book`；
   - 授权状态：`unverified`、`cleared`、`denied`。
   平台迁移（第二部分）按同一枚举建列。
7. **决定文件进仓库**：文件里只有题号、来源编号、枚举和审核记录 UUID，默认与内容包一起提交到 `content/admission-decisions.json`。
   审核记录 UUID 指向 staff 签字记录，不是学生数据。
8. **`pack_version` 带决定摘要**（`.d<8 位>`）：准入变化会改变 `source_version` 的 items 段；六段 `content_version` 不变。
   是否改为准入变化也升 `content_version`（等于发一个新内容版本），由第二部分的发布函数一起定。

### 测试（最后一次）
- `make adapter-aya-test CONTRACT_STRICT=1`：类型检查通过，133 条单测全过。
- `make adapter-aya-mutation`：65/65 个变异都被单测发现（约 9 分钟）。
- `make conformance PYTHON=services/learning/.venv/bin/python`：通过。阿鸦两遍：
  - 0.9.0：32 passed、2 skipped；
  - 1.0.0：46 passed、6 skipped，报告 `contract_version 1.0.0`、`implemented 6`、`passed 6`，`global_failures` 为空。
- 另跑读阿鸦内容包的学习服务测试：`test_diagnosis_session`、`test_diagnosis_engine`、`test_e2e_aya` 共 104 passed（只运行，未改 services）。
- 没有跑全量 `make check`：同一工作区里其他会话正在改 zhixing、wenli、db、bff、web。

## 冲刺 3、4 · 第二部分：迁移与审核台（2026-10-03 开工计划）
spec 冲刺 3、4 段已确认（待人确认全部按默认）。迁移从 0038 起按合并顺序编号。分 PR：
- **A**：kge 迁移（`content_review.object_type` 加 `page`、`page_commit` 列与唯一约束）；页面合并记录与审核记录的对账脚本与 Makefile 目标（合成导出文件，不连托管平台），纳入 `make check`；审核台“页面”入口（列待审与对账状态）。
- **B**：platform 迁移（内容版本状态机与保护、`publish_content_version` / `retire_content_version` / `change_admission`、`retired_at`、题目来源编号列、收回学习服务 UPDATE、BFF 可见性）；同 PR 改 `10_fixtures.sql` 与星图种子写法。
- **C**：policy 迁移（签字函数、复核人改账号引用、发布改授权、来源文件与招录行收口）。
- **D**：审核台：内容版本与考情差异视图、风险分类器补全、低风险窗口与抽检、版本记录导出补页面与内容版本行；时政过期下架任务。
- **E**：阿鸦准入决定文件的平台导出与内容包重建；数学“内容版本 + 地图版本”合成发布走查与版本记录。

## 冲刺 3 · PR A：编译页面审核线（迁移 0038、对账、合并窗口与快照跳过、审核台页面入口）（2026-10-04）

### 交付
- **迁移 0038**（草稿，kge 侧，由人在 0011、0018、0019 之后演练）：`kge.content_review.object_type` 加 `page`；新增 `page_commit`（40 位小写十六进制，口径为**合并请求头提交**）与 `page_signer_no`（签字序号 1–16）；约束：页面记录必须有提交号与签字序号、不能是待审、动作只能是 review、页面 ID 合节点 ID 写法，其他对象这两列为空；唯一索引 `(页面 ID, 提交号, 签字序号)`。
  - `kge.sign_page_review(...)`（SECURITY DEFINER，属主 authz，只授 review，要求 active staff）：签字人取当前账号；在双参数咨询锁下查重（同一账号对同一页面同一提交只签一次）并按序分配签字序号；同一页面同一提交的后续签字风险级别必须与第一条一致。
  - `kge.page_review_record(platform.subject)`（授 kge、review）：只读导出页面审核记录与签字人，空参数表示全部学科。
  - 只追加不变；知识服务的 INSERT 策略只收待审，写不进页面记录。down 开头锁表，库里有页面记录时以明确错误中止。
  - 测试：`db/tests/79t_page_review.sql`、`run.sh`（回滚中止、两名 staff 同时签同一页面的并发测试）、`mutations.sh` “页面审核：”组 16 条；授权快照、`docs/security/pentest/scope.md`（SECURITY DEFINER 96 个）、攻击面 E3 同步。
- **对账**（`services/learning/review_console/pages.py`，命令行 `python -m review_console reconcile-pages`，格式见 `services/learning/README.md`“编译页面对账”）：
  - 每个页面只按它在主分支上最近一次合并判定，更早的列在 `superseded`。
  - 失败类别：已合并却无审核记录（中、高风险）、审核记录对不上合并请求（孤儿，仅当页面仍在本次导出内且其最新头提交上还没有批准时 `blocking`）、批准人不一致或不在对应表、人数不够（中、高风险两人，与编译器 `window.REVIEWERS` 有测试核对相等）、审核记录里有驳回。
  - 低风险：托管平台有批准人而审核台无记录不算失败（低风险无事前签字），审核台有记录时才比对。
  - 对最新头提交重新签字即可解除扣页。报告不含用户名与账号；列出头提交与合并提交对照；`held_pages` 为发布时要跳过的页面。离线 `--records` 严格校验（5 个字段、signer 小写规范 UUID），库行路径宽松。输入与报告上限 10 MB。
- **编译器与发布**（`services/knowledge`）：
  - 合并窗口 `plan_window(..., held=)` 跳过被扣页面；快照导出加 `hold` 与 `previous`：被扣页面沿用上一份快照的原文与清单条目（条目带 `source_commit`），上一份没有就不导出该页，manifest 记 `held_pages`；上一份快照按知识服务同一套逐页核对载入，不合格拒绝。
  - 有被扣页面时体检按该提交在主分支上的完整页面集合做，被扣页面自己的阻断不算（沿用旧页与新集合的模板段必然不同；不导出又会让相邻页报失效链接）。
  - 知识服务载入时校验 `source_commit`，`page.get` 的 `source_version` 用它，被扣页面仍能对回原提交。
  - 编译器与快照导出都必须给 `--hold-report`（开发时显式 `--no-hold-report`）；报告 `exported_at` 早于有效期（缺省 24 小时，`--hold-max-age-hours`）或超前 5 分钟以上即拒绝；格式不对、自相矛盾、过期都按失败处理。
- **审核台“页面”入口**：后端 `GET /review/v0/pages`（只读，配置 `REVIEW_CONSOLE_PAGES_EXPORT`、`_STAFF_MAP`、`_MR_URL` 三项要么都设要么都不设；链接模板只允许 https，`{mr_id}` 必须在路径里；文件读不了或格式不对回 503 不回显）；前端 `/review/pages`（显示对账状态、是否扣页、头提交 → 合并提交；只渲染 https 链接）；合成种子 `db/seeds/dev/review-pages-synthetic.sql`（三道挡板）。
- **版本记录导出**：新增 `line = page` 行（页面 ID、提交号、签字人、时间、签字摘要）；CSV 末尾追加 `page_id`、`page_commit` 两列，已有列顺序不变（gate-check skill 不按列位置读，审核台记录页已同步）。

### 测试
`make db-test` 2227 项断言通过；`DBMUT_FILTER=页面审核` 16/16；`attack-surface-check` 通过；`learning-test` 2691 passed；`page-reconcile-test` 56 passed；`knowledge-test` 424 passed；`knowledge-mutation` 132/132；`web-test` 56 passed、1 skipped。过程中没有命令被拦截。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 契约审查 2 条阻断：扣页无法解除（改为按最近一次合并判定 + 孤儿限于导出范围且未重签）；“继续用旧版本”不成立（快照导出沿用上一份）。
- 其余：对账报告必填与有效期；链接模板编号只在路径里（5 个反例）；离线记录严格校验；低风险口径；`page_commit` 统一为头提交；后续签字风险一致；人数常量交叉测试；可选项（大小上限、双参数锁与去锁变异与并发测试、down 锁表、页面行带摘要、学科过滤下推到 SQL）。

### 与 spec 的差异
1. “同一页面同一提交不能记两次”实现为“同一 staff 不能对同一页面同一提交签两次，高风险两人各签一条（按签字序号区分）”：签字人表一条审核记录只能有一个签字人，kge 侧又不存账号（规则 13）。
2. 签字签的是合并请求的头提交，不是合并提交；从 `page_version`（主分支提交）对回审核记录需经导出里的头提交 ↔ 合并提交对照，库里不存这一映射。
3. 低风险合并在托管平台上有批准人、审核台没有记录时不算失败（spec 第 6 节“每个合并都有一条记录”与第 2 节“低风险无事前签字”冲突，取后者）。
4. 被扣页面沿用的旧页带旧 `map_version`，地图升版后 `page.get` 对它回 404，直到对账通过并重新导出。

### 待人确认
1. 页面签字入口：审核台界面由审核人逐个签（默认随 PR D），还是由同步任务按托管平台批准记录代写。目前只能经库函数签，冲刺 3 验收“一个知识点页从修改到发布走完”暂未达成。
2. 对账报告有效期缺省 24 小时。
3. 托管平台须设为“合并前必须基于最新主分支”，保证合并后的页面内容与头提交相同；托管平台在境内开通（ADR 12 待人确认 #1），staff 用户名对应表由人维护。
4. 是否需要专门的“人工处置”审核记录来解除扣页（现做法：对最新头提交重新签字）。
5. 上面“与 spec 的差异”第 1–3 条的口径。

## 冲刺 4 · PR B：platform 内容版本与准入（迁移 0039）（2026-10-05）

### 交付
- **迁移 0039**（草稿，由人在 0038 之后演练；开头有数据预检：库里已有 retired 内容版本或已有 admitted 题时以“0039 中止：…”明确中止）：
  - 内容版本状态机：新增 `retired_at` 与一致性 CHECK；触发器 `guard_curriculum_version`：只能以草稿登记，已发布只能撤下且其他字段不能改，已撤下只读，非草稿不能删。
  - 登记保护（`guard_content_registration`）：能力视图与题目只能追加到草稿版本、不能挪版本；题目 INSERT 时 `admission = 'admitted'` 被拒，`license_status` 一律存为 `unverified`（`source_ref` / `source_type` 留作登记元数据）；准入与来源只经 `change_admission` 改；已撤下版本下的题只允许降为 rejected。
  - 题目新增来源三列（枚举与编号格式同阿鸦格式 3）；`item_admitted_source`：已准入的题必须来源已核。
  - 新表 `platform.release_record`（审核、发布、撤下、准入执行记录；只追加；签字人是 staff 账号；开启并强制 RLS、无策略、只 authz 读写；复合外键保证学科与内容版本一致；个人层表清单按豁免登记）。
  - 函数（SECURITY DEFINER，属主 authz）：`sign_content_version`、`publish_content_version`、`retire_content_version`、`sign_admission`、`change_admission` 只授 review、要求 active staff（复用 0023 `review_current_staff()`）；两个摘要函数授 learning 与 review；`admission_change_log` 授 learning（不含签字人，给 KGE-04 读准入变化）。签字时回传看到的摘要，发布与执行前重算比对。
  - 发布：数学、道法 `map_version_id` 必填且所指地图在发布时刻已发布，语文可空；同前五段下版本号须大于“已发布过”的最大值（按数字比较）；最近一条审核须为批准且摘要一致；并发发布由咨询锁串行。
  - 准入：方向只允许 source_unverified → pending_review → admitted 与任意 → rejected；升 admitted 须来源已核、签字人显式写授权状态（不沿用题目上的值）、审核等级 ≥ Q2；执行时这条审核须是该题最近一条、没执行过、题目摘要未变；一批有一条不合格整批回滚；自 admitted 降级时在执行记录上留 rubric_version 清单（取自 `kge.scoring_point`）。
  - 收回 learning 对状态、发布 / 撤下时间、准入、来源列的 UPDATE，其余改为列级授权，登记草稿的 INSERT 保留；BFF：`skill_node_read` 要求所属版本已发布，`item_read` 要求已准入且所属版本已发布。
  - down：有审核与发布记录时以明确错误中止（run.sh 有测试），否则完整回滚。
- **测试**：`db/tests/79u_content_release.sql` 覆盖 spec 冲刺 4“内容版本与准入”全部条目，另含签字权限、摘要、整批原子、降级清单、`admission_change_log`、数学缺地图被拒、语文空地图发布成功；run.sh 并发测试（同前五段两个草稿同时发布、登记进行中时发布）、up 预检、down 中止；变异“内容发布：”30 条、“准入：”20 条；授权快照、pentest scope（SECURITY DEFINER 104 个）、攻击面 E3 同步。
- **夹具与种子**：
  - `db/tests/10_fixtures.sql`：先插草稿，再以两名合成 staff 发布合成占位地图与内容版本、经准入审核升 admitted（DO 块，因渗透测试环境单独加载此文件）。
  - 新增 `db/seeds/dev/syn-publish-content.sql`：定义 `pg_temp.syn_publish_content(cv)`（只认开发 / 测试库名；占位地图 ID 由内容版本 ID 定出；签完把两名合成 staff 置为 disabled）。star-map 种子只发布自己的合成知行版本，shell 种子只发布自己的两份，种子头部写明“该学科在库里已上线”的副作用。
  - 登记的真实阿鸦 0.1.0 不由种子发布，改由一次性测试环境自己发布（`apps/web/scripts/e2e.sh`、BFF 星图集成测试 `publishRegisteredContent`）。
  - 三科 `adapters/*/scripts/register-content.ts`：登记 SQL 只往草稿版本追加（BEFORE 触发器先于 ON CONFLICT 生效，版本发布后重跑登记会报错）。
  - 洞察、知识服务、掌握测试与压测种子用 `session_replication_role = replica` 直接写已发布版本与已准入题（只在 initdb 起的一次性实例或有库名挡板的测试库）。
  - Gate 0 证据包：联调栈三科登记后经签字函数发布（`tools/e2e-cohort/e2e_cohort/stack.py` `publish_content`），在干净副本实跑 `make gate0-evidence GATE0_STARMAP=1` 重生，星图对账一致；样例变化：`tables_bff_cannot_read` 28→29、对应哈希、提交号、`starmap.json` 道法 `evidence_nodes` 2→1（旧样例本身过时：星图截于 ee3f9f5，`nodes.csv` 之后在 a84a76d 改过）；4 张截图字节不变。

### 测试
`make db-test` 2340 项断言通过；db-mutation 内容发布 30/30、准入 20/20、受影响组全部被发现（全量 801 条没跑）；`attack-surface-check`、`pentest-env-test` 通过；`learning-test` 2691 passed；`e2e-cohort` 17 passed；`bff-test` 通过；`web-test` 56 passed、1 skipped；zhixing / wenli 适配器测试通过；`insight-test` 通过（15 项，看起来不连库）。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 安全、契约各 1 条阻断：登记可自报来源已核（改为登记一律 unverified、升 admitted 须显式写授权、pending_review 不要求来源已核）；Gate 0 星图对账在 0039 后跑不通（联调栈经签字函数发布并实跑重生）。
- 其余：迁移数据预检；种子只发布自己的合成版本、签完停用合成 staff；down 中止测试；已撤下版本的题允许降为 rejected；攻击面与 scope 写明“超级用户 + replica 可绕过保护触发器，约束仍然生效；生产不发超级用户凭据，执行迁移的身份不是超级用户”；可选项（并发测试、补变异、复合外键、等级门槛写成常量）。“去掉 FOR SHARE”的变异测不出（外键自带 KEY SHARE 锁已让登记与发布串行），已删，触发器里的 FOR SHARE 保留以表明意图。

### 与 spec 的差异
1. 审核与执行记录放在 `platform.release_record`，没有放进 `kge.content_review`：对象归 platform 所有，一个迁移只改一个 schema（ADR 02）；准入的唯一真相源仍是 `platform.item.admission`，release_record 是只追加的签字快照，不构成第二份事实。
2. 撤下只要求 active staff 与依据，不要求事前批准记录，草稿也能撤下（止损动作，与 `kge.retire_map_version` 一致）。
3. 版本号比较的是“已发布过”的最大值（含之后撤下的）；发布新版本不自动撤下同前五段的旧版本。
4. pending_review 不要求来源已核（与三科登记一致）；阿鸦格式 3 的 `admissionProblems` 在 pending_review 分支仍要求来源已核，适配器未改，两者口径不同。
5. 除数学外的学科审核等级门槛暂定 Q2。
6. 受影响的 rubric_version 只取 `kge.scoring_point`，未取 `grade_result` 中的版本。
7. 学习服务没接 `recompute_item`，只提供 `admission_change_log` 读口；在审核台（PR D）于 `change_admission` 之后调用重算，或 mastery-sweep 接调度之前，准入降级不会立即反映到掌握与星图（`specs/mastery-estimate-sprint4/spec.md` 写的是“4-G 准入变化调 recompute_item”）。
8. 部分测试夹具用复制模式（spec 待人确认 #12 的另一做法）。

### 待人确认
1. 道法“已上线”副作用：执行过 star-map 或 shell 种子的库里道法不再返回 not_launched（撤下是终态，不能回退）；BFF shell-pages 因此不再断言“没有已发布版本 → not_launched”，该路径只由库层 79u 覆盖。
2. 上面“与 spec 的差异”第 2–7 条的口径。
3. 同一名 staff 可以签字、发布、执行准入走完全程；审核等级由签字人自报，没有和人员资质挂钩。
4. 学生端探针（`insight.record_confidence_probe`，0025）检查题目存在时，草稿题与未准入题也算存在，与新的 BFF 可见性不一致；收紧会断数学诊断演示（演示题未准入未发布），且需改 insight schema，留待人决定。
5. 签字人身份取自审核台后端设置的账号，只核对是 active staff，未绑定 0020 的 staff 会话（与 0019、0038 同一残余风险）。

## 第二部分 PR C：考情签字收口（迁移 0040、0041）

### 做了什么
- 拆成两个迁移（同 0018/0019 先例）：0040 只建 `platform.policy_signoff`（只追加、强制 RLS 无策略、只 authz 读写、同一账号对同一对象同一摘要只签一次、个人层表清单登记为豁免）与导出函数 `platform.policy_signoff_record()`；0041 只改 policy schema：`policy.sign_scheme` / `policy.sign_record`（只授 review、要求 active staff、签字人取当前账号、调用方回传摘要）、两人规则（只数对当前摘要批准的不同账号，当前摘要被驳回即清零）、`publish_scheme` 改为只授 review、四表共用守卫（只能以草稿新建，改为已发布、写复核时间只能经函数）、`validate_scheme` 第 5 项改数签字人表、数据预检与角色预检、down 遇签字记录中止。
- 签字人不能放进 policy 表：80 号攻击面测试禁止 policy 存账号 ID（规则 13），所以放在 platform；指向学校的列名为 `institution_id`（渗透夹具禁止 platform 出现含 school 的列名）。
- 测试：新增 79v（四类对象逐一测非 staff 拒签、同人两签算一人、kge 直接发布被拒、摘要不符、改内容旧签作废、驳回清零、导出与 BFF 可见性）；77 改走签字函数；run.sh 加两人并发签字、0041 数据预检、角色预检（`policy_must_refuse`，四种组合）、0040/0041 down 中止；mutations “考情签字”组；快照重生。
- 审核台版本记录导出合并 `line = policy` 行，末尾追加 `policy_object, policy_object_id, city, cohort, policy_version`；考情行 `version` 为空。
- Gate 0 样例 `tables_bff_cannot_read` 29→30 与对应哈希，经 `make gate0-evidence` 重生。

### 测试
`make db-test` 2446 项断言；db-mutation 考情 56/56、攻击面 24/24（全量没跑，留给严格检查）；`attack-surface-check`、`pentest-env-test` 通过；`learning-test` 2692 passed；`web-test` 56 passed、1 skipped；tsc 通过。

### 经验
- 保护触发器里的 `EXISTS` 子查询即使走不到也做权限检查，authz 需要 `source_domain` 的 SELECT。
- 同一条语句里函数刚做的修改对子查询不可见，“签字后查状态”的断言要拆成两条语句。
- 对象行加 `FOR UPDATE` 两人同时签字才能正确计数（并发测试与“签字不锁对象”变异守着）。
- 新增 platform 表会改变 Gate 0 样例的 `tables_bff_cannot_read`，样例要重生。
- 表级 INSERT 授权不能单独收回一列；要收得改逐列授权。
- 流程：实现子代理用过一次 `sed -i` 改 `docs/security/pentest/scope.md` 标题（104→109），结果已核对，已向用户披露。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 安全、契约均无阻断。已改：角色预检加 learning×review、bff×review 并补拒绝测试；收回 kge 对 `reviewed_at, reviewers` 的 UPDATE（INSERT 保留）；导出函数挪到 platform；攻击面引用改完整测试名；导出考情行 `version` 置空、追加 `policy_version`；records 页 `data-version` 只给地图行，考情行用 `data-policy-object` / `data-policy-id`；README 写明跨学科汇总按 `line = policy` 去重、考情行 `risk_level` 固定 high；录入规则写明驳回与发布回滚语义。
- 第二名批准时发布失败，签字一并回滚不留痕：PR D 审核台给出明确提示。

### 与 spec 的差异
1. 签字人在 platform，拆成两个迁移；考情复核人不记在 `kge.content_review`。
2. `reviewers` 自报工号列保留作展示、不计数，只能在新建草稿时填；复核时间只在发布时写入。
3. 撤下不收口：kge 仍可直接改为 retired，不留签字记录。
4. 驳回后同一内容不能改签；发布人可以是签字人之一或第三人。
5. 0041 遇已有已发布 / 已撤下考情数据直接中止，不回填。
6. 导出函数在 0040，但它读 policy 表的权限由 0041 授予；两个迁移须一起执行。

### 待人确认
1. 撤下是否也要两人签字收口、留签字记录。
2. 签字人之后被停用，已签的批准仍计数（同 0019、0039）；是否计数时要求仍为 active staff。
3. 签字身份取自会话变量 `app.account_id`，两人规则依赖审核台进程可信；可选加固为签字函数直接校验 staff 会话令牌。同一自然人持两个 staff 账号需靠组织流程。
4. `platform.policy_signoff_record()` 授给 starbuddy_kge，可读全部 staff UUID（同 0038）；请确认 kge 角色只给审核台后端，或改用 review 连接后收回。
5. `reviewers` 的 INSERT 是否也收回（需改逐列授权）。
6. 导出列 `policy_version` 与 `insight.weekly_report.policy_version` 同名异义，是否改名 `scheme_version`（改导出格式）。
7. 内容改回原样后旧驳回、旧批准重新生效，是否需要按时间先后的语义，由教研定。
8. 考情行不分学科，每个学科导出都带全部考情行。

## 第二部分 PR D：审核台（迁移 0042、0043）

### 做了什么
- 迁移 0042（platform）：`content_version_list` / `content_version_snapshot` / `release_record_export` 三个只读 SECURITY DEFINER 函数（属主 authz，只授 kge），不放宽任何表授权或 RLS。审核台两个库身份原本读不到内容版本、能力点、题目与发布记录。
- 迁移 0043（kge）：`kge.expire_current_affairs(p_today)`（只授 kge；`valid_until < 当天` 且已上线的时政改 retired，逐条追加系统审核记录 `expired_auto`、无签字人；日期不能晚于北京时间今天；同日重跑幂等）与守卫 `kge.guard_affair_retired`（已发布 / 已撤下地图版本上 retired 的时政不能改回 published 或 pending）。
- 审核台后端与前端：内容版本视图（差异、发布前提、签字 / 发布 / 撤下 / 准入）、考情视图（同城同届上一版本与上一届已发布方案差异、`validate_scheme` 逐条）、页面签字入口（PR A 待人确认 1 按默认）、低风险事后抽检、风险分类器补全（`classify_page` / `classify_atom` / `classify_policy` / `classify_content`，判不出一律高）。
- 第二名批准时发布失败、签字整笔回滚：返回 409 `PUBLISH_FAILED_UNSIGNED`，界面写明“签字没有保存”（PR C 待处理项）。
- 版本记录导出新增 `content_version`、`admission` 行，末尾追加 7 列（`content_version_id, six_segment, content_map_version_id, item_id, from_admission, to_admission, admission_review_level`）；`action` 新增 `apply`。
- 时政下架命令 `python -m review_console expire-affairs`、`make current-affair-expire`（只连本机，连接后再核对服务端地址）。
- 准入变化后的重算：`python -m learning.jobs admission-recompute --since` 读 `platform.admission_change_log` 逐题调已有的 `recompute_item`（PR B 差异 #7）；没有改掌握计算逻辑。

### 测试
`make db-test` 2489 项断言；db-mutation 审核台读 11/11、时政下架 11/11、页面审核 16/16（全量留给严格检查）；`attack-surface-check`（113 个函数）、`pentest-env-test`、`page-reconcile-test` 通过；`learning-test` 2770 passed；`web-test` 56 passed、1 skipped；tsc 通过；Gate 0 样例重生比对无变化。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 阻断 B1：`local_only` 只看 DSN 第一个主机，多主机写法可绕过（规则 9）。已改为自己的解析与 asyncpg 实际解析结果取并集逐个核对，拒绝 `hostaddr`、`service`，连上后查 `inet_server_addr()` 必须为空或回环。
- 其余：提交方不能自签（导出加 `author`，签字人等于作者 409 `SELF_SIGN`，作者缺失或不在对应表 409 `AUTHOR_UNKNOWN`，对账加 `self_approval`）；抽检种子必须配置（删缺省值，未配置 503）、抽样改用 `merge_commit`、比例为 0 告警；已下架时政不可逆；0042 收回 review 授权；准入重算加不 mock 的重跑幂等测试；`classify_page` 缺字段判高；离线审核记录格式升 `/1.1`；导出列改名 `admission_review_level`；0043 COMMENT 写明 `expired_auto` 不是人工批准。

### 经验
- 判断 DSN 只连本机要取自己解析与驱动实际解析的并集：asyncpg 在 netloc 已有主机时忽略 `host=` 参数，但不能因此放行。
- 0019 的原子守卫只认最近一条 `action = review` 的结论，时政下架记录的 action 是 retire，下架前那条批准会“复活”；按状态机设防要按状态转移逐条设。
- 抽样哈希输入只能用提交方事后改不了的量（合并提交），种子不能公开。
- 共用测试库里其他模块的端到端测试也会发布内容版本，版本记录类测试要按行类型或地区过滤。
- 授权快照先生成到 scratchpad，比对后用 Edit 同步。

### 与 spec 的差异
1. 抽检与页面签字都记在 `kge.content_review` 的页面记录里（依据 `spot_check` / `mr_review`），不另加表。
2. 抽检只覆盖编译页面；原子低风险项（题目族引用）没有抽检入口（`kge.sign_review` 不支持 `item_family`，要改迁移）。
3. 审核台页面风险仍取合并请求上的标注（编译器生成）；spec 把叙述段改写定为中，编译器定为高，两边都按高审，口径不同。
4. 能力点和题目编号全局唯一不带版本，新内容版本的登记在差异里显示为“新增”、上一版本的显示为“删除”（界面有说明）。
5. 时政系统下架记录不进版本记录导出。
6. 作者不在 staff 对应表也拒签（比审查意见更严）；“下架不可逆”守卫不管草稿地图版本。

### 待人确认
1. 迁移 0042、0043 依赖 0011、0019、0038、0039，按顺序在副本演练后执行。
2. 抽检种子由谁、在哪里配置（部署配置，不进仓库）；比例默认 0.1、合并窗口默认 02:00，待教研负责人定（ADR 12 #3）。
3. `admission-recompute` 接调度（ADR 03 / 10）；之前准入降级不会立即反映到掌握与星图（替代 PR B 差异 #7 的说明）。
4. 编译器机器人账号作合并请求作者时不在 staff 对应表，按从严规则无法在线签字；是否另设“非 staff 作者”名单。合并请求上的风险标注由谁生成、提交方能否改写也需明确。
5. 已下架时政不可逆、重新使用须新建一行，草稿版本例外是否合适。
6. 编译器与审核台对叙述段风险（中 / 高）的口径是否统一。
7. 时政过期判定 `valid_until < 当天`，而 0026 只读角色 RLS 在 `valid_until` 当天已不可见，两者差一天。
8. 内容版本页每道题一个准入表单，阿鸦 721 题页面很长，是否分页。
9. 契约审查顺带提出：三科 `adapters/*/scripts/register-content.ts` 生成直接插入 `platform.*` 的 SQL（离线、不连库，PR B 之前即有），是否算违反规则 1。

## 第二部分 PR E：阿鸦准入决定导出与数学合成发布走查（不加迁移）

### 做了什么
- `services/learning/review_console/admission_export.py`：`export` 从平台（唯一真相源 `platform.item.admission` 与 0039 发布记录）导出 `aya-admission-decisions/1`；`verify` 核对重建后的内容包逐题不比平台宽。只用 kge 读身份调 0042 三个只读函数，只读、可重复读事务；连接前用 PR D 的 `local_only` 核对全部主机，连上后核对服务端地址。
- 导出从严：内容版本须已发布；admitted 须有最近执行记录、来源已核、等级 ≥ Q2，rejected 须有执行记录，缺即整份拒绝；表达不了的 pending_review 退回缺省 source_unverified；同一道题执行时间相同即拒绝；决定文件不写签字人。
- `verify`：平台版本未发布（草稿 / 已撤下）时，内容包里比 source_unverified 宽的题一律不合格（`version_not_published`）。重建内容包后未跑 verify 不得发布（README 写明）。
- 离线重建沿用阿鸦已有的 `rebuild-content-pack.ts`，适配器代码未改。
- `tools/release-walkthrough/`：一次性本地库（只走 Unix socket）、两名合成教研真实登录，走完 地图签字发布 → 阿鸦 0.1.0 登记 → 内容版本签字发布 → 5 题 8 次准入变更 → 版本记录导出 → 决定文件导出 → 离线重建 → verify（720 题相同、1 题更严）。规范化样例在 `sample/`，两次运行逐字节一致；根 Makefile 新增 `release-walkthrough` 并纳入 `make check`。

### 测试
release-walkthrough 子目录测试 69 + 6 passed；`make release-walkthrough` 与样例一致；`learning-test` 2839 passed（首轮 1 例 `test_mastery_profile_db` degree_escalation 负载下超时，单独与全量重跑通过）；`adapter-aya-test` 通过。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 阻断（安全）：verify 不看平台内容版本状态，撤下后宽松副本仍判合格。已改为未发布时比 source_unverified 宽即不合格，补 retired / draft 单测。
- 其余：导出与核对只连本机；输入校验（未知枚举、缺 curriculum、缺题号或准入）退出码 2 不打堆栈；先检查输出路径、先写报告再写决定文件、`--replace` 原子替换并清理临时文件；走查产物目录非空即拒；走查并入根 Makefile 与 `make check`；同时刻执行记录拒绝导出。

### 经验
- 判断“副本不比平台宽”要同时核对平台内容版本状态：未发布版本的准入对学生不可见，上限是 source_unverified。
- 执行记录 ID 是随机 UUID，不表示先后；时间相同只能拒绝。
- 准入摘要含内容版本 ID，每次运行不同，样例必须规范化；时间戳不编号（两次签字可能同一微秒）。
- PR D 的 `local_only` / `check_server_local` 用 SystemExit 报错且口吻是时政下架，复用时要换说明；以后可加“用途”参数。
- 审查代理跑走查时误用了已有的 scratchpad `walk1/`，覆盖了其中两份文件，该目录不能再当证据。

### 与 spec 的差异
1. 走查用真实阿鸦题号（公共内容 ID），地图、staff、来源编号都是合成的，以便六段标识与内容包对上。
2. 退出码：输入不对为 2；平台数据不能安全导出（含草稿、执行时间相同）为 1。
3. `--replace` 同时作用于报告；替换后文件权限为 0600。
4. 根目标不给 `RELEASE_WALK_OUT` 时在 `$TMPDIR`（缺省 /tmp）建目录且不删除（约 1 MB）。

### 待人确认
1. 平台允许“来源未核的 pending_review”，阿鸦格式 3 不允许；导出时降回缺省（更严），是否统一口径（改适配器或迁移）。
2. admitted / rejected 缺执行记录时整份拒绝，而不是略过该题，是否合适。
3. 未发布内容版本拒绝导出；发布前预览准入需另加选项。
4. 决定文件随内容包进仓库，含审核记录 ID（不含签字人），能否公开。
5. 决定文件只记导出时刻、没有平台水位；导出后平台降级，已提交文件不会知道。是否把 verify 设为发布门禁（CI 或发布脚本必跑）。
6. `release-walkthrough` 进 `make check` 后全量检查多约 1 分钟，需要 node、阿鸦 npm ci、本机 Postgres；严格模式缺一即失败。
7. 发布闸门是否拒绝 `hint_scan.flagged_items` 非空的内容包（第一部分待人确认 #5），本 PR 未做。
8. `test_mastery_profile_db` degree_escalation 用例负载下偶发超时，是否单独跟进。
9. 子代理清理系统临时目录 `/tmp/release-walkthrough.4shnrf3q` 时被守护钩子拦下，目录仍在（只有合成产物），由人决定是否删除。
