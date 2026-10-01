# 冲刺 5-C+D 端到端联调 · lessons

## 实现摘要（2026-09-30）
- 新增 `tools/e2e-cohort/`（生成器、回放、断言、只读核对、证据包、README、RUNBOOK、合成样例 `sample/`）、
  `services/learning/tests/test_e2e_cohort.py`（随 `learning-test` 运行）、Makefile 追加 `e2e-cohort`、`gate0-evidence` 两个目标；
  没有改契约、迁移、判分与掌握逻辑，没有改任何已有文件（Makefile 只追加）。
- 一轮合成联调：20 名学生、144 次契约调用（数学诊断 20、数学批改 36、语文诊断 20、作文批改 16、道法批改 20、复测 32），
  514 条证据、104 条判分；十二条断言全部通过；整轮重跑 144 次全是重放、适配器零调用、学习数据与计量零新增；
  两次独立运行（各自新的一次性 Postgres）证据包逐字节相同，pytest 共享库里的渲染也与样例一致。
- 断言自身的有效性有反例测试：私有表（学生键列、`evidence_id` 键、值藏在 jsonb、带双引号的标识符）、个人层里以学科名开头的表、
  insight 白名单外的学生键表、跨家庭绑定（隔离 + 绑定来历两条断言都报）、`USING (true)` 放行策略（学生、家长、两种未登录都报）、
  名单外孩子的应见集合、陌生连库角色、适配器依赖数据库驱动、缺任一溯源字段、重跑多一行计量、一次性库启动失败时的清理，都能被发现。

## 审查修改（2026-09-30，安全审查与契约审查按默认建议）
- 安全 M1：新增 `guardian_links_backed`（生效绑定既不是家庭创建人、也没有已使用邀请的条数，只输出条数）；RUNBOOK 第 3 节写明局限。
- 安全 M2：`verify` 名单有学生找不到返回 1；RUNBOOK 第 1 节写明核对角色须不受 RLS 读个人层（BYPASSRLS 或定义者视图），列为必停项。
- 安全 M3：`verify` 必填 `--expect-db` 与 `current_database()` 比对；拒绝超级用户与非只读会话；输出目录在仓库内拒绝写入（`e2e_cohort/verify.py`）。
- 安全 L1–L4：隔离按个人表清单逐表比对（清单未建回退 6 张并标注）；放行策略反例与“从不 set_config”的未登录；标识符转义、查找值为空判失败；
  一次性库 `start()` 移进 `try`。
- 契约 M1：insight 不再整体豁免，只豁免白名单（判断账本 0010/0032/0033/0034 与分表事实 0022/0025 的 12 张表）；
  `platform` / `insight` 里以 `aya_ / wenli_ / zhixing_ / echo_` 开头的表直接判失败。
- 契约 M2：查找值加本批学生的证据 ID、作答 ID、家庭 ID、家长账号 ID（按 UUID 词元比对，大小写不敏感）；学生键列名加
  `evidence_id / grade_result_id / user_id / learner_id`。
- 契约 M3、M4：见下文“跨任务依赖”；复测事件在 `events.csv` 里带“（桩）”后缀。
- 契约 L1–L6：`all_calls_via_contract` 改名 `all_calls_succeeded`；新增“联调期间连库的只有学习服务登录角色与管理员”
  （临时实例开连接日志 + 结束时 `pg_stat_activity`）与“适配器 package.json 无数据库驱动”；内容登记 SQL 前加 `SET ROLE starbuddy_learning`；
  `check_retest` 包只读事务；`verify` 家长应见集合取其全部生效绑定再与名单取交集；三科适配器都设 `<科>_CONTRACT_VERSION=1.0.0`；运行时长统一见“运行备注”。

## 跨任务依赖
1. **个人表清单（迁移 0035，草稿）**：私有副本扫描 (c) 与隔离逐表比对都读 `platform.personal_table_registry`。
   清单不存在的库上：(c) 标“待 0035”并跳过，隔离回退 6 张表（档案 + 5 张学习数据表）并在 `table_source` 标注，均不报错（有测试）。
   0035 改动清单内容（增删表、改谓词）会改变 `checks.json` 里的比对表数，需重生样例。
2. **样例的工作区**：本次 `sample/` 按**当前工作区**重生——工作区里有其他任务未合入的改动（db/、bff、web、契约等，
   `run_metadata.json` 里 `worktree_dirty = true`）。0035 定稿合入后，应在**干净工作区**重生一次并在 PR 里说明差异。
