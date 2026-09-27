# 冲刺 4-G：发布闸门 v0（确定性检查 → 教研审核 → 版本发布；内容六段标识）

状态：**草稿，等人确认后才开始实现**（CLAUDE.md：先 spec，确认后再动手）。确认人、日期：＿＿＿

> **2026-09-27：按开发计划 v2.0 待重审。** 本 spec 按 v1.0 起草。上游已换成完整方案 v1.1、开发计划 v2.0、KGE V1.1、SIE V1.1，影响见下面“开发计划 v2.0 的影响”一节与 `specs/plan-v2-alignment/spec.md`。人确认那里第三节的待决事项之前，不按本 spec 实现；设计正文暂不改。

关联：
- **开发计划 冲刺 4-G**（W9–W10）：“发布闸门 v0：AI 草稿 → 确定性检查 → 教研审核 → 版本发布；内容六段标识 region/exam_year/grade/textbook_version/subject/content_version”。负责是 Py 后端 + 教研，验收是“四科各发布一个经闸门的内容版本”。
- **工作流 G 的其他任务**：
  - 冲刺 2-G“统一复核队列：三科低置信度 / 申诉 / 抽检进入同一队列，教研后台可处理”，验收“三科各一条复核记录走完闭环”。仓库里**没有 2-G 的 spec 和实现**，只有 `0006` 的表和结案函数。
  - 甘特图 G 行：“统一复核队列（human_review_required）”“发布闸门 v0 · 内容版本六段标识”。
  - 教研轨 W9–W13：“各科星亮门槛配置；**发布闸门首批内容审核**；寒假试点内容包（数学、道法）”。
- **Gate 0 软条件**：“发布闸门四科各有一个内容版本”，证据是“版本记录”。`.claude/skills/gate-check/SKILL.md` 会收集“发布闸门版本记录”。
- **完整方案 v1.0**（`docs/archive/完整方案-v1.0（已被v1.1取代）.html`）：
  - 第三章：每个学科包交付“审核过的内容”；内容资产以六段标识；南京首发，以后按地区加包。
  - 5.3 架构图：平台共享引擎里的“内容发布闸门：草稿 → 确定性检查 → 审核 → 版本”。
  - 5.5：各科题库元数据补齐六段标识；“审核状态字段统一”。
  - 第六章“内容发布闸门”：AI 草稿 → 可重复验证的确定性检查 → 学科教研抽检 / 审核 → 版本发布 → 线上申诉和错误回收。用于判分的题、答案、Rubric 与基础讲解要有审核版本；时政与当年考情有生效和失效日期；四科既有的审核流程合并为同一条流水线的四条审核线，通识内容为第五条。
  - 第七章关键对象里有 `content_review`、`item_exposure`（库里都还没有）。
  - 第十一章：系统性误判时可一键停用该评分版本并重新计算受影响报告。
  - 第十二章“可信度（发布阻断）”：各科批改与教师一致率等任一超阈值，阻断该科发布。
  - 第十三章“审核中心”：七科统一审核队列，人设语料、生成题、时政、批改抽检、通识内容五线。
- **开发计划第八章“工程规范”**：pilot 环境只有经闸门发布的内容。ADR 09 第 96 行同此。
- **表结构**：`db/migrations/0003`（内容登记）、`0004`（证据的 admission 快照）、`0006`（`review_task`、`resolve_review_task`）、`0008`（`plan_config` 的发布模型）、`0009`（`enroll_subject`、`all_launched`）；`docs/adr/erd.md`。
- **契约**：`packages/contracts/openapi/plugin-api.v0.9.yaml` 的 `ContentVersion`、`Admission`、`generate_variation`（“生成器产出只能进入平台 content_review”）。
- **ADR**：ADR 04（模型路由，草稿）、ADR 07（讲解 DSL 也要过闸门）、ADR 09（内容属于 L0；pilot 只有经闸门的内容）。
- **CLAUDE.md**：第 1、2、3、6、7、8、9、10 条；必停项全部五类都要逐条判断。

## 开发计划 v2.0 的影响（2026-09-27）

v2.0 把 G 扩为 **发布流水线与审核台**，分三个冲刺交付：冲刺 1“审核台 v0：能改、能审、能发布、差异对比”（验收：教研可独立完成一次节点审核发布）；冲刺 2“统一复核队列：低置信、申诉、抽检、画像异议四类进同一队列”（验收：三科各一条记录走完闭环）；冲刺 4“分级发布：低风险合并窗口、中风险抽检与小流量试用、高风险两人确认”（验收：四科各发布一个经闸门的内容版本）。阶段 0 终点交付物另有“六段内容标识”。规则以 KGE V1.1 第四章“分级发布闸门”与完整方案 v1.1 第六章“内容发布闸门”为准。

与本 spec 的差异：
1. **审核对象变多**：除题目、答案、Rubric、基础讲解外，新增知识地图（节点、边、投影）与编译页面两条审核线；结构性地图变更两人确认；地图版本号“主.次.修订”，修订周发、次版本月发、主版本学期发。
2. **按风险分级，而不是一条流程**：
   - 低：新增题目族引用、考情数值、渲染段变化、错别字，程序体检通过后进每日合并窗口，每页每天最多发布一次，事后抽检；
   - 中：讲解要点、方法说明、易错话术改写，语义体检后进抽检队列，以“新版试用”小流量发布；
   - 高：两人确认；全量重编按高风险处理，旧版本在新版发布前继续服务。
3. **审核界面看差异不看全文**：本 spec 第 5 节“v0 用命令行审核”不满足 v2.0 冲刺 1 的“差异对比”与“教研可独立完成”验收，需改为网页审核台（或合并请求网页界面，见 ADR“编译层试点期用 Git”）。
4. **复核队列加“画像异议”**：来源是 SIE 的 dispute；本 spec 没有覆盖。
5. **Rubric 版本**：scoring_point 带 `rubric_version`，判分结果绑定版本；系统性误判时可一键停用该评分版本并重算受影响报告，画像模型版本同样可停用并重算受影响判断。
6. **时政条目**：带生效与失效日期，过期由体检自动下架。
7. **表与迁移编号**：`content_review`、`map_change` 等在 KGE 数据模型里属于 `kge.*`，表放哪里待对照文档待决 #3；本 spec 的“迁移 0011”编号作废待重排。
8. **依赖**：地图与页面的审核依赖 KGE-01 骨架与 KGE-03 编译层；题目与 Rubric 的审核可先行。

