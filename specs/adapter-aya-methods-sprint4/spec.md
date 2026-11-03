# 冲刺 4-D（阿鸦部分）：阿鸦补齐 plan、teach、generate_variation、report

状态：**已确认（2026-09-30，用户“按默认建议，开始实现”）**，待人确认全部按默认执行。

> **2026-09-28：按开发计划 v2.0 改写**（方案见 `specs/sprint4-v2-rewrite/spec.md`，待人确认 #1 按默认：按插件 API v1.0 候选版写）。
> - 本 spec 按 `packages/contracts/openapi/plugin-api.v1.0.yaml`（**候选版，待评审签字**）写。评审会推翻其中任何一项结论时，本 spec 随之修改，修改后再确认。
> - 本 spec 在插件 API v1.0 第二步完成之前不能开始实现（见“前置依赖”）。
> - **2026-09-30 复核**：插件 API v1.0 第二步已实现（`specs/plugin-api-v1.0-step2/`：阿鸦支持 1.0.0 候选版、学习服务 `/internal/v1` 写入与封存表 0016），前置依赖第 1 条已满足（仍是候选版，签字前以 `--allow-candidate-contract` 运行）。KGE-03 知识服务已实现 `node.path`、`page.get`、`item.search`、`item.similar`（本 spec 的 teach(lesson) 与 generate_variation 可由编排层按契约把结果放进请求）；SIE-02a 诊断已合并，plan 的 `mastery[]` 仍按 `aya:` 能力点（KGE-04 第二步前）。
> - 按 v1.0 口径起草的上一版在 git 历史里（提交 `ff9d3f3` 及以前），逐项差异见“相对 v1.0 版本的变化”。

关联（现行依据）：
- **开发计划 v2.0**（`docs/plan/开发计划-v2.0.html`）冲刺 4（W9–W10）D 流“四科补齐剩余方法”，验收“阿鸦 6/6、知行与文砺 ≥ 5/6、开口成章 ≥ 4/6”；Gate 0 软条件“契约测试覆盖率”。阿鸦 diagnose、grade 属冲刺 1 的 ADP-数学-01，已合并。
- **覆盖率口径**：`specs/plan-v2-alignment/spec.md` 第三节决定 #1（2026-09-27）：英语继续搁置，“开口成章 ≥ 4/6”不计，`/gate-check` 标为“英语搁置，需人定口径”。本 spec 只做阿鸦（用户 2026-09-26 决定）；知行、文砺没有适配器，只登记。
- **契约**：`packages/contracts/openapi/plugin-api.v1.0.yaml`（`info.description` 列出全部变化）；`specs/plugin-api-v1.0/spec.md` 与 `lessons.md`；评审清单 `specs/contract-conformance-sprint1/v1.0-review.md` 最后一列（默认结论，待签字）。请求里衔接字段的形状来自 `knowledge-service.v0.9.yaml`、`insight-service.v0.9.yaml`（跨文件引用）。
- **两个核心**：`docs/plan/KGE-知识与成长引擎-V1.1.html` 第八章（知识服务工具与调用方：`node.path` 给 plan，`page.get` 给 teach，`item.search` / `item.similar` 给 generate_variation、plan）；`docs/plan/SIE-学生洞察引擎-V1.1.html`（diagnose、plan、report 读画像；grade、teach 只写证据）。
- **ADR**：11（星图统一定义，`docs/adr/11-star-map-unified-definition.md`）、13（判断账本唯一写入方，`docs/adr/13-claim-ledger-single-writer.md`）、15（输入通道与作答证据分级，`docs/adr/15-input-channels-and-answer-evidence.md`）；04（模型路由）、06（计量）、07（讲解 DSL 与许可证）。以上都是草稿。
- **前序**：`specs/adapter-aya-sprint1/spec.md` 与 `lessons.md`（capabilities、grade、diagnose 已合并）；`specs/contract-conformance-sprint1/`（一致性测试）。
- **盘点**：`specs/inventory-aya/adapter-design.md` v0.2 第 1 节“六方法映射”、第 3.1 节“内容准入”；`specs/inventory-aya/decisions.md` #3、#4、#9、#11。
- **同期 spec**：4-C `specs/mastery-estimate-sprint4/spec.md`（KGE-04 掌握 v0）、4-G `specs/release-gate-sprint4/spec.md`、4-H `specs/unified-shell-sprint4/spec.md`，三份也在按 v2.0 改写；接口约定以本文“与同期 spec 的协调”为准。
- **计量**：`specs/metering-v2/spec.md`（资源类型 `ai_active_time`、`lesson_whiteboard`）。
- **人设（只作下游引用，本 spec 不改）**：`docs/personas/阿鸦-规格书-V1.1.md`；`specs/persona-governance-sprint3/spec.md`（草稿）。
- **CLAUDE.md**：规则 1、2、3、4、6、8、10、11、12、13。
- **追溯（已被取代，不作依据）**：`docs/archive/开发计划-阶段0-1-v1.0（已被v2.0取代）.html` 冲刺 4-D；`docs/archive/完整方案-v1.0（已被v1.1取代）.html`；`packages/contracts/openapi/plugin-api.v0.9.yaml`（迁移期保留到 W6）。

