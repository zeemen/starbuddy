# 冲刺 5-H 信任界面 · lessons

## 前端：“为什么”“我不同意”、考试录入与对账卡、家长数据权利（spec 范围 1、2、4；周报第 3 项本次不做）

PR 首行应写 `[NEEDS-HUMAN]`：涉及画像可见范围（学生 / 家长各看到什么）、异议与对账卡的话术（全部是占位，人写人审）。
本次只改 `apps/web/` 与本文件；没有改 `services/`、`db/`、`packages/contracts`、`Makefile`。

### 做了什么
- **开关**：全部挂在 4-H 的信任界面开关后面（`apps/web/src/shell/features.ts`，默认全关、只在 dev / test 可开）。关时：入口组件返回 null
  （无元素），BFF 客户端不发请求（`src/lib/trust-api.ts` 的每个调用先过开关），页面在读会话之前 404（`src/shell/guard.ts` 的
  `requireTrust`），下载路由 404。`features.ts` 注释改为“开关关时 BFF 不注册对应路由”，并写明 why / weekly 在 BFF 没有新路由。
- **“为什么”**（开关 why）：`/s/{id}/why/{claim}`、`/family/{id}/why/{claim}`（`src/trust/WhyPage.tsx`）。入口 `ClaimLinks` 放在今日计划的
  判断理由旁（`TaskList`，含家长孩子页与数学科目页）。显示：陈述（只取判断的 `text`，带 `data-claim-id`；待验证是虚线，不显示结论文字）、
  状态（学生看“初步判断 / 待验证”占位）、家长另附置信度说明占位（`CONFIDENCE_BAND`）、证据链（时间、证据种类、证据等级、知识点编号四列，
  `view.ts` 只挑这四个字段，对话与作答原文进不来）、依据版本。读不到 / 不可见 / 不存在一律“暂时无法获取”，也不显示异议按钮（不暴露存在性）。
- **“我不同意”**（开关 dispute）：在“为什么”页下面，学生三张表单（`i_know` / `one_off_slip` / `data_error`），家长两张（没有 `one_off_slip`）；
  每次渲染生成幂等键；结果只显示 `next_step` 编码映射的占位。异议状态页 `/s/{id}/disputes`、`/family/{id}/disputes`（类型、状态、提出与结束日期、
  对应判断的“为什么”链接；结论码不显示）。
- **考试录入与对账卡**（开关 reconcile）：`/…/exams`（考试安排：学科只有数学、语文、道法，类型、日期、满分——家长按年级默认 100 / 120，
  学生会话拿不到年级默认 100——范围节点编号）→ 保存后进 `/…/exams/{exam_id}`（成绩录入、预测区间、对账卡）。预测与对账卡外层挂预测所引判断的
  `data-claim-id`；对账卡句子每句带自己的 `claim_id`，`view.ts` 再断言一次（无 ID 句丢弃、方向 / 调整出现契约外编码整卡不显示、所依据的预测
  对不上整卡不显示），组件里再挡一次；偏差方向只显示四个编码之一对应的占位。BFF 读取通道缺省 503 → “暂时无法获取”。
- **家长数据权利**（开关 data_rights，只给家长、只对会话里已绑定的孩子）：`/family/{id}/data`（`src/trust/DataRightsPage.tsx` + 客户端面板
  `DataPanels.tsx`）。导出：生成 → 一次性下载凭据只在本页客户端内存里 → 点“下载”由本页 fetch POST 到本前端的 `/family/{id}/data/download`（核对 CSRF，
  转 BFF 兑换，回 no-store、附件、nosniff；凭据失效 404，页面提示重新生成）。删除：申请 → 勾选“已了解”后二次确认
  （BFF 要求重新登录时显示引导与登录链接）→ 冷静期倒计时（页面渲染时的剩余天、小时）→ 撤回；回执（执行后才有）只显示表名与行数、范围占位。
- **入口行** `TrustNav`：学生壳顶栏下（考试、我提出的异议）、家长孩子页（另加数据权利）；周报仍是 `TrustSlot` 预留位。
- **文案**：`src/shell/copy.ts` 新增 `TRUST` 等常量与 `ALL_TRUST_COPY`，全部是 `{{TODO_COPY:键}}` 占位（界面标签在占位后附中性提示便于走查）；
  异议类型、下一步、异议状态、偏差方向、平台调整、证据种类只映射到编号键（如 `{{TODO_COPY:dispute.kind.2}}`），不附提示、键名不含编码原文。
  数字、日期、编号不拼进文案，放在 `data-value` 元素里。
