# 冲刺 4-D（阿鸦部分）：阿鸦补齐 plan、teach、generate_variation、report

状态：**草稿，等人确认后才开始实现**（CLAUDE.md：先 spec，确认后再动手）。确认人、日期：＿＿＿

> **2026-09-27：按开发计划 v2.0 待重审。** 本 spec 按 v1.0 起草。上游已换成完整方案 v1.1、开发计划 v2.0、KGE V1.1、SIE V1.1，影响见下面“开发计划 v2.0 的影响”一节与 `specs/plan-v2-alignment/spec.md`。第三节的待决事项已于 2026-09-27 确认（英语继续搁置、标准团队模式、其余按默认）；本 spec 的设计正文待按 v2.0 改写后再确认，改写前不按本 spec 实现。

关联：
- **开发计划 冲刺 4-D**（W9–W10）：“四科补齐剩余方法：阿鸦六方法全覆盖；知行、文砺补 plan / teach / report；开口成章补 plan”。负责人是学科开发者，验收是“契约测试覆盖率：阿鸦 6/6、知行与文砺 ≥ 5/6、开口成章 ≥ 4/6”。
- **用户决定（2026-09-26）**：本 spec **只做阿鸦**。知行、文砺没有适配器；开口成章已搁置（`specs/adapter-echo-sprint3/lessons.md`）。三科对 Gate 0 的影响只登记，不实现，见“其他三科与 Gate 0”一节。
- **Gate 0 软条件**：“契约测试覆盖率达标”，证据是一致性测试的覆盖率报告（`/gate-check 0`）。
- **前序**：`specs/adapter-aya-sprint1/spec.md` 与 `lessons.md`（capabilities、grade、diagnose 已合并）；`specs/contract-conformance-sprint1/`（一致性测试与 v1.0 评审清单）。
- **盘点**：`specs/inventory-aya/adapter-design.md` v0.2 第 1 节“六方法映射”、第 3.1 节“内容准入”；`decisions.md` #3、#4、#9、#11。
- **契约**：`packages/contracts/openapi/plugin-api.v0.9.yaml`；`learning-internal.v0.yaml`。
- **同期 spec（其他会话起草）**：4-C 掌握估计（`specs/mastery-estimate-sprint4/`）、4-H 统一壳（`specs/unified-shell-sprint4/`），起草时仓库里还没有；4-G 发布闸门（`specs/release-gate-sprint4/spec.md`）在起草中途出现，已按它核对（见“依赖与输入”）。
- **人设**：《阿鸦规格书》V1.1（`docs/personas/阿鸦-规格书-V1.1.md`）；3-F 人设治理（`specs/persona-governance-sprint3/spec.md`，草稿）。
- **ADR**：04（模型路由）、06（计量）、07（讲解 DSL 与许可证），都是草稿。
- **CLAUDE.md**：第 1、2、3、4、6、7、8、10 条。

## 开发计划 v2.0 的影响（2026-09-27）

v2.0 冲刺 4-D 仍是“四科补齐剩余方法”，验收“阿鸦 6/6、知行与文砺 ≥ 5/6、开口成章 ≥ 4/6”；阿鸦的 diagnose 与 grade 在 v2.0 属于冲刺 1（ADP-数学-01），本仓库已完成。

与本 spec 的差异：
1. **读画像与写证据的通道**：SIE V1.1 规定 diagnose、plan、report 通过洞察服务的接口（insight.context / insight.claims）读画像；grade、teach 只经 KGE 的 `evidence.write` 写证据，不调用 insight 的写接口；适配器不得直接改掌握度或判断（CLAUDE.md 规则 11）。这两个服务的接口还没有契约（对照文档第五节第 4 项），本 spec 的 plan / report 读取方式要等它们。
2. **知识服务**：plan、generate_variation 选题改经 `item.search` / `item.similar` 与 `node.path`；teach 读 `page.get` 的已发布页面（页面人设中立，阿鸦话术在外层加）。在知识服务就绪前，沿用本 spec 的内容包读取方式，但要标为过渡实现。
3. **节点与能力视图**：阿鸦图谱节点将成为 KGE 的 knowledge_node（前置关系按必要性检验重审、删除传递边）；100 能力点降为 skill_node 能力视图；能力五层级退役、题目层用课标四级。report 的字段要按“节点 + 能力视图”两层给。
4. **report 里的陈述**：各科只提供字段，所有推断性陈述引用判断 ID，由报告引擎与洞察服务生成；本 spec 的 report 不得自己写结论句。
5. **题目六段标识**：阿鸦题库导入 KGE 原子层时补齐 region / exam_year / grade / textbook_version / subject / content_version（v2.0 冲刺 2 KGE-02）；难度先验由七因子连乘改为分级加权求和。
6. **其他三科**：v2.0 的 D 流仍要知行、文砺 ≥ 5/6。开口成章：用户 2026-09-27 确认 v2.0 没有安排调整后的英语方案，继续搁置，覆盖率不计（对照文档 #1）。

## 目标
阿鸦适配器的六个方法都声明“已实现”，并且都通过同一套一致性测试。覆盖率报告显示 **aya 6/6**。

新补的四个方法都是**确定性的**：
- 不调用模型；
- 不产生人设话术；
- 不读写平台表。

它们先在适配器层可用、可测。平台什么时候真正调用它们，由 4-H、5-H 和阶段 1 决定。

## 现状（起草时核实）