## 相对 v1.0 版本的变化
| 改写点 | 依据 |
|---|---|
| 契约从 v0.9 改为 v1.0 候选版；`contract_version = 1.0.0`；实现以 v1.0 第二步为前置 | `specs/sprint4-v2-rewrite/spec.md` 待人确认 #1；`specs/plugin-api-v1.0/lessons.md`“第二步” |
| 适配器只读编排层放进请求的数据：plan 读 `learner_context`、`node_path`、`exam_config`；report 读 `learner_context`；teach 读 `knowledge_page`；generate_variation 读 `item_family`、`exam_config`。不调用知识服务、洞察服务，不读考情库 | v1.0 `info.description`“与两个核心、考情库、输入通道的衔接”；评审 svc-#1、svc-#6、pol-#1；规则 1 |
| plan 的前置关系改由 `node_path` 提供，内容包**不再**加 legacy `prerequisites`；映射不到知识节点时不用前置因子 | 合并规则“知识结构以 KGE 为准”；KGE 第八章 `node.path`；v2.0 对阿鸦图谱“前置关系按必要性检验重审” |
| generate_variation 优先从 `item_family` 选题，内容包选题降为过渡实现；`difficulty` 取 `item_family` 的 `prior_difficulty` | 评审 svc-#6；`ItemTagView`（`knowledge-service.v0.9.yaml`）；v2.0 难度先验改分级加权求和（KGE-02） |
| teach 的 `lesson` 模式改为读 `knowledge_page`（页面人设中立，阿鸦话术在外层人设层加）；`hint` 仍是确定性提示阶梯 | 评审 svc-#6、sie-#3；KGE 第八章 `page.get` |
| teach 结果字段 `verified_claims` 改名 `verified_statements`（v0 不输出） | 评审 sie-#4 |
| report 只给字段与证据引用，不写结论句；`forecast` 移除（预测只由 `insight.predict` 给编排层）；能力声明去掉 `forecast_omitted` | 评审 sie-#1、sie-#2 与 v1.0 lessons 偏差 1；规则 11；ADR 13 |
| report 的 `attempts` 按 `attempt_id` 去重计数 | 评审 N3、N4（作答类证据必带 `attempt_id`） |
| 时间比较一律按带时区的时刻，不按字符串 | 评审 N10（`Timestamp` pattern） |
| 幂等键是小写 UUIDv4（夹具与 `conflict_body` 随之改）；派生 ID 仍用现有 `deriveUuid` | v1.0 lessons 偏差 7 |
| 请求前缀一致性（`aya:`）先由 schema 拒绝，适配器仍自查 report 的历史数据 | 评审 N7（report 不在 schema 约束内） |
| 不支持的模式改用 422 `MODE_NOT_SUPPORTED` | 评审 N9 |
| 采分点 `point_id` 只允许小写：本 spec 四个方法不涉及（阿鸦判分是验证器型），只在第二步核对 | 评审 svc-#7 |
| 覆盖率口径改为“阿鸦 6/6；英语搁置不计”；开口成章从登记表移除 | 对照文档决定 #1 |
| 关于学生的陈述：plan 的 `rationale_code`、report 的 `trend` / `highlight_codes` 都是编码，面向学生家长的句子由洞察服务按判断模板生成 | 规则 11；ADR 13；评审 sie-#1 |
| 迁移编号的协调改为“0010–0015 已占用，新迁移从 0016 起按合并顺序” | 仓库 `db/migrations/` 现状 |
| 待人确认去重：v1.0 已定的（PlanRequest 不加 `as_of` / `exclude_item_refs`、teach 计量资源、Gate 0 英语口径）改为引用 | 见“待人确认”末尾“已决项” |

## 目标
阿鸦适配器按插件 API v1.0 声明六个方法都“已实现”，并在 `contract_version = 1.0.0` 下通过同一套一致性测试，覆盖率报告显示 **aya 6/6**。

新补的四个方法都是**确定性的**：
- 不调用模型（没有模型路由，ADR 04）；
- 不产生人设话术，不写关于学生的陈述（规则 11）；
- 不读写平台表、两个核心的表、考情库（规则 1）；
- 不产生证据，不改掌握度或判断（规则 2、11）。

它们先在适配器层可用、可测。平台什么时候真正调用它们，由 4-H、5-H 与阶段 1 决定。

## 前置依赖
1. **插件 API v1.0 第二步**（`specs/plugin-api-v1.0/lessons.md`“第二步”，另起任务）必须先合并：
   - ajv 能解析 v1.0 的跨文件引用（`./knowledge-service.v0.9.yaml`、`./insight-service.v0.9.yaml`），或者把契约打包成单文件，并有一条检查覆盖。现在 `adapters/aya/src/contract.ts` 只 `addSchema` 一份文件，PlanRequest、TeachRequest、GenerateVariationRequest、ReportRequest 的跨文件引用都解析不了。
   - 阿鸦适配器切到 v1.0：`CONTRACT_VERSION = 1.0.0`；作答类证据带 `attempt_id`；时间与前缀已符合。
   - 学习服务同时接受 v0.9 与 v1.0。
   - `make conformance` 的阿鸦一行按 `STARBUDDY_CONTRACT_VERSION=1.0.0`、`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1` 运行（现在 Makefile 里阿鸦仍按 0.9.0 跑）。
2. **v1.0 评审签字**：签字前本 spec 按候选版实现；签字推翻的项，按“状态”一节处理。
3. 4-G 的内容包格式变更先合并（与同期 spec 的协调第 5 条）。

## 现状（2026-09-28 核实）

### 适配器
- `adapters/aya/src/adapter.ts`：
  - 第 289–303 行 `capabilities`：只有 diagnose、grade 是 `implemented: true`；`adapter_version` 为 `0.1.0`（第 21 行）。
  - 第 412–431 行：plan、teach、generate_variation、report 统一返回 501 `METHOD_NOT_IMPLEMENTED`。
  - 第 260–277 行：幂等包装，只存请求体指纹，同 key 换请求体返回 409。新方法直接复用。它用 `Date.now()` 算缓存过期，这是包装层，不是方法逻辑。
  - 第 280–284 行：出门前按契约自检，不合契约返回 500（fail closed）。
- `adapters/aya/src/contract.ts` 第 9 行：契约路径仍是 `plugin-api.v0.9.yaml`。
- `adapters/aya/src/server.ts`：只做令牌、请求体与路由转发，新方法不需要改 HTTP 层。
- `adapters/aya/src/ids.ts`：`deriveUuid` 由 key 与请求体指纹确定性派生 ID，指纹不含 `request_id`。

### 内容包
- `adapters/aya/content/pack.json`，格式 `aya-content-pack/1`，legacy 提交 `0ed2fe45`：
  - 721 道题（单选 369、填空 338、多选 14），**全部是 `source_unverified`**，没有一道已准入；每题只有 `id`、`format`、`answer`、`mainNodes`、`reviewStatus`、`admission`、`options`。
  - 959 个节点，只有 `id`、`kind`、`cluster`；429 个陷阱码。
