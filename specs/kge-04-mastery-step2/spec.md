# KGE-04 第二步：掌握写库、复测任务、服务接入、mastery.get / explain 与学情档案页

状态：**已确认（2026-10-01，用户“按默认建议，开始实现”）**，待人确认 #1–#12 按默认执行。实现 PR 首行 `[NEEDS-HUMAN]`（迁移、RLS、契约、掌握计算、画像可见范围）。
任务包：KGE-04 掌握 v0（开发计划 v2.0 冲刺 4 C 流）的第二步。设计正文以 `specs/mastery-estimate-sprint4/spec.md` 为准，下文只写第二步的范围、调研后新增的接口点与默认决定。第一步的产物与偏差见同目录 `lessons.md`。
关联：w13 阻塞项 B04（掌握未写库）；SIE-03 验证任务接口桩、`insight.predict` 的 `NoMasterySource`；5-C+D 联调复测桩；5-H 周报；H5 星图 v1（依赖本任务与 ADR 11）。

## 现状（2026-10-01 调研）
- **第一步已合入**：`services/learning/learning/mastery/`（config、evidence、bkt、gates、write、consistency），52 条变异全部被发现；没有任何库读写。
- **`platform.mastery_estimate`**：仍只有 `skill_local_id` 键。读它的地方有三处：
  - 学习服务 `plan.py` 的 `_mastery()`，按 `skill_local_id` 组 `MasterySnapshot.skill_node`；
  - 洞察服务经迁移 0028 只读（`mastery_insight_read`）；
  - 3-H 星图 BFF。
  另外 0035 删除登记表里它的导出列是 `subject, skill_local_id, state, next_retest_at, computed_at`。
- **洞察服务侧的两个占位**：
  - `dispute.py` 的 `StubVerificationScheduler`，只在 test 环境注入，其他环境不可用；
  - `predict.py` 的 `NoMasterySource`，`insight.predict` 因此一律回“证据不足”。
  `claims.MasteryRow` 已按第一步 spec 第 13 节定形（`node_id`、`cognitive_level`、`gate`、`p_mastery`、`confidence`、`evidence_ids`、`algorithm_version`、`computed_at`）。
- **联调复测桩**：`tools/e2e-cohort/replay.py` 的复测是桩（每生每科固定 7 天后一次，标“桩”）。证据包写明“第二步合入后切真实调度并重生样例”。
- **契约**：
  - `knowledge-service.v0.9.yaml` 有 `/v0/mastery.get`、`/v0/mastery.explain` 两个工具，知识服务回 501。按第一步 #22 的决定，由学习服务实现。
  - `learning-internal.v1.yaml` 只有 grade、diagnose、replay、plan、model/route，没有掌握、验证任务与档案页的入口。
- **迁移**：已用到 0036，本任务从 **0037** 起。迁移 0010–0036 均为草稿，由人执行。测试只在一次性库里跑。

## 设计要点（相对第一步 spec 的增量与落地方式）

### 1. 迁移 0037（一个文件，必停，先在副本演练）
- **`platform.mastery_estimate`**：
  - 加 `node_id`、`cognitive_level`、`map_version_id`；`skill_local_id` 改为可空，加 CHECK“两种键恰好一种”；
  - 节点键的当前记录加部分唯一索引；
  - `check_supersede_target` 放行按节点键比较（同学生、同学科、同节点键）；
  - 现有 RLS 不改；0028 的洞察只读策略照旧生效。
- **新表 `platform.retest_task`、`platform.profile_page`、`platform.profile_page_check`**：
  - 字段按第一步 spec 第 7、14 节；
  - ENABLE + FORCE RLS；`student_id` 外键 ON DELETE CASCADE；
  - 权限按第 15 节：learning 读写，BFF 按 `can_read_student` 只读，其他角色无权限。
- **删除登记**：
  - 三张新表登记进 0035 的 `personal_table_registry`（删除方式 cascade，导出列只含 ID、状态与时间，不含题目与作答）；
  - `mastery_estimate` 的导出列补 `node_id, cognitive_level`；
  - `79q_erasure_coverage` 补合成行，全库扫描仍零遗漏。
- **测试**：
  - DB 测试 `79s_mastery_step2.sql`：节点键约束、更正链、部分唯一索引、RLS 矩阵（本人、已绑定家长、兄妹、另一户、撤销绑定、各服务角色）、删除级联；
  - 权限快照、`docs/security/scope.md` 计数与攻击面清单同步更新；
  - 变异加进 `db/tests/mutations.sh`。

### 2. 学习服务：读、算、写（`learning/mastery/` 下新增 `store.py`、`recompute.py`、`jobs.py`）
- **读取层**：
  - 按第一步 lessons“第二步计划”第 2 条组装 `Evidence`：`item_tag` 按证据自身的地图版本解析，再经 `id_migration` 映射到当前版本；
  - 准入取“证据自身”与“题目当前登记”里更严的那个；
  - 计入口径照第一步 #23：只计有 `grade_result` 且有作答引用的证据。