### 适配器
- `adapters/aya/src/adapter.ts`：
  - 第 289–303 行 `capabilities`：只有 diagnose、grade 声明 `implemented: true`，其余四个是 `false`。
  - 第 412–430 行：`plan`、`teach`、`generate_variation`、`report` 统一抛 501 `METHOD_NOT_IMPLEMENTED`。
  - 第 260–277 行：幂等包装。只存请求体指纹，同 key 换请求体返回 409。新方法可以直接复用。
  - 第 280–284 行：出门前按契约自检，不合契约就返回 500。
- `adapters/aya/src/server.ts`：只做令牌、请求体和路由转发（第 75 行交给 `adapter.handle`），不感知具体方法。新方法**不需要改 HTTP 层**。
- `adapters/aya/src/ids.ts`：证据 ID 由 key 与请求体指纹确定性派生，指纹不含 `request_id`。

### 内容包
- `adapters/aya/content/pack.json`，格式 `aya-content-pack/1`，版本 `0.1.0-0ed2fe4`（legacy 提交 `0ed2fe45`）：
  - 721 道题：单选 369、填空 338、多选 14。**全部是 `source_unverified`**，没有一道已准入。
  - 959 个节点，只有 `id`、`kind`、`cluster` 三个字段（`content.ts` 第 31 行）。
  - 429 个陷阱码。
- 内容包刻意只放判分需要的字段（README“结构”一节）：**没有题干、解析、提示、前置关系、题目用途、预估时长、板块**。
- 下面几项新方法要用，legacy 里都有（按 legacy 实际数据统计）：
  - `hintPath`：721 道题都有。其中 600 道只有 0–5 级（6 条），121 道有 0–6 级（7 条）。`lib/types.ts` 第 131 行。
  - `prerequisites`：959 个节点中，851 个至少有一个前置是节点 ID；其余是自然语言。`lib/types.ts` 第 13 行。
  - `usage`：D 419、T 119、R 126、P 29、L 28（`lib/types.ts` 第 111 行）。有 R（复测）题的节点 134 个。
  - `estMinutes`：721 道都有。
  - 板块与中考分值占比：`content/examweights.ts` 的 `BOARD_EXAM_SHARE`，注释写明是“产品推导值，Q1 级，待教研校准”。
- **有题的节点只有 452 个**（959 个里的 47%）。其余 507 个节点既不能练习，也不能复测。

### legacy 里可包装的实现（`legacy/aya` 只读，即 `/home/user/ayaheshui`，HEAD 为 `0ed2fe45`）
| 方法 | legacy 实现 | 能否包装 | 差距 |
|---|---|---|---|
| plan | `lib/scoring.ts` 第 145–178 行 `computePriorities`：优先级 = 薄弱 × 前置影响 × 考查价值 × 板块频次 ÷ 成本，取前 6 | **能**，纯函数 | 输入是 legacy 自己算的 `nodeMastery`，要改成平台传入的 `MasterySnapshot`；输出是中文 `reason`，要改成编码 |
| plan（复测） | `lib/review.ts` 第 18 行间隔链 **2 / 7 / 14 / 21 天**，第 29–59 行复测队列 | 只借选题规则 | 与 4-C 的 **0 / 3 / 14 天**不同。复测调度归 4-C（平台），适配器不再算间隔 |
| generate_variation | `lib/mistakes.ts` 第 54–63 行 `pickVariant`：同主节点、非原题、没答对过 | **能** | 没有准入过滤、没有同错因匹配；`exclude_item_refs` 替代“没答对过” |
| report | `app/report/narrative.ts`、`lib/weekly.ts`、`lib/trends.ts`、`lib/exam.ts` | **只借口径** | 全部输出中文叙事，而契约只要字段；`trends.ts` 从作答重放算 legacy 掌握度，平台不允许（规则 2）；`exam.ts` 的预测区间基于 Q1 题 |
| teach（提示） | `lib/prompts.ts` 第 8–34 行把题干、**完整解析**、0–6 级提示塞进系统提示，交给模型；`app/api/coach/route.ts` 由客户端决定提示等级 | **不能直接包装** | 要模型（1-F 不存在）；Prompt 无版本；人设与规格书 V1.1 冲突（inventory §7） |
| teach（讲课） | `lib/lesson.ts` 第 49 行九步微课，从节点元数据和例题解析拼装 | 不能 | 需要解析与节点中文名，内容包里都没有；没有白板或讲解 DSL |

### 契约（`plugin-api.v0.9.yaml`）
| 方法 | 路径 | 请求 | 响应 `result` | 消耗类 | 可异步 |
|---|---|---|---|---|---|
| plan | 第 100 行 | 第 769 行：`mastery[]`（`MasterySnapshot`：state、p_mastery、next_retest_at）、`constraints`（时间预算、考试日期、重点能力点） | 第 788–819 行：`tasks[]`，每项有 task_id、kind、skill_nodes、item_refs、priority、estimated_minutes、prerequisite_task_ids、`rationale_code`（编码，叙述由平台生成） | 否 | 否 |
| teach | 第 127 行 | 第 839 行：`MeteredContext`、`mode`（hint / lesson）、`skill_node`、`item_ref`、`allowed_hint_level`（0–6，**平台定的上限**）、`persona`、`conversation`（平台提供）、`recent_evidence` | 第 880–917 行：`segments[]`（text / formula / whiteboard_dsl / item_ref）、`hint_level_used`、`ai_disclosure_shown`、`referral_triggered`、`verified_claims` | **是** | 否 |
| generate_variation | 第 194 行 | 第 1128 行：`source_item_ref` 或 `skill_node`、`subject_error_category`、`count`、`exclude_item_refs` | 第 1146–1185 行：`candidates[]` 的 `admission` **必须是 `admitted`**（第 1154 行）；为空时必须给 `empty_reason` | 否 | 否 |
| report | 第 222 行 | 第 1196 行：`window`、`audience`、平台选好的 `evidence`、`error_attributions`、`mastery_history` | 第 1232–1274 行：`skill_summary`、`error_distribution`、`highlight_codes`、`forecast`（只基于已准入内容，不够就**省略**） | 否 | 否 |