- **测试**：`tests/trust.test.ts`（14 项：前端再断言、表单解析、删除状态与倒计时、开关关时客户端不发请求、每个页面先过闸门、BFF 调用只在
  trust-api、编码不上页面与禁用词、组件无中文、`data-claim-id` 只在登记的组件）；`tests/shell.test.ts` 两处随之更新（`data-claim-id` 渲染组件
  名单、无中文检查含 `src/trust`）；壳文案扫描（`personas.guard`、`sie_lexicon`、比较 / 营销词表）自动覆盖新文案。
  端到端：`playwright.config.ts` 分两套项目——`trust-off`（原有全部用例 + `e2e/trust-off.spec.ts`：无元素、无请求、页面与下载路由 404、
  BFF 路由 404）与 `trust-on`（`e2e/trust-on.spec.ts`：4 项，学生 / 家长 / 另一户 / 数据权利全流程，每页 axe、同源、
  “信任界面区域的每个文本节点要么是文案、要么在 `data-claim-id` 或 `data-value` 里”、页面上没有编码原文）。`scripts/e2e.sh` 在同一个一次性库上
  另起一个开关打开的 BFF（`STARBUDDY_TRUST_UI=dispute,reconcile,data_rights`）；`e2e/session.ts` 的补同意按页面所在的那一套选 BFF 与来源。

### 偏离（请确认）
1. **“为什么”没有 BFF 读取路由**：PR③ 只做了 dispute / reconcile / data_rights，4-H 的 `insight.explain` 读取只留陈述、丢弃证据链。
   下面是**前端提议的形状**（`src/trust/view.ts` 的 `pickWhy` 按它核对），**由 BFF 的 PR 写进 `trust.v0.yaml` 定稿**，前端随定稿调整：
   - `GET /bff/v0/students/{student_id}/claims/{claim_id}/explain`，开关 why，会话（学生本人 / 有效绑定家长），no-store；不可见、不存在、不属于该学生同一个 404；
   - 200：`{ student_id: uuid, claim_id: uuid, statement, evidence_chain, source_version }`，`additionalProperties: false`：
     - `statement`：`claim_id`（uuid，须等于路径里的）、`status`（`hypothesis` / `established` / `to_verify`）、`display`（`solid` / `dashed`，待验证只能 dashed）、
       `text`（1–300 字，模板渲染）、`confidence_band`（`few_evidence` / `moderate` / `strong`）——即契约 `RenderedStatement` 去掉模板键与版本；
     - `evidence_chain`：最多 200 条，每条只有 `occurred_at`（date-time）、`source_type`（`objective` / `self` / `guardian` / `inferred`）、
       `evidence_level`（`A` / `B`，可缺）、`node_id`（知识节点编号，可缺）；**不转** `evidence_id` 以外的任何字段，更不转对话与作答原文（前端也只挑这四个）；
     - `source_version`：依据版本（`^[A-Za-z0-9:@._-]{1,64}$`）。
   在 BFF 补上之前一律 404 → “暂时无法获取”，异议按钮也不出现；**dev 默认不开 why**（`features.ts` 注释写明），端到端的 trust-on 打开它只为核对
   这一点。所以端到端里“为什么”与“我不同意”只覆盖到“暂时无法获取”，异议的提交走单测与静态检查。
2. **星的详情位没有入口**：现在的星图仍是 3-H v0 形状，星上没有 `claim_id`（7.2 的 v1 才带）；按规则 11 不渲染没有判断的入口。原来开关打开时
   星图下方那个不可操作的占位也去掉了（它不挂在任何判断上）。v1 切换时每颗非中性的星旁放 `ClaimLinks`。
3. **异议结果不单独成页**：提交后在该表单的状态区显示 `next_step` 占位，并给“查看我提出的异议”链接（结果不经地址栏，与壳的其他表单一致）。
   `verify_by` 没有显示（没有文案）。
