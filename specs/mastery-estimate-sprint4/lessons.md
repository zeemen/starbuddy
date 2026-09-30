# KGE-04 掌握 v0 · lessons

## 第一步：纯函数部分（2026-09-30）

按 spec“2026-09-30 复核”：不依赖未执行迁移的部分先做，不连库、不接服务接口。待人确认 #1–#23 全部按默认。
签字前代码最多写到“稳定”；仓库里没有任何教研签字数值。

### 产物
| 位置 | 内容 |
|---|---|
| `services/learning/learning/mastery/config.py` | 配置加载与校验：结构（配置位齐全、类型、不认识的键拒绝）、取值范围、第 3 节方向与大小顺序、门槛逐级不降、复测间隔递增；签字状态；配置指纹写进 `algorithm_version`（`kge-mastery@0.1.0.<配置版本>.<指纹前 8 位>`，符合 `platform.versioned_name`） |
| `.../mastery/evidence.py` | 单条证据分类：计入 / 不计入 + 原因编码，计入时的方向、权重类别、生效等级与封顶原因 |
| `.../mastery/bkt.py` | 简化 BKT（强度插值）、置信度、时间衰减（只降置信度） |
| `.../mastery/gates.py` | 五道门槛重放状态机、复测调度（rung0 / rung1 / rung2）、复测失败与退级、#13 提前复测、任务清单、`explain_gates()`、`star_lit()` |
| `.../mastery/write.py` | 写入判定（#12）与写库前自检（迁移 / 保持只引用 A 级、已准入、formal、不待复核的证据） |
| `.../mastery/consistency.py` | 档案页陈述—判断一致性检查的确定性规则引擎（第 14 节六条规则 + 固定短语表），阻断后降级为纯模板版本 |
| `services/learning/config/mastery/aya.draft.json` | 数学配置草案：只有配置位，数值全部留空（加载器拒绝加载） |
| `services/learning/config/mastery/synthetic-test.json` | 合成占位配置（文件头写明“合成占位，非教研签字值，禁止用于任何非测试环境”），只供测试 |
| `services/learning/config/mastery/profile-check.synthetic.json` | 程度词表、结论词表、衔接短语表、人设词表的合成占位（#20） |
| `services/learning/tests/test_mastery_*.py` | 单测、性质测试（固定种子随机序列，300 个种子 × 6 条性质）、刷同题专项、一致性检查正反例 |
| `services/learning/scripts/mastery_mutations.py` | 52 条变异，全部被发现；`make mastery-mutation` 已加进 `make check` |

