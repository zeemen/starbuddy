# 冲刺 2-G：统一复核队列（低置信、申诉、抽检、画像异议）

状态：**已确认（2026-09-29，用户“按默认建议，开始实现”）**，待人确认 #1–#7 按默认执行；迁移 0023 为草稿，由人执行。PR 首行 `[NEEDS-HUMAN]`。
任务包：开发计划 v2.0 冲刺 2（W5–W6）G 流“统一复核队列：低置信、申诉、抽检、画像异议四类进同一队列”，验收“三科各一条记录走完闭环”。
关联：迁移 0006（`platform.review_task`、`resolve_review_task`）、0001（`review_reason`）、0005（grade_result 复核约束与 supersede 规则）、0010（判断账本）、0020（staff 登录）；
审核台（`services/learning/review_console/`、`apps/web/src/app/review/`，`specs/release-gate-sprint4/` 设计要点 3：沿用同一套 staff 登录与签字模式）；
完整方案 v1.1 :465、:502、:542、:663；SIE V1.1 异议三路（:515-518、:566）；CLAUDE.md 规则 3、11、13。

## 现状（调研结论）
- 表已有：`platform.review_task`（source_kind 只有 grade_result / skill_observation / error_attribution；open → in_progress → resolved；同一对象一条；结案函数只写结案码）。
- **学习服务从不插入 review_task**：判分只把 `human_review_required` 与原因写进 grade_result；也没有把状态改成 in_progress 的函数。
- 申诉：契约 `GradeRequest.appeal` 与学习服务的 C 级处理都已有，阿鸦会带 `student_appeal`；BFF 与前端没有申诉入口（“我不同意”在冲刺 5 的 4-H）。
- 画像异议：`insight.dispute` 表与洞察服务都还没有（SIE-03）；`review_task` 不能指向判断，`starbuddy_review` 对 insight.* 没有权限。
- 计划没有给抽检比例、复核时限、复核结果怎样回写的数字。

## 设计要点
### 1. 进队（四类，同一张表）
- **低置信与主观**：学习服务写入判分时，`human_review_required = true` 就在**同一事务**里插入 `review_task(source_kind = grade_result, reasons = 判分的原因)`；重放不重复插入（UNIQUE 已保证）。三科都走这条：阿鸦（验证器边界、未准入）、知行（主观维）、文砺（主观维、卷面不可判）。
- **申诉**：`GradeRequest.appeal = true` 的判分带 `student_appeal`，同上进队；本任务不做 BFF / 前端入口（4-H），测试直接经学习服务。
- **抽检**：新函数 `platform.review_sample_spot_checks(p_subject, p_since, p_ratio)`（SECURITY DEFINER，只授调度角色 `starbuddy_scheduler`）：按 grade_result id 的哈希确定性抽样，没有复核任务的判分按比例进队，原因 `spot_check`；默认比例 5%（待人确认 #2）。
- **画像异议**：`review_task.source_kind` 加 `claim`，`review_reason` 加 `profile_dispute`；新函数 `platform.enqueue_claim_dispute(p_claim, p_kind)` **只授洞察服务**（判断与异议路由归洞察服务，规则 11），写入时核对判断存在且非终态。
  本任务不建 `insight.dispute` 表、不做异议三路的自动处理（SIE-03），只让洞察服务能把“需要人看”的异议放进同一队列。

### 2. 处理（审核台，沿用 staff 登录与签字模式）
- 新列 `review_task.assigned_to`；函数 `platform.take_review_task(p_task)`：open → in_progress，记领取人（active staff），已被别人领取回 409；`release_review_task` 放回。
- 结案码白名单表 `platform.review_resolution_code`（`upheld` 维持、`regrade_required` 需改判、`content_issue` 题目或 Rubric 有问题、`dispute_forwarded` 已转洞察服务、`not_actionable`），`resolve_review_task` 只收白名单里的码，只能由领取人结案。
- 审核台后端：`GET /review/v0/queue`（按学科、原因、状态筛选，分页）、`POST …/{id}/take`、`…/release`、`…/resolve`；前端 `/review/queue` 列表与详情。
  **可见范围**：只显示学生的不透明指针（student_id 截断哈希）、学科、题目 ID、原因、维度分与置信度、证据 ID；不显示作答原文（原文在证据表里，按需另起任务、单独授权）。