## 目标
教研能把一个学科的内容版本，按固定流程变成“已发布”：
1. 内容以六段标识登记为草稿；
2. 机器跑一遍确定性检查，结果可重复；
3. 教研人员（staff 账号）看检查报告并签字；
4. 签字后发布。发布后内容不可改，只能停用，或发新版本。

题目的准入（`source_unverified → pending_review → admitted`）走同一条流程，每一次升级都有人、有时间、有依据。

完成后：
- BFF 只看得到已发布的课程版本和其中的能力点、已准入的题目；
- 学生可以选这门课（`enroll_subject` 不再返回 `not_launched`）；
- 4-C 可以只用已准入内容的证据点亮星。

## 现状（起草时核实）

### 内容登记表（`0003_platform__content_registry.up.sql`）
- **`platform.curriculum_version`**（第 4–16 行）：
  - 六段标识各有格式约束：`region`（第 6 行）、`exam_year` 2026–2100（第 7 行）、`grade` 7–9（第 8 行）、`textbook_version`（第 9 行）、`subject`（第 10 行）、`content_version` 三段数字（第 11 行）。格式与契约 `ContentVersion`（plugin-api 第 321–332 行）一致。
  - `status` 只有 `draft / published / retired`，默认 `draft`（第 12 行）。
  - 六段组合唯一（第 14 行）。
  - `CHECK ((status = 'draft') = (published_at IS NULL))`（第 15 行）。**没有 `retired_at`**，停用时间无处记录。
- **`platform.skill_node`**（第 18–26 行）：
  - 主键是 `(subject, local_id)`，**不含课程版本**（第 24 行）。一个能力点永远属于第一次登记它的版本；新版本不能重新登记同名能力点。
  - 只存 `label_code`，显示名由内容包提供（第 27 行）。
- **`platform.item`**（第 29–40 行）：
  - 主键 `(subject, item_id)`，同样不含版本（第 36 行）。
  - `admission` 默认 `pending_review`（第 34 行）；`CHECK ((admission = 'admitted') = (admitted_at IS NOT NULL))`（第 38 行）。
  - 枚举 `platform.admission` 是 `admitted / pending_review / source_unverified / rejected`（`0001` 第 46 行）。
  - 题面与答案不在平台（第 40 行注释）。
- **权限**：
  - `starbuddy_bff`、`starbuddy_learning` 可读三张表（第 42 行）。
  - **`starbuddy_learning` 对三张表有不受限的 INSERT 和 UPDATE**（第 43–44 行，注释写“内容发布流水线由学习服务执行”）。它可以把自己登记的版本直接改成 `published`、把题改成 `admitted`、把已发布版本改回 `draft`。
  - `starbuddy_review` 对三张表**没有任何权限**。
- **RLS**：
  - `cv_public_read`：BFF 只读 `status = 'published'`（第 48 行）；
  - `skill_node_read`：BFF **全部可读**，`USING (true)`，不看版本状态（第 53 行）；
  - `item_read`：BFF 只读 `admission = 'admitted'`（第 58 行），不看版本状态；
  - 学习服务三张表都是 `FOR ALL USING (true)`（第 49、54、59 行）。
  - RLS 测试：`db/tests/20_rls_household.sql` 第 50–51 行断言 BFF 看不到未发布版本、未准入题目。
- **三张表都没有保护触发器**（全部迁移里 `CREATE TRIGGER` 没有一条落在它们上）。已发布的内容可以被改写或删除，只有外键挡着。

### 可以参照的发布模型：额度配置（`0008_platform__metering.up.sql`）
- `plan_config`（第 40–48 行）：`draft / published / retired`，有 `published_at`、`retired_at` 和两条状态一致性 CHECK。
- `guard_plan_config()`（第 76–106 行）：
  - 已停用的不能改；已发布的只能改成已停用，其他字段不变；
  - 草稿不能改名；
  - 草稿发布时校验内容齐全（每档每种资源、每种权益都有）；
  - 只有草稿能删；
  - 发布、停用与改内容行用同一把 advisory 锁串行（第 69–73 行）。
- `guard_plan_rows()`（第 108–128 行）：只有草稿配置下的额度行、权益行能增删改。
- **没有任何角色有写权限**（第 506 行注释），BFF 只读非草稿（第 526 行）。
- DB 测试：`db/tests/60_metering.sql` 第 72–106 行覆盖了这些规则。

### 复核队列（`0006`，结案规则被 `0005` 引用）
- `platform.review_task`（`0006` 第 17–31 行）：`student_id` 必填，`source_kind` 只能是 `grade_result / skill_observation / error_attribution`。它是**判分复核**的队列，放不下“内容版本”这类没有学生的对象。
- 学习服务只能新建 `open` 的任务（第 36–37、77–78 行）；`starbuddy_review` 只读（第 79 行）；BFF 不能读（第 80 行）。
- `resolve_review_task`（第 42–60 行）：`SECURITY DEFINER`，属主 `starbuddy_authz`，只有 `starbuddy_review` 能执行；要求当前账号是 `kind = 'staff'` 且 `active`；记录结案人与结案码。
- `0005` 第 183–209 行：待复核的判分只有经教研结案后才能被更正。这就是“被复核方不能自己签字”（安全审查 R6a）。
- 学习服务**还没有写 `review_task` 的代码**（`services/learning/learning/` 下无引用），2-G 未实现。
- `starbuddy_review` 的说明见 `db/bootstrap/roles.sql` 第 6 行：“教研复核后台，只能读复核队列、调用结案函数”。
- staff 账号没有创建函数，也没有登录流程；现在只能由人用 SQL 建。

### 发布状态已经在影响计量与选科（`0009`）
- `meter_subject_limit`（第 90–94 行）：`all_launched` 档的选科上限 = **存在已发布课程版本的学科数**。
- `enroll_subject`（第 524–526 行）：学科没有已发布版本时返回 `not_launched`，不能选科。
- 所以现在开发库里**任何学科都选不了**（阿鸦是 `draft`）。星图种子是直接 INSERT `subject_enrollment` 绕过去的（`db/seeds/dev/star-map-synthetic.sql` 第 60–65 行）。
- 测试夹具直接 INSERT 已发布的阿鸦、文砺版本（`db/tests/10_fixtures.sql` 第 29–39 行）。

