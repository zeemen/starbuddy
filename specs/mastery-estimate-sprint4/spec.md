# KGE-04 掌握 v0（冲刺 4-C）：节点 × 认知层次的掌握估计、五道门槛、复测调度与学情档案页

任务包：**KGE-04 掌握 v0**（开发计划 v2.0 冲刺 4，W9–W10，C 流）。目录名沿用 `mastery-estimate-sprint4`。

状态：**草稿，等人确认后才开始实现**。确认人：＿＿＿；日期：＿＿＿。门槛、证据权重、BKT 参数、衰减与复测间隔的数值另需各科教研签字（待人确认 #1）；签字前实现的代码最多写到“稳定”。

> 2026-09-28：设计正文按开发计划 v2.0 改写（方案见 `specs/sprint4-v2-rewrite/spec.md`）。v1.0 版正文可从 git 历史查看；与它的差别见“相对 v1.0 版本的变化”。

> 本 spec 涉及掌握计算、迁移与 RLS、画像可见范围，全部属于 CLAUDE.md 必停项。实现 PR 首行必须写 `[NEEDS-HUMAN]`。

## 关联
- **依据**（合并规则：知识结构、掌握计算以 KGE 为准；画像、判断、呈现以 SIE 为准；其余以总纲为准）：
  - 完整方案 v1.1（`docs/plan/完整方案-v1.1.html`）：4.2“星图的统一定义”、4.3 三圈学习者模型、4.4“证据与判断规则”、5.5 映射（五道门槛 → `mastery_estimate` 状态机 + 复测调度）。
  - 开发计划 v2.0（`docs/plan/开发计划-v2.0.html`）：冲刺 4 C 流 KGE-04，验收是“‘刷同题’无法点亮；一致性检查拦截程度升级”；冲刺 1 C 流（证据写入含 evidence_kind、提示等级、地图版本）；Gate 0 硬条件“画像底线”。
  - KGE V1.1（`docs/plan/KGE-知识与成长引擎-V1.1.html`）：第三章“三根轴”（认知层次、掌握状态、能力视图）；第五章地图迭代（按旧→新 ID 迁移证据，无法迁移的标“待复测”）；第六章“学生层：证据、掌握与学情档案”；第八章数据模型与知识服务接口、质量门槛总表（档案页一致性检查通过率）；附录 V1.1 修订 1、2。ADR 11 把证据与掌握写作“第五章”，按 V1.1 目录应为第六章。
  - SIE V1.1（`docs/plan/SIE-学生洞察引擎-V1.1.html`）：第五章“推断模型 · 证据权重”表与时效；第六章更新节奏（每次作答后、每天）；第九章档案页编译与接口。
- **ADR**（都是草稿，待签字）：
  - ADR 11 星图统一定义（`docs/adr/11-star-map-unified-definition.md`）：星 = 知识节点；星亮 = 进入“迁移”门槛；只有 A 级证据点亮；待复核不推动星亮门槛；3-H 过渡条款。
  - ADR 13 判断账本唯一写入方（`docs/adr/13-claim-ledger-single-writer.md`）：只有 `starbuddy_insight` 写 `insight.claim`；节点类推断判断经 `basis_kind = mastery_estimate` 引用本模块的行。
  - ADR 15 输入通道与作答证据分级（`docs/adr/15-input-channels-and-answer-evidence.md`）：A / B / C 三级；证据等级、`evidence_kind`、准入三者并存。
  - 另引用：ADR 01（学习服务是个人层唯一写入方）、ADR 02（共库分 schema）、ADR 03（复测调度是状态机定时事件）、ADR 04（模型路由按数据级别）、ADR 10（境内托管，定时任务托管）。
- **迁移**：`db/migrations/0001`（`mastery_state`、`admission`）、`0004`（`evidence_event`、`attempt`）、`0005`（`mastery_estimate`、`grade_result`、`skill_observation`）、`0007`（幂等）、`0009`（`starbuddy_scheduler` 先例）；v2.0 草稿 `0010`（`insight.claim`）、`0011`（`kge.knowledge_node`、`skill_link`、`item_family`、`item_tag`、`id_migration`）、`0012`（`platform.curriculum_version.map_version_id`）、`0013`–`0015`（与本 spec 无直接关系：政策库、届别、计量 v2）。
- **契约**：
  - 知识服务 `packages/contracts/openapi/knowledge-service.v0.9.yaml`：`evidence.write`（只收 A、B 级）、`mastery.get`、`mastery.explain`（个人层工具，过渡期由学习服务实现）。
  - 洞察服务 `packages/contracts/openapi/insight-service.v0.9.yaml`：`insight.context`、`insight.claims`、`RenderedStatement`。
  - 学科插件 API v1.0 候选版 `packages/contracts/openapi/plugin-api.v1.0.yaml` 与 `specs/plugin-api-v1.0/spec.md`：`EvidenceDraft` 不带 `evidence_kind`、`map_version`、`cognitive_level`、证据等级与通道，由平台补齐（svc-#2 / kge-#5，已按默认确认）；`human_review_required = true` 的结果不推动掌握门槛与星亮；`MasterySnapshot.next_retest_at`。
- **对照文档** `specs/plan-v2-alignment/spec.md`：第三节 #3（个人层表留在 `platform.*`）、#5（evidence_event 缺列）、#8（本次改写）；第七节 7.1 #1（A / B / C 与掌握计算）。
- **相关 spec**：`specs/kge-schema-v0.9/spec.md`（个人层三列在冲刺 1-C 加；学情档案页随 KGE-04 建）、`specs/service-openapi-v0.9/spec.md`、`specs/sie-00-field-dictionary/spec.md`（禁用推断词表与匹配函数 `docs/design/sie/sie_lexicon.py`）、`specs/contract-conformance-sprint1/v1.0-review.md`、`specs/star-map-sprint3/spec.md` 与 `lessons.md`、`specs/learning-ingest-sprint1/spec.md`、`specs/learner-schema-v0.9/spec.md`。
- **同期 spec**：`specs/unified-shell-sprint4/`（4-H）、`specs/release-gate-sprint4/`（4-G）、`specs/adapter-aya-methods-sprint4/`（4-D）。
- **人设规格**（只作下游消费方，本任务不改）：`docs/personas/阿鸦-规格书-V1.1.md`（尾巴发光与星亮同一时刻，只由引擎触发）、`docs/personas/点点-规格书-V1.2.md`（“接近掌握”= 过了执行、没过稳定或迁移）。
- **追溯引用**（已被取代，只供对照）：`docs/archive/完整方案-v1.0（已被v1.1取代）.html`、`docs/archive/开发计划-阶段0-1-v1.0（已被v2.0取代）.html`。
- **CLAUDE.md**：规则 1、2、3、5、6、8、9、10、11、12、13。