- 包里刻意没有题干、解析、提示、前置关系、题目用途、预估时长、板块（`adapters/aya/README.md`“结构”）。
- 新方法要用、legacy 里有的：
  - `hintPath`：721 道都有；600 道只有 0–5 级，121 道有 0–6 级。
  - `usage`：D 419、T 119、R 126、P 29、L 28；有 R（复测）题的节点 134 个。
  - `estMinutes`：721 道都有。
  - 板块与中考分值占比 `BOARD_EXAM_SHARE`（legacy `content/examweights.ts`，路径相对于 legacy 根目录），注释写明“产品推导值，Q1 级，待教研校准”。
- **有题的节点只有 452 个**（47%）。
- 包里还没有阿鸦节点与 KGE 知识节点的对应关系：KGE-01 还没导入阿鸦图谱，`kge.skill_link` 没有阿鸦的行。

### legacy 可借用的实现（`legacy/aya` 只读，即 `/home/user/ayaheshui`，提交 `0ed2fe45`；下表路径相对于它）
| 方法 | legacy 实现 | 用法 |
|---|---|---|
| plan | `lib/scoring.ts` `computePriorities`：薄弱 × 前置影响 × 考查价值 × 板块频次 ÷ 成本 | 借公式；输入改为请求的 `mastery[]` 与 `node_path`，输出改为编码 |
| plan（复测） | `lib/review.ts` 2 / 7 / 14 / 21 天间隔 | 不用。复测时间归 4-C（0 / 3 / 14 天），适配器只按 `next_retest_at` 排序 |
| generate_variation | `lib/mistakes.ts` `pickVariant` | 借“同主节点、非原题”；加准入过滤与同错因匹配 |
| report | `app/report/narrative.ts`、`lib/weekly.ts`、`lib/trends.ts`、`lib/exam.ts` | 只借口径；叙事、重放算掌握度、预测都不用 |
| teach | `lib/prompts.ts`（提示与完整解析塞进模型 Prompt）、`lib/lesson.ts`（九步微课） | 不包装；只取 `hintPath` 原文 |

### 契约（`plugin-api.v1.0.yaml`，候选版）
| 方法 | 路径 | 请求（schema 起始行） | 响应 `result` | 消耗类 | 可异步 |
|---|---|---|---|---|---|
| plan | 第 147 行 | 第 927 行：`mastery[]`、`constraints`、可选 `learner_context`、`node_path`、`exam_config` | 第 979–1010 行：`tasks[]`，`rationale_code` 是编码 | 否 | 否 |
| teach | 第 174 行 | 第 1030 行：`MeteredContext`、`mode`、`skill_node`、`item_ref`、`allowed_hint_level`、`persona`、`conversation`、`recent_evidence`、可选 `knowledge_page` | 第 1111–1138 行：`segments[]`、`hint_level_used`、`ai_disclosure_shown`、`referral_triggered`、`verified_statements` | **是** | 否 |
| generate_variation | 第 247 行 | 第 1384 行：`source_item_ref` 或 `skill_node`、`subject_error_category`、`count`、`exclude_item_refs`、可选 `item_family`、`exam_config` | 第 1436–1475 行：候选必须 `admitted`；为空时必须给 `empty_reason` | 否 | 否 |
| report | 第 277 行 | 第 1486 行：`window`、`audience`、`evidence`、`error_attributions`、`mastery_history`、可选 `learner_context` | 第 1529–1562 行：`skill_summary`、`error_distribution`、`highlight_codes`；**没有 `forecast`** | 否 | 否 |

另外：
- 计量口径见 `info.description`“计量”：diagnose、grade、teach 的 `usage_id` 必填，其余可省；`idempotency_key` 都必填，是小写 UUIDv4（第 521 行）。适配器只原样使用，不扣费。
- teach 的 `mode` 描述（第 1036 行起）：`hint` 计入学时（`ai_active_time`），`lesson` 按节（`lesson_whiteboard`）。
- 只有 diagnose、grade 可以异步（第 1582、1594 行）。
- 四个响应都是 `unevaluatedProperties: false`。
- generate_variation 是**选题，不是生成**（第 253 行）。
- `ItemTagView`、`PathStep`、`PageGetResponse.result`、`ContextEntry` 来自两份服务契约 v0.9。`ItemTagView` 不含准入状态，也不含题干、答案、解析（“由调用方从学科内容包取”）。
- `PathStep` 只有 `from_id`、`to_id`、`depth`，`from_id` 是 `to_id` 的前置（`knowledge-service.v0.9.yaml` 的 `NodePathResponse` 示例）；PlanRequest 里的 `node_path` 不带根节点与方向。
- `SourceVersion` 的类别只有 rubric / verifier / prompt / items / algo / graph，写不进知识页面的版本。
- `ReportFields.skill_summary` 只有 `skill_node` 一层，没有知识节点层。

### 一致性测试与覆盖率
- `packages/contracts/conformance/test_conformance.py`：六个方法都做 schema 校验、坏请求、幂等；平台侧核对与泄露检查只覆盖 grade、diagnose（`packages/contracts/python/starbuddy_contract/checks.py` 第 29 行 `SUPPORTED_METHODS`）；没有作答的方法，夹具必须给 `conflict_body`（第 199 行）。
- `packages/contracts/conformance/conftest.py`：一个方法算“通过”，要声明已实现、至少一条用例、用例全过、整套没有全局失败。
- 阿鸦夹具 `adapters/aya/conformance/fixtures.yaml` 只有 grade、diagnose。夹具由另一个会话编写（`packages/contracts/conformance/README.md`“夹具由谁写”）。
- 所以 6/6 只证明“结构符合契约、坏请求被拒、幂等成立”，不证明内容正确；内容正确靠适配器单测。

### 平台侧
- `learning-internal.v0.yaml` 与 `learning-internal.v1.yaml` 都只有 grade、diagnose（及 v1 的重放接口），学习服务不调用这四个方法。
- 计量：`services/bff/src/metering/meter.ts` 只接了 diagnose、grade 的资源；teach 的资源类型在迁移 0008 / 0015 与 `specs/metering-v2/spec.md` 里已有，但没有 BFF 流程。
- 掌握：4-C 按 v2.0 把计算单元改为“学生 × knowledge_node × 认知层次”；插件 `MasterySnapshot` 仍按 `skill_node`（`aya:` 前缀）给。两者怎么对应，见待人确认 #2。
- 模型路由（ADR 04）、`evals/`、`packages/stage` 都不存在；`make eval-persona` 只打印“跳过”。