- **写入层 `recompute(student, subject, node_id, level)`**：
  1. 取 `pg_advisory_xact_lock`；
  2. 重读当前记录；
  3. 依次过 `replay`、`write_decision`、`precommit_check`；
  4. 写入顺序：生成新 ID → 旧记录指向新 ID → 插入新记录；
  5. 同一事务里开、关、撤 `retest_task`（每单元每种 reason 最多一条 open）；
  6. 撞唯一索引重试一次，再失败留给对账。
- **接入**：
  - `grade`、`diagnose` 的落库事务提交后，另起事务同步重算涉及的单元；失败只记日志与指标，接口照常 200（第一步 #6）；
  - 幂等重放不触发重算。
- **其他入口**：
  - `recompute_item(subject, item_id)`，供 4-G 准入变化时调用；
  - `recompute_map(subject, from, to)`，供地图版本切换时调用；
  - 对账入口 `python -m learning.jobs mastery-sweep`：重算、衰减、任务缺的补多的撤、输出计数；时钟可注入。
- **`schedule_verification(student, subject, node_id, level, reason, dispute_ref)`**：只建 `verify_self_report` 任务，不改掌握；同一异议重复调用返回同一组任务（幂等）。
- **能力视图汇总**（`skill_view_aggregation`）：读时计算，不写库。
- **没有签字配置的学科**（知行、文砺）不写；测试环境用合成配置。

### 3. 接口（契约变更，必停）
在 `learning-internal.v1.yaml` 增加三组内部接口（小版本 1.1.0），只供编排层、BFF 内部与洞察服务以服务身份调用：
- `POST /internal/v1/mastery/get`、`POST /internal/v1/mastery/explain`：
  - 请求与响应形状逐字照搬 `knowledge-service.v0.9.yaml` 的两个工具，信封五字段齐全；
  - `star_lit` 按 `star_level_rule`；
  - 知识服务继续回 501（#2）。
- `POST /internal/v1/verification`：SIE-03 的“我其实会”调用它建验证任务。返回任务 ID 与 `item.similar` 选题入口所需的节点与层次，不返回题目内容。
- `POST /internal/v1/profile-page/ensure` 与读取接口：会话开始与周报时调用（#6）。
- 适配器侧的插件 API 不改。`plan` 的 `MasterySnapshot` 仍按 `skill_node` 给：由节点键行经 `kge.skill_link` 读时汇总；旧 `skill_local_id` 行（只剩 3-H 合成种子）继续读（#4）。

### 4. 洞察服务与联调切换
- **`VerificationScheduler` 真实实现**：调学习服务 `/internal/v1/verification`；接口桩仍只留给 test 环境。指标里桩与真实分开计数，已有逻辑不变。
- **`MasterySource` 真实实现**：直接读 `platform.mastery_estimate` 的节点键当前行（0028 已授权只读）。这样 `insight.predict`、`i_know_outcome` 与 SIE-02b 的节点类判断都开始有依据行。
- **5-C+D 联调切换**：复测改由 `retest_task` 驱动（注入时钟推进到 `due_at`），去掉“（桩）”标注，重生合成样例；Gate 0 证据包的复测栏改为真实调度。
- **3-H 星图不改**：节点键行不会出现在旧星图上。H5 星图 v1 另起，依赖本任务与 ADR 11。

### 5. 学情档案页（第一步 spec 第 14 节落地）
- **编译器**：
  - 以 `starbuddy_learning` 身份只读 `insight.claim`，读不到的判断不进页；
  - 每条陈述由判断模板渲染，用洞察服务 `RenderedStatement` 的同一套规则；
  - 衔接语只用固定短语表；
  - 调第一步的一致性检查，不过就降级为纯模板版本，并写一条 `profile_page_check`。
- **惰性编译**：
  - `ensure_profile_page` 比较 `claims_digest`，判断没变就不重编；
  - 会话开始时超出时间预算，返回纯模板版本；
  - 作答与重算都不触发编译。
- **存储**：只存引用（`claim_id`、模板键与版本）与衔接语，陈述文字不落库，读取时按判断现状渲染。学生与家长视图由 BFF 按判断的前端可见范围过滤（第一步 #18 的默认）。
- **不调任何模型**（第一步 #19）。

### 6. 测试
- **单测**：读取层组装、写入判定接线、对账计数。
- **集成测试**（一次性 Postgres，执行 0010–0037；合成学生、地图版本、题目族、合成配置）：
  - 覆盖第一步 spec“验收标准”的全部库相关场景：刷同题、证据等级、待复核、未准入、未签字、自述、衰减、正常路径、复测任务、退级、地图版本、并发、重算失败不影响落库、档案页四项；
  - 每个测试结束时跑全库不变量。
- **变异**（追加到 `mastery_mutations.py`）：去掉 advisory lock、间隔改按 `occurred_at`、复测任务不在同一事务、重放触发重算、档案页把未成立的判断放进学生视图，都必须被发现。
- **洞察服务**：真实 scheduler 与 MasterySource 的契约测试；`insight.predict` 在有节点行时不再回“证据不足”。
- **完成定义**：禁用推断与陈述—判断一致性测试接入 `make check`。全部数据合成，不读金标与留出集。