另外：
- 计量口径在第 27–30 行：diagnose、grade、teach 是消耗类，`usage_id` 必填；其余三个 `usage_id` 可省，`idempotency_key` 都必填。适配器只原样使用，不扣费。
- **只有 diagnose、grade 可以异步**：`JobAccepted.method` 与 `JobStatus.method` 的枚举只有这两个（第 1294、1306 行）。
- 四个响应都是 `unevaluatedProperties: false`，信封外不能夹带文本。
- generate_variation 的语义是**选题，不是生成**（第 197–203 行；decisions #9）。
- 第 1336 行写明：Gate 0 契约覆盖率按 `capabilities.methods` 统计。

### 一致性测试与覆盖率
- `packages/contracts/conformance/test_conformance.py`：
  - 第 136–146 行：对 plan、teach、generate_variation、report，**只校验响应 schema**。平台侧核对与泄露检查只覆盖 grade、diagnose（`starbuddy_contract/checks.py` 第 29 行 `SUPPORTED_METHODS`）。
  - 第 110–132 行：坏请求由契约自动变出。teach 是消耗类，会多一条“缺 usage_id”（`support.py` 第 53–55 行从契约推导）。
  - 第 181–203 行：幂等。没有作答的方法，夹具必须提供 `conflict_body`，否则测试失败。
- `conftest.py` 第 84–99 行：一个方法算“通过”，要同时满足：声明已实现、至少有一条用例、用例全部通过、整套测试没有全局失败。
- 阿鸦夹具 `adapters/aya/conformance/fixtures.yaml` 目前只有 grade、diagnose 的用例。按 README“夹具由谁写”，学科夹具**由另一个会话或该科负责人编写**。
- `make conformance`（Makefile 第 86–93 行）已经会跑阿鸦，并把报告写到 `conformance-aya.json`。
- 所以本冲刺的 6/6 只证明“**结构符合契约、坏请求被拒、幂等成立**”，不证明内容正确。内容正确要靠适配器自己的单测。

### 平台侧
- **学习服务不调用这四个方法**。`learning-internal.v0.yaml` 只有 `/internal/v0/grade`（第 37 行）和 `/internal/v0/diagnose`（第 55 行）。
- **计量**：
  - `services/bff/src/metering/meter.ts` 第 63–66 行只接了 `diagnosis_screen`、`diagnosis_full`（diagnose）和 `mistake_analysis`（grade）。
  - `0008` 第 558–565 行的 7 种资源里，按 teach 计的是 `lesson_whiteboard`（节）和 `ai_active_time`（分钟）。`0009` 第 79–81 行已允许它们来自 teach。
  - 所以 teach 的计量**不需要新迁移**，但要接 BFF 流程，属于计量逻辑（必停）。2-E spec 第 27 行已写明 teach 类资源“只实现数据库函数，不接 BFF 流程”。
  - plan、generate_variation、report 不消耗，没有对应资源类型。
- **掌握估计**：平台 `mastery_estimate` 有 `next_retest_at` 字段（`0005`），但还没有生产代码写它（归 4-C）。plan 的输入依赖它。
- **模型路由**（1-F）不存在。ADR 04 第 3 节第 1 条：适配器不能自己持有供应商 Key 或 BaseURL。
- **`evals/` 不存在**。`make eval-persona` 只打印“跳过”（Makefile 第 136–137 行）。
- **`packages/stage` 不存在**。ADR 07 规定只引入 OpenMAIC 的渲染层包，不引入 LGPL 的 `packages/mathml2omml`；讲解 DSL 要过发布闸门。

### 人设
- 《阿鸦规格书》V1.1：
  - §2.3 第 2 条（第 63 行）：提示等级由引擎决定，阿鸦不越级。
  - §3.1（第 75–84 行）：0–6 级提示各有“阿鸦话术模板”。第 6 级是“提供变式题”。
  - §4.1：首次对话必须做 AI 身份披露。§4.9：未经验证的题“阿鸦不讲”。§4.10：转介话术。
  - §5.2（第 223–232 行）：F 类禁语，其中包括“越级提示”。
- 用 F 类禁语里的字面词（粗心、马虎、不认真、很简单、不应该错、应该是对的、大概没问题、聪明、笨）扫了一遍 legacy 721 道题的提示，**没有命中**。越级提示这类语义禁语扫不出来。
- 提示里会出现答案：338 道填空题中，有 **45 道**在 0–3 级提示里就出现了标准答案原文（只统计 2 个字符以上的答案）。

## 设计要点