## 相对 v1.0 版本的变化
| # | 改写点 | 依据 |
|---|---|---|
| 1 | 计算单元从“学生 × skill_node”改为“学生 × knowledge_node × 认知层次” | KGE 第三章“三根轴”、第六章掌握估计表；完整方案 v1.1 4.2；ADR 11 |
| 2 | skill_node 降为能力视图：由节点结果按 `kge.skill_link.weight` 汇总，读时计算、不单独点亮、不另存 | KGE 第六章；ADR 11 §3；迁移 0011 `skill_link` 注释；规则 2 |
| 3 | “哪些作答算数”改为按 `evidence_kind` 四类与提示等级给出影响方向；证据等级 A / B / C 决定能推到哪一层 | SIE 第五章证据权重表；完整方案 v1.1 4.4；ADR 15；知识服务 `EvidenceLevel` |
| 4 | 删去 `aya-t1` 草案的全部数值（题数、提示上限、间隔、BKT 参数、`p` 下限、置信度公式），只列配置位 | CLAUDE.md 必停项“判分与掌握计算逻辑”；`specs/sprint4-v2-rewrite/spec.md`“不做” |
| 5 | 五道门槛与星亮口径改为引用 ADR 11；“迁移”门槛要求迁移类证据（配置位） | ADR 11 §3；完整方案 v1.1 4.2 |
| 6 | 新增时间衰减：只降置信度，不降掌握度与门槛，由复习调度排复测 | KGE 第六章；SIE 第五章“时效” |
| 7 | 待复核结果一律不推动门槛（原 v1.0 默认允许“只因未准入而复核”的结果推到稳定，改为待人确认的另一选择） | ADR 11 §3；插件 API v1.0 `semantics` 描述；`v1.0-review.md` 第三节 |
| 8 | 新增：学生自述“我会了”不改掌握，只排验证题 | SIE 第五章证据权重表、第八章“我其实会” |
| 9 | “不同的题”改按题目族计数，v1.0 的“近似题识别不了”由 `kge.item_family` 解决 | 迁移 0011 `item_family`、`item_tag.family_id`；KGE 第五章 |
| 10 | 题型来源从 `platform.item.item_type` 改为默认放在 `kge.item_tag` | 迁移 0011（题目打标与元数据只在 `kge.item_tag`） |
| 11 | 新增学情档案页：模板渲染为主、每条陈述引用判断 ID、惰性编译、陈述—判断一致性检查 | 开发计划 v2.0 KGE-04；KGE 第六章与附录修订 1、2；SIE 第九章；`specs/sprint4-v2-rewrite/spec.md` 待人确认 #3（默认放进本 spec） |
| 12 | 写入分工：本模块只写掌握与复测；判断只由洞察服务写，本模块只读 | CLAUDE.md 规则 11；ADR 13 |
| 13 | 新增：个人层新表全部启用 RLS，学生档案删除时一并删除 | 开发计划 v2.0 KGE-04 条目（KGE 第九章 W9–10）、Gate 0“画像底线” |
| 14 | 表留在 `platform.*`（已决定）；v1.0 预订的迁移编号 `0012` 已被 `curriculum_map_version` 占用，本 spec 的迁移编号按合并顺序另排 | 对照文档第三节 #3（2026-09-27 按默认确认）；迁移 0012 |
| 15 | 解释接口从“只有服务内函数”改为实现知识服务契约的 `mastery.get` / `mastery.explain` | `knowledge-service.v0.9.yaml` |
| 16 | 证据缺列（`evidence_kind`、`map_version`、`cognitive_level`、`node_id`、`evidence_level`、`channel`）写成前置依赖，不在本任务加 | 对照文档 #5；`specs/kge-schema-v0.9/spec.md` 第 42 行；ADR 15；插件 API v1.0 svc-#2 |
| 17 | 依据换成完整方案 v1.1、开发计划 v2.0、KGE / SIE V1.1、ADR 11 / 13 / 15；v1.0 文档只作追溯引用 | `specs/sprint4-v2-rewrite/spec.md` 验收第 1 条 |

## 目标
学习服务根据平台上的作答证据，为每个学生在每个“知识节点 × 认知层次”上算出一条当前掌握记录（门槛状态、掌握概率、置信度、下次复测时间），并排好复测；再把判断账本里已有的判断编成每生每科一页学情档案。

完成后：
- 星的真实来源就位：`mastery.get` 能对任一知识节点回答“亮没亮、到哪道门槛”，3-H 星图切换数据源（ADR 11 过渡条款，另开 H 流任务）时直接读它。
- 同一道题、同一族题反复答对，星不会亮；拍照（B 级）、未准入内容、待复核结果都不会让星亮。
- 到了复测时间，平台上有一条可查询的复测任务。
- 对任何一颗星都能回答“为什么亮了 / 为什么没亮”，答案落到条件编码和证据 ID。
- 学情档案页每句话都能追到一条判断；程度被写重、凭空多出结论的版本发不出去。

v0 实际能跑的只有数学（阿鸦），而且要等 KGE-01 骨架导入、数学地图首个版本发布、冲刺 1-C 证据加列之后（见“前置依赖”）。英语搁置（对照文档 #1，已决定）；知行、文砺没有适配器与真实证据，只配置空位。

## 现状（改写时核实）

### `platform.mastery_estimate`（`db/migrations/0005_platform__inference.up.sql`）
- 键是 `(student_id, subject, skill_local_id)`，部分唯一索引 `mastery_estimate_current_uq` 保证每键只有一条 `is_current`。**没有** `node_id`、`cognitive_level`、`map_version`。
- 其余字段可直接沿用：`state`（`platform.mastery_state`：`unseen / understand / execute / stable / transfer / retain`，`0001` 注释 `transfer` = 星亮、`retain` = 外圈）、`p_mastery`、`confidence`、`algorithm_version`（`versioned_name`）、`evidence_ids`（至少一个）、`computed_at`、`next_retest_at`、更正链 `is_current / superseded_by / superseded_at`。
- 保护触发器：`computed_at` 由库写 `now()`，调用方不能指定；`superseded_by` 只能从空改一次；提交时检查新旧记录同学生、同学科、同键。推论：更正顺序只能是“生成新 ID → 旧记录指向新 ID → 插入新记录”；测试不能回拨 `computed_at`，间隔只能按证据时间算。
- 权限：`starbuddy_learning` 可 SELECT、INSERT，只能 UPDATE 更正三列；`starbuddy_bff` 按 `platform.can_read_student` 只读，RLS 不按 `is_current` 过滤。`student_id` 外键没有 `ON DELETE CASCADE`。
- 表注释：“只能由已准入内容的证据推动星亮门槛，由学习服务保证”，数据库本身不检查。
- 没有任何生产代码写这张表；3-H 的合成种子以 `skill_local_id` 写过 `transfer` / `retain`（`specs/star-map-sprint3/lessons.md` 注明 4-C 必须拒绝这种写法）。

### 证据（`db/migrations/0004_platform__evidence.up.sql`）
- `evidence_event` 有 `admission`、`skill_local_id`、`source_method`、`recorded_at`；它的 `kind` 列是 8 种**载荷类型**，不是 KGE 的 `evidence_kind`。没有 `evidence_kind`、`map_version`、`node_id`、`cognitive_level`、`evidence_level`、`channel`（对照文档 #5）。
- `attempt` 有 `hint_level_used`（0–6，可空）、`self_report`（`confident / unsure / guessed`，只是作答把握度，不是 SIE 的自述表）、`occurred_at`（客户端时间）、`recorded_at`（服务器时间）。
- `grade_result`（0005）有 `verdict`、`confidence`、`human_review_required`、`review_reasons`、`semantics`。

### 知识地图（草稿迁移 0011，待人执行）
- `kge.knowledge_node`：`cognitive_levels` ⊆ `know / understand / apply / synthesize`，只有 `kp / ability_kp / viewpoint` 能带认知层次（即参与掌握计算）。
- `kge.item_tag`：每题按地图版本一行，`main_node_id`、`node_ids`（次要节点）、`cognitive_level`、`family_id`、`quality_grade`；**没有题型列**。题目身份与准入仍只在 `platform.item`。
- `kge.skill_link(node_id, local_id, weight)`：能力视图的挂接与汇总权重。
- `kge.id_migration`：`rename / split / merge / retire / manual`；`retire` 的旧编码对应掌握度标“待复测”。

### 判断账本（草稿迁移 0010，待人执行）
- `insight.claim` 只有 `starbuddy_insight` 能写；`starbuddy_learning` 只能读可见范围含 `agent`、未过期、模板有效的非终态判断；`starbuddy_bff` 按学生本人 / 有效绑定家长与可见范围读。
- 节点类推断判断必须 `basis_kind = mastery_estimate`、`basis_id` 指向本模块写的行；判断 `value` 只放模板变量，不作为计算输入。
- `target_id` 注释：KGE-01 之前节点类判断指向 `skill_node`，之后改指 `knowledge_node`。

### 学习服务（`services/learning/learning/`）
- 只有 `POST /internal/v0/grade`、`/diagnose`；流程是校验 → 题目已登记 → 幂等占位 → 调适配器 → 平台核对 → 一个事务落库（`service.py`、`store.py`）。准入取“证据自身”与“题目当前登记”里更严的那个。
- 没有定时任务入口、后台 Worker、`mastery.*` 实现、档案页。

### 内容
- 阿鸦内容包 721 道题全部是 `source_unverified`；登记脚本最高登记到 `pending_review`（`adapters/aya/scripts/register-content.ts`）。按知识服务契约，未准入内容上的证据只能是 B 级，所以 **4-G 把题升为 `admitted` 之前，数学一颗星也亮不了**，最多到“稳定”。