### 人设（只引用）
- 《阿鸦规格书》V1.1：§2.3（提示等级由引擎决定，阿鸦不越级）；§3.1（0–6 级提示，第 6 级是“提供变式题”）；§4.1（首次对话 AI 身份披露）；§4.9（未经验证的题阿鸦不讲）；§4.11（家长端）；§5.2（F 类禁语，含“越级提示”）。
- legacy 721 道题的提示用 F 类字面禁语扫过，没有命中；338 道填空题中 **45 道**在 0–3 级提示里出现了标准答案原文（只统计 2 个字符以上的答案）。

## 设计要点

### 0. 四个方法的共同做法
- **不调模型，不带人设**：`model_version` 写 `algo:<算法>@<版本>`；`source_version` 带 `algo:` 与 `items:` 两段；用到 `node_path` 或知识节点对应表时再加 `graph:<学科>-map@x.y.z` 段（评审 kge-#4）。
- **只读请求，不留副本**（规则 1、2）：学习者状态只来自请求；`learner_context`、`conversation`、`recent_evidence`、`evidence` 只在本次请求内使用，不保存、不写日志、不回显。
- **不输出掌握度**：plan、report 只读平台给的 `state`，不重算。
- **不读系统时钟**：输出只由请求体与内容包决定，同 key 重试得到逐字节相同的响应。
- **时间**：所有时间按带时区的时刻比较（`+08:00` 与 `Z` 混用时结果正确）；到期与否由平台判断。
- **幂等**：复用现有包装；`task_id` 这类 ID 用 `deriveUuid(方法, key, 指纹, 标签)` 派生。
- **前缀**：请求里出现非 `aya:` 的引用时返回 400 `SUBJECT_MISMATCH`（plan、teach、generate_variation 由 schema 先拒绝，report 由适配器检查）。
- **日志**：沿用白名单（方法名、状态码、错误码、request_id、耗时）。
- **准入与效力**：
  - 结果引用了未准入的题 → `semantics = practice`；
  - 四个方法不判分，**不置 `human_review_required`**（sprint1 lessons 决定第 3 条的同一问题）；
  - 置信度口径见待人确认 #5。
- **关于学生的陈述**（规则 11）：四个方法只输出编码、计数、ID。`rationale_code`、`trend`、`highlight_codes` 不直接展示给学生家长；平台要把它们说成句子，必须引用判断账本里的判断（洞察服务按模板生成，ADR 13）。
- **不推断**（规则 12）：编码表是封闭的白名单，不含性格、态度、天赋类编码（“粗心”“不认真”这类不作为编码）。
- **能力声明**：六个方法 `implemented: true, async: false`，`adapter_version = 0.2.0`；teach 标 `notes_code: deterministic_no_model`。
- **内容包升级**（`adapters/aya/scripts/build-content-pack.ts` 从同一 legacy 提交重新生成；格式版本见待人确认 #8）：
  - 题目加 `usage`、`estMinutes`、`hintPath`；题型沿用已有的 `format`；
  - 节点加 `board`；另加 `board_exam_share`（标注“Q1 推导值，待教研校准”，过渡用，见待人确认 #6）；
  - **不加** legacy `prerequisites`（知识结构以 KGE 为准，前置关系由 `node_path` 提供）；
  - 预留“阿鸦节点 → KGE 知识节点”对应表，内容来自平台导出的 `kge.skill_link`（KGE-01 导入阿鸦图谱之后才有），带地图版本；没有时为空（待人确认 #2）；
  - 仍然**不放题干和解析**；答案仍只在进程内使用；
  - 提示原文进包后，4-G 的 C9 词表检查要覆盖 `hintPath`。

### 1. plan：学科内的候选任务
**算法** `aya-plan@0.1.0`，改写自 legacy `computePriorities`。
- **读哪些字段**：
  - `mastery[]`：平台的掌握快照。不在内容包里的能力点跳过，不报错；
  - `constraints`：`time_budget_minutes`、`focus_skill_nodes`；`exam_date` v0 不用（考前四周模式留给阶段 1）；
  - `node_path`：前置边，`from_id` 是 `to_id` 的前置；
  - `exam_config`：v0 只用 `question_type_codes`：非空时只选题型在列表内的题（阿鸦题型与考情编码的对应见待人确认 #7）；满分、时长不参与计算，也不在包内写死；
  - `learner_context`：v0 不用于计算（每个方法可收哪些字段，随洞察服务 SIE-01 定，见 `specs/plugin-api-v1.0/lessons.md` 待人确认 #5），只参与请求体指纹。
- **复测任务**：`state` 为 stable / transfer / retain 且带 `next_retest_at` 的能力点，生成 `kind = retest`、`rationale_code = retest_scheduled`。
  - 何时复测由 4-C 决定：适配器不算间隔，不判断“今天到没到期”，只按 `next_retest_at` 的时刻升序排列；“今天做哪几个”由平台筛。
  - 选题：同节点的 R 题优先，没有就用 T / L 题；平台可以改用 generate_variation 并排除做过的题（4-C 的做法）。
- **练习任务**：`state` 为 understand / execute 的能力点，生成 `kind = practice`。
  - 薄弱度：有 `p_mastery` 用 `1 − p_mastery`；没有按状态取默认值（understand 0.7，execute 0.5）。
  - 后续节点数：该能力点对应的知识节点在 `node_path` 里作为 `from_id` 的不同 `to_id` 个数。没有 `node_path`，或对应表里查不到该能力点，记 0。
  - 优先级原始值 = 薄弱度 × (1 + 0.5 × 后续节点数) × 考查价值（S、M 类 1.2，其他 1.0）× (0.8 + 2.5 × 板块占比) ÷ 成本（M 类 1.3，其他 1.0）。公式照搬 legacy，参数待教研校准（待人确认 #6）。
  - `rationale_code` 取最主要的一个：后续节点数 ≥ 3 → `prerequisite_of_many`；板块占比 ≥ 0.15 → `high_exam_weight`；其余 → `weak_mastery`。
  - 选题：同节点的 D、T、L 题，按题号取前 3 道。