### 实现要点
- **签字与环境**：配置带 `signed_by` / `signed_at`（两者同空或同填，时间必须带时区）与 `synthetic`。未签字或 `synthetic = true` 的配置只允许 `LEARNING_ENV=test` 加载；状态机在 `signed` 为假时不晋级到迁移（解释里 `transfer.config_signed` 不满足）。测试里的“已签字”配置只在内存里构造，仍是 `synthetic`，离开测试环境同样被拒。
- **方向校验固定方向不固定幅度**：独立 > 低级提示 > 高级提示 = 0；迁移、延迟复测 ≥ 独立；独立做错 < 0；独立做错 ≤ 提示后做错 ≤ 0；B 级权重 ∈ (0, 1)；平台诊断 > 日常练习；错因系数 ∈ (0, 1]；全部权重 ∈ [-1, 1]，所以更新强度 a = |权重| × 等级权重 × 来源权重 × 错因系数 ≤ 1，不封顶截断，大小顺序原样保留在更新幅度里。BKT 要求 `p_slip + p_guess < 1`（否则做对会下调）。
- **BKT**：每步先求后验（做对再加学习转移，做错不加，保证做错只下调），再按强度 a 在旧值与目标值之间插值；a = 0 时不变。置信度 = 质量 /（质量 + k），提示后做错不增加质量。
- **衰减**：宽限期后按半衰期降置信度，不降 p、不退门槛；置信度跌破 `review_confidence_floor` 时出一条 `decay` 任务，到期时间 = 最后计入证据时间 + 宽限期 + 半衰期 × log2(置信度 / 下限)，只取决于证据。
- **刷同题**：重放前按 `evidence_id`、`attempt_id` 去重（`duplicate`）；每个题目族只计第一次（`repeat_family`）；没有题目族的题按单题自成一族；执行、稳定按不同题目族计数，稳定还要求题型组数量；复测只认到期后、新族、非提示的第一道题。
- **题目族占用**：猜的做对（`guessed`）、高级提示做对（`hint_heavy`）虽不计入，也占用题目族（学生已见过这道族题的结果或解法）；待复核、低置信、无判分、未打标等没有有效判分的不占用，复核结案后仍可按第一次计入。
- **复测**：rung0 在到达执行时排、rung1 在到达稳定时排、rung2 在到达迁移时排，到期 = 促成晋级那条证据的 `recorded_at` + 配置间隔；rung0 认 A、B 级，rung1 / rung2 只认严格证据（A 级、已准入、formal）。复测失败：执行 / 稳定保持原级、从失败时刻重排本轮；迁移 / 保持退回稳定、重排 rung1。错过复测不降级（#15），到达保持后不再排（#14）。
- **已亮的星**：普通练习做错使 p 跌破稳定层 `p_floor` 时，rung2 提前到当下（`reason = early`），下一道严格的新族题做错才退回稳定（#13）。
- **任务清单**：纯函数输出应开着的任务（`scheduled` / `early` / `hint_followup` / `verify_in_platform` / `decay`），第二步落到 `platform.retest_task` 并按“每单元每种 reason 最多一条 open”对账。`verify_self_report` 由第二步的 `schedule_verification` 建，不经重放。
- **一致性检查**：程度词按最长优先匹配并遮掉命中处，避免“尚不稳定”里的“稳定”被当成结论词；禁用推断复用 `docs/design/sie/sie_lexicon.py` 的同一个匹配函数与词表；阻断后去掉全部衔接语，只留引用了可见判断（成立、假设）的陈述，降级版本自身必须通过检查。不调模型（#19）。

### 与 spec 的偏差（待人确认）
1. **配置目录**：spec 第 12 节写 `services/learning/learning/mastery/config/<学科>.<版本>.json`；本次按任务要求放在 `services/learning/config/mastery/`（与 `config/model_routes.yaml` 同级）。测试用合成配置也放在这里（spec 写“只在 `tests/` 下”），靠文件头标注与加载器的环境限制兜底。
2. **签字字段名**：spec 写 `approved_by` / `approved_at`，实现用 `signed_by` / `signed_at`，另加 `synthetic`；含义相同。
3. **结构性下限**：校验要求 `execute.families_min ≥ 2`、`stable.item_type_groups_min ≥ 2`、`stable.families_min ≥ execute.families_min`（“不同题目族”“只刷一种题型不能稳定”的结构保证）。这是对取值的约束，不是具体数值，但请教研确认。
4. **迁移 / 保持记录的 `evidence_ids`**：spec 第 9 节写“全部计入证据”，第 4 节与全库不变量又要求迁移 / 保持记录不引用 B 级、未准入、待复核证据。实现取后者：迁移及以上只列严格证据，其余计入证据（仍影响 p 与置信度）放在解释的 `supporting_evidence_ids` 与 `capped` 里。
5. **迁移的“重新检查稳定条件”**：用严格证据重查答对数、题目族数、题型组数，不重查“当日复测通过”（rung1 已要求严格证据通过，比 rung0 更强）；否则当日复测用 B 级过的学生永远卡在稳定。
6. **B 级 rung1 的“宽松通过”**：rung1 到期后第一道非严格新族题做对只记“宽松通过”，用来判断“满足星亮其他条件”以排 `verify_in_platform`，不关闭本轮复测。
7. **旧证据（`evidence_kind` 或 `evidence_level` 为空）**：一律按 B 级、编码 `legacy_no_kind`；提示等级为 0 时按独立作答计题目族、权重取更保守的“低级提示”；没有提示等级按高级提示（#10），诊断来源没有提示等级按无提示（#23）。有情境没等级（v0 路径）也用 `legacy_no_kind`，没有另起编码。
8. **题型组**：题型来源未定（#4），没有题型或题型未映射到组的作答不计入任何题型组。后果：`kge.item_tag` 加题型列之前，真实数据到不了“稳定”。
9. **新增原因编码**（第 13 节以外）：`duplicate`、`no_grade`（#23）、`not_scored`（verdict 不是 correct / incorrect）、`level_c`、`secondary_node`（#16）、`other_unit`；注记 `delayed_retest_too_soon`（延迟复测未满最小间隔，按独立权重计）。
10. **一致性检查的第 0 条**：衔接语必须在固定衔接短语表里（`connective_unapproved`），与 #19 默认一致；短语表本身在加载时不许含程度词、结论词、人设词或数字。
11. **没做**：能力视图汇总（`skill_view_aggregation` 只有配置位）、`schedule_verification`、`recompute_*`、对账入口都依赖库表，放第二步。