## 分 PR（每个 PR 单独审查、严格检查、提交）
1. **迁移**：0037、79s、79q 补行、删除登记、快照、攻击面。
2. **学习服务读算写**：读取层、`recompute`、复测任务、grade / diagnose 接入、`recompute_item`、`recompute_map`、对账入口、`schedule_verification`；`plan` 改读节点键行。
3. **接口与切换**：契约 1.1.0 三组接口、`mastery.get` / `explain`；洞察服务真实 scheduler 与 MasterySource；联调去桩并重生样例。
4. **学情档案页**：编译器、`ensure`、读取与 BFF 过滤、周报触发。

## 范围
### 做
上面四个 PR 的全部内容；lessons（实现记录、新的待确认项、签字栏沿用第一步）。
### 不做
- 教研数值（签字前最多写到“稳定”；仓库内没有签字值）；题型列（第一步 #4，未决前真实数据到不了“稳定”）；
- H5 星图 v1、能力视图侧栏、点亮动效；复测选题与今日计划编排的改动（只换数据源）；
- 调度器部署（ADR 03 / 10）；计量变更（复测、验证题扣不扣额度仍是第一步 #21）；
- 衔接语接入模型；IRT、FSRS；插件 API 变更。

## 验收标准（可测试）
- [ ] 第一步 spec“验收标准”中依赖库表的各条，全部在一次性库的集成测试里通过。
- [ ] 迁移 0037 的约束、RLS 矩阵与删除级联由 79s 覆盖；79q 全库扫描零遗漏；权限快照与攻击面同步更新；变异全部被发现。
- [ ] `grade`、`diagnose` 之后同步出现节点键掌握记录与复测任务；注入重算异常时接口仍 200，之后对账补上。
- [ ] 三组内部接口符合契约 1.1.0，信封五字段齐全；契约测试与 `make contract-test` 通过。
- [ ] 洞察服务在非 test 环境使用真实 scheduler；“我其实会”产生真实 `verify_self_report` 任务且不改掌握；`insight.predict` 有节点行时给出结果。
- [ ] 联调证据包里不再有“桩”；合成样例已重生，并且确定性输出。
- [ ] 档案页：引用、拦截程度升级、惰性编译、边界四项全部通过；学生与家长视图里没有“待验证”判断。
- [ ] 严格 `make check`、契约审查、安全审查通过。

## 涉及的必停项
- 迁移、RLS 与权限：PR 1。
- `packages/contracts`：PR 3，learning-internal 1.1.0。
- 掌握计算的接线：PR 2，算法沿用第一步，数值不签。
- 画像可见范围：PR 4，档案页对学生与家长的过滤。
- 不涉及：计量；人设 Prompt；新增模型供应商。

## 待人确认（括号内为默认）
1. **迁移编号与形态**：0037 一个文件，内含 `mastery_estimate` 改键与三张新表。另一个选择是拆成两个文件。
2. **`mastery.get` / `explain` 的挂载**：学习服务内部接口，形状照搬知识服务 v0.9，知识服务继续 501（沿用第一步 #22）。另一个选择是由知识服务代理转发。
3. **验证任务的入口**：SIE-03 经学习服务内部接口 `/internal/v1/verification` 建任务。另一个选择是给洞察服务 `retest_task` 的受限写权限（会违背“学习服务唯一写个人层”）。
4. **`plan` 的掌握快照**：节点键行经 `skill_link` 读时汇总成 `skill_node`，与旧 `skill_local_id` 行并读。另一个选择是插件 API 改成按节点给快照（契约变更）。
5. **洞察服务读掌握行**：直接读 `platform.mastery_estimate`，用 0028 已有的只读授权。另一个选择是经学习服务 `mastery.get`（多一跳，好处是只认一个接口）。
6. **档案页的触发者**：会话开始由 BFF 壳调 `ensure`；周报由洞察服务生成前调 `ensure`，失败不阻塞周报。另一个选择是周报不触发编译，直接读判断账本。
7. **档案页导出口径**：数据导出只导引用与时间，不导渲染文字（文字随判断现状变化）。另一个选择是导出时渲染一份快照。
8. **重算失败的可观测性**：只记计数与单元 ID，不记证据内容（规则 8 与日志口径）。
9. **联调样例重生**：PR 3 合入后在干净 worktree 重生并替换仓库内的合成样例；Gate 0 证据包锁定的提交号随之更新，并在 `gate-0-evidence.md` 记一笔。
10. **旧 `skill_local_id` 行**：不迁移、不删除，只保留 3-H 合成种子。生产库里不应存在这种行，迁移演练时由人核对。
11. **对账任务的运行身份**：`starbuddy_learning` 一次性入口（沿用第一步 #7），接入调度器等 ADR 03 / 10。
12. **PR 顺序**：1 → 2 → 3 → 4 顺序合入。另一个选择是档案页（PR 4）与 PR 3 并行。