### 准入在证据链上的实际效果
- `evidence_event.admission` 是写入时的快照，证据只追加（`0004` 第 40 行；`db/tests/30_learning_service.sql` 第 18 行）。
- 共享核对库“准入以平台为准，只能往更严的方向改”（`packages/contracts/python/starbuddy_contract/checks.py` 第 193–201 行）：适配器声明与平台登记，取更严的一个。
- 引用既有证据时，取“证据快照”与“题目当前登记”中更严的一个（`services/learning/learning/store.py` 第 116–130 行）。因此：
  - **准入升级不追溯**：题目改成 `admitted` 以前的证据，仍按旧的快照算；
  - **准入降级会追溯**：题目改成 `rejected` 后，引用旧证据时一律按 `rejected` 算。
- 任何证据未准入 → `practice` + `content_not_admitted`（同文件第 204–208 行；阿鸦 `src/adapter.ts` 第 242–246 行）。
- `mastery_estimate` 表注释：只有已准入内容的证据能推动 `transfer` 及以上（`0005` 第 64 行），由学习服务保证。
- 学习服务不检查请求里的 `content_version` 是否已发布（`services/learning/learning/` 与共享核对库里都没有对 `curriculum_version` 的查询）。

### 阿鸦内容（唯一有真实内容的一科）
- **登记脚本** `adapters/aya/scripts/register-content.ts`：
  - 只输出 SQL，不连库（第 4 行）；以 `starbuddy_learning` 身份执行（第 32 行）；
  - 只增不改，`ON CONFLICT DO NOTHING`（第 8、36、43、50 行）；
  - 课程版本按默认值登记，即 `draft`（第 34–36 行）；
  - 内容包里的 `admitted` 最多登记为 `pending_review`，“准入升级属于发布闸门”（第 8–10、14–15、26 行）；
  - 准入降级也不同步，“要走发布流程”（第 9–10 行）。
- **内容包** `adapters/aya/content/pack.json`：
  - 六段标识 `nanjing / 2027 / 9 / suke-2024 / aya / 0.1.0`；`pack_version` `0.1.0-0ed2fe4`，带 legacy 提交号；
  - 959 个能力点、721 道题（单选 369、填空 338、多选 14）；
  - **721 道全部是 `Q1`、全部 `source_unverified`**；`source_verified` 是整包一个布尔值（`false`），**没有逐题的来源字段**；
  - 包里没有题面文字，只有 ID、题型、答案、主测节点、选项与陷阱码。题面在 legacy 仓库。
- **准入规则在适配器里**：`admissionOf`（`src/content.ts` 第 44–48 行）= 审核等级 ≥ Q2 且来源已核。`indexPack` 发现包里的 admission 与规则不符就拒绝启动（第 77 行）。
  - 所以**只在平台把题改成 `admitted` 没有用**：适配器仍声明 `source_unverified`，更严的一方胜出。要让阿鸦的证据真正“已准入”，内容包也要跟着更新，而且要能逐题标来源。
- **阿鸦 lessons**（`specs/adapter-aya-sprint1/lessons.md`）：
  - 待人确认 #1：年级 9、教材 `suke-2024`、考试年份都是**猜的**。发布会把它们冻结。
  - 待人确认 #3：`content_not_admitted` 是否进人工队列由平台定。
  - 待人确认 #5：`calibrated` 一律 `false`，因为 `evals/grade` 不存在。
  - 待人确认 #9：准入升级、降级都只能走发布闸门。
  - 已知两道题的“典型错误答案”与标准答案等价（`Q-ALG-U02-D-001`、`Q-DCE-U03-D-001`），`tests/contract.test.ts` 固定了清单。
- **来源与版权**：`specs/inventory-aya/decisions.md` #3：693 道章节题“来源待核”，逐题核实来源与授权前不得作为已审内容使用；`inventory.md` 第 187 行：来源是“真题”和“教科书 PDF 抽取”，版权风险高。`adapter-design.md` 第 112 行建议 28 道压轴题和 6 份 rubric 也按待核处理。

### 其他三科
- **知行**：没有适配器（`adapters/` 下只有 `aya`），没有平台内容。库里唯一的知行版本是星图种子的**合成**版本（`SYN-ZX-*`，`draft`）。
  - 教研坐标未定（`specs/inventory-zhixing/decisions.md` #7 / A1）；内置题、样卷、教材原文“来源待核”（#3、#4、#5）；
  - 时政只能取白名单源，并“走 content_review 审核发布”（#6）；
  - legacy 有“学生能把自己生成的题改成 approved”的缺陷（ZHIXING-KD-03）。
- **文砺**：没有适配器，没有平台内容。教材全文 1097 段、古诗文底本“来源待核”，Z-Library 教辅永不入库（`specs/inventory-wenli/decisions.md` #3）。
- **开口成章**：**已搁置**（`specs/adapter-echo-sprint3/lessons.md`，2026-09-26）。那份 lessons 已经把 Gate 0 相关条件交给人决定。
- 结论：按现状，**冲刺 4 结束时只有阿鸦可能发布真实内容版本**。寒假试点计划的是“数学 + 道法”，道法的真实内容取决于 2-D 与教研。

### “AI 草稿”所需的条件都不具备
- **模型路由（冲刺 1-F）不存在**：`services/` 下只有 `bff`（计量、星图）和 `learning`（写入）。
- ADR 04 还是草稿，并规定适配器不能自持供应商 Key（第 57 行）。
- 生成题需要新的 Prompt。CLAUDE.md 第 7 条要求 Prompt 变更跑 `evals/persona` 回归，但**`evals/` 目录不存在**；3-F 人设治理只有 spec，没有实现，禁语分类器也不存在。
- 契约 `generate_variation` 规定“选题，不是生成”，“生成器另立项，其产出只能进入平台 content_review”（plugin-api 第 199–202 行）。库里没有 `content_review`。
- 接入任何模型供应商都是必停项（新增第三方、可能的数据出境）。

### 其他相关
- 验证器 / Rubric 注册表不存在（`specs/learning-ingest-sprint1/lessons.md` #9；契约 v1.0 评审 N5 建议“2-G 或 4-G 建表”）。
- `evals/` 金标准由教研标注，只能读（CLAUDE.md 必停项）。现在目录都不存在，闸门没有“判分一致率”可查。
- 3-H 星图取某学科**全部** `skill_node`，不按课程版本过滤（`services/bff/src/star-map/query.ts` 第 47–57 行），因为阿鸦版本是 `draft`，按版本过滤会让数学星空整片消失（`specs/star-map-sprint3/lessons.md` 第 111 行，安全审查 S3 未照做）。
- 攻击面测试 `db/tests/80_attack_surface.sql` 会枚举所有 `SECURITY DEFINER` 函数（第 35–42 行）和函数越权矩阵（第 240 行起）。新增函数必须登记。