### 定时任务与配置
- `starbuddy_scheduler`（`db/bootstrap/roles.sql`）没有表权限，只能调计量的 `SECURITY DEFINER` 函数；ADR 03、ADR 10 都没签字。
- 平台没有门槛配置表或配置文件；BKT 参数没有校准数据，`evals/` 下没有掌握相关的评测。

## 设计要点

### 1. 计算单元与三根轴
- **单元**：`(student_id, subject, node_id, cognitive_level)`。`node_id` 是当前已发布地图版本里 `status = active`、`kind ∈ {kp, ability_kp, viewpoint}` 的 `kge.knowledge_node`；`cognitive_level` 必须在该节点的 `cognitive_levels` 里。
- **三根轴不混用**（KGE 第三章）：认知层次描述题目与节点要求；掌握状态（五道门槛）描述某学生在某单元上的进度；能力视图是呈现层。
- **星与单元的关系**：星 = 知识节点（ADR 11），而门槛按“节点 × 层次”算。一颗星亮不亮，由该节点“星亮层次”上的门槛决定，星亮层次由教研按节点配置（配置位 `star_level_rule`，待人确认 #3）。`mastery.get` 返回每个层次的 `gate`，`star_lit` 按这条规则算。
- **能力视图**：`skill_node` 的掌握分布由它挂接的节点结果按 `kge.skill_link.weight` 汇总，**读时计算，不写入 `mastery_estimate`**（规则 2，不存第二份），也没有“亮”这个状态。汇总公式的配置位是 `skill_view_aggregation`，数值由各科教研配置。
- **证据归属**：一次作答按 `kge.item_tag`（作答时的地图版本）归到 `main_node_id × item_tag.cognitive_level`。次要节点 `node_ids` 在 v0 不计入（待人确认 #16）。没有打标或打标被驳回的题不计入，解释里编码 `untagged`。

### 2. 谁算、什么时候算
- **只有学习服务算**，以 `starbuddy_learning` 身份写（ADR 01；CLAUDE.md“学习服务是过渡期唯一写 platform.* 个人层的服务”）。代码放新目录 `services/learning/learning/mastery/`（待建）；KGE 服务的最终代码位置等 ADR（CLAUDE.md 仓库结构），决定后整体搬迁。BFF、前端、适配器、学科 Agent、点点都不算，也不写（规则 2、11）。
- **触发点**，都走同一个函数 `recompute(student, subject, node_id, cognitive_level)`：
  1. 落库之后：`grade` / `diagnose` 的落库事务提交后另起事务，重算这次涉及的单元。失败只记日志与指标，不影响已落库的证据和接口返回（待人确认 #6）。
  2. 定时对账（第 7 节）：补漏算、维护复测任务、按时间衰减更新置信度。
  3. 按题重算 `recompute_item(subject, item_id)`：4-G 在题目准入变化时调用。
  4. 地图版本切换：按 `kge.id_migration` 迁移后重算（第 11 节）。
- 幂等重放（`ingest_request` 已成功）不触发重算，漏掉的由对账补。
- 学科没有已签字或草案配置的（知行、文砺），一律跳过，不写记录。

### 3. 证据的分类与方向
每条作答证据在重算时带三个互相独立的属性（ADR 15：三者并存，互不替代）：

| 属性 | 取值 | 谁给 | 管什么 |
|---|---|---|---|
| `evidence_kind` | `independent` / `hinted` / `transfer` / `delayed_retest` | 平台补齐（插件 API v1.0 svc-#2），冲刺 1-C 落列 | 作答情境 → 对掌握与置信度的方向 |
| `evidence_level` | A / B（C 不写入） | 平台按输入通道补齐（ADR 15） | 能推到哪一层：只有 A 级能推动“迁移”及以上 |
| 准入 `admission` | `admitted` / 其他 | 平台登记，取更严者 | 未准入内容上的证据只能是 B 级（知识服务契约） |

**影响方向**（SIE 第五章证据权重表；“上调 / 不变 / 下调”是方向，具体幅度是配置位 `evidence_weights`，待教研签字）：

| 证据 | 掌握 | 置信度 | 补充动作 |
|---|---|---|---|
| 独立做对（`independent`，无提示） | 明显上调 | 上调 | — |
| 低级提示后做对（`hinted`，提示等级 ≤ `low_hint_max_level`，即各科阶梯前段） | 小幅上调 | 小幅上调 | — |
| 高级提示或看过讲解后做对（`hinted`，提示等级 > `low_hint_max_level`） | 不变 | 不变 | 记“辅导有进展”编码；自动排一条无提示变式任务（第 7 节，`reason = hint_followup`） |
| 变式或跨章独立做对（`transfer`） | 明显上调，计入迁移 | 明显上调 | — |
| 延迟复测做对（`delayed_retest`，距该单元上一条计入证据 ≥ `delayed_retest_min_interval`） | 明显上调 | 明显上调 | 个体遗忘参数 v0 不做（第 5 节） |
| 独立做错 | 按错因类型下调（`error_attribution` 的跨科大类 → 配置位 `error_penalty`） | 上调（错误也是确定证据） | — |
| 提示后做错（SIE 表未列） | 默认不上调，按错因小幅下调 | 不变 | 待人确认 #2 |
| 自评“猜的”做对（`attempt.self_report = guessed`，SIE 表未列） | 默认不变 | 不变 | 待人确认 #2 |
| C 级（低置信未确认、外部题、申诉中） | 不参与 | 不参与 | `evidence.write` 直接拒收，不进库 |
| B 级（拍照题库题，高置信或已确认） | 与同类 A 级同向、降权（`level_weight_B`） | 同向、降权 | 可推到“稳定”为止；满足星亮其他条件时排一条平台内验证任务（`reason = verify_in_platform`，ADR 11） |
| 学生自述“我会了” | 不变 | 不变 | 只排验证题（`reason = verify_self_report`，第 7 节） |

- **配置校验固定方向，不固定幅度**：启动时校验 `evidence_weights` 满足上表的方向与大小顺序（独立 > 低级提示 > 高级提示 = 0；迁移、延迟复测 ≥ 独立；独立做错 < 0；B 级权重 < 对应 A 级），违反就拒绝启动。这样教研改数值不会改掉 SIE 规定的方向。
- **来源权重**：正式考试 > 平台诊断 > 日常练习 > 对话表现（KGE 第六章），配置位 `source_weights`；v0 平台内只有“平台诊断”（`diagnose`）与“日常练习”（`grade`）两种来源，结构化自报与成绩只作先验，不直接生成掌握结论（完整方案 v1.1 4.4）。
- **判分门槛**：判分只来自验证器或 Rubric（规则 3）。`verdict` 为 `partial / inconclusive / needs_review / not_applicable` 的不计入；判分置信度低于 `grade_confidence_min` 的不计入（编码 `low_confidence`）。
- **待复核**：`human_review_required = true` 的判分在结案前不推动任何门槛，也不进 BKT 序列（编码 `pending_review`；ADR 11、插件 API v1.0）。结案后判分被更正或确认，由对账重算。只因 `content_not_admitted` 而复核的结果是否按 B 级计入，见待人确认 #11。
- **缺属性的旧证据**：冲刺 1-C 加列之前落库的证据没有 `evidence_kind` / `evidence_level`，一律按 B 级、按 `hinted` 与 `independent` 中更保守的方向处理（不能推动“迁移”及以上）；缺 `hint_level_used` 的按“非低级提示”处理（待人确认 #10）。
- **一条作答同时符合多类时**（例如跨章又隔了很久）：`evidence_kind` 是单值，取值优先级由冲刺 1-C 写入时决定，默认 `hinted` > `delayed_retest` > `transfer` > `independent`（有提示就不算强证据）。本模块只读，不改写（待人确认 #2 一并签）。

### 4. 五道门槛与星亮
门槛名称与顺序：理解 → 执行 → 稳定 → 迁移（星亮）→ 保持（KGE 第三章；完整方案 v1.1 5.5）。对应 `platform.mastery_state` 的 `understand / execute / stable / transfer / retain`，另有 `unseen`（有计入证据但尚未过“理解”）。星亮 = 进入“迁移”（ADR 11 §3）。