### 仍待人确认
- **#1 数值签字**：`aya.draft.json` 全部数值留空，由数学教研填写并签字；签字记录写在下方“签字栏”，与 `signed_by` 对应。签字前任何环境都不加载它。
- **#4 题型列**：`kge.item_tag` 仍没有题型列（改 0011 草稿或新迁移，必停）；单选与多选是否同组由教研定。
- **#5 复测任务表**：默认新建 `platform.retest_task`（第二步迁移）。
- **#17 契约缺口**：`explain_gates()` 的条件编码与不计入原因暂不进契约。
- **#20 词表**：程度词表、结论词表、衔接短语表、人设词表的正式版本与审核人。
- 上面“偏差”第 3–8 条。

### 签字栏
| 学科 | 配置版本 | 签字人 | 时间 | 备注 |
|---|---|---|---|---|
| 数学（阿鸦） | — | — | — | 未签字 |

## 第二步计划（库表与服务接入）
1. **迁移（从 0028 起按合并顺序编号，必停，先在副本演练）**：`mastery_estimate` 加 `node_id`、`cognitive_level`、`map_version_id`，`skill_local_id` 改可空并加“两种键恰好一种”CHECK，节点键的当前记录部分唯一索引，`check_supersede_target` 放行节点键；新表 `platform.retest_task`、`profile_page`、`profile_page_check`（ENABLE + FORCE RLS，`student_id` 外键 ON DELETE CASCADE）；RLS 测试与权限快照更新；视 #4 决定题型列。
2. **读取层**：从 `evidence_event` / `attempt` / `grade_result` / `error_attribution` / `kge.item_tag`（按证据自身的 `map_version_id`，经 `kge.id_migration` 映射）/ `platform.item` 组装 `Evidence`；准入取证据自身与题目当前登记里更严的那个。
3. **写入层**：`recompute(student, subject, node_id, cognitive_level)`：`pg_advisory_xact_lock` → 重读当前记录 → `replay` → `write_decision` → `precommit_check` → 生成新 ID、旧记录指向新 ID、插入新记录 → 同事务开 / 关 / 撤 `retest_task`；撞唯一索引重试一次。
4. **接入**：`grade` / `diagnose` 落库事务提交后另起事务同步重算（失败只记日志与指标，#6）；`recompute_item`（4-G 准入变化）、`recompute_map`（地图版本切换）；`python -m learning.jobs mastery-sweep` 对账（重算、衰减、任务缺补多撤、计数）；`schedule_verification`（SIE-03 异议）。
5. **接口**：学习服务按 `knowledge-service.v0.9.yaml` 实现 `mastery.get` / `mastery.explain`（#22），信封五字段齐全；`star_lit` 按 `star_level_rule`。
6. **档案页**：编译器（判断模板渲染、固定衔接短语、惰性编译 `ensure_profile_page`、周报触发）接上本次的一致性检查；陈述文字不落库，读取时按判断现状渲染。
7. **测试**：集成测试（一次性 Postgres，合成学生、地图版本、题目族）覆盖 spec 全部验收场景；全库不变量；RLS 与删除级联；变异补上 advisory lock、间隔按 `occurred_at` 等库层判定。