- **unseen 不排任务**：没有证据，应先诊断；诊断入口由 4-H 与点点决定。
- **排序与截断**：复测优先级落在 0.85–1.0，练习折算到 0.1–0.8；有时间预算时按优先级累加 `estimated_minutes`（所选题 `estMinutes` 之和），超出就停；没有预算时最多 8 个任务；`focus_skill_nodes` 非空时只在其中排。
- **前置**：两个任务的能力点在 `node_path` 里有前置边时，填 `prerequisite_task_ids`；没有 `node_path` 时不填。
- **mastery 为空** → 返回空 `tasks`。
- **其他**：确定性算法，不是判分；不消耗；输出只有 ID、编码、数字；`evidence_ids = []`。

### 2. generate_variation：从同族候选题里选
**算法** `aya-variant-select@0.1.0`，改写自 legacy `pickVariant`。
- **目标**：
  - 给了 `source_item_ref`：目标节点是该题的主节点；目标错因是它所有陷阱码对应的阿鸦错因编码；
  - 给了 `skill_node`：只按节点匹配；
  - 给了 `subject_error_category`：必须是映射表里的阿鸦编码，否则 422 `INVALID_REQUEST`。
- **候选池**：
  - 有 `item_family`：只从其中选（知识服务 `item.similar` 的结果）；`license_state = restricted` 的排除；
  - 没有 `item_family`：从内容包按“同主节点或同错因”取。这是**过渡实现**，知识服务上线、编排层开始填 `item_family` 后不再使用；
  - 两种来源都要满足：在内容包里（判分要用答案）、不是原题、不在 `exclude_item_refs` 里、内容包里是 `admitted`、题型符合 `exam_config.question_type_codes`（非空时）。
- **排序**：同节点且同错因的在前，然后同 `usage`，最后按题号；取前 `count` 道。
- **`match`**：同节点按内容包主节点判断；同错因按陷阱码映射判断。
- **`difficulty`**：候选来自 `item_family` 且带 `prior_difficulty` 时原样输出，否则不输出（legacy 难度是 8 维向量，不折算）。
- **空结果的原因**：节点不在内容包 → `skill_node_unknown`；有匹配的已准入题但全被排除 → `all_excluded`；其他 → `no_admitted_candidate`。
- **现在一定返回空**：721 道题都没准入，所有请求都得到 `no_admitted_candidate`。这是契约要求的正确行为，不是缺陷；选题逻辑用合成内容包的单测覆盖。
- 空结果时 `semantics = formal`、`confidence = 1.0`，与契约示例一致。不消耗，不产生证据。
- **准入从哪来**：唯一真相源是 `platform.item.admission`；内容包里是副本，由 4-G 的准入决定文件重建。`ItemTagView` 不含准入状态，所以 `item_family` 里的题也要在内容包里查准入。
- **不做 AI 生成**：生成器另立项，产出只能进平台审核流程，过 4-G 闸门后才能成为候选。

### 3. report：按时间窗提供学科字段
**算法** `aya-report@0.1.0`。
- **只接受阿鸦的数据**：`evidence`、`error_attributions`、`mastery_history` 里出现别的学科 → 400 `SUBJECT_MISMATCH`。
- **时间窗**：`window.to` 不晚于 `window.from` → 422 `INVALID_REQUEST`；证据按 `occurred_at ∈ [from, to)` 的时刻取，窗外的不计入，也不进 `evidence_ids`。
- **`skill_summary`**：
  - `attempts`：窗内 `response_text` / `response_choice` 证据按能力点、按 `attempt_id` 去重计数（同一道作答的多条证据只算一次）；
  - `trend`：按 unseen < understand < execute < stable < transfer < retain，比较窗内最早和最晚的记录，得 up / flat / down；窗前没有任何记录的记为 `new`。这要求平台在 `mastery_history` 里附上窗前最后一条记录（登记给 5-H）。
  - 只按 `skill_node` 一层给（v1.0 没有知识节点层，见待人确认 #2）。
- **`error_distribution`**：按（跨科大类，阿鸦编码）计数；**敏感类“心理与习惯”不计入**，与 grade 一致。
- **`highlight_codes`**：封闭白名单，v0 只有三个（编码待教研与 5-H 确认）：`skills_improved`（至少一个 up）、`skills_declined`（至少一个 down）、`no_activity`（窗内没有作答证据）。
- **没有 `forecast`**：v1.0 已移除，考试预测只由洞察服务 `insight.predict` 提供给编排层。
- **不写结论句**：字段里没有文本。面向学生家长的句子由洞察服务按判断模板生成、引用判断（规则 11，评审 sie-#1）。student、parent、teacher 三种受众输出同样的字段；家长端不出现阿鸦（规格书 §4.11）。
- **不输出**：原文、排名、百分位、同伴比较、XP、掌握度数值。
- **`learner_context`**：v0 不用于计算，只参与指纹。
- **信封**：`evidence_ids` 是计入统计的请求证据 ID；计入的证据有未准入的 → `practice`；`confidence` 取所用错因记录置信度的最小值，没有错因记录时是 1.0。
- 不消耗，不产生新证据；平台报告引擎属 5-H。

### 4. teach：确定性提示阶梯 + 知识页面直出
完整的 teach 依赖下列东西，**目前都没有**：模型路由（ADR 04）、阿鸦人设 Prompt 按 V1.1 重写（必停）、`evals/persona`、teach 计量的 BFF 流程、学习服务 teach 接口、讲解 DSL 与白板（`packages/stage`，ADR 07）。

**v0 方案（默认，待人确认 #1）**：
- **`mode = hint`：提示阶梯** `aya-hint-ladder@0.1.0`
  - 必须带 `item_ref`，题目在内容包里（否则 404 `ITEM_NOT_FOUND`），`skill_node` 是该题主节点之一（否则 422 `INVALID_REQUEST`）。
  - 输出等级 `min(allowed_hint_level, 5)`；第 6 级（变式）交给 generate_variation。
  - **不越级**：只返回这一级的那一条提示，不附带更高等级的内容（规格书 §2.3、§5.2 F 类“越级提示”）。
  - **防止提前露答案**：0–3 级提示含标准答案原文时，逐级降到不含答案的最高一级，`hint_level_used` 如实写降级后的等级；比较在进程内完成；命中的题目清单写进 lessons 交教研改题，适配器不改题。
  - `segments`：一条 `text`，即提示原文；`$...$` 公式原样保留，由前端渲染。
  - `knowledge_page` 在 hint 模式下不使用。
  - `source_version`：`algo:aya-hint-ladder@0.1.0;items:<内容包版本>`，**不写 `prompt:`**。
  - 题目未准入 → `practice`。