重算是**按时间重放**：把计入的证据按 `recorded_at` 排序，从 `unseen` 起逐条推进。同样的证据、同样的配置版本，永远得到同样的结果。每道门槛的条件都由配置给出，下表只写条件的**种类**与配置位，不写数值：

| 门槛 | 条件（全部满足；数值见配置位） | 能推动它的证据 | 星图 |
|---|---|---|---|
| 理解 `understand` | 至少 `understand.correct_min` 次做对（可含低级提示后做对） | A、B | 未亮 |
| 执行 `execute` | 至少 `execute.families_min` 个**不同题目族**独立做对；`p_mastery ≥ execute.p_floor` | A、B | 半亮 |
| 稳定 `stable` | 已是执行；**当日复测轮**通过（到达执行后间隔 ≥ `rung0.min_gap` 的第一道新族题独立做对）；累计覆盖 `stable.item_type_groups_min` 个题型组、`stable.families_min` 个题目族；`p_mastery ≥ stable.p_floor` | A、B | 半亮 |
| 迁移 `transfer`（星亮） | 已是稳定；**第二轮复测**通过（到达稳定后满 `rung1.interval` 的第一道已准入新族题独立做对）；至少 `transfer.transfer_evidence_min` 条 `transfer` 类证据；**只用 A 级、已准入、`formal`、不待复核的证据重新检查稳定的全部条件**仍满足；`p_mastery ≥ transfer.p_floor`；配置已签字 | 只有 A | 亮 |
| 保持 `retain` | 已是迁移；**第三轮复测**通过（到达迁移后满 `rung2.interval` 的第一道已准入新族题独立做对，通常即 `delayed_retest` 类） | 只有 A | 亮 + 外圈 |

- 三轮复测对应开发计划 v2.0 的“0 / 3 / 14 天复测”，间隔 `rung0 / rung1 / rung2` 是配置位，起点取开发计划的写法，数值待教研签字。复测轮次（调度概念）与 `evidence_kind = delayed_retest`（证据情境）是两回事：前两轮的复测作答通常是 `independent` 或 `transfer`。
- **题型组**：题型 → 组的映射是配置位 `item_type_groups`；题型来源见待人确认 #4。
- **配置未签字时最多写到“稳定”**，教研签字前不会出现真实亮星。
- **写库前自检**：准备写 `transfer` / `retain` 时，逐条核对 `evidence_ids` 对应的证据都是 A 级、题目当前已准入、判分不待复核；不满足就拒绝写入并报警。这是 `mastery_estimate` 表注释那条不变量的落实。
- 点点“接近掌握”对应执行与稳定，不需要额外字段。

### 5. BKT 与置信度
- **规则决定能到哪一层，BKT 只做下限**：每一层除规则条件外，还要求 `p_mastery` 不低于该层的 `p_floor`。规则负责可解释，BKT 负责“最近的表现”（连续做错时挡住晋级）。
- 简化 BKT（KGE 第六章），参数按学科配置：`bkt.p_init`、`bkt.p_learn`、`bkt.p_slip`、`bkt.p_guess`（按题型给）。证据权重按第 3 节的方向调节每一步更新的幅度（SIE：“提示等级调节证据权重”）。数值待教研签字，配置里标 `calibrated: false`，解释里如实显示。
- 输入序列：每个题目族只取第一条计入证据，同族重做不进序列（待人确认 #9）。
- v0 不做个体遗忘参数；遗忘由间隔复测与置信度衰减处理。
- 自写，不引入 pyBKT 等依赖（规则 10）。
- **置信度**：由计入证据的数量、判分置信度与证据权重算出，公式参数是配置位 `confidence_formula`。
- **时间衰减**（KGE 第六章；SIE 第五章“时效”）：长期没有新证据时，按 `confidence_decay` 降低 `confidence`，**不降 `p_mastery`，不退门槛，已亮的星不熄灭**。置信度降到 `review_confidence_floor` 以下时排一条复习复测（`reason = decay`）。到期的相关判断转“待验证”由洞察服务处理，不在本模块（ADR 13）。

### 6. “刷同题”防护
| 刷法 | 防护 |
|---|---|
| 同一道题反复答对 | 同一题目族只计第一次，之后只记录不计入（编码 `repeat_family`）；执行及以上按不同题目族计数 |
| 换数字、换顺序的近似题 | 同族题（`kge.item_tag.family_id`）按一族计；没有题目族的题按单题自成一族 |
| 同一证据重复提交 | 同 key 重放不写行（`ingest_request`）；同一 `evidence_id` 只登记一次（`evidence_registry`）；重算按 `attempt_id` 去重 |
| 靠高级提示做对 | 掌握不变，只排无提示变式 |
| 猜对 | 默认不上调（待人确认 #2） |
| 只刷一种题型 | 稳定要求覆盖配置数量的题型组 |
| 连着做、当场“复测” | 复测轮只认到期后的作答；过早作答编码 `retest_too_early` |
| 改客户端时间提前“过”间隔 | 间隔一律按服务器 `recorded_at` 算（待人确认 #8） |
| 复测失败后换题再试直到做对 | 每轮只看到期后的第一道符合条件的题，做错即本轮失败（第 8 节） |
| 拍照刷 | B 级不推动迁移及以上 |
| 用未准入的题点亮 | 未准入内容上的证据只能是 B 级；写库前自检 |

### 7. 复测与验证任务
**时间点**：
| 轮次 | 何时排 | 到期时间 |
|---|---|---|
| 当日复测（`rung0`） | 到达执行时 | 到达时刻 + `rung0.min_gap` |
| 第二轮（`rung1`） | 到达稳定时 | 到达时刻 + `rung1.interval` |
| 第三轮（`rung2`） | 到达迁移时 | 到达时刻 + `rung2.interval` |

- “到达时刻”是促成晋级的那条证据的 `recorded_at`，所以到期时间只取决于证据，重算多少次都一样。
- `mastery_estimate.next_retest_at` 写这个到期时间，是复测时间的**真相源**；知识服务契约里的 `next_review_at` 与插件 API 的 `MasterySnapshot.next_retest_at` 都由它填。
- 到达保持之后 v0 不再排复测（待人确认 #14）。

**任务表**（默认新表，待人确认 #5）：`platform.retest_task`
- 字段：`id`、`student_id`、`subject`、`node_id`、`cognitive_level`、`rung`（`rung0 / rung1 / rung2`）、`reason`（`scheduled / early / decay / hint_followup / verify_in_platform / verify_self_report`）、`due_at`、`status`（`open / passed / failed / cancelled`）、`scheduled_by_estimate_id`、`closed_at`、`closing_attempt_id`、`created_at`。
- 不存题目、答案、作答内容。每个单元每种 `reason` 最多一条 `open`（部分唯一索引）。
- **生成**：写入带 `next_retest_at` 的掌握记录时，在同一事务里建任务；`hint_followup`、`verify_in_platform`、`decay` 由重算在同一事务里建；`verify_self_report` 由服务内函数 `schedule_verification(student, node, level, reason)` 建，调用方是 SIE-03 的异议流程（经洞察服务接口，本任务只提供函数并用合成调用测试）。验证任务**不改掌握**，只有做题产生的证据会改。
- **关闭**：重算发现本轮通过或失败时，同一事务里改为 `passed / failed` 并记下作答；排程变了（退级、题被撤销）旧任务改 `cancelled`。
- **逾期不降级**：错过复测，任务一直开着，门槛不变；置信度照第 5 节衰减（待人确认 #15）。
- 复测选题不归本模块：由 `plan`（4-D）或统一壳（4-H）经 `item.similar` / `generate_variation` 从已准入题库按题目族选，排除做过的族。

**对账任务**：`python -m learning.jobs mastery-sweep`
- 一次性执行入口，以 `starbuddy_learning` 身份运行（与计量任务同一做法）；接入调度器等 ADR 03 / 10（待人确认 #7）。
- 每次运行：① 重算最近一段时间（`sweep.lookback`）有新证据、新判分或新复核结论的单元；② 按时间衰减更新置信度（有实质变化才写，第 9 节）；③ 核对每条带 `next_retest_at` 的当前记录都恰有一条对应的 `open` 任务，缺补多撤；④ 输出计数。
- 小批量、每个单元一个事务；时钟可注入（只在服务里，库里的 `computed_at` 仍是真实时间）。