### 0. 四个方法的共同做法
- **不调模型，不带人设**：`model_version` 一律写 `algo:<算法>@<版本>`；`source_version` 带 `algo:` 与 `items:` 两段。
- **不读写平台表**（规则 1）：需要的学习者状态都由请求带进来；本次只读，不留副本。
- **不输出掌握度**（规则 2）：plan、report 只读平台给的 `state`，不自己重算。
- **不读系统时钟**：输出只由请求体和内容包决定。同 key 重试一定得到同一响应。
- **幂等**：复用现有包装。`task_id` 这类 ID 用 `deriveUuid(方法, key, 指纹, 标签)` 派生。
- **日志**：沿用现有白名单（方法名、状态码、错误码、request_id、耗时），不记请求内容。
- **准入与效力**：
  - 结果里引用了未准入的题（`item_ref`）→ `semantics = practice`。
  - 这四个方法不产生判分，**不置 `human_review_required`**，避免刷满复核队列（sprint1 lessons 待人确认 #3 同一问题）。
  - 置信度口径见待人确认 #6。
- **能力声明**：六个方法都是 `implemented: true, async: false`，`adapter_version` 升到 `0.2.0`。
  - 能力受限的方法用 `notes_code` 如实标注：teach 标 `hint_mode_only`，report 标 `forecast_omitted`。
- **内容包升级**（由 `scripts/build-content-pack.ts` 从同一 legacy 提交重新生成）：
  - 节点加：`prerequisites`（只留节点 ID）、`board`。
  - 题目加：`usage`、`estMinutes`、`hintPath`。
  - 另加 `board_exam_share`（来源 `content/examweights.ts`，标注“待教研校准”）。
  - 仍然**不放题干和解析**。答案仍只在进程内使用。
  - 内容包格式属于适配器私有，不是契约，也不是迁移。
  - **与 4-G 撞版本号**：4-G spec 第 6 节（`specs/release-gate-sprint4/spec.md` 第 275–285 行）也要把格式升到 `aya-content-pack/2`，内容是“逐题来源状态 + 从平台导出的准入决定文件”。两边改的是同一个构建脚本和 `content.ts`。默认：4-G 的 PR 3 先合并，本 spec 在它之上再加字段，格式记为 `aya-content-pack/3`（待人确认 #10）。
  - 提示原文进包以后，内容包第一次有了面向学生的文字。4-G 检查 C9（词表检查）原本写“对阿鸦不适用”（4-G spec 第 243 行），届时应覆盖 `hintPath`。

### 1. plan：学科内的候选任务
**算法** `aya-plan@0.1.0`，改写自 legacy `computePriorities`：
- **输入**：请求里的 `mastery[]`。不在内容包里的能力点跳过，不报错。
- **复测任务**：`state` 为 stable / transfer / retain 并且带 `next_retest_at` 的能力点，生成 `kind = retest`，`rationale_code = retest_scheduled`。
  - **什么时候复测由 4-C 决定**。适配器不算间隔，也不判断“今天到没到期”，只按 `next_retest_at` 升序排列。今天做哪几个，由平台筛（待人确认 #2）。
  - 复测选题：同节点的 R 题优先，没有就用 T / L 题。
- **练习任务**：`state` 为 understand / execute 的能力点，生成 `kind = practice`。
  - 薄弱度：有 `p_mastery` 就用 `1 − p_mastery`；没有就按状态取默认值（understand 0.7，execute 0.5）。
  - 优先级原始值 = 薄弱度 × (1 + 0.5 × 以它为前置的节点数) × 考查价值（S、M 类 1.2，其他 1.0）× (0.8 + 2.5 × 板块占比) ÷ 成本（M 类 1.3，其他 1.0）。这是 legacy 公式原样搬过来的。
  - `rationale_code` 取最主要的一个：以它为前置的节点 ≥ 3 → `prerequisite_of_many`；板块占比 ≥ 0.15 → `high_exam_weight`；其余 → `weak_mastery`。
  - 选题：同节点的 D、T、L 题，按题号取前 3 道。
- **unseen 的能力点不排任务**：没有证据，应先诊断。诊断入口由 4-H 和点点决定。
- **排序与截断**：
  - 复测优先级落在 0.85–1.0，练习折算到 0.1–0.8。
  - 有 `time_budget_minutes` 时，按优先级累加 `estimated_minutes`（所选题的 `estMinutes` 之和），超出预算就停；没有预算时最多 8 个任务。
  - `focus_skill_nodes` 非空时，只在这些能力点里排。
  - `exam_date` v0 不使用（考前四周“稳住”模式留给阶段 1）。
- **前置关系**：两个任务的能力点互为前置时，填 `prerequisite_task_ids`。
- **mastery 为空** → 返回空 `tasks`。

**其他**：
- 判定来源：确定性算法，不是判分。
- 不消耗，不调模型。
- 数据最小化：输出只有 ID、编码和数字。
- 不产生证据：`evidence_ids = []`。
- 请求里没有作答历史，所以 plan 可能连续几天推荐同样的题。“刷同题不能点亮”由 4-C 的门槛防住；是否需要排除已做题，见待人确认 #2。

### 2. generate_variation：从已准入题库选题
**算法** `aya-variant-select@0.1.0`，改写自 legacy `pickVariant`：
- **目标**：
  - 给了 `source_item_ref`：目标节点是这道题的主节点；目标错因是它所有陷阱码对应的阿鸦错因编码。
  - 给了 `skill_node`：只按节点匹配。
  - 给了 `subject_error_category`：必须是映射表里的阿鸦编码，否则 422。
- **候选池**：不是原题、不在 `exclude_item_refs` 里、同主节点或同错因，并且内容包里是 `admitted`。
- **排序**：两项都匹配的在前，然后同 `usage` 的在前，最后按题号。取前 `count` 道。
- **空结果的原因**：
  - 节点不在内容包 → `skill_node_unknown`；
  - 有匹配的已准入题，但全被排除 → `all_excluded`；
  - 其他 → `no_admitted_candidate`。