## 设计要点

### 1. 两件事，一条流程
v0 把闸门拆成两类“发布单”（release），走同一条流程、同一套留痕：

| 发布单类型 | 作用 | 对 BFF 的效果 |
|---|---|---|
| `publish_version` | 把一个草稿课程版本（连同其中的能力点、题目登记）发布 | 版本与能力点对 BFF 可见；学科可选 |
| `change_admission` | 批量改一批题目的准入状态（升级或降级） | 升到 `admitted` 的题对 BFF 可见，可进入正式测评与星亮 |

- 两者分开，是因为阿鸦的现实是：**框架（能力点）可以先发布，题目的来源核实要慢得多**。
- 发布版本时，题目可以全部还是 `pending_review` / `source_unverified`。这些题只能做练习，已有规则保证（`content_not_admitted`）。
- 准入升级只允许发生在**已发布或同一发布单里发布**的版本上。

### 2. 课程版本的状态机与不可变规则
沿用 `plan_config` 的模型（`0008` 第 76–128 行），状态值不变，仍是三个：

```
draft ──(publish_content_release)──▶ published ──(retire_content_version)──▶ retired
  ▲ 只有 draft 能增删改内容             内容冻结，只能改为 retired              终态，只读
```

- **草稿**：
  - 可以增加能力点与题目（登记脚本只增不改）；可以删整个草稿版本；
  - 新登记的题目，`admission` 最高 `pending_review`，数据库层强制（现在只有脚本保证）。
- **已发布**：
  - 课程版本行只能改为 `retired`，其他字段不变；
  - 挂在它下面的 `skill_node` 不能增、删、改；
  - 挂在它下面的 `item` 只有 `admission`、`admitted_at` 两列能改，而且只能经 `change_admission` 发布单改。
- **已停用**：全部只读。新增 `retired_at` 列，加一条与 `plan_config` 相同的状态一致性 CHECK。
- **没有“撤回发布”**。发布错了，要么停用，要么发新版本。
- **版本号**：同一组前五段下，新发布的 `content_version` 必须大于已发布的最大版本（按三段数字比较）。
- 这些规则用触发器实现（与 `guard_plan_config` 同一风格），不依赖调用方自觉。

**已知限制（待人确认 #5）**：能力点与题目的主键不含版本。所以“新版本”只能**追加**新的能力点和题目，不能带一份修订过的同名能力点。要修一道已发布的题，v0 的做法是把旧题降为 `rejected`，以新 ID 登记新题。

### 3. 发布单与审核记录（迁移 `0011`，必停）
新增两张表，一个状态机，一组函数：

**`platform.content_release`**（发布单）：
- `id`、`kind`（`publish_version` / `change_admission`）、`subject`、`curriculum_version_id`；
- `manifest_sha256`：发布单内容的摘要。内容包括：版本六段标识、能力点清单、题目清单与目标准入、内容包名称与版本、legacy 提交号；
- `registry_digest`：提交时按数据库里该版本的登记内容算出的摘要（见下面“看到的就是发布的”）；
- `check_report_sha256`、`checker_version`（例如 `algo:content-gate@0.1.0`）、`check_passed`；
- `status`：`submitted → checked → approved → published`，旁路 `check_failed`、`rejected`、`withdrawn`；
- `submitted_at`、`published_at`、`published_by_account`。

**`platform.content_release_item`**（只用于 `change_admission`）：
- `release_id`、`subject`、`item_id`、`from_admission`、`to_admission`；
- `source_ref`：来源记录的编号（外部来源登记表的行号或文件号），`to_admission = 'admitted'` 时必填；
- `review_level`：学科自己的审核等级（阿鸦是 Q0–Q5），`admitted` 时必填。

**`platform.content_review_record`**（审核记录，只追加）：
- `release_id`、`reviewer_account_id`（必须是 active 的 staff）、`decision`（`approve` / `reject`）；
- `basis_code`：依据，枚举，例如 `full_review`、`sample_review`、`source_license_verified`、`six_segments_confirmed`；
- `sample_size`、`note`（≤ 500 字，不得含学生信息）、`decided_at`。
- 触发器拒绝 UPDATE、DELETE、TRUNCATE。

**状态流转只经函数**（`SECURITY DEFINER`，属主 `starbuddy_authz`，`search_path` 固定）：

| 函数 | 谁调用 | 做什么 |
|---|---|---|
| `submit_content_release(...)` | `starbuddy_learning`（流水线） | 建发布单，记录 `manifest_sha256` 与 `registry_digest`，状态 `submitted` |
| `record_content_check(release_id, report_sha256, checker_version, passed)` | `starbuddy_learning` | 写检查结果；`registry_digest` 与当前库里的内容不一致就拒绝 |
| `review_content_release(release_id, decision, basis_code, sample_size, note)` | `starbuddy_review`，当前账号是 active staff | 写审核记录；检查未通过的不能 `approve` |
| `publish_content_release(release_id)` | `starbuddy_review`，当前账号是 active staff | 重算 `registry_digest`，与提交时相同才发布；改版本状态或批量改准入；写 `published_by_account` |
| `retire_content_version(cv_id, basis_code, note)` | `starbuddy_review`，staff | 停用版本，同时写一条审核记录 |

- **学习服务不能再直接改状态和准入**：收回 `starbuddy_learning` 对 `curriculum_version.status/published_at` 和 `item.admission/admitted_at` 的 UPDATE。与 `review_task` 的 R6a 同一原则：提交方不能自己签字。
- **看到的就是发布的**：审核人签字后、发布前，如果草稿又被追加了内容，`registry_digest` 会变，发布函数拒绝，要重新提交、重新检查、重新审核。
- **签字人数**：v0 默认一名 staff 审核即可发布（待人确认 #7）。
- **不复用 `review_task`**：它要求 `student_id`，而且是判分复核队列。两者将来都属于“审核中心”，是否合并留给 2-G（待人确认 #9）。

### 4. 确定性检查清单
检查是纯函数：同一份输入，报告逐字节相同，报告的 SHA-256 写进发布单。分两层：

**平台通用检查**（Python，`services/learning/learning/release/`）：