### 8. 复测失败与退级
| 当前 | 本轮失败 | 结果 |
|---|---|---|
| 执行 | 当日复测 | 保持执行；从失败时刻重排当日复测 |
| 稳定 | 第二轮 | 保持稳定；从失败时刻重排第二轮 |
| 迁移 / 保持 | 第三轮（或提前复测） | 写一条新的**稳定**记录（星退回半亮）；从失败时刻重排第二轮，重新走第二轮 → 第三轮 |

- 已亮的星，普通练习做错**不直接熄灭**：`p_mastery` 跌破稳定层 `p_floor` 时，把第三轮复测提前到当下（`reason = early`），复测再失败才退回半亮（待人确认 #13）。
- 证据被撤销（判分更正、题目撤销准入、证据降级）时，重放自然得到更低的状态，照常写新记录。

### 9. 写入、更正与历史
- **表结构**（迁移，必停）：`platform.mastery_estimate` 加 `node_id`、`cognitive_level`、`map_version_id`；`skill_local_id` 改为可空，加 CHECK“两种键恰好一种”；新增部分唯一索引 `(student_id, subject, node_id, cognitive_level) WHERE is_current`；`check_supersede_target` 放行按节点键比较。本模块只写节点键的行；旧的 `skill_local_id` 行只剩 3-H 合成种子，不再新增（ADR 11 过渡：3-H 的 BFF 查询按 `skill_local_id` 连接，节点键的行不会出现在旧星图上）。表继续留在 `platform.*`（对照文档 #3，已决定）。
- 每次重算得到 `state`、`p_mastery`、`confidence`、`next_retest_at`、`algorithm_version`、`evidence_ids`。**只有实质变化才写新记录**：`state`、`next_retest_at`、`algorithm_version` 任一变化，或 `p_mastery`、`confidence` 变化超过 `write_threshold`（待人确认 #12）。
- 写入顺序：生成新 ID → 旧记录 `is_current = false`、`superseded_by = 新 ID` → 插入新记录，同一事务。旧记录不删不改，就是历史（周报 `mastery_history` 从这里取）。
- `algorithm_version` 同时带算法版本与配置版本，例如 `kge-mastery@0.1.0.<配置版本>`，符合 `versioned_name`；知识服务信封里的 `source_version` 另带地图版本段。
- `evidence_ids` = 全部计入证据的作答证据 ID；没有计入证据就不写记录。
- **规则 6 的来源标注**：本表每行都是“模型推断”，由 `algorithm_version`、`confidence`、`evidence_ids` 表达；v2.0 的来源列按 CLAUDE.md 规则 6 随 KGE / SIE 首个任务包补齐（冲刺 1-C），本任务不另加。

### 10. 幂等与并发
- 重算前对 `(student, subject, node_id, cognitive_level)` 取事务级 advisory lock（`pg_advisory_xact_lock`，键由四者哈希），同一单元的同步重算、对账、按题重算互相串行；第一次写入时还没有行可锁，所以不用行锁。
- 拿到锁后重读当前记录再比较，无实质变化不写：同样输入重算任意次，最多写一次。
- 兜底：撞唯一索引或 `guard_supersede` 时整个事务回滚、重试一次，再失败留给对账。

### 11. 地图版本
- 每条证据带作答时的 `map_version`（前置依赖），重算按证据自身的地图版本解析 `item_tag`，再经 `kge.id_migration` 映射到当前已发布版本的节点。
- 映射规则引用 ADR 11 待人确认 #2 的默认：`rename`、`merge` 延续原单元的证据；`split` 的新节点重新判定；`retire` 的单元不再计算，原当前记录置 `next_retest_at = now()` 并在解释里标“待复测”（KGE 第五章）。已亮的星不因改版熄灭（ADR 11 §3）。
- 地图版本切换由 4-G / KGE-01 的发布流程调用 `recompute_map(subject, from_version, to_version)`，分批重算。

### 12. 门槛配置
- 默认放在仓库里，版本化 JSON，不建表（待人确认 #2）：`services/learning/learning/mastery/config/<学科>.<版本>.json`。v0 只有数学的**未签字草案**；数值由教研填写，本任务的代码与测试不依赖它的任何具体值。
- 配置位清单（全部待教研签字）：`low_hint_max_level`、`grade_confidence_min`、`evidence_weights`、`level_weight_B`、`source_weights`、`error_penalty`、`delayed_retest_min_interval`、`item_type_groups`、各门槛的 `correct_min / families_min / item_type_groups_min / transfer_evidence_min / p_floor`、`rung0.min_gap`、`rung1.interval`、`rung2.interval`、`bkt.*`、`confidence_formula`、`confidence_decay`、`review_confidence_floor`、`write_threshold`、`star_level_rule`、`skill_view_aggregation`、`sweep.lookback`，以及 `calibrated`、`approved_by`、`approved_at`。
- 启动时按 JSON Schema 校验，并做第 3 节的方向与顺序校验；不合法拒绝启动。
- 一经使用就不改，改数值只能新增版本；当前生效版本由服务配置指定。签字记录写本目录 `lessons.md`，与 `approved_by` 对应。
- 测试一律用夹具里的**合成配置**（`test.*`，只在 `services/learning/tests/` 下，标明“测试值，不是教研默认”）。