- **现在一定返回空**：721 道题都没准入，所有请求都得到 `no_admitted_candidate`。这是契约要求的正确行为（第 199–201 行），不是缺陷。
  - 选题逻辑用单测覆盖：测试里放一份**合成内容包**，其中几道题标为 admitted。
- `difficulty` 不输出：legacy 难度是 8 维向量，怎么折成 0–1 没有定义。
- 空结果时 `semantics = formal`、`confidence = 1.0`，与契约示例一致。
- 不消耗，不调模型，不产生证据。

**不做 AI 生成**：
- 生成器另立项。生成的题只能进平台 `content_review`，要过 4-G 发布闸门才能进候选池。
- 验证器 v0 只能比较“学生答案”与“已登记的标准答案”是否等价，**不会解题**。它不能单独证明一道新生成题的答案是对的，这要靠 4-G 的确定性检查加教研审核。

**准入状态从哪来**：
- 准入的唯一真相源是 `platform.item.admission`，内容包里的是副本（4-G spec 第 6 节）。
- 内容包里的 `admission` 目前全部是 `source_unverified`。
- 4-G 计划让构建脚本读取从平台导出的“准入决定”文件，再生成新包。本方法直接用包里的准入，不另建同步机制（待人确认 #7）。
- 在第一批题通过闸门之前，候选池一直是空的。

### 3. report：按时间窗提供学科字段
**算法** `aya-report@0.1.0`：
- **只接受阿鸦的数据**：`evidence`、`error_attributions`、`mastery_history` 里出现别的学科 → 400 `SUBJECT_MISMATCH`。
- **`skill_summary`**：
  - `attempts`：时间窗内 `response_text` / `response_choice` 证据按能力点计数。
  - `trend`：按状态顺序 unseen < understand < execute < stable < transfer < retain，比较时间窗内最早和最晚的一条记录，得出 up / flat / down。时间窗之前没有任何记录的能力点记为 `new`。
  - 这要求平台在 `mastery_history` 里附上时间窗之前的最后一条记录。否则所有能力点都会是 `new`（登记给 5-H）。
- **`error_distribution`**：按（跨科大类，阿鸦编码）计数。**敏感类“心理与习惯”不计入**，与 grade 的规则一致。
- **`highlight_codes`**（v0 只有三个，编码待教研和 5-H 确认）：
  - `skills_improved`：至少一个 up；
  - `skills_declined`：至少一个 down；
  - `no_activity`：时间窗内没有作答证据。
- **`forecast` 一律省略**：没有已准入的题，契约要求省略（第 1264 行）。v0 也不实现预测算法。
- **受众**：student、parent、teacher 三种受众输出同样的字段。字段里没有文本，口吻由平台报告引擎负责。家长端不出现阿鸦（规格书 §4.11）。
- **不输出**：原文、排名、百分位、同伴比较、XP、掌握度数值。只输出编码、计数和请求里带来的证据 ID。这些限制与 3-D 对 echo report 的约束一致。
- **信封**：
  - `evidence_ids` 是本次计入统计的请求证据 ID。
  - 引用了未准入证据 → `practice`。
  - `confidence` 取所用错因记录置信度的最小值，没有错因记录时是 1.0。
- 不消耗，不调模型，不产生新证据。
- 学习服务现在不调用 report。平台报告引擎属于 5-H。

### 4. teach：v0 只做确定性的提示阶梯
teach 是四个方法里最难的一个。完整的 teach 依赖这几样东西，**目前都没有**：

| 依赖 | 现状 |
|---|---|
| 模型路由（1-F） | 不存在。适配器不能自持 Key（ADR 04） |
| 阿鸦人设 Prompt 重写（V1.1） | 必停项，没开始 |
| 人设回归（`evals/persona`） | 不存在，`make eval-persona` 只打印“跳过” |
| teach 计量的 BFF 流程 | 只有数据库函数 |
| 学习服务 teach 接口 | 不存在 |
| 讲解 DSL 与白板（`packages/stage`） | 不存在 |

**v0 方案（默认，待人确认 #1）：确定性提示阶梯 `aya-hint-ladder@0.1.0`**
- 只支持 `mode = hint`。`mode = lesson` 返回 422，处理方式与 grade 的拍照作答相同（sprint1 lessons 待人确认 #10）。
- 必须带 `item_ref`，`skill_node` 必须是这道题的主节点之一，否则 422。
- **输出等级**：`min(allowed_hint_level, 5)`。
  - 第 6 级（变式）不由 teach 给，交给 generate_variation。600 道题本来也只有 0–5 级。
  - **不越级**：只返回这一级的那一条提示，不附带更高等级的内容（规格书 §2.3、F 类“越级提示”）。
- **防止提前露答案**：
  - 0–3 级提示如果含标准答案原文，就逐级降到不含答案的最高一级。`hint_level_used` 如实写降级后的等级。
  - 答案原文的比较在进程内完成，答案不出进程。
  - 命中的题目清单（起草时统计是 45 道填空题）写进 lessons，交教研修改，适配器不改题。
- **segments**：一条 `text`，就是内容包里的提示原文。
  - 提示里的 `$...$` 公式不拆成 `formula` 段，原样放在文本里，由前端用 KaTeX 渲染。
  - 不输出 `whiteboard_dsl`。