3. **KGE-04 第二步合入时必须重生样例**：复测改由真实调度产生后，`events.csv` 的“（桩）”后缀、`retests.csv` 的调度方、
   `run_metadata.json` 的 `retest_scheduler` 都要改，节点状态也会从 `evidence_only` 变为掌握状态；届时去掉桩并重生。

## 偏离 spec（按默认处理，需人知悉）
1. **道法没有“诊断”**：知行适配器的 diagnose 只支持 `mode = process`，学习服务 1 期只接受 `intake / gap_test`（`service.py` 的同步诊断限制），
   所以道法只走批改与复测；“三科诊断”实际为数学、语文两科诊断 + 道法批改。三科事件并存的断言不受影响。
2. **诊断走单次 `/internal/v1/diagnose`（intake）**，没有走 SIE-02a 的自适应诊断会话接口（会话只覆盖数学、需要合成题面呈现器）。
3. **星图截图已接上（2026-10-01，用户确认“接上星图截图”；spec #4 默认用 3-H / 4-H 现有星图）**：`make gate0-evidence GATE0_STARMAP=1`
   在联调完成后的同一一次性库上起 BFF 与前端测试构建，本机 Chromium 经真实会话（+999 合成号码、短信捕获桩）截学生视角 3 张 + 家长视角 1 张，
   写到 `starmap/`（`tools/e2e-cohort/e2e_cohort/starmap.py`、`apps/web/scripts/gate0-starmap.ts`）。页面是**星图 v0（3-H），掌握未写库，无点亮星**。
   - **v0 截图证明什么（如实）**：只证明真实会话 + RLS 能打开该学生页（同一会话打开另一户学生的星图页与 BFF 星图接口都是 404，页面与接口各一）、
     星空等于库里 `subject_enrollment.status = 'active'` 的在读科目（有已登记能力点的科目，严格相等）。v0 每片星空画该科全部已登记能力点、
     全部中性底星，页面上没有只属于某名学生的星，三名学生的学生视角截图逐字节相同；对账规则 3–6（节点 = 登记表、证据节点 ⊆ 页面、
     带状态的星 ⊆ 证据节点、带状态的星 = 0）在 v0 下**与学生无关或空真**，`starmap.json` 的 `reconciliation_rule` 逐条写明。
     Gate 0 硬1“同一学生模型”主要由 `events.csv`、`nodes.csv`、`checks.json` 证明。
   - **H5 星图 v1 合入或掌握写库（KGE-04 第二步）后**：规则 5“带状态的星 ⊆ 该学生证据节点”才真正生效，规则 6 要改成按掌握状态核对；
     **样例必须重截**（`make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample GATE0_STARMAP=1`）。
   - **重截规则（2026-10-01 用户确认默认）**：只在星图页面改版时重截；不带 `GATE0_STARMAP=1` 重生样例时保留 `sample/starmap/` 不删，
     `starmap/provenance.md` 写截图的页面版本与截取提交号，与本证据包提交号不同时标“截图沿用”，页面版本变了标“须重截”。
   - **节点身份**：截图脚本用同一会话 cookie 取 BFF 星图 JSON，以其中 `skill`（科目:local_id）为节点身份；页面 DOM 只核对行数、三态、保持计数与 JSON 一致；
     登记表另取 `label_code` 建 local_id → label_code 映射核对，不假设二者相等。`registered_nodes` 注明来源（公共登记表 `platform.skill_node`，
     一次性库管理员连接只读查询）并附各科 `content_version`。
   - **截图前置在临时库里写数据**：给选中学生的合成家长写 `platform.account_contact`（+999 号码）、建 `bff_app` 登录角色（只继承 starbuddy_bff）；
     `seed_logins` 断言库是 `pgharness.EphemeralPG` 的 `starbuddy_test`，否则拒绝。这些写入发生在联调断言与证据导出之后，不影响证据包数据。
   - **星图记录放 `starmap/starmap.json`，`run_metadata.json` 只放固定的指针**：`test_e2e_cohort.py` 把 `run_metadata.json`、`summary.md` 与样例逐字节比对
     （只豁免提交号与工作区状态），该测试不在本次可改范围；所以截取提交号与“截图沿用”写在 `starmap/provenance.md`，`summary.md` 只写固定的说明与指向。
     截图**失败**时删掉本轮已写的截图，`run_metadata.json` 与 `summary.md` 另标 `status = failed` 与原因，并非零退出；不写 skipped。
   - **安全**：BFF、next start、next build 都以新进程组启动，结束时 `killpg` SIGTERM、超时 SIGKILL；`apps/web/.next-e2e` 开头 `os.mkdir` 原子占位，
     已存在即 failed，只删自己建的目录；`apps/web/.env*` 存在时拒绝运行（只看文件名）。与 `make web-test` 不能同时跑。
   - **文案定位依赖**：截图脚本靠界面文案与 data-testid 定位（登录页“手机号”“获取验证码”“登录”、家长首页“打开学生模式”、
     `data-testid="login" / open-student-* / watermark`、`section.sky`、`.sky-field svg.star`、`tbody tr`），与 `apps/web/e2e/session.ts` 同源；
     这些文案或结构改版时截图会失败（记 failed），需同步改 `apps/web/scripts/gate0-starmap.ts`。
   - 对账与护栏单测（`tools/e2e-cohort/tests/test_starmap.py`）不在 `learning-test` / `e2e-cohort` 的收集范围（两个目标不在本次可改范围），
     由 `gate0-evidence` 带 `GATE0_STARMAP=1` 时先跑；**后续**：并入 `e2e-cohort` 目标。