### 13. 可解释：`mastery.get` 与 `mastery.explain`
- 按 `knowledge-service.v0.9.yaml` 实现两个个人层工具，调用方只有编排层与学习服务（契约 `caller` 没有 `bff`）。信封带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required`。
- `mastery.get`：每个节点、每个层次的 `gate`、`p_mastery`、`next_review_at`，`star_lit` 按 `star_level_rule` 算。
- `mastery.explain`：证据链，每条带 `evidence_kind`、`evidence_level`、`weight`、`hint_level`。
- 门槛条件的逐条结果（编码、是否满足、当前值、要求值、相关证据 ID）与不计入原因编码（`repeat_family`、`guessed`、`hint_heavy`、`low_confidence`、`pending_review`、`not_admitted`、`level_b_capped`、`not_formal`、`retest_too_early`、`untagged`、`legacy_no_kind`）现契约没有位置：v0 放在服务内函数 `explain_gates()` 与开发用命令行（只连开发库、只用合成数据），进契约见待人确认 #17。
- 只输出编码与数字，不输出自然语言；对学生、家长的任何陈述都经判断账本（规则 11），不从这里直出。

### 14. 学情档案页（每生每科一页）
依据 KGE 第六章与附录修订 1、2，SIE 第九章，开发计划 v2.0 KGE-04。

**定位**
- 档案页是判断账本的**可读视图**，自身不产生新结论。内容分六节：当前阶段、已稳定掌握、正在攻克、根因线索、最近进步、下一步。
- 只写学习事实；不写性格、情绪、家庭推断，不写“你不擅长数学”式的定性判断（规则 12）。
- 只存在数据库个人层（`platform.*`，对照文档 #3），**永不进入 Git 页面仓库**（规则 13；ADR 12 只管公共页面）。
- Agent 读完整版；学生端“星图旁的成长手记”、家长端证据与建议视图由 4-H / SIE-03 的界面读取，本任务只提供数据与读取函数。

**与判断账本的关系（只读）**
- 本模块以 `starbuddy_learning` 身份**只读** `insight.claim`（0010 的 `claim_agent_read`：可见范围含 `agent`、未过期、模板有效的非终态判断），**不写任何判断**，不改掌握度以外的个人层表（规则 11；ADR 13）。判断由洞察服务在 SIE-02b 里依据本模块的 `mastery_estimate` 写入。
- 每条陈述引用一个 `claim_id`（判断再引用证据），不直接引用证据事件。
- 对学生、家长可见的陈述只能来自“成立”或“假设”状态的判断；“待验证”的判断只作为 Agent 版里的待办条目，不作为陈述（规则 11；ADR 13 待人确认 #2）。
- 编译者身份与学生、家长视图的过滤方式见待人确认 #18。

**生成：模板渲染为主**
- 每条结论句由判断按其模板（`insight.claim_template`）直接渲染，渲染用洞察服务契约的 `RenderedStatement` 同一套规则，不另写一套。
- “衔接语”（排序、分组、节与节之间的连接）是唯一可以不来自模板的文字。v0 默认用人审过的**固定衔接短语表**，不调模型；接入模型时只能经模型路由层（ADR 04）调用**境内已备案模型**，数据级别按 L2（规则 13），并且只写排序与衔接（待人确认 #19）。
- 存储（迁移，必停；表名暂定 `platform.profile_page`）：`student_id`、`subject`、`map_version_id`、`sections`（jsonb：每节的陈述列表 `[{claim_id, template_key, template_version}]` 与衔接语）、`claims_digest`（编译时所引判断的 `id + updated_at` 摘要）、`compiled_at`、`compiler_version`、`model_version`（未用模型时为空）、`check_status`（`passed / blocked_fallback`）。每生每科一条当前页；陈述**文字不落库**，读取时按判断现状渲染，所以判断被推翻、撤回、过期后，旧页上的对应陈述自动消失。
- 阻断记录（暂定 `platform.profile_page_check`）：编译时间、阻断编码、涉及的 `claim_id`、衔接语片段。只存个人层。

**惰性编译**
- 只有两个触发点：① 会话开始时，编排层调 `ensure_profile_page(student, subject)`，`claims_digest` 与当前判断不一致才重编；② 周报（5-H）生成时编一次。
- 每次作答、每次掌握重算**都不触发**重编。两次编译之间，Agent 直接读判断账本的上下文包（`insight.context`）。
- 会话开始时编译超出时间预算或失败，返回纯模板版本（无衔接语），不阻塞会话。

**陈述—判断一致性检查**（每次编译都跑，通过率要求 100%，KGE 第八章质量门槛）
- 逐条检查，任何一条不过就阻断这次编译：
  1. **无引用陈述**：结论句必须恰好对应一条 `claim_id`，且该判断在编译时可读、状态合规。
  2. **程度升级**：衔接语里出现的程度词不能比所引判断的模板程度更重（如判断是“尚不稳定”，衔接语写“薄弱”即阻断）。程度词表按模板分级，属人审项（待人确认 #20）。
  3. **新增结论**：衔接语里出现任何结论性表述（程度词、掌握词、评价词）而同组没有对应判断，即阻断。
  4. **数值**：衔接语不得包含数字；陈述里的数值只来自判断 `value` 的模板渲染。
  5. **禁用推断**：全文过 `docs/design/sie/sie_lexicon.py` 的匹配（词表 `forbidden-inference.v0.yaml`），命中即阻断（规则 12）。
  6. **角色语言**：衔接语不得出现人设名、人设口头禅（KGE 第四章“页面人设中立”；角色语言混入数要求为 0）。词表取自 `docs/personas/`，属人审项。
- 阻断后不发布新衔接语：本次页面降级为纯模板版本（`check_status = blocked_fallback`），记录阻断，计入指标。
- v0 检查全部是确定性规则，不用模型当裁判；将来增加模型辅助检查也只能是追加的阻断条件，不能放行规则已阻断的内容。

### 15. RLS 与删除
- 本任务新增的个人层表（`retest_task`、`profile_page`、`profile_page_check`）全部 `ENABLE` + `FORCE ROW LEVEL SECURITY`：
  - `starbuddy_learning` 读写（策略全放行，与其他 learning 策略一致，按学生隔离由调用方负责）；
  - `starbuddy_bff` 按 `platform.can_read_student(student_id)` 只读；`profile_page` 的陈述对学生、家长的过滤另按待人确认 #18；
  - `starbuddy_review`、`starbuddy_scheduler`、`starbuddy_kge`、`starbuddy_insight` 无权限（洞察服务要读档案页时另议）。
- 三张表的 `student_id` 外键 `ON DELETE CASCADE`，学生档案删除时一并删除（Gate 0“删除覆盖全表”）。`mastery_estimate`、`evidence_event` 等已有表的删除路径不在本任务，归 SIE-03“导出与删除”（依赖）。
- `mastery_estimate` 的现有 RLS 不改。

### 16. 与其他模块的接口
- **SIE-02b（洞察服务）**：读当前 `mastery_estimate` 行写节点类判断（`basis_kind = mastery_estimate`）。本模块保证行 ID 稳定、更正链完整；判断的读取方式（直接读表还是经 `mastery.get`）由 SIE-02b spec 定。
- **SIE-03**：异议“我其实会”调 `schedule_verification`；导出与删除覆盖本模块的表。
- **星图（H 流，ADR 11 过渡后）**：读 `mastery.get` 与判断账本；本任务不改 3-H 代码。
- **4-D 阿鸦 `plan`**：平台用 `next_retest_at` 填 `MasterySnapshot`；适配器只按它排序，不自己算间隔。
- **4-H 统一壳**：作答时必须带 `hint_level_used`；读 `retest_task` 显示“待复测”；会话开始时调 `ensure_profile_page`。
- **4-G 发布闸门**：准入变化调 `recompute_item`；地图版本切换调 `recompute_map`。

### 17. 测试
- **单测**（纯函数，不连库）：证据分类与方向、重放状态机、BKT、衰减、配置校验（含方向与顺序校验）、解释、一致性检查的每条规则；每条规则都有正例与反例。
- **集成测试**（一次性 Postgres，与 `learning-test` 同一套；需 0010、0011、0012 与本任务迁移已在测试库执行）：合成学生、合成地图版本、合成题目族，覆盖全部验收场景。判断用测试夹具以 `starbuddy_insight` 身份写入（只在测试库；生产代码没有这个身份）。
- **全库不变量**（每个集成测试结束时跑）：没有 `transfer / retain` 记录引用 B 级、未准入或待复核的证据；每单元至多一条当前记录；每条带 `next_retest_at` 的当前记录恰有一条 `open` 任务；`profile_page.sections` 里每个 `claim_id` 都存在。
- **变异测试**：去掉“不同题目族”判定、去掉提示等级判定、去掉证据等级判定、去掉准入判定、把间隔改按 `occurred_at`、去掉 advisory lock、去掉衰减“不降掌握”、去掉一致性检查的任一条，都必须被测试发现。
- 全部数据合成（规则 8）；不读留出集与金标数据（规则 14）。

## 前置依赖（未满足前本任务不开工，或只能按标注的部分开工）
| 依赖 | 内容 | 状态 | 是否必停 |
|---|---|---|---|
| 冲刺 1-C 证据加列 | `evidence_event`（或 `attempt`）加 `evidence_kind`、`map_version`、`node_id`、`cognitive_level`、`evidence_level`、`channel`，学习服务按请求上下文与输入通道补齐；`evidence_kind` 不能复用现有 `kind` 列的语义 | 没有 spec（对照文档 #5；`specs/kge-schema-v0.9/spec.md` 第 42 行把它留给冲刺 1-C） | 是（迁移；学习服务写入） |
| KGE-01 骨架 | 迁移 0011、0012 已执行；数学地图首个版本经 `kge.publish_map_version` 发布；阿鸦节点经 `skill_link` 挂接（ADR 11 待人确认 #3） | 迁移为草稿，待人执行 | 是 |
| KGE-02 原子与打标 | 阿鸦题目的 `kge.item_tag`（主节点、认知层次、题目族）；题型（待人确认 #4） | 未开始 | 是（题型列涉及迁移） |
| SIE-02b 判断账本接入掌握 | 迁移 0010 已执行；首批模板（数学“知识掌握”“知识迁移”）已审核登记（ADR 13 待人确认 #4） | 迁移为草稿 | 是 |
| 4-G 发布闸门 | 有题被升为 `admitted`，才可能出现 A 级证据与真实亮星 | spec 草稿 | 否（对本任务） |
| ADR 11 / 13 / 15 签字 | 星亮口径、判断写入方、证据分级 | 草稿 | 是 |
| 教研签字 | 数学配置的全部数值 | 未开始 | 是 |
| ADR 03 / 10 | 定时任务托管 | 草稿 | — |
| ADR 04 模型路由 | 只在衔接语接入模型时需要 | 草稿 | 是（新增模型供应商） |

可以提前做的部分：配置加载与校验、重放状态机、BKT、衰减、一致性检查的规则引擎，全部是纯函数，可以在依赖落地前用单测完成。

## 范围

### 做
1. `services/learning/learning/mastery/`（新建）：配置加载与校验、证据读取与分类、重放状态机、BKT、衰减、写入（含更正与复测任务）、能力视图汇总（读时计算）、`explain_gates()`、对账任务入口、`schedule_verification`。
2. 学习服务实现知识服务契约的 `mastery.get`、`mastery.explain`（按 `knowledge-service.v0.9.yaml`，不改契约）。
3. `grade` / `diagnose` 落库成功后接入同步重算（失败不影响接口返回）。
4. 学情档案页：编译器（模板渲染、固定衔接短语、惰性编译）、一致性检查、`ensure_profile_page` 与读取函数。
5. 迁移（编号按合并顺序排，必停）：`mastery_estimate` 节点键；`retest_task`、`profile_page`、`profile_page_check` 及其 RLS；RLS 测试与权限快照更新。
6. 数学配置草案（未签字，数值留空由教研填）与测试用合成配置。
7. 测试：单测、集成、RLS、删除级联、全库不变量、变异；接入 `make learning-test` 与 `make check`。
8. 本目录 `lessons.md`：实现记录、教研签字栏、新增待确认项。

### 不做
- 冲刺 1-C 的证据加列与学习服务补齐证据属性（前置依赖）。
- 判断的写入与模板（SIE-02b）；异议、验证题的学生端流程、导出与删除（SIE-03）。
- 星图数据源切换、能力视图侧栏、待验证虚线（H 流，ADR 11 过渡）；本任务不改 3-H 代码。
- IRT、个体遗忘参数、FSRS（语文、英语素材，KGE-05）、中圈学习力指标。
- 复测选题、今日计划编排（4-D、4-H）；点亮动效。
- 调度器部署（ADR 03 / 10）；计量变更（待人确认 #21）。
- 契约变更（`mastery.explain` 的层次与条件编码，待人确认 #17）。
- 知行、文砺、英语的配置数值。
- 衔接语接入任何模型（待人确认 #19 选另一选择时另起 PR）。

## 验收标准（可测试）
以下场景全部用合成学生、合成地图版本与**合成配置**；除特别说明，题目已准入、证据为 A 级、配置已签字（测试签字）。

- [ ] **“刷同题”无法点亮**（开发计划验收）。逐项断言最终门槛：
  - 同一道题分多天、每天换 key 独立做对 → 不超过“理解”；
  - 同一题目族的多道题都独立做对 → 按一族计，不超过配置允许的层；
  - 同一请求重放多次、多个并发同 key → 行数不变，掌握记录只写一次；
  - 不同族的题都做对但只覆盖一个题型组 → 不超过“执行”；
  - 全部在高级提示后做对 → `p_mastery` 与门槛不变，每个单元各有一条 `hint_followup` 任务；
  - 复测轮到期前作答 → 不算复测，解释里有 `retest_too_early`；
  - `occurred_at` 提前、`recorded_at` 是现在 → 不算过了间隔；
  - 复测失败后换题做对 → 本轮仍记失败，从失败时刻重排。
- [ ] **影响方向**：对第 3 节表格的每一行，在任一状态上追加一条该类证据，`p_mastery` 与 `confidence` 的变化方向与表格一致；“低级提示做对”的上调小于“独立做对”；配置把任一方向或顺序写反时服务拒绝启动。
- [ ] **证据等级**：只有 B 级证据、其余条件全满足 → 最高“稳定”，并有一条 `verify_in_platform` 任务；`evidence.write` 带 C 级被拒（契约），库里没有 C 级行；冲刺 1-C 之前的旧证据（无等级）→ 最高“稳定”，解释里有 `legacy_no_kind`。
- [ ] **待复核不推动门槛**：`human_review_required = true` 的判分不改变门槛与 `p_mastery`，解释里有 `pending_review`；判分被更正后对账重算，结果按更正后的判分计入。
- [ ] **未准入不点亮**：条件全满足但题目未准入 → 最高“稳定”；题目改为 `rejected` 后 `recompute_item` → 亮星退回“稳定”，旧记录被取代；全库不变量通过。
- [ ] **未签字不点亮**：配置 `approved_by` 为空时条件全满足也最高“稳定”。
- [ ] **自述不改掌握**：`schedule_verification(..., verify_self_report)` 后 `mastery_estimate` 不写新行，只多一条任务；`attempt.self_report = confident` 的作答与无自评的同类作答得到相同结果。
- [ ] **时间衰减**：注入时钟推进、不加证据 → `confidence` 单调不增，`p_mastery` 与门槛不变，已亮的星仍是 `transfer`；降到 `review_confidence_floor` 以下时有且只有一条 `decay` 任务。
- [ ] **正常路径**：按“执行 → 当日复测 → 第二轮 → 第三轮”作答，门槛依次为执行、稳定、迁移、保持；每步一条新记录，旧记录 `is_current = false` 并指向新记录；`mastery.get` 在迁移与保持时 `star_lit = true`。
- [ ] **复测任务按时生成**（注入时钟）：到达执行 / 稳定 / 迁移时同一事务出现对应轮次任务，`due_at` 精确等于到达时刻加配置间隔且等于 `next_retest_at`；到期前查不到、到期后查得到；删掉一条后跑对账 → 补回且只有一条；连跑三次任务数不变；通过或失败后状态为 `passed / failed` 并记下作答 ID。
- [ ] **复测失败退回半亮**：迁移后第三轮答错 → 新记录 `state = stable`。
- [ ] **能力视图不点亮**：`skill_node` 的汇总只由节点结果与 `skill_link.weight` 算出；`mastery_estimate` 中没有本模块写的 `skill_local_id` 行；任何返回里能力视图都没有 `star_lit`。
- [ ] **地图版本**：`rename` / `merge` 后门槛延续；`split` 的新节点重新判定；`retire` 的单元当前记录 `next_retest_at` 被置为当下。
- [ ] **幂等与并发**：同一单元多个并发重算 → 只有一条当前记录、更正链完整、调用方无错误；同样输入重算两次，第二次不写行。
- [ ] **重算失败不影响落库**：注入重算异常 → `grade` 仍返回 200、证据完整；之后对账补上掌握记录。
- [ ] **规则 6**：每条新记录的 `algorithm_version` 含配置版本；`confidence` 非空；`evidence_ids` 非空、全部存在、都属于同一学生同一单元；`mastery.*` 的信封五个字段齐全。
- [ ] **可解释**：上面每个场景都调用 `explain_gates()`，未满足条件编码与不计入原因编码符合预期；解释里的证据 ID 都在记录的 `evidence_ids` 或不计入清单里；输出不含自然语言句子。
- [ ] **档案页 · 引用**：每条陈述都有 `claim_id`；所引判断被推翻、撤回或过期后，读取结果里对应陈述消失；“待验证”判断不出现在学生、家长可读的陈述里。
- [ ] **档案页 · 一致性检查拦截程度升级**（开发计划验收）：合成用例“判断为尚不稳定、衔接语写薄弱”被阻断；衔接语新增结论、含数字、命中禁用推断词表、含人设名的用例各自被阻断；阻断后页面为纯模板版本，并有一条阻断记录；合规用例全部通过（通过率 100%）。
- [ ] **档案页 · 惰性编译**：连续写入多条证据并重算 → 编译次数为 0；会话开始时判断未变 → 不重编；判断变化后会话开始 → 重编一次；周报调用 → 编一次。
- [ ] **档案页 · 边界**：编译模块不依赖页面仓库的任何客户端（静态检查）；默认配置下不调用任何模型；学习服务角色对 `insight.claim` 的 INSERT / UPDATE 被拒（0010 权限测试仍通过）。
- [ ] **RLS**（规则 5）：`retest_task`、`profile_page`、`profile_page_check` 学生本人、已绑定家长能读；兄妹、另一户家长、撤销绑定后读不到；BFF 不能写；复核、调度、知识服务、洞察服务角色无权限。
- [ ] **删除**：删除合成学生档案 → 三张新表中该生的行全部消失。
- [ ] **没有配置的学科不写**：文砺的合成证据不产生任何掌握记录与档案页。
- [ ] 变异测试全部被发现；严格模式 `make check` 通过；涉及画像的禁用推断与陈述—判断一致性测试随本任务落地并接入 `make check`（CLAUDE.md 完成定义）。

## 涉及的必停项（见 CLAUDE.md）
| 必停项 | 本 spec | 说明 |
|---|---|---|
| `db/migrations` 下的任何文件 | **是** | `mastery_estimate` 节点键；`retest_task`、`profile_page`、`profile_page_check`；题型列视待人确认 #4。数据迁移先在副本演练 |
| RLS 与权限策略 | **是** | 三张新表的策略与测试；不改 `mastery_estimate`、`insight.claim` 的现有策略 |
| 计量与权益 | 否 | 重算、对账、建任务、编译都不是消耗类请求；复测作答扣不扣额度见待人确认 #21 |
| `packages/contracts` | 否（默认） | 按知识服务 v0.9 实现，不改契约；待人确认 #17 选另一选择时为“是” |
| 判分与掌握计算逻辑 | **是** | 本 spec 的全部计算规则；门槛、权重、BKT、衰减、复测间隔的数值由教研签字 |
| 判断模板、禁用推断词表、画像可见范围 | **是** | 档案页的可见范围（待人确认 #18）；程度词表与衔接短语表（待人确认 #20）；不改模板与禁用词表本身 |
| 教研标注的金标准数据（`evals/`，目录尚未建立） | 否 | 不读不写；测试全用合成数据 |
| 人设 Prompt、禁语表、转介话术 | 否（默认） | 档案页人设中立，默认不用 Prompt；接入模型写衔接语时要跑 `make eval-persona` 并附前后对比 |
| 新增第三方服务、模型供应商或数据出境 | 否（默认） | 默认不调模型；待人确认 #19 选模型时为“是”，只能是境内已备案模型 |

## 与同期 spec 的协调（冲刺 4 四份 spec）
1. **迁移编号**：`0010`–`0015` 已被 v2.0 草稿占用。4-C、4-G、4-H 的新迁移从 `0016` 起按合并顺序编号，正文不预订编号。若题型列放在 `platform.item`（待人确认 #4 的另一选择），需同时放行 4-G 的保护触发器。
2. **复测的唯一来源**：本模块的 `next_retest_at` 与 `retest_task`。平台调 `plan` 时由它填 `MasterySnapshot.next_retest_at`；适配器只排序，不自己算间隔；阿鸦 legacy 的间隔链不再使用。
3. **作答要带提示等级**：4-H 提交作答时必须带 `hint_level_used`，否则按待人确认 #10 处理。
4. **档案页入口**：4-H 在会话开始时调 `ensure_profile_page`；学生、家长端的展示界面在 H 流信任界面（冲刺 5）。
5. **复测作答扣不扣额度**：见待人确认 #21。
6. **v1.0 版本登记的两个缺口**（阿鸦题干无处显示、pilot 家庭开通订阅入口）不属于本任务，仍由人决定（见 4-H spec）。

## 待人确认
已决定、只作引用的：个人层表留在 `platform.*`（对照文档 #3，2026-09-27）；英语搁置（对照文档 #1）；证据属性由平台补齐（插件 API v1.0 svc-#2，2026-09-28）；档案页放进本 spec（`specs/sprint4-v2-rewrite/spec.md` 待人确认 #3 的默认，随该方案确认）；地图升级时星的继承（ADR 11 待人确认 #2）；待验证判断不作陈述（ADR 13 待人确认 #2）；B 级权重的经验值来源（ADR 15 待人确认 #2，并入下面 #1）。

仍未决的：
1. **全部数值由谁、何时签字**：第 12 节配置位清单（门槛、证据权重含 B 级、BKT、置信度公式、衰减、三轮复测间隔、延迟复测下限、写入阈值）。默认：数学教研在教研轨 W9–W13 签字；签字前最多写到“稳定”。另一个选择：等数值到位再开工。
2. **SIE 表没有覆盖的方向**：提示后做错、自评“猜的”做对、多类同时成立时 `evidence_kind` 的优先级、B 级能否推动理解—稳定。默认见第 3 节，由教研与 SIE 负责人确认。
3. **星亮看哪个认知层次**（`star_level_rule`）：默认每个节点由教研标一个“星亮层次”（通常是课标对该节点要求的最高层次），该层次进入迁移即星亮；另一个选择：节点所有已标层次都进入迁移才亮。
4. **题型从哪里来**：默认在 `kge.item_tag` 加题型列，由 KGE-02 打标写入（改 0011 草稿，必停）；另一个选择：`platform.item.item_type`，或让适配器在证据里带（契约变更）。单选与多选是否同组由教研定。
5. **复测任务存哪里**：默认新表 `platform.retest_task`；另一个选择：只用 `next_retest_at`，不建表（验证任务与完成率就没有记录）。
6. **什么时候算**：默认落库后同步另起事务，加对账兜底；另一个选择：全部异步。
7. **定时任务用什么身份跑**：默认学习服务的一次性入口（`starbuddy_learning`）；另一个选择：`starbuddy_scheduler` 调 `SECURITY DEFINER` 函数（判定逻辑要写成 SQL）。
8. **间隔按哪个时间算**：默认服务器 `recorded_at`；另一个选择：客户端 `occurred_at`（补录纸质作业更准，但可伪造）。
9. **同一题目族重做算不算**：默认每族只计第一次；另一个选择：隔一段时间（配置）以上可再计一次。
10. **没有提示等级怎么办**：默认按“非低级提示”处理，最多到理解；另一个选择：不提供提示的场景（如入口诊断）按无提示处理，由请求模式决定。
11. **只因 `content_not_admitted` 而复核的结果**：默认与其他待复核结果一样不推动任何门槛（与插件 API v1.0 描述一致；阿鸦内容准入前连半亮都没有）；另一个选择：按 B 级计入，最多推到稳定（v1.0 版的默认）。
12. **什么时候写新记录**：默认只在门槛、复测时间、算法版本变化，或数值变化超过 `write_threshold` 时写；另一个选择：每次重算都写。
13. **已亮的星做错普通练习**：默认不直接熄灭，`p` 跌破稳定层下限时把第三轮复测提前到当下；另一个选择：直接退回稳定。
14. **到达保持之后**：默认不再排复测；另一个选择：定期维持性复测（间隔由教研配置）。
15. **错过复测**：默认不降级，只按衰减降置信度；另一个选择：逾期超过一定时间退回稳定。
16. **题目的次要节点**（`item_tag.node_ids`）：默认只计主节点；另一个选择：次要节点按降权计入。
17. **`mastery.explain` 的契约缺口**：请求没有 `cognitive_level`；`EvidenceLink.hint_level` 上限 5 而 `attempt.hint_level_used` 上限 6；没有门槛条件与不计入原因的字段。默认 v0 不改契约，条件编码只在服务内函数与开发命令行；另一个选择：随知识服务 v1.0 冻结一起补（契约变更，必停）。
18. **档案页的编译者与视图过滤**：`starbuddy_learning` 只能读 `agent` 可见的判断。默认由学习服务编 Agent 完整版；学生、家长视图在读取时由 BFF 按 `insight.claim` 的前端策略过滤陈述，看不到的判断连同所在组的衔接语一起不显示。另一个选择：由洞察服务编译并按受众各存一版（洞察服务要获得档案页表的写权限）。
19. **衔接语用不用模型**：默认 v0 用固定衔接短语表，不调模型；另一个选择：经模型路由层调境内已备案模型（新增模型供应商，必停；要跑人设回归）。
20. **程度词表与衔接短语表**：默认由数学教研与 SIE 负责人起草、产品负责人审核，按判断模板与禁用词表同级管理（必停）；审核人待定。
21. **复测与验证题作答扣不扣额度**：`grade` 现按资源计量。默认本任务不动计量，建议平台发起的复测与验证题不扣（届时是计量变更，必停）；另一个选择：照常扣。