| # | 检查 | 不通过时 |
|---|---|---|
| C1 | 格式：发布单清单符合 JSON Schema；内容包 `format` 已知 | 整单失败 |
| C2 | 六段标识：格式与契约 `ContentVersion` 相同；`region` 在白名单（v0 只有 `nanjing`）；`textbook_version` 在教研维护的清单里；`subject` 与发布单一致 | 整单失败 |
| C3 | 版本号：同前五段下严格大于已发布的最大版本 | 整单失败 |
| C4 | 引用闭合：题目的主测能力点都已登记（本版本或同科已发布版本）；`parent_local_id` 闭合、无环；ID 格式、无重复 | 整单失败 |
| C5 | 登记一致：清单与数据库草稿逐条一致（数量、ID、目标准入），`registry_digest` 相符 | 整单失败 |
| C6 | 准入方向：只允许 `source_unverified → pending_review → admitted`，以及任意状态 → `rejected`；`rejected` 不能再升 | 该题失败 |
| C7 | 来源字段：目标为 `admitted` 的题必须有 `source_ref` 和 `review_level`；`review_level` 达到学科门槛（阿鸦 ≥ Q2） | 该题失败 |
| C8 | 不改已发布内容：清单不触及已发布版本下的能力点 | 整单失败 |
| C9 | 敏感词：对有文字的内容按词表扫描 | 该条失败 |
| C10 | 来源类型：`ai_draft` 来源一律不通过（v0 不启用 AI 草稿，见第 7 节） | 该条失败 |
| C11 | 判分评测：有没有该科 `evals/grade` 的最近一次结果。v0 只记“未评测”，不阻断（待人确认 #11） | 只记录 |

- “该题失败”不让整单失败，但该题不进入本次准入变更；报告列出清单。整单是否可以带着失败题发布，由审核人看报告决定（默认：失败题自动从发布单剔除）。
- **C9 的词表**：阿鸦内容包里没有题面文字，C9 对阿鸦实际不适用。词表来源待人确认 #10。**不使用、也不修改** `docs/personas` 的禁语表（那是人设必停项）。

**学科检查**（由适配器提供，平台调用并记录版本与报告摘要）：
- 阿鸦：`adapters/aya/scripts/check-content.ts`：
  - 每道题的标准答案，经验证器 v0 判为正确；
  - 每个“典型错误答案”经验证器判为错误。已知的两道冲突题（`Q-ALG-U02-D-001`、`Q-DCE-U03-D-001`）会在这里被标为失败；
  - 单选、多选的答案 key 都在选项里；
  - 陷阱码都能解析到十二类根因；
  - 输出验证器版本（写进报告）。
- 学科检查只读内容包，不连库、不连网（与登记脚本同一规则）。
- 答案不进报告，只进“通过 / 失败 + 题目 ID + 原因码”（与适配器“答案不出进程”的规则一致）。

### 5. 教研审核：v0 用命令行
v0 不做网页后台。理由：统一壳的登录（4-H）和教研后台（2-G）都还没有，staff 账号也没有登录流程。

**命令** `python -m learning.release`（`services/learning`）：
- `submit <manifest.json>`：流水线身份，提交发布单；
- `check <release_id>`：跑通用检查，调用学科检查，写报告文件与摘要；
- `show <release_id>`：打印报告摘要、失败清单、抽样清单（按固定种子从题目中抽 N 道，给审核人对照 legacy 题面看）；
- `review <release_id> --approve|--reject --basis <code> [--sample N] [--note ...]`：审核身份；
- `publish <release_id>`、`retire <cv_id>`：审核身份；
- `list`：列出各科版本与发布单状态，这就是 Gate 0 要的“版本记录”的来源。

**身份**：
- 审核命令以继承 `starbuddy_review` 的登录角色连库，事务里 `SET LOCAL app.account_id = <staff 账号>`，与 `resolve_review_task` 相同。
- 这意味着持有审核库账号的人可以自称任何 staff。v0 在 dev / staging 接受；pilot 之前要有真实的 staff 登录（待人确认 #8）。
- 流水线命令以继承 `starbuddy_learning` 的角色连库。一个登录角色不能同时继承两者（`0001` 第 27–33 行已经断言）。

**审核依据**：
- 审核人在 `basis_code` 里说明做了什么（全审 / 抽审 / 来源授权已核 / 六段标识已确认）。
- 来源与授权的原始材料（合同、授权书、来源登记表）**不进本仓库，也不进平台库**，只在 `source_ref` 里记编号。

### 6. 与适配器内容包的关系
- **平台是登记处，适配器是只读内容包**。题面、答案、Rubric 留在适配器（CLAUDE.md 第 1 条；`0003` 第 40 行注释）。
- **准入的唯一真相源是 `platform.item.admission`**。适配器内容包里的准入是它的副本。
- 共享核对库“取更严的一方”（`checks.py` 第 193–201 行）保证：副本落后时只会更严，不会放松。
- 为了让副本跟上，阿鸦内容包要改两处（本冲刺范围内，第 3 个 PR）：
  1. 格式升到 `aya-content-pack/2`：逐题的来源状态，替代整包一个 `source_verified`；
  2. 构建脚本读一份“准入决定”文件（由 `python -m learning.release export-admission` 从平台导出，只含题目 ID、准入、审核等级），生成新包。
- 适配器启动时的自检（`indexPack`）继续保证包内一致。
- **不追溯**：题目升为 `admitted` 以前的证据仍按旧快照算，不能事后推动星亮。这是现有代码的行为（见“现状”），v0 不改。降级会追溯（`store.py` 取更严）。

### 7. AI 草稿：v0 不启用
开发计划写的流程第一步是“AI 草稿”。v0 **保留这个入口的位置，但不接模型**：
- 发布单里每条内容带 `origin`：`legacy_import`、`staff_authored`、`ai_draft`。v0 只接受前两种，`ai_draft` 在 C10 直接失败。
- 理由：
  - 模型路由（1-F）不存在，ADR 04 未签，适配器不能自持 Key；
  - 生成 Prompt 属于 Prompt 变更，要跑回归，而 `evals/` 不存在；
  - 接模型是新增第三方，可能涉及数据出境，是必停项；
  - 契约规定生成器另立项，产出只能进 `content_review`；
  - 用户是未成年人，生成题的错误代价高，v0 先让人写、人审的内容走通流程。
- 启用 AI 草稿的前提：模型路由上线、ADR 04 签字、生成 Prompt 的评测集入库。另起 spec。

### 8. 停用与回收
- **停用版本**（`retire_content_version`）：
  - BFF 立刻看不到该版本和其中的能力点、题目（RLS，见第 9 节）；
  - 该学科没有其他已发布版本时，`enroll_subject` 对新选科返回 `not_launched`；已在读的学生不受影响；
  - `all_launched` 的计数随之减少（`0009` 第 90–94 行）。已经超出新上限的学生不会被自动退课，函数只在选科时检查。