4. **节点查询的“状态”**：KGE-04 第二步（掌握写库）前一律 `evidence_only`（有 `mastery_estimate` 当前行时显示其 state），
   不调用掌握纯函数算状态，避免证据包被读成掌握结论。
5. **计量零新增是“没有经过 BFF 的零”**：学习服务内部接口不做预留、结算，回放直接调学习服务，所以计量三表（`usage_reservation`、
   `usage_ledger`、`practice_log`）在这批学生下始终为 0；断言能发现增长（反例测试），但真正的“消耗类请求带 usage_id 与幂等键、
   预留 → 结算 → 回滚”的幂等仍由 `db-test`（70、78）与 `bff-test` 覆盖。**后续**：BFF 的学习写入路由稳定后，让回放改经 BFF（带 usage_id 预留）。
6. **年级只写在学生档案上**：三科内容包目前只有九年级（`content_version.grade = 9`），七、八年级合成学生也用九年级内容；
   学习服务不核对“学生年级 vs 内容年级”（见契约缺口 5）。
7. **英语**：部分合成学生选了 echo（只写 `subject_enrollment`），断言英语零事件；没有英语适配器参与。

## 契约与接口缺口（登记 v1.x 评审，本任务不改）
1. **grade 摘要不带 `confidence / source_version / model_version`**：learning-internal v1 的 `SummaryBase` 只有 `evidence_ids`、
   `human_review_required`（spec 待人确认 #6 的最小摘要）。溯源断言因此在两处做：适配器 → 学习服务的响应体（契约边界，内存里检查）与
   `platform.grade_result` 落库行。BFF 侧无法逐条核对五字段；是否在摘要里加只读的溯源字段，交 v1.x 评审。
2. **没有“按学生查节点”的读接口**：节点查询目前只能用只读 SQL 聚合 `evidence_event / skill_observation / mastery_estimate`；
   KGE 或洞察服务的画像视图接口上线后，节点查询应改走接口（规则 11 的读路径）。
3. **没有复测调度契约**：`retest_task` 与调度在 KGE-04 第二步；本任务用 `StubRetestScheduler`（实现 `RetestScheduler` 协议）决定复测，
   执行经 `ingest.purpose = retest`。第二步合入后换真实调度，执行与断言不变；验收“复测切真实调度”是后续项。
4. **道法 `process` 诊断没有平台入口**：适配器声明 `diagnose: process_mode_only`，学习服务不接受 `process`；需要评审
   “道法过程诊断”走学习服务同步 diagnose 还是异步任务。