4. **“数据有误”的原始事实选择与更正、异议撤回没做**：BFF 不转发 `fact_ref`、没有撤回路由（SIE-03 lessons PR③ 待人确认 6、7）。
5. **考试安排没有列表**：BFF 只返回新建的 `exam_id`，没有列出已录安排的接口；录入后直接进这场考试的页面，以后找不回来（需 BFF 列表路由）。
   范围节点靠手填编号（没有给前端用的节点选择接口）；“同一场考试已有安排时返回原安排”由库函数保证。
6. **学生端不显示置信度说明**：spec 写“学生版只显示区间”，预测区间与对账句的置信度说明只在家长端。
7. **二次确认**：用“勾选已了解 + 确认按钮”（没勾选按钮不可用，服务端再核一次勾选），确认凭据绑定本会话（BFF）；短信复核现有接口不支持（PR③ 偏离 2）。
   “需要重新登录”的引导是：重新登录后再申请一次（未结束的申请会返回原申请）并确认——因为确认凭据绑定会话，换会话后原凭据失效。
   这条在端到端里没有覆盖（两套 BFF 的会话密钥不同，同一个会话造不出“不新鲜”），只有静态与单测。
8. **导出下载经本前端转一次、用 fetch 而不是表单导航**：浏览器不直连 BFF，下载由本页 fetch POST 到 `/family/{id}/data/download`，它转 BFF
   兑换再原样回（no-store、附件），浏览器端存成文件。第一版用普通表单提交，BFF 回 403：全站 `Referrer-Policy: no-referrer` 下表单导航的
   `Origin` 是 `"null"`，BFF 的 Origin 核对拒绝；fetch 总带真实 Origin（另显式 `referrerPolicy: 'same-origin'`）。凭据不在页面 HTML 里（端到端断言），兑换一次即作废。
9. **依赖库里的异议去重**：前端每次渲染表单生成一个幂等键，BFF 按账号派生后转洞察服务（进程内缓存）；重启、多实例或重新打开页面后的重复提交，
   靠库里“同一人同一判断同一类只有一条未结束异议”（0032 `dispute_active_uq`）去重，前端不另做。

### 待人确认
1. 上面偏离 1（“为什么”的 BFF 读取路由与响应形状）、5（考试安排列表与节点选择）。
2. 全部占位文案（`{{TODO_COPY:…}}`）：入口、状态、异议三条路、下一步、异议状态、考试表单、预测与对账卡、偏差方向、平台调整、证据种类与等级、
   数据权利、删除状态与撤回原因、回执范围。**删除回执的表名是否要按范围合并成人能读的条目**（PR③ 待人确认 4）。
3. 学生端能否看到“待验证”的判断的“为什么”（现在：看得到状态与依据，不显示结论文字）；学生端是否需要预测与对账卡（现在：只看区间与对账卡，无置信度说明）。
4. 家长端对“对账卡”的可见范围（PR② 偏离 6，`reconcile_visibility` / `card_audience`）签字前，BFF 本身会让卡片不显示。
5. 可用性走查（spec #7）：走查脚本与结果随范围 4 的走查补；本次只提供合成家庭上的端到端。
6. **对账卡结构部分的依据**：现在区间内外、偏差节点、偏差方向、平台调整都挂在预测所引的判断上（外层一个 `data-claim-id`），只有句子各带自己的
   `claim_id`。另一种做法是每个偏差方向（及调整）各带一条判断 ID（需要契约与 0034 改动）。默认前者，待人定（契约审查 M2）。
7. **“无请求”的口径**：开关关时，“无请求”指浏览器不发任何信任界面请求、前端服务端也不向 BFF 发信任界面请求（客户端先判断开关，单测用假 fetch 断言零次）；
   页面本身（壳的其他读路由，如 `/me`、今日计划）照常请求。端到端只能看浏览器侧的请求，服务端侧靠单测与静态检查（安全审查 7）。
8. **外显的编号与版本**（安全审查 8）：“为什么”证据表显示知识点编号（`MATH.FUNC.QUAD` 这类）与依据版本（`algo:…@x.y.z`），对账卡显示偏差节点编号，
   删除回执显示表名（`insight.claim` 这类）。默认应改为节点名、回执按范围合并；但 BFF 不给节点名，前端也没有“表名 → 范围”的映射
   （0035 的清单在库里、回执只分别给表名与范围列表），所以**暂时保持编号与表名原样**，登记待人定：由 BFF 补节点名 / 按范围汇总的回执，或前端单处维护映射。