- **`mode = lesson`：知识页面直出** `aya-page-lesson@0.1.0`
  - 必须带 `knowledge_page`，否则 422 `MODE_NOT_SUPPORTED`（v0 不自己生成讲解）。
  - `segments`：把 `body_md` 按空行切成若干 `text` 段（每段不超过 4000 字），原文不改、不加阿鸦话术；页面本身人设中立（KGE），阿鸦的口吻由外层人设层加（不在本 spec，接入时需人设回归）。
  - `hint_level_used = 0`；不输出 `whiteboard_dsl`。
  - 页面是已发布的公共层内容（规则 13），适配器不往里混入任何学生数据；`atom_refs` 不输出。
  - 页面版本写不进 `source_version`（契约类别里没有页面），由编排层记录它放进请求的 `page_id`、`page_version`（待人确认 #1 附带项）。
  - `semantics = formal`，`confidence = 1.0`。
- **两种模式共同**：
  - `ai_disclosure_shown = false`、`referral_triggered = false`：v0 不写任何人设话术，身份披露与转介由平台人设层负责；
  - `conversation`、`recent_evidence`、`persona` 只参与指纹，不读、不存、不记；
  - `verified_statements` 不输出（静态内容，没有需要核验的模型断言）；代码里不出现 `verified_claims`；
  - teach 不判对错，不写关于学生的判断（评审 sie-#3）；
  - 不产生证据，`evidence_ids = []`：提示依赖由平台通过 `AttemptInput.hint_level_used` 记录，4-C 的门槛直接用它；
  - 计量：teach 是消耗类，请求必带 `usage_id`；适配器原样使用，不扣费。按 v1.0 描述 hint 计入学时、lesson 按节，是否对确定性输出豁免属计量逻辑，本 spec 不定（必停，见“已决项”第 2 条）。

**teach 通过一致性测试只表示这两小块符合契约，不等于阿鸦教练可以上线。**

### 5. 一致性测试夹具
- 在 `adapters/aya/conformance/fixtures.yaml` 追加用例，**由独立会话编写**，不参考适配器实现；按 `contract_version = 1.0.0`；幂等键是小写 UUIDv4；每条都带 `conflict_body`；时间带时区；全部合成数据。

| 方法 | 用例 |
|---|---|
| plan | 有复测、有练习，带 `node_path` 与 `exam_config`；mastery 为空 |
| teach | hint 2 级；hint 上限给 6；lesson 带 `knowledge_page` |
| generate_variation | 按原题选（空，`no_admitted_candidate`）；带 `item_family`（空）；未知节点（`skill_node_unknown`） |
| report | 家长受众；窗内没有证据 |

- 通用测试对这四个方法只查 schema、坏请求与幂等；内容断言写在适配器单测里。
- 不改 `packages/contracts/conformance/` 与共享核对库（待人确认 #4）。

### 6. 与平台的接口
本 spec **不新增**学习服务接口。平台接入这四个方法时各自新增 `learning-internal` 接口，都属于契约变更（必停）：
- plan：4-H 今日计划；编排层先调 `insight.context`、`node.path`、读已发布考情方案，再调适配器；
- report：5-H 周报；结论句由洞察服务生成；
- teach：teach 接口、BFF 计量流程、人设层，放在阶段 1；lesson 的 `knowledge_page` 由编排层调 `page.get` 取；
- generate_variation：阶段 1 “错题再练闭环”；`item_family` 由编排层调 `item.similar` 取。

## 范围

### 做
1. `adapters/aya/src/`：plan、generate_variation、report、teach（hint 与页面直出）四个处理函数；更新 capabilities；删掉 501 分支。
2. 内容包升级（设计要点第 0 节）：生成脚本、`content.ts` 的加载与引用完整性检查；重新生成 `adapters/aya/content/pack.json`。
3. 单测（`adapters/aya/tests/`），见验收标准。
4. 一致性测试夹具追加用例（独立会话编写）。
5. 更新 `adapters/aya/README.md` 的接口表与边界说明。
6. `specs/adapter-aya-methods-sprint4/lessons.md`：实现记录；提示含答案的题目清单；新增待人确认项。

### 不做
- 插件 API v1.0 第二步本身（前置依赖）；diagnose 读 `learner_context`、`node_path` 的改造（属 SIE-02a 数学自适应诊断，另起）。
- 知行、文砺的任何方法；英语（搁置）。
- 调用任何模型；阿鸦人设 Prompt 与话术；`evals/persona`。
- 白板与讲解 DSL、`packages/stage`、OpenMAIC；AI 生成题目；考试预测。
- 调用知识服务、洞察服务，读考情库或任何平台表。
- `learning-internal` 新接口；BFF 计量流程；迁移、RLS、契约变更；共享核对库与一致性测试框架。
- 修改 legacy 或内容包里的题目。

## 其他学科与 Gate 0（只登记，不实现）
| 学科 | 现状 | v2.0 冲刺 4 目标 | 按现状能达到 |
|---|---|---|---|
| 阿鸦 | 3 个方法已实现 | 6/6 | 默认方案 6/6；teach 推迟则 5/6 |
| 知行 | `adapters/` 下没有；没有 spec | ≥ 5/6 | 0/6 |
| 文砺 | 同上 | ≥ 5/6 | 0/6 |
| 英语（开口成章） | 搁置 | — | 不计（对照文档决定 #1） |

- Gate 0 软条件“契约测试覆盖率”在 W13 对知行、文砺**不会满足**；按开发计划 v2.0，软条件不过可带条件进入阶段 1、首月关闭，由人决定。
- 硬条件“各科写入同一学生模型”同样受影响，超出本 spec，只在这里提醒。
- 建议（由人决定，本 spec 不改 `docs/plan/`）：知行优先于文砺（寒假试点先上数学 + 道法；知行 skill_node 坐标还等道法教研，`specs/inventory-zhixing/decisions.md` #7）；Gate 0 证据包如实写“阿鸦 6/6；知行、文砺未开始；英语搁置不计”。