- **人设字段**：
  - `ai_disclosure_shown = false`：v0 不输出任何人设话术，AI 身份披露由平台人设层负责。
  - `referral_triggered = false`：v0 **不读 `conversation`**，所以也不做情绪识别和转介。
  - 这两条意味着：**teach v0 不能直接当聊天入口使用**（见“不做”与待人确认 #3）。
- **不读、不存、不记**：`conversation`、`recent_evidence`。它们只参与请求体指纹。
- **`verified_claims`**：不输出。提示是静态内容，正确性由内容审核负责。legacy `lib/verify-claims.ts` 留给以后校验模型输出。
- **判定来源**：teach 不判对错，输出里没有对错结论（规则 3）。提示是题目级的内容，不针对学生这一次的作答。
- **证据**：不产生证据，`evidence_ids = []`。
  - 原因：TeachRequest 没有事件发生时间，而证据的 `occurred_at` 在重试时必须不变。
  - 提示依赖由平台通过 `AttemptInput.hint_level_used` 记录，4-C 的门槛可以直接用。
- **效力**：题目未准入 → `practice`。
- **计量**：契约规定 teach 是消耗类，所以请求里 `usage_id` 必填。适配器照常原样使用，不扣费。
  - 平台对“确定性提示”是否扣、扣哪种资源，不在本 spec 内（待人确认 #4）。
- **source_version**：`algo:aya-hint-ladder@0.1.0;items:<内容包版本>`。**不写 `prompt:`**，因为没有用任何 Prompt。

**另一个选择：teach 推迟到模型路由和人设重写之后**
- 阿鸦停在 **5/6**，Gate 0 这条软条件对阿鸦也不满足。
- 好处：不会让人误以为 teach 已经可用。

**为什么默认选 v0**：
- 提示阶梯是规格书 §3.1 里“引擎动作”那一列，属于学科内容，不是人设话术。
- 它不需要模型，数据不出适配器。
- 以后接模型时，可以把它当作阿鸦话术的“内容底稿”。

**但要说清楚**：teach 通过一致性测试，只表示“提示阶梯这一小块”符合契约。它**不等于**阿鸦教练可以上线。

### 5. 一致性测试夹具
- 在 `adapters/aya/conformance/fixtures.yaml` 追加用例，**由独立会话编写**，不参考适配器实现。每条用例都带 `conflict_body`。

| 方法 | 用例 |
|---|---|
| plan | 有复测、有练习；mastery 为空 |
| teach | 2 级提示；上限给 6 |
| generate_variation | 按原题选（空结果，`no_admitted_candidate`）；未知节点（`skill_node_unknown`） |
| report | 家长受众；时间窗内没有证据 |

- 通用测试对这四个方法只查 schema、坏请求和幂等。**内容断言写在适配器单测里**（见验收标准）。
- 不修改 `packages/contracts/conformance/` 与共享核对库。给这四个方法补平台侧核对，属于契约包变更（必停），另立任务（待人确认 #5）。

### 6. 与平台的接口
本 spec **不新增**学习服务接口。按计划，平台接入这四个方法要各自新增 `learning-internal` 接口，都属于契约变更（必停）：
- plan：4-H 的“今日计划（读 plan）”需要 `/internal/v0/plan`：学习服务读 `mastery_estimate`，再调适配器。
- report：5-H 周报。
- teach：teach 接口、BFF 计量流程、人设层，放在阶段 1。
- generate_variation：阶段 1 M4 的“错题再练闭环”。

## 范围

### 做
1. `adapters/aya/src/`：新增 plan、generate_variation、report、teach（hint）四个处理函数；更新 capabilities；删掉 501 分支。
2. 内容包升级（设计要点第 0 节；格式版本号见待人确认 #10）：修改生成脚本与 `content.ts` 的加载和引用完整性检查；重新生成 `content/pack.json`。
3. 单测（`adapters/aya/tests/`），见验收标准。
4. 一致性测试夹具追加用例（独立会话编写）。
5. 更新 `adapters/aya/README.md` 的接口表与边界说明。
6. `specs/adapter-aya-methods-sprint4/lessons.md`：实现记录；提示含答案的题目清单；新增待人确认项。

### 不做
- 知行、文砺、开口成章的任何方法（见下一节）。
- 调用任何模型；阿鸦人设 Prompt 与话术；`evals/persona`。
- `teach` 的 `lesson` 模式、白板与讲解 DSL、`packages/stage`、OpenMAIC。
- `generate_variation` 的 AI 生成。
- 预测区间（`forecast`）算法。
- `learning-internal` 新接口；BFF 计量流程；任何迁移、RLS 与契约变更。
- 共享核对库与一致性测试框架的修改。
- 修改 legacy 或内容包里的题目。

## 其他三科与 Gate 0（只登记，不实现）
| 学科 | 现状 | 4-D 目标 | 按现状能达到 |
|---|---|---|---|
| 阿鸦 | 3 个方法已实现 | 6/6 | 默认方案 6/6；teach 推迟则 5/6 |
| 知行 | `adapters/` 下没有；2-D 没有 spec，也没有实现 | ≥ 5/6 | 0/6 |
| 文砺 | 同上 | ≥ 5/6 | 0/6 |
| 开口成章 | 3-D 已搁置，没有 `adapters/echo` | ≥ 4/6 | 0/6（目标待人改） |