- **题目降级**（`change_admission` 到 `rejected`）：
  - 引用旧证据时按 `rejected` 算（现有行为）；
  - **已经点亮的星不会自动熄灭**。“按降级重算受影响的掌握估计”属于 4-C（完整方案第十一章“一键停用并重新计算”），本冲刺只在发布单上留一个“受影响的题目清单”，供 4-C 读取。
- 两者都要 staff 签字，都有审核记录。

### 9. 权限与 RLS 调整（必停）
- `starbuddy_learning`：
  - 保留三张内容表的 INSERT（登记草稿）；
  - 收回 `status`、`published_at`、`admission`、`admitted_at` 的 UPDATE，改由函数；
  - 新增：执行 `submit_content_release`、`record_content_check`；读发布单与审核记录。
- `starbuddy_review`：
  - 新增：读三张内容表（含草稿）、发布单、审核记录；
  - 新增：执行 `review_content_release`、`publish_content_release`、`retire_content_version`；
  - 没有任何表的直接写权限。
- `starbuddy_bff`：
  - `skill_node_read` 改为：所属版本 `status = 'published'`（落实 3-H 安全审查 S3）；
  - `item_read` 改为：`admission = 'admitted'` 且所属版本已发布；
  - `cv_public_read` 不变；
  - 发布单、审核记录对 BFF 不可见。
- 新函数全部登记进 `80_attack_surface.sql` 的函数清单与越权矩阵，攻击面清单加条目。

## 范围

### 做
分三个 PR，每个只做一件事：

**PR 1：库（`[NEEDS-HUMAN]`）**
1. 迁移 `0011_platform__release_gate`（up / down）：`retired_at` 列与 CHECK；三张新表；保护触发器；五个函数；权限与 RLS 调整。
2. DB 测试（新文件，例如 `db/tests/90_release_gate.sql`）与变异（`db/tests/mutations.sh`）。
3. 调整受影响的夹具与种子：
   - `db/tests/10_fixtures.sql`：直接插入已发布版本的写法，改为先插草稿再经函数发布，或按待人确认 #12 处理；
   - `db/seeds/dev/star-map-synthetic.sql`：知行合成版本要经函数发布，否则 RLS 调整后知行星空消失；
   - 快照（`db/tests/expected`、`snapshot`）随之更新。
4. `docs/adr/erd.md`：补新表与“内容登记”行的写入方。

**PR 2：检查与命令行**
1. `services/learning/learning/release/`：清单 Schema、C1–C11、报告、命令行；单测（合成清单，含每条检查的通过与失败样例）。
2. `adapters/aya/scripts/check-content.ts`：阿鸦学科检查与测试。
3. `Makefile` 目标 `release-test`，纳入 `make check`。

**PR 3：阿鸦首个经闸门的版本**
1. 阿鸦内容包格式 `aya-content-pack/2`、准入决定文件、构建脚本与测试（第 6 节）。
2. 在开发库上走完一次：登记 → 提交 → 检查 → 审核（合成 staff 账号）→ 发布。
3. 若教研在冲刺内给出第一批已核来源的题：再走一次 `change_admission`。没有就不做，只用合成题在测试里覆盖。
4. `specs/release-gate-sprint4/lessons.md`：实现记录、版本记录、新增待确认项。

pilot 环境的发布由人执行（CLAUDE.md 第 9 条）。

### 不做
- AI 草稿与任何模型调用（第 7 节）。
- 网页审核后台、staff 登录（2-G / 4-H 之后）。
- 统一复核队列 2-G 的入队规则与 `review_task` 改造。
- 能力点、题目主键加版本（待人确认 #5）。
- 时政内容的生效与失效日期（知行没有内容；待人确认 #13）。
- 验证器 / Rubric 注册表（契约评审 N5；待人确认 #14）。
- 按降级重算掌握估计（4-C）。
- 学习服务按 `content_version` 拒绝未发布内容的请求（待人确认 #15）。
- `item_exposure`（题目曝光记录）。
- 知行、文砺、开口成章的真实内容版本（没有适配器和内容）。
- 修改 `evals/`、`docs/personas/`、`packages/contracts/`、`docs/plan/`。

## 验收标准（可测试）

**状态机与不可变**（DB 测试，一次性 Postgres）：
- [ ] 草稿可以追加能力点与题目；已发布、已停用版本下，能力点的 INSERT / UPDATE / DELETE 全部被拒。
- [ ] 已发布版本只能改为 `retired`；改任何其他字段、改回 `draft`、删除，全部被拒；已停用版本任何改动被拒。
- [ ] 题目 INSERT 时 `admission = 'admitted'` 被拒（任何角色，包括超级用户走触发器）。
- [ ] 同前五段下发布一个不大于已发布最大值的 `content_version` 被拒。

**谁能做什么**（DB 测试）：
- [ ] `starbuddy_learning` 直接 UPDATE `status`、`published_at`、`admission`、`admitted_at` 得到权限错误（42501）。
- [ ] `starbuddy_learning` 调用审核、发布、停用函数被拒。
- [ ] `starbuddy_review` 以非 staff 账号、停用的 staff 账号、没有设置账号时调用审核与发布函数，全部被拒。
- [ ] 检查未通过的发布单不能 `approve`；没有 `approve` 记录的不能发布。
- [ ] 审核后草稿被追加一条题目，发布函数因 `registry_digest` 不符而拒绝。
- [ ] 审核记录的 UPDATE、DELETE、TRUNCATE 被拒。
- [ ] 准入方向：`rejected → admitted`、`admitted → source_unverified` 被拒；目标 `admitted` 但缺 `source_ref` 或审核等级不够被拒。

**BFF 可见性**（RLS 测试，写进 `20_rls_household.sql` 或新文件）：
- [ ] BFF 看不到草稿、已停用版本下的能力点；看得到已发布版本下的。
- [ ] BFF 看不到“已准入但所属版本未发布”的题目。
- [ ] BFF 读不到发布单与审核记录。
- [ ] 版本发布后，`enroll_subject` 对该学科从 `not_launched` 变为可选；停用后新选科回到 `not_launched`。

**检查**（单测）：
- [ ] C1–C10 每条都有“通过”与“失败”的合成样例。
- [ ] 同一输入跑两次，报告字节相同；清单任意改一个字节，`manifest_sha256` 变化。
- [ ] 阿鸦学科检查：对真实内容包运行，两道已知冲突题（`Q-ALG-U02-D-001`、`Q-DCE-U03-D-001`）报失败；全部失败题的清单固定在测试里，新增失败会让测试变红；报告里没有任何标准答案原文。
- [ ] `origin = ai_draft` 的条目 C10 失败。