## 验收标准（可测试）
- [ ] 前置依赖第 1 项已合并：阿鸦 ajv 能解析 v1.0 的跨文件引用，有测试覆盖。
- [ ] `capabilities`：`contract_version = 1.0.0`；六个方法都是 `implemented: true`；teach 带 `notes_code: deterministic_no_model`；report 不带 `forecast_omitted`；`adapter_version = 0.2.0`。
- [ ] 按 `STARBUDDY_CONTRACT_VERSION=1.0.0` 运行 `make conformance`，`conformance-aya.json` 为 `"implemented": 6, "passed": 6`，`global_failures` 为空。
- [ ] 四个方法的每个响应都通过 v1.0 契约自检（fail closed）。
- [ ] 夹具的 `idempotency_key` 都匹配小写 UUIDv4；时间都带时区；有测试断言。
- [ ] **plan**（单测）：
  - 同一请求两次调用，响应逐字节相同；
  - 能力点都来自请求的 `mastery`，unseen 不出现；
  - 复测任务按 `next_retest_at` 时刻升序（用例混用 `+08:00` 与 `Z`，字符串顺序与时刻顺序相反），优先级都高于练习；
  - 给了预算时 `estimated_minutes` 之和不超过预算；
  - 所有 `item_ref` 都在内容包里；`question_type_codes` 非空时题型都在列表内；
  - 去掉 `node_path` 后没有 `prerequisite_task_ids`，也没有 `prerequisite_of_many`；带 `node_path` 时前置边与 `prerequisite_task_ids` 一致；
  - `learner_context` 变化不改变 `result`（只改变指纹）；
  - 引用了未准入的题时 `semantics = practice`。
- [ ] **generate_variation**（单测，合成内容包）：
  - 候选全部 `admitted`；不含原题与 `exclude_item_refs`；每个候选至少同节点或同错因；
  - 带 `item_family` 时候选都在 `item_family` 里，`restricted` 被排除，`difficulty` 等于 `prior_difficulty`；
  - 三种 `empty_reason` 各有一条用例；
  - 用真实内容包时一律返回 `no_admitted_candidate`。
- [ ] **report**（单测）：
  - 响应里没有请求证据的 `payload` 原文，没有 `forecast`，`highlight_codes` 只含白名单三项；
  - 同一 `attempt_id` 的多条证据只计一次 `attempts`；窗外证据不计入、不进 `evidence_ids`；
  - 敏感类错因不计入 `error_distribution`；
  - 出现别的学科的数据返回 400 `SUBJECT_MISMATCH`；`to ≤ from` 返回 422；
  - 七种 `mastery_history` 组合（up、flat、down、new 及边界）都有断言。
- [ ] **teach**（单测）：
  - hint：对每个 `allowed_hint_level`（0–6），`hint_level_used ≤ min(allowed, 5)`；输出等于该级提示原文，不含更高等级内容；
  - **对内容包里每一道填空题**逐题断言：0–3 级输出里没有标准答案原文；
  - lesson：带 `knowledge_page` 时各段拼接等于 `body_md`（去掉段间空行），不含页面以外的文字；不带时返回 422 `MODE_NOT_SUPPORTED`；
  - hint 缺 `item_ref`、`skill_node` 与题目不符返回 422；未知题目返回 404；
  - 响应里没有 `verified_claims` 字段；
  - `conversation` 与 `learner_context` 里的内容不出现在响应和日志里。
- [ ] 四个方法的日志只含白名单字段（沿用 `adapters/aya/tests/leak.test.ts` 的做法）。
- [ ] 四个方法的代码不调用 `Date.now` / `new Date()`；有测试断言。
- [ ] 适配器代码里没有指向知识服务、洞察服务、考情库或数据库的客户端与连接串；有测试断言（静态扫描 `adapters/aya/src/`）。
- [ ] 升级后的内容包没有题干、解析与 `prerequisites` 字段；有测试断言。
- [ ] 测试数据全部是合成数据（规则 8）。
- [ ] `make check` 通过。不新增依赖。

## 涉及的必停项（见 CLAUDE.md）
| 必停项 | 默认方案是否涉及 | 说明 |
|---|---|---|
| `db/migrations`、RLS | 否 | 不写迁移 |
| 计量与权益逻辑 | 否 | 适配器只原样使用 `usage_id`；接 BFF 的 teach 计量另立任务，届时必停 |
| `packages/contracts` | 否 | 按 v1.0 候选版实现，不改契约；第二步、新增学习服务接口、补平台侧核对都另立任务 |
| 判分与掌握计算 | 否 | 四个方法不判分、不算掌握；plan 的优先级是排序启发式，不是证据权重或门槛 |
| 判断模板、禁用推断词表、画像可见范围 | 否 | 只输出编码；结论句与模板归洞察服务 |
| `evals/` 金标准 | 否 | 不存在，也不创建 |
| 人设 Prompt、禁语表、转介话术 | **默认否，待人确认 #1** | teach 只返回内容包提示原文与已发布页面原文，不写话术。若人认定提示原文属于话术，teach 变成必停，**需人设回归**（`evals/persona` 建好后才能跑） |
| 新增第三方服务、模型供应商、数据出境 | 否 | 不调模型，不新增依赖 |

**结论**：默认方案下 PR 不需要 `[NEEDS-HUMAN]`。以下情况会变成必停：待人确认 #1 认定提示原文属于话术；实现中发现契约需要改动（例如给 `node_path` 加根与方向、给 report 加知识节点层、给 `SourceVersion` 加页面类别）。

## 依赖与输入
- **已有**：v1.0 候选版契约与两份服务契约 v0.9；错因映射表 v0.9（待教研签字）；阿鸦内容包与生成脚本；legacy 只读副本；一致性测试框架。
- **需要先完成**：插件 API v1.0 第二步（前置依赖）。
- **需要另一个会话**：编写四个方法的一致性测试夹具。
- **需要教研**：抽查 plan 的参数与 `rationale_code`；确认 `highlight_codes`；修改提示里过早出现答案的题；审核阿鸦节点与 KGE 知识节点的挂接（ADR 11 待人确认 #3）。