**影响**：
- Gate 0 软条件“契约测试覆盖率达标”在 W13 **不会满足**。按开发计划第六章，软条件不过可以带条件进入阶段 1，但要在阶段 1 首月关闭。
- 硬条件一“四科能写入同一学生模型”同样受影响。这超出本 spec，只在这里提醒。

**建议**（由人决定，本 spec 不改 `docs/plan/`）：
1. 知行优先于文砺，尽快起草 2-D 知行 spec。
   - 寒假封闭试点只上“数学 + 道法”（开发计划阶段 1 M4）；风险表的应对也写着“先上数学 + 道法”。
   - 知行的 skill_node 坐标还等道法教研拍板（`specs/inventory-zhixing/decisions.md` #7），这是 2-D 的前置。
2. Gate 0 证据包里，对这一条如实写“阿鸦 6/6；知行、文砺未开始；开口成章搁置”，由人决定是否带条件进入。
3. 开口成章的目标改为“不适用”，或等英语方案调整后重定（与 3-D lessons 待人确认 #1 合并处理）。

## 验收标准（可测试）
- [ ] `capabilities` 六个方法都是 `implemented: true`；teach、report 带上面说的 `notes_code`；`adapter_version = 0.2.0`。
- [ ] `make conformance` 生成的 `conformance-aya.json` 为 `"implemented": 6, "passed": 6`，并且 `global_failures` 为空。
- [ ] 四个方法的每个响应都通过契约自检（沿用第 280–284 行的 fail closed）。
- [ ] **plan**（单测）：
  - 同一请求两次调用，响应逐字节相同；
  - 输出里的能力点都来自请求的 `mastery`；unseen 的能力点不出现；
  - 复测任务按 `next_retest_at` 升序，优先级都高于练习任务；
  - 给了 `time_budget_minutes` 时，任务的 `estimated_minutes` 之和不超过预算；
  - 所有 `item_ref` 都在内容包里；
  - 引用了未准入的题时 `semantics = practice`。
- [ ] **generate_variation**（单测，合成内容包）：
  - 候选全部是 `admitted`；
  - 不含原题，不含 `exclude_item_refs` 里的题；
  - 每个候选至少同节点或同错因之一；
  - 三种 `empty_reason` 各有一条用例；
  - 用真实内容包时一律返回 `no_admitted_candidate`。
- [ ] **report**（单测）：
  - 响应里没有请求证据的 `payload` 原文，也没有 `forecast`；
  - 敏感类错因不计入 `error_distribution`；
  - 出现别的学科的数据时返回 400 `SUBJECT_MISMATCH`；
  - 七种 `mastery_history` 组合（up、flat、down、new 及边界）都有断言。
- [ ] **teach**（单测）：
  - 对每个 `allowed_hint_level`（0–6），`hint_level_used ≤ min(allowed, 5)`；
  - 输出文本等于该级提示原文，不含任何更高等级的提示；
  - **对内容包里每一道填空题**逐题断言：0–3 级的输出里没有标准答案原文；
  - `mode = lesson`、缺 `item_ref`、`skill_node` 与题目不符，都返回 422；
  - `conversation` 里的内容不出现在响应和日志里。
- [ ] 四个方法的日志只含白名单字段（沿用 `leak.test.ts` 的做法）。
- [ ] `src/` 不读系统时钟：有测试断言四个新方法的代码不调用 `Date.now` / `new Date()`。
- [ ] 升级后的内容包仍然没有题干和解析字段；有测试断言。
- [ ] 测试数据全部是合成数据（规则 8）。
- [ ] `make check` 通过。不新增依赖。

## 涉及的必停项（见 CLAUDE.md）
| 必停项 | 默认方案是否涉及 | 说明 |
|---|---|---|
| `db/migrations`、RLS | 否 | 不写迁移。teach 需要的资源类型 `0008` 已有 |
| 计量与权益逻辑 | 否 | 适配器只原样使用 `usage_id`。接 BFF 的 teach 计量流程另立任务，届时必停 |
| `packages/contracts`（插件 API、learning-internal、一致性测试、共享核对库） | 否 | 按 v0.9 实现，不改契约。新增学习服务接口、给四个方法补平台侧核对，都另立任务，届时必停 |
| `evals/` 金标准 | 否 | 不存在，也不创建 |
| 人设 Prompt、禁语表、转介话术 | **默认否，待人确认 #1** | teach v0 只返回内容包里的提示原文，不写任何话术。如果人认定“提示原文就是面向学生的话术”，teach 就变成必停，并且要等 `evals/persona` 建好才能跑回归 |
| 新增第三方服务、模型供应商、数据出境 | 否 | 不调模型，不新增依赖 |

**结论**：默认方案下 PR **不需要** `[NEEDS-HUMAN]`。以下情况会变成必停，届时 PR 首行写 `[NEEDS-HUMAN]`：
- 待人确认 #1 认定提示原文属于话术；
- 实现中发现契约需要改动（例如 TeachRequest 要加时间字段、PlanRequest 要加排除已做题）。

## 依赖与输入
- **已有**：契约 v0.9；错因映射表 v0.9（待教研签字）；阿鸦内容包与生成脚本；legacy 只读副本（提交 `0ed2fe45`）；一致性测试框架。
- **需要另一个会话**：编写四个方法的一致性测试夹具。
- **需要教研**：
  - 抽查 plan 的优先级参数和 `rationale_code`；
  - 确认 report 的 `highlight_codes`；
  - 修改提示里过早出现答案的题（清单见 lessons）。