**端到端**（开发库）：
- [ ] 阿鸦 `nanjing/2027/9/suke-2024/aya/<版本>` 走完 提交 → 检查 → 审核 → 发布，`list` 输出一行已发布版本记录，含审核人、时间、依据、检查报告摘要。
- [ ] 发布后星图（BFF 集成测试）只含已发布版本的能力点；星图测试、端到端测试按新种子全部通过。
- [ ] 合成的知行、文砺版本（`SYN-` 前缀，只在测试库）同样能走完流程，证明闸门对学科无关。**它们不计入 Gate 0 版本记录。**

**开发计划验收“四科各发布一个经闸门的内容版本”**：
- [ ] **按现状做不到**。本冲刺可交付：阿鸦 1 个真实版本；知行、文砺只有合成版本的测试；开口成章已搁置。lessons 如实记录，Gate 0 软条件怎么改由人定（待人确认 #1）。

**通用**：
- [ ] 严格模式 `make check` 通过，包含新的 DB 测试、变异、`release-test`、攻击面检查。

## 涉及的必停项（见 CLAUDE.md）
逐条判断（默认方案下）：

| 必停项 | 是否涉及 | 说明 |
|---|---|---|
| `db/migrations` 下的任何文件 | **是** | 新增 `0011`：`retired_at`、三张表、触发器、五个函数、权限调整 |
| RLS 策略 | **是** | 改 `skill_node_read`、`item_read`；新表的 RLS；附测试 |
| 计量与权益逻辑 | **否，但有行为影响** | 不改计量代码和函数。可是发布、停用会改变 `enroll_subject` 能否选科和 `all_launched` 的上限（`0009` 第 90–94、524–526 行）。请计量负责人确认这是预期（待人确认 #6） |
| `packages/contracts` 的任何变更 | **否** | 发布单清单与命令行是学习服务内部格式，不进契约。如果待人确认 #8 选择做 HTTP 审核接口并放进 `learning-internal`，就变成“是” |
| `evals/` 金标准 | **否** | 不读不写（目录也不存在）。C11 只记录“未评测” |
| 人设 Prompt、禁语表、转介话术 | **否** | 不产生人设文本；C9 不使用、不修改 `docs/personas` 的禁语表 |
| 新增第三方服务、模型供应商或数据出境 | **否** | v0 不启用 AI 草稿，不接任何模型。启用时是“是” |

**PR 1 首行写 `[NEEDS-HUMAN]`**（迁移、RLS、权限）。PR 2 不涉及必停项。PR 3 改阿鸦内容包格式，不涉及必停项；但如果教研给了真实准入批次，发布动作本身要人确认六段标识（待人确认 #2）。

## 依赖与输入
- **已合并**：迁移 `0001`–`0009`；阿鸦适配器与登记脚本；学习服务写入（1-C）；星图（3-H）。
- **教研（冲刺内必须拿到，否则只能用合成数据验收）**：
  - 阿鸦六段标识的确认：年级、教材版本、考试年份（阿鸦 lessons #1）。发布会冻结这些值；
  - 至少一名 staff 账号的持有人，以及审核依据的写法；
  - `textbook_version` 清单、`region` 白名单；
  - 第一批来源已核的题（可选）。
- **法务**：来源与授权的核实口径（阿鸦 decisions #3）。没有它，任何阿鸦题都不能升为 `admitted`，阿鸦的星**一颗也点不亮**。
- **同期冲刺**：
  - **4-C 掌握估计**（`specs/mastery-estimate-sprint4/spec.md`，另一会话起草，本仓库暂无此文件）：它要求“只有已准入内容的证据能推动 transfer 及以上”。所以**4-G 决定阿鸦能不能点亮星**。4-C 还需要读取本冲刺的“降级受影响题目清单”来重算。
  - **4-H 统一壳**（`specs/unified-shell-sprint4/spec.md`，另一会话起草中）：“学生选科”依赖 `enroll_subject`，而它要求学科有已发布版本。4-G 不发布，4-H 在真实流程里选不了任何学科。4-H 计划在测试库里用合成种子直接插入“已发布”的课程版本；本冲刺的保护触发器会拦下这种写法，4-H 的种子要改为经发布函数发布（与待人确认 #12 同一处理）。
  - **4-D 四科补方法**：阿鸦 `generate_variation` 只能返回已准入的题；没有已准入的题时只能返回空。
- **3-H 星图**：PR 1 改了 `skill_node_read`，星图查询不用改就只剩已发布版本的能力点；但星图种子与测试必须在同一个 PR 里调整（知行合成版本要经函数发布）。
- **2-G 复核队列**：未实现。本冲刺不依赖它，但两者将来要合并进“审核中心”。

## 与同期 spec 的协调（冲刺 4 四份 spec 复核时统一，以本节为准）
四份 spec（4-H `specs/unified-shell-sprint4/`、4-C `specs/mastery-estimate-sprint4/`、4-G `specs/release-gate-sprint4/`、4-D `specs/adapter-aya-methods-sprint4/`）是并行起草的。起草时彼此看不到，正文里“某 spec 还不存在”之类的说法以本节为准。
1. **迁移编号**：按需要的先后排定，最终以合并顺序为准。
   - `0010`：4-H 会话；
   - `0011`：4-G 发布闸门；
   - `0012`：4-C 复测任务表与题型列。4-C 要在 4-G 改过的 `platform.item` 上加 `item_type`，需同时放行 4-G 保护触发器里的这一列。