9. **WebKit 与 Firefox**：数据权利两个用例（标 `@data-rights`）可在 WebKit、Firefox 上跑（`STARBUDDY_E2E_BROWSERS=webkit,firefox`，
   `playwright.config.ts` 按需加项目）；**本机只有 Chromium 二进制**（`/opt/pw-browsers` 下只有 chromium），按要求没有执行 `playwright install`，
   所以这两个浏览器没跑过，需要在有二进制的环境（CI）跑一次。

### 测试结果（本机）
- `apps/web`：`tsc --noEmit` 通过；`node --test tests/*.test.ts` 79 项通过（`tests/trust.test.ts` 18 项，含审查修改后新增 4 项）。
- `make web-test`：许可证、`next build`（生产构建）、构建产物扫描、单测、端到端 **46 passed**（trust-off 42 项含原有 40 项与 `trust-off.spec.ts` 2 项；
  trust-on 4 项）。第一轮 45 / 46：数据权利的下载 403（偏离 8），改成 fetch 后全量重跑通过。
  审查修改后全量重跑 **47 passed**（trust-on 新增“没有 JS 时”一项；学生越权、下载路由非 POST / 学生会话、开关关 GET 的断言并进原用例）。
- `make check-fast`：通过（contract-test 因本机缺 jsonschema 跳过，既有情况；本次未改契约）。严格检查（`make check`）由主会话统一跑。

### 文案自查（禁语 A–G）
新增文案全部是 `{{TODO_COPY:…}}` 占位；附带的提示只有中性的界面标签（“考试日期”“满分”“申请删除”等），过了 `personas.guard`（platform 口径）、
`sie_lexicon`、壳的比较 / 评价 / 营销词表与 `tests/trust.test.ts` 的禁用词（粗心、马虎、失误等）；异议类型、偏差方向没有提示文字，页面上不出现编码原文。

## 周报：跨科周报 v0（spec 范围 3）与可用性走查脚本

PR 首行应写 `[NEEDS-HUMAN]`：新迁移草稿 0036（新表、RLS、复核队列约束与结案码、个人层表清单登记）、画像可见范围（周报内容、学生 / 家长各看到什么）、
周报标题 / 段落衔接短语 / 排除词表 / 页面文案（全部占位，人写人审）、复核抽检口径（#4）。按已确认的 #2、#3、#4、#5、#7 默认执行。

### 做了什么
- **迁移草稿** `db/migrations/0036_insight__weekly_report.{up,down}.sql`：`insight.weekly_report`（学生、周一到周日、受众 student / guardian、
  句子数组每句带 `claim_id` 与段落、客观栏标 `source_type = objective`、算法与策略版本、状态 generated / withheld、抽检标记 `spot_check_task_id`）。
  只由 `starbuddy_insight` 写（列级 INSERT，没有 UPDATE / DELETE）；守卫逐句核对判断（同一学生、成立或假设、未过期、知识节点、模板已审核且键与版本对得上、
  可见范围含受众；学生版不收来源为家长观察的判断）、只追加（抽检标记只能由抽样函数写一次）、删除只随删除请求；RLS：学生本人读学生版、有效绑定家长读家长版，
  只读 generated，BFF 读不到阻断编码 / 版本 / 抽检标记。登记进 0035 个人层表清单（delete，回执范围 reports，不进导出包）。
  复核队列：`source_kind` 加 `weekly_report`（只以已有原因 `spot_check` 进队，不新增枚举值）、结案码 `weekly_report_ok` / `weekly_report_issue`、
  `review_task_detail` 加周报分支（受众、周、渲染句、客观栏，不给学生 ID）；抽样函数 `platform.review_sample_weekly_reports(周, 比例, 最少份数)`（属主 authz，只授洞察服务）。
- **DB 测试** `db/tests/79r_weekly_report.sql`（写入守卫、形状、只追加、删除分支、按受众读、抽检份数与幂等、审核台详情与结案码）；
  `80_attack_surface.sql` 可见性矩阵登记周报；渗透夹具每个学生两份周报；快照更新；`db/tests/mutations.sh` 加 22 条“周报：”变异；
  `79e` 结案码条数 6 → 8；`run.sh` 的 0035 角色断言测试库改为只执行 0035 之前的迁移（0036 依赖 0035 的清单）；`docs/security/pentest/scope.md` 登记新函数（94 个）。