### 3. 回写（本任务只做到“有记录、有依据”）
- **不改分、不改掌握、不改判断**：`regrade_required` 结案后，改判由判分侧按 0005 的 supersede 新写一条 grade_result（已结案的 review_task 是它的依据）；新判分的来源字段怎么填属于判分逻辑，另起 spec。
- `dispute_forwarded` 结案只写码；判断的流转（to_verify、withdrawn）由洞察服务做（规则 11），洞察服务上线前不回写。

### 4. 闭环验收（“三科各一条记录走完闭环”）
- 学习服务端到端：阿鸦、知行、文砺各一道合成题判分 → 自动进队 → staff 经审核台后端领取 → 结案（码在白名单）→ 任务 resolved、结案人与时间齐全；另加一条申诉、一条抽检、一条画像异议（以洞察服务角色在库测试里入队）。
- 前端端到端：staff 登录后看到队列、领取、结案。

## 范围
### 做
迁移 0023（platform：枚举与 source_kind 扩展、assigned_to、领取 / 放回 / 抽检 / 异议入队函数、结案码表、结案函数加白名单与领取人核对）；DB 测试、变异、快照、攻击面；
学习服务进队；审核台后端队列接口与前端页面；端到端；lessons。
### 不做
BFF / 前端的申诉与异议入口（4-H、冲刺 5）；`insight.dispute` 与异议三路自动处理（SIE-03）；人工改判写回与重算（判分 spec）；复核时限提醒；
打标低置信队列（KGE-02 另一条队列）；一键停用评分版本并重算。

## 验收标准（可测试）
- [ ] 需要复核的判分在同一事务里进队，重放不重复；三科各一条；申诉带 `student_appeal` 进队。
- [ ] 抽检确定性、按比例、不重复进队；只有调度角色能调用。
- [ ] 画像异议只有洞察服务能入队，判断不存在或已终结时拒绝。
- [ ] 领取、放回、结案：只有 active staff；别人领取的不能领、不能结案；结案码只收白名单；BFF 读不到队列。
- [ ] 审核台列表不含作答原文与任何可识别信息；三科闭环端到端通过；严格 `make check`、安全审查、契约审查通过。

## 涉及的必停项
- **db/migrations 与 RLS**：迁移 0023（草稿，人执行）。**判分逻辑**：复核结果回写的口径（本任务只到结案码）。**画像可见范围**：异议入队与审核台可见字段。PR 首行 `[NEEDS-HUMAN]`。
- 契约：否（审核台是服务内部接口）；计量、人设、第三方：否。

## 待人确认
1. **进队范围**：默认本冲刺只让判分（grade_result）进队；能力观察与错因的复核随掌握 spec（KGE-04）。
2. **抽检比例与频率**：默认 5%，按学科、按天调用；KGE 的一致率抽检目标（客观 99%、步骤 90%、采分点 90%）要教研定抽样口径。
3. **画像异议入队**：默认只提供洞察服务可调用的入队函数，不建 dispute 表；另一个选择是本任务就按 SIE 设计稿建 `insight.dispute`（扩大到 SIE-03 的范围）。
4. **审核台可见字段**：默认不显示作答原文、只显示不透明学生指针；教研判主观题需要看原文时，另起任务做单条授权查看（附审计）。
5. **结案码**：默认五个（维持、需改判、题目或 Rubric 问题、已转洞察服务、无需处理）。
6. **领取与超时**：默认领取后不自动超时释放，只能本人放回；以后按时限加提醒。
7. **角色**：默认沿用 `starbuddy_review`（读队列、领取、结案）；是否拆出只签字的角色（release-gate lessons :69）以后再定。