2. **plan 内部接口**：4-H 负责在 `learning-internal` 里新增 plan 接口（契约变更，必停）。4-D 只实现适配器侧的 `plan`，不新增平台接口。
3. **复测任务的唯一来源**：4-C 的 `platform.retest_task`。平台调 `plan` 时，由它填好 `mastery[].next_retest_at`；适配器的 `plan` 只按它排序，不自己排复测。legacy 的 2/7/14/21 天间隔不再使用。
4. **已发布课程版本的测试数据**：4-G 上线后，已发布版本与已准入题目只能经发布函数产生。4-G 的 PR 要在同一个 PR 里改掉 `db/tests/10_fixtures.sql`、3-H 的 `db/seeds/dev/star-map-synthetic.sql`（知行合成版本要发布，否则会从星图上消失），以及 4-H 已有的测试种子。4-H 的验收用合成已发布版本，不以 4-G 为门禁。
5. **阿鸦内容包格式**：4-G 升到 `aya-content-pack/2`（逐题来源、准入决定文件），4-D 在其上升到 `/3`（提示阶梯原文）。4-G 的 C9 词表检查要覆盖 4-D 加进去的提示原文。
6. **作答要带提示等级**：统一壳（4-H）提交作答时必须带 `hint_level_used`。4-C 的“低提示依赖”条件要用它，没有就最多到 understand。
7. **复测作答扣不扣额度**：4-C 建议不扣，4-H 本冲刺不做消耗类入口。接入时如果要不扣，属于计量逻辑变更，必停。
8. **两个没有任何 spec 负责的缺口**（交人决定）：
   - 阿鸦题干：平台和内容包里都没有题干，`plan` 和变式题只返回题目 ID，统一壳显示不了题目；
   - pilot 家庭开通订阅：`enroll_subject` 要求有效主订阅，而 `meter_open_subscription` 的入口不在任何 spec 里。
9. **Gate 0 口径**（交人决定，与 `specs/adapter-echo-sprint3/lessons.md` 待决事项是同一件事）：
   - 4-G：“四科各发布一个版本”只能到 1/4；
   - 4-D：“契约覆盖率达标”只有阿鸦能到 6/6；
   - 4-H：“进入四科”只能是数学真实、其余占位。

## 待人确认
1. **开发计划验收与 Gate 0 软条件“四科各有一个内容版本”**：
   - 现状只有阿鸦有真实内容；知行、文砺没有适配器也没有内容；开口成章已搁置。
   - 默认：本冲刺验收改为“阿鸦发布一个真实版本；闸门对学科无关，用合成的知行、文砺版本在测试里走通”。Gate 0 这一条按 1/4 记录，由人决定是改口径还是在阶段 1 首月关闭。
   - 另一个选择：等知行（道法）有真实内容后再验收，4-G 延期到冲刺 5。
2. **阿鸦六段标识在发布前确认**：
   - 年级 9、`suke-2024`、2027 都是登记时猜的，发布即冻结。
   - 默认：教研确认前只在开发库发布；pilot 发布必须有教研书面确认。
   - 另一个选择：按“适用年级范围”拆版本或改表结构（迁移，必停）。
3. **发布版本与题目准入分开**：
   - 默认分成两类发布单（第 1 节）；版本可以带着全部未准入的题先发布，这些题只能练习。
   - 另一个选择：版本发布时至少要有 N 道已准入的题，否则不许发布。
4. **准入的真相源与阿鸦内容包**：
   - 默认：平台 `item.admission` 是唯一真相源；阿鸦内容包改为逐题来源状态（格式 v2），按平台导出的准入决定文件重建。
   - 另一个选择：改共享核对库，让平台登记直接覆盖适配器声明（两个方向都以平台为准）。这改的是 `packages/contracts`，必停，并且改变契约“适配器只能更严”的语义。
5. **能力点与题目的主键不含版本**：
   - 默认 v0 不改。新版本只能追加；修题 = 旧题降为 `rejected` + 新 ID 新题。
   - 另一个选择：主键加 `curriculum_version_id`。证据、推断、复核多张表的外键都要跟着改，迁移量大，必停。
6. **发布会改变选科与额度上限**：
   - 默认接受：发布即“上线”，`enroll_subject` 与 `all_launched` 按已发布版本计（沿用 2-E 待人确认 #7 的默认）。
   - 另一个选择：“内容发布”与“学科上线”分开，另建上线配置表（2-E 待人确认 #7 的另一选择；改计量函数，必停）。
7. **审核签字人数**：
   - 默认一名 active staff 审核即可发布。
   - 另一个选择：准入升级（尤其来源授权）要求两名不同的 staff，其中一名是该科教研。
8. **审核入口与身份**：
   - 默认 v0 用命令行，审核人以 `starbuddy_review` 连库并自报 staff 账号，与 `resolve_review_task` 同一做法；只在 dev / staging 使用。pilot 前必须有真实 staff 登录。
   - 另一个选择：本冲刺做一个最小网页后台加 HTTP 接口。要做登录，还可能进契约（必停），超出两周范围。
9. **与 `review_task` 的关系**：
   - 默认新建发布单与审核记录两张表，不复用 `review_task`（它要求学生，是判分复核）。
   - 另一个选择：把 `review_task` 泛化为“审核中心”一张表（改 `0006`，必停），2-G 与 4-G 一起做。
10. **敏感词表（C9）从哪里来**：
    - 默认：由合规与教研提供一份内容审核词表，放在 `services/learning/learning/release/wordlist/`，与人设禁语表分开；v0 阿鸦不适用（包里没有文字）。
    - 另一个选择：复用 `docs/personas` 的禁语表（只读引用）。但禁语表是给角色话术定的，套到题目上可能误报。
11. **判分评测是否阻断发布（C11）**：
    - 完整方案第十二章说“各科批改与教师一致率超阈值阻断该科发布”，但 `evals/grade` 不存在。
    - 默认 v0 只在报告里记“未评测”，不阻断。
    - 另一个选择：没有评测结果的学科，题目一律不能升为 `admitted`。
12. **测试夹具怎么插入已发布版本**：
    - 触发器也会拦超级用户。默认夹具改为“先插草稿，再经函数发布”，用一个合成 staff 账号。
    - 另一个选择：触发器对 `session_replication_role = replica` 放行，只供夹具使用。更省事，但留了一个绕过口。
13. **时政等有时效的内容**：
    - 默认 v0 不做生效与失效日期（知行没有内容）。
    - 另一个选择：现在就在题目登记上加 `effective_from`、`expires_at`（迁移，必停）。
14. **验证器 / Rubric 注册表**（契约评审 N5、1-C lessons #9）：
    - 默认不在 4-G 做，另立任务。
    - 另一个选择：随 `0011` 一起建表，Rubric 与验证器版本也经闸门发布。
15. **学习服务是否拒绝未发布版本的请求**：
    - 现在请求里的 `content_version` 不做发布状态检查。
    - 默认 v0 不改学习服务；pilot 靠“只登记、只发布经闸门的内容”保证。
    - 另一个选择：学习服务在写入前核对 `content_version` 已发布，不是就拒绝（改学习服务，不改契约）。
16. **来源材料放在哪里**：
    - 默认：合同、授权书、来源登记表不进仓库、不进平台库，`source_ref` 只记编号；由法务或教研保管。
    - 另一个选择：放进受控的对象存储，平台记录对象引用（新增存储路径，要按 ADR 09 登记）。