- **洞察服务** `insight/weekly.py`（纯函数：固定段落 + 衔接短语占位 + 判断渲染句；复用首份报告的 `render_text`、排序与
  `learning.mastery.consistency.check_page`，另查标题禁用推断与按受众的排除词；任一不过 → withheld）、`insight/store_weekly.py`（读可见判断与客观记录、
  写快照 ON CONFLICT DO NOTHING、生成后抽样）、`jobs.weekly_reports`；配置 `config/weekly.synthetic.json`、`config/weekly-check.synthetic.json`（合成占位，只 test）。
  测试 `tests/test_weekly.py`、`tests/test_weekly_db.py`；变异 +20 条。
- **BFF** `GET /bff/v0/students/{student_id}/weekly-report`（开关 weekly，`src/http/trust-routes.ts` + 纯函数 `src/trust/weekly.ts`）：按受众读最近一周已生成的，
  每句先挑形状（无 `claim_id` 丢弃）、再在库里核判断（可见、成立或假设、未过期、节点、模板已审核、学生版排除家长观察来源），再过对外模板白名单，丢弃数记日志；
  一句不剩 `report: null`。`openapi/trust.v0.yaml`、授权矩阵（从路由表生成，自动覆盖）、限流、变异 +9 条。
- **前端** `/s/{id}/weekly`（成长手记）、`/family/{id}/weekly`（周报）：`src/trust/WeeklyPage.tsx`，开关 weekly 关时 404、不发请求；每句 `data-claim-id`、
  旁边“为什么”入口（开关 why）；家长端附置信度说明，学生端不附；客观栏标“来自使用记录，不是判断”，数字在 `data-value`；入口行加“成长手记 / 本周周报”，
  家长孩子页原来的周报预留位去掉。文案全部 `{{TODO_COPY:weekly.*}}`。端到端：trust-on 新增周报用例（合成种子 `db/seeds/dev/weekly-synthetic.sql`，
  第二个 BFF 用把合成模板列进白名单的显示规则），trust-off 加周报页与 BFF 路由 404。
- **可用性走查脚本** `specs/trust-ui-sprint5/walkthrough.md`：学生、家长各 5 个任务、成功标准、建议通过线、记录表与问题登记；结果由产品执行后填。

### 偏离（请确认）
1. **复核任务的学科**：`platform.review_task.subject` 必填，周报是跨科的——抽检任务的学科取周报第一句所引判断的学科（路由快照，首发只有数学）。
   另一种做法是放开 `subject` 可空（改 0006 约束，审核台按学科筛选时周报会消失），默认前者。
2. **抽检原因复用 `spot_check`**，没有新增 `review_reason` 值（避免 ALTER TYPE ADD VALUE 回滚不掉）；约束限定周报只能以 `spot_check` 进队。
   `weekly_report_issue` 结案后由谁、怎么处理（改模板、撤回判断）是人工流程，本次没有自动联动。
3. **段落规则**（v0）：点亮 / 本周进展 = 本周（上海时区周一 00:00 起）新建的“知识迁移”判断；攻克 / 当前卡点 = 门槛低于“迁移”的“知识掌握”判断；每段最多 5 句。
   “下一步”“一条具体配合建议”“预测与对账摘要”没有对应的判断模板，**v0 不出**（这三段需要新模板，必停项）。
4. **句子是生成时的快照，判断修订后句子消失**（审查后改，安全审查 F1 / 契约审查 M1）：每句存判断修订标记 `claim_revision`
   （写入时判断的 `updated_at`，由 0036 守卫盖上，调用方给了就必须一致）；BFF 读取时判断被修订过（`updated_at` 变了）的句子丢弃并记数。
   代价：判断常规重算（revised）也会让上周周报的对应句子消失，周报可能很快变短；请定是否可接受。
5. **客观栏口径**（快照口径以生成时刻为准：判断的筛选按生成时的数据库时间，客观栏按那一周的上海时区窗口）：使用时长 = 本周 `session_end` 事件时长之和（不分学科）；计划完成 / 跳过 = 本周计划事件按任务去重（先跳过后完成算完成，不分学科——
   计划事件没有学科）；各科练习 = 本周作答用时之和与题数（`platform.attempt.elapsed_sec`，英语不出现）。spec 写“各科使用时长与计划完成”，
   按学科的计划完成要等计划任务带学科。