## 与同期 spec 的协调（冲刺 4 四份 spec 按 v2.0 改写时统一）
1. **迁移编号**：0010–0015 已被 insight、kge、课程地图版本、policy、学生届别、计量 v2 占用；冲刺 4 各 spec 的新迁移从 0016 起按合并顺序编号。本 spec 不写迁移。
2. **plan 内部接口**：4-H 负责在 `learning-internal` 新增 plan 接口（契约变更，必停）；编排层负责填 `learner_context`、`node_path`、`exam_config`。4-D 只实现适配器侧。
3. **复测**：真相源是 4-C 的 `next_retest_at` 与复测任务表；适配器只排序，不排期；legacy 间隔不再使用。
4. **掌握快照的粒度**：4-C 按“knowledge_node × 认知层次”计算，插件 `MasterySnapshot` 按 `skill_node` 给；由编排层按 `kge.skill_link` 投影到阿鸦能力点，还是契约升级，见待人确认 #2。
5. **阿鸦内容包格式**：4-G 先升格式（逐题来源、准入决定文件），4-D 在其上加字段；4-G 的 C9 词表检查覆盖 4-D 加的提示原文。
6. **作答带提示等级**：4-H 提交作答时带 `hint_level_used`，4-C 的“低提示依赖”用它。
7. **两个没有 spec 负责的缺口**（交人决定）：阿鸦题干（平台与内容包都没有题干，`ItemTagView` 也不带，plan 与变式题只返回题目 ID，统一壳显示不了题目）；pilot 家庭开通订阅的入口。

## 待人确认
1. **teach v0 做到哪一步**：
   - 默认：hint 做确定性提示阶梯（最高 5 级、不调模型、不写话术），lesson 只做知识页面直出；阿鸦 6/6。提示原文与页面原文视为学科内容，不视为人设话术。
   - 另一个选择：teach 推迟到模型路由、人设 Prompt 重写与 `evals/persona` 就绪之后；阿鸦 5/6。
   - 附带：
     - 使用边界：默认 v0 只作“提示按钮”和“看页面”用，平台不把学生自由输入送进 teach（v0 不读对话、不做转介、不做身份披露）；若要在对话里调用，转介与披露须由平台人设层先做（依赖 3-F）；
     - 规格书 §4.9“未经验证的题阿鸦不讲”是否适用于 Q1 题的提示：默认不适用（§4.9 说的是生成题与超纲内容）；
     - 这些题来源待核（decisions #3），展示提示原文的版权风险与展示题干相同；
     - 页面版本写不进 `source_version`：默认由编排层记录；另一个选择是契约 v1.1 给 `SourceVersion` 加页面类别（必停）。
2. **阿鸦能力点与 KGE 知识节点的对应**（新）：`node_path`、`item_family` 用知识节点 ID，`mastery[]`、report 用 `aya:` 能力点。
   - 默认：内容包带一份由平台导出的 `kge.skill_link` 对应表（与准入决定文件同一机制，带地图版本，写进 `source_version` 的 `graph:` 段）；没有对应表时不用 `node_path`。report 按能力点一层给，知识节点层由平台按对应表聚合。
   - 另一个选择：契约 v1.1 让编排层在请求里带对应关系，或让 `MasterySnapshot`、`ReportFields` 按知识节点给（必停）。
3. **`node_path` 缺根节点与方向**：默认按 `from_id` 是 `to_id` 的前置理解（服务契约示例），并请契约评审在签字时写进描述。
4. **一致性测试对新方法的深度**：默认只靠通用测试加适配器单测；另一个选择是另立任务在共享核对库补平台侧核对与泄露检查（`packages/contracts` 变更，必停）。
5. **新方法的置信度口径**：默认 plan、teach(hint) 引用了未准入的题时 `confidence = 0.5`，否则 1.0；generate_variation 与 teach(lesson) 为 1.0；report 取错因记录置信度最小值；都不置 `human_review_required`。另一个选择：统一 1.0，只用 `semantics` 表示效力。
6. **plan 的考查价值参数**：默认沿用 legacy 公式与 `BOARD_EXAM_SHARE`（Q1 推导值，标“待教研校准”）作过渡；v2.0 的目标来源是 KGE 节点的 `region_weights`，要进请求需契约变更（必停）。另一个选择：等教研给出权重再实现 plan（阿鸦 5/6）。
7. **题型编码**（新）：`exam_config.question_type_codes` 在考情库里还没有词表。默认阿鸦题型写作 `single_choice`、`fill_blank`、`multi_choice`，与考情库对齐前请求里不带这个字段时不过滤。
8. **内容包格式与 4-G 的合并顺序**：默认 4-G 先合并；本 spec 在其上加字段，格式号顺延。另一个选择：两边合成一次格式变更。
9. **4-G 放行的题怎样进入内容包**：默认沿用 4-G 的准入决定文件、重建内容包、重新部署；另一个选择是适配器启动时从平台读准入（新只读接口，契约变更，必停）。

### 已决项（改为引用，不再列为待确认）
1. **PlanRequest 不加 `as_of` 与 `exclude_item_refs`**（原 #2 的另一个选择）：v1.0 候选版没有加（`specs/plugin-api-v1.0/spec.md`）。plan 不判断到期、不排除做过的题；到期由平台筛，排除做过的题由 4-C 所说的 generate_variation 负责。
2. **teach 的计量资源**（原 #4）：v1.0 评审 v09-#2 默认结论（待签字）：`hint` 计入学时 `ai_active_time`，`lesson` 按节 `lesson_whiteboard`。是否对确定性输出豁免，随 teach 接入平台时在计量 spec 里定（必停）。
3. **Gate 0 口径**（原 #9）：英语搁置、不计（对照文档决定 #1）；知行、文砺是否带条件进入阶段 1 由 Gate 0 评审决定，本 spec 只登记。
4. **每个方法可以收哪些画像字段**：随 SIE-01 定，见 `specs/plugin-api-v1.0/lessons.md` 待人确认 #5；本 spec v0 不使用 `learner_context`。
5. **v1.0 评审签字**：见 `specs/plugin-api-v1.0/spec.md` 待人确认 #1。