5. **学生年级与请求内容版本不核对**：`context.content_version.grade` 与 `student_profile.grade` 可以不一致而照常写入；是否应拒绝或标记，交评审。
6. **适配器能力声明的默认版本**：三科适配器的能力声明默认都报 `0.9.0`（`DEFAULT_CONTRACT_VERSION`），`1.0.0` 是候选版
   （需 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`）；现有单科 e2e 只有语文、道法以 `<科>_CONTRACT_VERSION=1.0.0` 启动。
   本联调三科都设同一环境变量，运行元数据里三科能力声明一致为 `1.0.0`。v1.0 签字后默认版本与候选开关一并收口，需重生样例。
7. **个人层的归属**：私有副本扫描把 `platform.*` 整体、`insight.*` 的 12 张白名单表当作个人层；ADR（个人层放 platform 还是 kge/insight）
   定稿后白名单要随之调整。

## 待人确认
1. **真实内测的只读核对登录**（RUNBOOK 第 1 节）：需要不受 RLS 读个人层（`BYPASSRLS` 或 `SECURITY DEFINER` 只读视图，二选一）、
   读公共层与 `platform.personal_table_registry`（现只授予 authz、eraser）、能 `SET ROLE starbuddy_bff`。
   这是 RLS 与权限策略变更（必停项），本任务只写建议形态与合成库上的同形夹具，不写迁移。
2. **家长绑定是否“对的人”**：`guardian_links_backed` 只能核对绑定有来历（家庭创建人或已使用邀请）；监护关系真实性需对照线下同意书人工核对，
   流程由合规确认（RUNBOOK 第 3 节）。
3. **Gate 0 证据取舍**（spec #1）：合成证据包（本任务产出）与真实内测汇总（RUNBOOK）哪一份作为“同一学生模型”的证据。
4. **“四科”改“三科”**（spec #2）：**已决定（2026-10-01 用户决定：Gate 0 “四科”改为“三科”，英语搁置不计）**。证据包按三科出，Gate 0 硬1、软·契约覆盖率（开口成章 ≥4/6 不计）、软·发布闸门均按三科。
5. **私有副本扫描的范围**：结构扫描看列名（学生、作答、证据、判分、家庭、家长、账号、user / learner 共 10 个），
   取值扫描看本批学生相关 ID 是否出现在个人层以外任何表的任何列；个人层内以个人表清单为准。
   适配器没有数据库（package.json 无驱动、进程环境无连接信息、联调期间没有别的角色连库），进程内只有幂等缓存（指纹与判定结果，不落盘）——
   是否还要扫适配器侧的其他持久化（目前没有），请确认。
6. **样例随内容变化**：内容包、适配器版本、契约版本、个人表清单变更会让 `sample/` 不一致而使 `learning-test` 失败；这是有意的确定性守护，
   变更方需在 PR 里重生样例（`make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample`；截图保留并标“截图沿用”，星图页面改版时才带 `GATE0_STARMAP=1` 重截）并说明差异。
7. **星图截图能否作为 Gate 0 硬1 的证据**：v0 截图不含任何只属于某名学生的星，只能证明会话、授权与在读科目；
   默认**只作辅证**，硬1“同一学生模型”以 `events.csv`、`nodes.csv`、`checks.json` 为准。H5 v1（星上带掌握状态与判断）合入重截后是否升格，请确认。

## 运行备注
- `make e2e-cohort` 约 1 分钟（一轮联调约 30 秒，其余是反例与 verify 护栏测试）；`make gate0-evidence` 约 30 秒（含新建一次性 Postgres 与三科适配器），
  带 `GATE0_STARMAP=1` 约 1 分钟（另含前端测试构建与 4 张截图）；样例截图 4 张共约 450 KB（JPEG 质量 60，单张 ≤ 120 KB）；
  它们也随 `make learning-test` 运行，使其多约 1 分钟。
- 隔离断言每个查看者约 0.35 秒（RLS 函数逐行求值），20 名学生一轮约 15 秒；反例测试用整户子集。
- 联调期间临时设置 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`（插件契约 1.0.0 仍是候选版），结束后恢复，不影响同一 pytest 会话里的其他测试；
  一次性库上开启 `log_connections` 只影响该临时实例。

## 补记（冲刺 5-H 跨科周报 v0，迁移 0036）
- `insight.weekly_report`（周报快照，判断的派生视图，登记在 0035 个人层表清单）加入 `tools/e2e-cohort/e2e_cohort/checks.py` 的
  `INSIGHT_PERSONAL_TABLES`；否则“没有私有副本”检查把它当成个人层以外的表（它有 student_id 列）而失败。
- 合成样例 `tools/e2e-cohort/sample/` 按 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample` 重生：只有隔离断言比对的表数
  25 → 26（多了周报表）、提交号与 `checks.json` 摘要变化，其余逐字节不变。