6. **没有可见判断的学生不写周报**（记 empty，不算 withheld）；检查不过才写 withheld 行（只存阻断编码，便于审计）。
7. **周报不进导出包**（清单 `export_topic` 为空，与预测、对账卡一致），可见范围待人定。
8. **BFF 只给最近一周**（没有历史列表接口）。
9. **每周任务只提供入口** `jobs.weekly_reports(store, 周一, …, env=…)`，没有接调度（每周一凌晨跑上一周）；只接受已结束的周
   （周一 + 7 天 ≤ 今天，生成端与 0036 守卫两处，契约审查 L3）、35 天内。周报配置未签字时 test 以外抛 `Unavailable`（env 由调用方传，没传按非 test；安全审查 F2）；
   某个学生某个受众被库里守卫拒收时记 `failed` 继续（契约审查 L4）。
10. **对外模板白名单**：周报句子要在 `services/bff/config/shell.json` 的 `external_claim_templates` 里才显示（正式配置为空 → 生产不显示任何周报句）；
    端到端用一份测试专用的显示规则。登记白名单属画像可见范围，必停。
11. 洞察服务变异脚本的副本目录另外软链接了 `services/bff` 与 `apps`（`tests/test_forbidden_inference_sie03.py` 要扫它们的源码，否则对照组不通过）。

### 待人确认
1. ~~79q 补周报行~~：已由另一代理（经用户批准）补上；本次只按 L3 把那段写入改为上一周。
2. 上面偏离 1–10。
3. 周报标题、七个段落的衔接短语、页面文案（`WEEKLY*`）、排除词表（家长版：同伴比较、排名、层级标签；学生版：家长问卷与评价的说法）：人写人审。
4. 抽检比例与最少份数（默认 10%、至少 5 份、不阻断，#4）由产品与教研最终确认；可用性走查通过线（`walkthrough.md` 第四节是建议默认值，#7）由产品定。

### 审查修改（安全审查 F1–F6、契约审查 M1、M2、L1–L5，按默认建议）
- F1 / M1：句子加 `claim_revision`（0036 句子键、`trust.v0.yaml` 描述更新）；BFF 按 `unnest(ids, revisions)` 连判断，修订过的丢弃；“初步判断”按判断当前状态推导；
  **置信度分档沿用快照**（修订标记对得上 = 置信度没变；分档阈值在洞察服务策略里，BFF 不另存一份——偏离，请确认）；
  BFF 对句子文字再过禁用推断词表：`src/trust/lexicon.ts` 直接读 `docs/design/sie/forbidden-inference.v0.yaml` 与 `sie_lexicon.py` 的繁简对照（不抄一份），
  读不到即一句不显示；**部署时 BFF 制品要带上这两个文件**（待人确认）。
- F2：未签字周报配置在 test 以外不生成。F3：守卫要求 `label = preliminary` 当且仅当判断是假设。
- F4：抽样函数在库里兜底至少 5 份（`greatest(…, p_min, 5)`，不超过总数）；取不到学科（所引判断不在）时报错，不静默跳过。
- F5：审核台详情去掉客观栏；**待人确认**：抽检是否需要看客观栏。F6：周报仍不进导出包（偏离 7）。
- M2 **契约差异（待人确认）**：周报读取经 BFF 直读 0036，SIE 第九章接口没有周报工具；是否新增 `insight.weekly` 或扩展 `insight.claims`，由人定（契约变更，必停）。
- L1：BFF 随句返回判断的 `source_type`（可选字段），前端对客观记录、学生自述、家长观察来源加占位标注（`WEEKLY_SOURCE`）。
- L2：偏离 5 补“快照口径以生成时刻为准”。L3：见偏离 9；79r、渗透夹具、种子、79q 那段写入都改为上一周。L4：见偏离 9。
- L5：`shell-openapi.test.ts` 加反向断言：`trust.v0.yaml` 的操作集合恰好等于信任界面路由表。
- 每项配了测试或变异（DB“周报：”变异 29 条、洞察服务周报变异 +4、BFF 周报变异 13 条）。

### 测试结果（本机）
见本次回报。

### 可用性走查结果
（待产品按 `walkthrough.md` 执行后填写：参与者数、各任务成功率、平均用时、SEQ 中位数、问题清单与结论签字。）