- **同期 spec 的接口约定**（起草时还没有这些文件，下面是本 spec 的假设，需要对齐）：
  - **4-C**：`mastery_estimate.state` 与 `next_retest_at` 由 4-C 写；复测间隔以 4-C 的 0 / 3 / 14 天为准，legacy 2 / 7 / 14 / 21 天不再使用。
  - **4-G**（起草中途已出现 `specs/release-gate-sprint4/spec.md`，下面按它核对过）：
    - 只有闸门能把题目升为 `admitted`；内容包按平台导出的准入决定文件重建（4-G 第 6 节）。与本 spec 一致。
    - 两边都要改内容包格式，版本号撞在 `aya-content-pack/2`（待人确认 #10）。
    - 4-G 还要新增 `adapters/aya/scripts/check-content.ts`，与本 spec 改同一个适配器目录，合并顺序要协调。
    - 4-G 的 C9 词表检查应覆盖本 spec 新放进包里的提示原文。
  - **4-H**：
    - 今日计划读 plan，需要新增 `learning-internal` 接口（必停，归 4-H 或学习服务）。
    - plan 和 generate_variation 只返回 `item_ref`。**平台上没有阿鸦题干的来源**：内容包不含题干，`aya.question` 表没建（sprint1 待人确认 #2）。4-H 要显示题目，必须先定题干从哪来。
  - **5-H**：report 需要平台附上时间窗之前的最后一条掌握记录。

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
1. **teach 做不做 v0**：
   - 默认：做确定性提示阶梯（只有 hint 模式、最高 5 级、不调模型、不写话术）。阿鸦达到 6/6。提示原文视为学科内容，不视为人设话术，所以不是必停项。
   - 另一个选择：teach 推迟到模型路由、人设 Prompt 重写和 `evals/persona` 都就绪之后。阿鸦停在 5/6，这条软条件对阿鸦也不满足。
   - 附带要确认的两点：
     - 规格书 §4.9“未经验证的题，阿鸦不讲”是否适用于 Q1 题的提示？默认不适用，因为 §4.9 说的是“生成题和超纲内容”，而且 Q1 题本来就允许做练习。
     - 这些题来源待核（decisions #3），把提示原文展示给学生，版权风险与展示题干相同。
2. **plan 的复测与选题**：
   - 默认：适配器不读时钟，给出所有带 `next_retest_at` 的复测任务，“今天做哪些”由 4-C / 4-H 筛；练习选题不排除已做过的题。
   - 另一个选择：契约 v1.0 给 PlanRequest 加 `as_of` 与 `exclude_item_refs`，让适配器直接给出“今天到期”的复测和没做过的题（契约变更，必停）。
3. **teach v0 的使用边界**：
   - 默认：v0 只作为“提示按钮”使用，平台不能把学生的自由输入直接送进 teach。因为 v0 不读对话，不做转介识别，也没有 AI 身份披露。
   - 另一个选择：允许平台在对话里调用 teach v0，这时转介识别和身份披露必须由平台人设层先做（依赖 3-F，目前是草稿）。
4. **确定性提示是否计量**：
   - 默认：本 spec 不定。建议平台接入 teach 时，确定性提示不扣额度：预留后按 0 结算，或者契约 v1.0 让 `mode = hint` 且不调模型时 `usage_id` 可省（计量与契约都必停）。
   - 另一个选择：按 `ai_active_time` 计分钟。
5. **一致性测试对新方法的深度**：
   - 默认：本冲刺只靠通用测试（schema、坏请求、幂等）加适配器单测。
   - 另一个选择：另立任务，在共享核对库里给 plan、teach、generate_variation、report 补平台侧核对与泄露检查（`packages/contracts` 变更，必停）。这会让 Gate 0 的 6/6 更可信。
6. **新方法的置信度与效力口径**：
   - 默认：
     - plan、teach 引用了未准入的题时，`confidence = 0.5`，否则 1.0；
     - generate_variation 为 1.0；
     - report 取所用错因记录置信度的最小值；
     - 四个方法都不置 `human_review_required`。
   - 另一个选择：统一写 1.0，只用 `semantics` 表示效力。
7. **4-G 放行的题怎样进入阿鸦内容包**：
   - 默认：沿用 4-G 的方案——构建脚本读取从平台导出的准入决定文件，重新生成内容包、重新部署。本 spec 不另建机制。
   - 另一个选择：适配器启动时从平台读取准入状态（需要新的只读接口，契约变更，必停）。
8. **plan 的参数来源**：
   - 默认：沿用 legacy 公式和 `BOARD_EXAM_SHARE`（Q1 级推导值），在 lessons 里标“待教研校准”。
   - 另一个选择：等教研给出板块权重后再实现 plan。代价是 plan 推迟，阿鸦 5/6。
9. **Gate 0 口径**（登记，由人改开发计划）：
   - 默认：本 spec 只保证阿鸦 6/6。知行、文砺、开口成章在证据包里如实写“未开始 / 搁置”，并建议优先起草 2-D 知行 spec。
   - 另一个选择：人工修改 Gate 0 软条件（例如知行、文砺目标延到阶段 1 首月，开口成章改为不适用）。
10. **内容包格式与 4-G 的合并顺序**：
    - 默认：4-G 的 PR 3（`aya-content-pack/2`，逐题来源与准入决定文件）先合并；本 spec 在它之上加字段，记为 `aya-content-pack/3`。
    - 另一个选择：本 spec 先合并并占用 `/2`，4-G 在其上改为 `/3`。或者两边合成一次格式变更，由一个 PR 完成。
