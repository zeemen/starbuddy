# 阶段 1 · 1 月：错题再练（闭环）

状态：**已确认（2026-10-05，用户“按默认建议，开始实现”）**，待人确认 #1–#13 按默认执行。PR 3 首行写 `[NEEDS-HUMAN]`（计量扣次规则）；其余 PR 默认不改契约、不改迁移，如果评审要求改，按“涉及的必停项”另起 PR。
任务包：开发计划 v2.0 第七节“阶段 1 月度计划”，1 月“产品与工程”列中的“错题再练”。开发计划没有给它编号，暂用目录名 `practice-loop-stage1`，编号待人定（#1）。服务对象是 1 月寒假封测，约 100 户，数学 + 道法。
关联：
- **总纲 v1.1**
  - 学生端功能含“错题再练”“先练后看答案”；
  - 主流程“每周巩固与错题变式 → 2–4 周延迟复测”；
  - 4.4“高级提示或看过讲解后做对只记‘辅导有进展’并自动安排无提示变式”；
  - 4.6“错题本 / 错因库 = `attempt + error_attribution`，错题是带归因的作答记录”；
  - 5.8 计量。
- **KGE V1.1**
  - 第五章“平台内原生作答（诊断、练习、变式、复测）是主通道”；
  - 第六章证据四类、来源权重、星亮防“刷同题”；
  - 第八章 `item.search / item.similar → generate_variation、plan`、`evidence.write`（Agent 不直接改掌握度）。
- **SIE V1.1**
  - 第五章证据权重表：高级提示后做对“自动安排无提示变式”；独立做错“按错因类型下调”；
  - 中圈指标“迁移差”的原料是变式与跨章题；
  - 第八章“我其实会”需要 2 道验证题，其中 1 道变式；“那次是粗心”只记录、不改判。
- **已合并的实现**
  - 插件 API v1.0 `generate_variation`（选题，不生成）；
  - 知识服务 `item.similar` / `item.search`；
  - 学习服务 grade 写入、KGE-04 掌握重算与 `retest_task`；
  - SIE-02a 诊断会话：学习服务内部接口与 BFF 会话计量的先例；
  - SIE-03 异议；
  - metering-v2 的题库自练 `bank_practice`。
- **相关 lessons**
  - `specs/sie-03-dispute-reconcile/lessons.md:141`（“粗心”异议触发同节点练习未做，待编排层定接口）；
  - `specs/kge-04-mastery-step2/lessons.md:188, 203, 209`（验证任务只有任务引用、没有题目；编排层尚未实现）；
  - `specs/mastery-estimate-sprint4/spec.md:220, 454`（复测选题不归掌握模块；复测与验证题扣不扣额度待定）；
  - `specs/adapter-aya-methods-sprint4/spec.md:268`（generate_variation 留给阶段 1 的“错题再练闭环”）。
- **CLAUDE.md**：规则 1、2、3、4、6、8、11、12、13；必停清单中的计量与权益、契约、迁移、掌握计算。

## 现状（调研结论：现在一道错题之后系统做什么、缺什么）

### 一道错题之后，现在实际发生的事
1. **作答落库**。学生作答经 BFF → 学习服务 `POST /internal/v1/grade` → 适配器判分（验证器或 Rubric）。学习服务在一个事务里写 `attempt`、`evidence_event`、`grade_result`、`error_attribution`（`services/learning/learning/service.py:147-243`）。
   - `ingest.purpose` 只有 `practice / retest / transfer_check`，由编排层给出，客户端不能指定（`packages/contracts/openapi/learning-internal.v1.yaml:771-776`）。
   - 用途经 `attributes.py:22` 映射成证据情境：`practice → independent`。
2. **同步重算掌握**。提交后另起事务同步重算涉及的单元，失败只记计数（`service.py:44-61, 209-210`；`mastery/recompute.py:329-338`）。
   - 独立做错算作计入证据，**并占用题目族**（`mastery/evidence.py:183-184`）。之后同族题再做对，记 `repeat_family` 不计入（`mastery/gates.py:213-216`）。
   - 高级提示后做对：不计入，排一条 `retest_task(reason = hint_followup)`（`evidence.py:162`；`gates.py:298-300`）。这就是 SIE 说的“自动安排无提示变式”，**但没有任何消费方**。
   - 已亮的星做错，`p` 跌破稳定层下限时，把 rung2 提前到当下（`reason = early`，`gates.py:242-245`）。
   - **独立做错本身不产生任何任务**。`retest_task.reason` 的 CHECK 只有 `scheduled / early / decay / hint_followup / verify_in_platform / verify_self_report`（`db/migrations/0037_platform__mastery_node_keys.up.sql:95`）。
3. **之后不再有动作**。系统里没有“错题本”，没有“再练”入口，也没有人调用变式选题。

### 缺什么
- **编排层没有调用 generate_variation**。三科适配器都已实现：
  - 阿鸦：`adapters/aya/src/methods/variation.ts:53-122`；
  - 知行：`adapters/zhixing/src/adapter.ts:615-670`；
  - 文砺：`adapters/wenli/src/adapter.ts:694-760`，内容包没有错因标注，`same_error_category` 恒为 false。

  但学习服务、BFF、前端都没有调用方：全仓库检索只有适配器自身和测试引用它。契约规定 `item_family` 由“编排层”从 `item.similar` 取（`plugin-api.v1.0.yaml:1395-1401`），而编排层没有实现（`specs/kge-04-mastery-step2/lessons.md:209`，待人确认 V）。
- **`item.similar` 不能直接当“变式”用**（`services/knowledge/knowledge/service/reader.py:185-195`；`tools.py:260-267`）：
  - 它只按“同题目族或同主节点”取，**同族排在前面**，`limit` 最大 20（`knowledge-service.v0.9.yaml:537`）；
  - 不按认知层次过滤，不排除学生做过的题，也不看平台准入（只看 `tag_review_status = approved`，`reader.py:108`）；
  - 同族的题做对后不计入掌握，见上文第 2 点。所以直接拿它的前几道当再练题，会出现“做对了但掌握不动”。
- **今日计划给不出具体的题**：
  - `plan` 只把掌握快照发给适配器（`services/learning/learning/plan.py:108-112`），请求里没有近期错题；
  - 平台摘要 `PlanDigestTask` 没有 `item_refs`（`learning-internal.v1.yaml:860-875`），所以今日计划只能显示“练习 / 复测”的类型与目标节点。
- **前端没有作答入口**：
  - BFF 学生端只有诊断会话的作答路由（`services/bff/src/http/feature-routes.ts:147-164`）；
  - 壳路由不调任何计量函数（`services/bff/src/http/shell-routes.ts:8`）；
  - 数学科目页写明“作答、诊断、批改等消耗类入口在冲刺 5 联调”（`apps/web/src/subjects/math/Entry.tsx:1`）。
- **取不到题面**：
  - 插件 API 与知识服务都不返回题干，`present_item` 契约至今未加（SIE-02a 待人确认 #2，`specs/sie-02a-math-diagnosis/spec.md:42`）；
  - 学习服务只有测试环境的合成题面（`services/learning/learning/diagnosis/presenter.py:1-5, 43-61`）。

  这是真实封测的**前置缺口**，不是本任务能补的。
- **真实内容下候选为空**：阿鸦 721 道题目前全部未准入，真实内容包下变式一律 `no_admitted_candidate`（`adapters/aya/src/methods/variation.ts:7`）。KGE-04 联调里计入的证据也是 0 条（`kge-04-mastery-step2/lessons.md:189`）。准入决定由人执行（4-G）。
- **计量没有接上**。插件 API 把 grade 列为消耗类，要求 `usage_id` 与 `idempotency_key`（`plugin-api.v1.0.yaml:70-75`）。BFF 的 `meteredCall` 只接通了 `diagnosis_screen / diagnosis_full / mistake_analysis`（`services/bff/src/metering/meter.ts:69-74`）：
  - `mistake_analysis` 的语义是“新上传错题深度分析”，不是平台内再练；
  - 迁移 0015 已有题库自练 `platform.meter_practice`（按次、不进额度账本、留存态每天 5 次，`db/migrations/0015_platform__metering_v2.up.sql:455-497`；“自练不是消耗类”见 `specs/metering-v2/lessons.md:29`），但没有任何调用方。

  平台发起的复测与验证题扣不扣额度至今未定（`mastery-estimate-sprint4/spec.md:454`，#21）。
- **SIE-03 的两处悬空**：
  - “那次是粗心”承诺的“触发一次同节点练习”没做：0033 的动作没有“练习”编码，又缺编排接口（`sie-03-dispute-reconcile/lessons.md:141`）；
  - “我其实会”的验证任务只有任务引用 `<学科>:verify-task-<ID>`，没有真实题目（`kge-04-mastery-step2/lessons.md:188, 203`，待人确认 Q）。
- **可以直接复用的部分**：
  - 学习服务已有知识服务客户端，`caller = learning` 可调 `item.search / item.similar`（`learning/config.py:12-14, 137-140`；`knowledge/service/tools.py:28-33`）；
  - 已有通用的适配器调用 `call_adapter`；
  - 诊断会话给出了“学习服务内部接口放服务目录、BFF 会话计量、题面白名单”的先例：`services/learning/openapi/diagnosis-internal.v0.yaml:1-2`、`services/bff/src/diagnosis/service.ts:207-245`。

## 设计要点

### 0. 原则
- **再练不是新的掌握逻辑**。再练题的作答走现有 grade 写入路径（规则 1、3），证据由学习服务落库，掌握由 KGE-04 重算。本任务不改证据权重、门槛、复测调度与 BKT 参数，不写判断账本（规则 2、11）。
- **错题本不存副本**。“错题”与“再练状态”都由 `attempt`、`grade_result`、`evidence_event`、`kge.item_tag` 读时计算，不新增表（规则 2：不存可推导的副本）。这样也就**不需要迁移**，删除与导出范围不变。
- **只做客观判分的题**。首发只覆盖验证器判分或确定性 Rubric 判分的题（阿鸦验证器题、知行客观题），不调模型（#7）。主观题判分走模型并一律待复核（`adapters/zhixing/src/route-client.ts:5`），在掌握计算里不计入，放进再练也闭不了环。
- **学科范围**：默认数学（aya）、道法（zhixing）开启，语文（wenli）留开关、默认关（语文切片 2 月过门）。英语不涉及。

### 1. 错题条目（读时计算，`services/learning/learning/practice/queue.py`）
- **单元**：一个条目对应一个“学生 × 学科 × 主节点 × 认知层次”（与掌握单元同粒度）。条目下挂这个单元里仍未闭环的错题作答，展示用的是原题。
- **进入条件**：一次作答同时满足以下全部条件，才成为错题：
  - 判分 `verdict ∈ {incorrect, partial}`；
  - `human_review_required = false`；
  - 证据等级 A 或 B，不是 C，也不在申诉中；
  - 输入通道是 keypad 或 canvas；
  - 题目在作答时的地图版本里有主节点与层次的打标；
  - 发生在 `lookback_days` 之内。

  拍照上传的错题不进（上传深度分析是另一项计量资源，KGE-05）。诊断会话里的错题默认也进（#8）。
- **闭环条件**：在该单元最近一次错题之后，同单元出现一次“独立做对”就闭环：
  - 必须是另一道题；
  - 判分 `verdict = correct`，且不待复核；
  - `hint_level_used = 0`，自报不是“猜的”。

  闭环只是再练状态，**不是掌握结论**：不写任何表，不对学生说“掌握了”。是否计入掌握由 KGE-04 自己判断。
- **轮次**：
  - 第 1 轮在错题落库后立即可做；
  - 某一轮再练做错，下一轮到 `round_delay_hours[k]` 后才开放；
  - 做满 `max_rounds` 轮仍未闭环，状态为 `exhausted`，界面只给中性提示“可以先看看讲解”。讲解入口随 teach 接入，本任务不做。

  再练做错本身也是一道新错题，但归入同一条目，不另开条目，避免越错越多。
- **状态**：`due`、`waiting`（下一轮未到）、`closed`、`exhausted`、`unavailable`（没有合格候选，附 `empty_reason`）。条目数超过 `queue_cap` 时，只返回最近的若干条。
- **一并读出 `hint_followup` 任务**：读开着的 `retest_task(reason = hint_followup)`（KGE-04 已建，只读，不写），作为同一类条目出现，来源标 `hint_followup`。这类任务的关闭仍由 KGE-04 重算负责：独立做对之后，`diff_tasks` 会自动关闭它。
  - `scheduled / early / decay` 复测轮、`verify_in_platform`、`verify_self_report` 任务不在本任务（#9）。
- **与复测轮的关系**：单元有到期的复测轮时，到期后第一道非提示的计入作答就决定本轮通过还是失败，不看作答用途（`gates.py:257-275`）。所以这时的再练作答同时就是复测。这是 KGE-04 的既有规则，本任务不改，只在条目上给出 `retest_due` 标记，前端照常显示“再练”。

### 2. 选题（`practice/select.py`，确定性）
1. **取候选**：
   - 用错题原题调知识服务 `item.similar`（`limit` 用满 20）；
   - 候选里同主节点、同层次的不同题目族少于 `variants_per_round` 时，再用 `item.search(node_id, cognitive_level)` 补足，`limit` 50；
   - 只留同主节点、同认知层次的题。
2. **排除**：
   - 学生在 `exclude_window_days` 内做过的题；
   - 原题；
   - `license_state = restricted` 的题；
   - 平台表上未准入的题。准入以平台表为准，适配器那边再过滤一次。
3. **偏好**：
   - 默认**不同题目族优先**，这样做对能计入掌握；
   - 没有不同族的候选时才用同族题。这时作答照常写证据，但掌握不变，条目上标 `same_family_only`（#5）。
4. **调适配器**：调 `generate_variation`（插件 API v1.0）。
   - 请求带 `source_item_ref`、`item_family`（上面的候选）、`exclude_item_refs`、`count`；
   - 错因编码只在 `error_attribution.subject_category` 存在、且不属于敏感类时传入；
   - 适配器只从候选里选已准入的题，没有合格候选时返回空数组加 `empty_reason`，**不生成新题**（契约语义）。
   - `idempotency_key` 必填；generate_variation 是非消耗类，`usage_id` 省略。
   - 签字前与 `plan` 一样，需要候选版开关（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`）。
5. **核对与签发**：
   - 学习服务核对返回的每道题都在候选集里、已在平台登记、已准入，否则这一轮记为 `unavailable`，不外露；
   - 之后签发**再练票据**：HMAC 签名，内容是学生、学科、单元、题目引用、轮次、签发时间，有效期 `ticket_ttl_minutes`。票据不落库，密钥来自环境变量。

### 3. 作答（`practice/answer.py`）
- 请求必须带票据。学习服务验签，并重新确认这个条目仍是 `due`，否则返回 409 `PRACTICE_NOT_DUE`，或 410 `TICKET_EXPIRED`。
- 然后组装 learning-internal v1 的 `GradeIngest` 信封，在进程内直接调用 `IngestService.ingest("grade", …)`，复用幂等、封存、核对、落库与重算：
  - `ingest.channel = keypad`，`ingest.purpose = practice`，由服务端写入，客户端不能指定；
  - `hint_level_used = 0`。本任务不提供提示，所以这是如实上报；提示随 teach 接入。
- 返回给 BFF 的只有：判分结论（来自 grade 摘要的 `verdict`）、条目新状态、下一轮开放时间。
  - **不含**掌握、置信度、证据 ID、错因，也不含任何解释文字（规则 3、11）。
  - 答案与解析不返回（`present_item` 白名单不含答案；“先练后看答案”的看答案随题面契约另定，#6）。

### 4. 学习服务内部接口（服务目录内，不进 `packages/contracts`）
- 沿用 SIE-02a 先例，写 `services/learning/openapi/practice-internal.v0.yaml`，只供 BFF 调用。鉴权与诊断接口相同：Bearer 服务令牌、常量时间比较、64 KB 上限、拒绝未知键与重复键。并入 learning-internal 契约另起 `[NEEDS-HUMAN]` PR（#3）。
- 四个接口：
  - `POST /internal/v1/practice/queue`：入参学生、学科；返回条目列表，每条有单元、状态、原题引用、轮次、下一轮时间、`retest_due`、`same_family_only`。
  - `POST /internal/v1/practice/next`：入参学生、条目；返回一道再练题：题目引用、题面（复用 `ItemPresenter` 白名单 `{format, stem, options}`）和票据。没有候选时返回 200 `status = unavailable` 加 `empty_reason`。
  - `POST /internal/v1/practice/answer`：入参是票据、作答、`idempotency_key`、`usage_id`；返回第 3 节的摘要。5xx 一律是结果未知，与 learning-internal v1 的规则相同。
  - `POST /internal/v1/practice/replay`：只查不执行。直接复用已有的 `GET /internal/v1/replay/grade/{key}`，不另建。
- 题面：非 test 环境没有真实题面提供者时，`next` 一律 501 `PRACTICE_UNAVAILABLE`，与诊断的做法相同。`present_item` 契约签字后再接真实题面（前置依赖）。

### 5. BFF 与计量（PR 3，必停）
- **路由只给学生本人**（`auth = 'student'`）：
  - `GET /students/{id}/practice?subject=`
  - `POST /students/{id}/practice/{entry}/next`
  - `POST /students/{id}/practice/answer`

  家长默认看不到再练列表（#10）；兄妹、另一户、撤销绑定一律 404。
- **计量默认**（#4）：每次再练作答先调已有的 `platform.meter_practice(student, key)`，也就是题库自练 `bank_practice`，再用它返回的 `usage_id` 与同一个 `idempotency_key` 调学习服务。
  - 付费与试用不限次，留存态每天 5 次；
  - `no_entitlement`、`daily_limit_reached` 返回 402 `QUOTA_UNAVAILABLE`；
  - 同 key 重试拿回同一个 `usage_id`，学习服务按幂等返回原结果，不重复写；
  - `next` 与 `queue` 不计量。

  理由：首发只覆盖客观题，不调模型。按 metering-v2 已记录的决定，题库自练“开始即计数、没有释放”，不属于预留 / 结算 / 回滚的消耗类（`specs/metering-v2/lessons.md:29`）。这仍是扣次规则的变更（新增一类动作的计量口径），属于必停项。
- **功能开关**：`practice`，默认关；限流桶 `practice.session.minute`；进路由矩阵与安全用例。

### 6. 学生端页面（PR 4）
- 位置：统一壳里每科一个“错题再练”入口与列表页，作答页复用诊断作答组件。只在开关打开时出现；合成数据下可走通。
- 文案只陈述事实：
  - 列表上是“这道题上次没做对”“再练一道同类题”“已做对”“可以先看看讲解”；
  - **不显示**错因、掌握度、星态、“薄弱”等推断，也**绝不**出现“粗心”（七科合同第 5 条）。
  - 所有文案先用占位，由产品审，并过 SIE-00 禁用推断词表检查（`services/bff/src/trust/lexicon.ts`）。
- 不改点点与七科老师的人设 Prompt，不出现人设话术。界面上的人设陪伴随人设迁移另做。

### 7. 配置位（`practice/config.py`，全部待教研签字）
- **数值**：`lookback_days`、`round_delay_hours[]`、`max_rounds`、`variants_per_round`、`exclude_window_days`、`queue_cap`、`ticket_ttl_minutes`。
- **开关**：`prefer_new_family`、`include_diagnosis_attempts`、`subjects_enabled`。
- **未签字时**：只允许 `LEARNING_ENV=test/dev` 使用合成值，非 test / dev 环境拒绝启动这一模块（返回 501），沿用掌握配置“未签字不上线”的做法。仓库里不写签字值。
- 占位默认，只用于测试，不是建议值：`lookback_days = 30`、`round_delay_hours = [0, 48, 120]`、`max_rounds = 3`、`variants_per_round = 1`、`exclude_window_days = 30`、`queue_cap = 20`、`ticket_ttl_minutes = 60`。

### 8. SIE-03 “那次是粗心”
- 默认不另建触发：那次作答如果判错，已经按第 1 节自动成为错题条目，“同节点练习”由本任务的通用流程提供。洞察服务“观察后续同类作答”照旧读证据，不需要新接口。
- 学习服务读不到 `insight.dispute`（0032 未授），本任务不给被异议的作答加优先级（#11）。

### 9. 观测与日志
- 计数：条目各状态数量、`unavailable` 按 `empty_reason` 分、票据拒绝、作答结果。
- 日志不记学生 ID、作答内容与题面（规则 8）。
- 为 Gate 1“8 周延迟复测改善”提供的只是证据，不在本任务算指标。

## 分 PR（每个 PR 单独审查、严格检查、提交）
1. **PR 1 纯函数与配置**：`learning/practice/config.py`、`queue.py`（条目推导、闭环、轮次）、`select.py`（候选过滤、排序、核对）、票据签发与验签。
   - 单测、性质测试（确定性；闭环单调：新增做对的证据不会把已闭环的条目重新打开，除非之后又出现新的错题）、变异测试。
   - 不连库，不发 HTTP。
2. **PR 2 学习服务读与接口**：读取层（一次性库上的合成学生与合成题）、知识服务与适配器调用、`practice-internal.v0.yaml` 与四个接口、作答接到 `IngestService`。
   - 集成测试，覆盖错 → 选 → 对 → 闭环、再错 → 下一轮、做满轮次、无候选、同族兜底、票据伪造或过期或跨学生、幂等重放、重算失败不影响作答，以及服务目录 OpenAPI 的一致性测试。
3. **PR 3 BFF 与计量**（`[NEEDS-HUMAN]`）：三条路由、`meter_practice` 接入、开关与限流、路由矩阵与授权用例、幂等测试（同 key 重试只计一次、结果未知用同 key 重试）、BFF 变异测试。
4. **PR 4 学生端**：列表页与作答页（开关默认关）、占位文案与禁用推断词表检查、E2E（合成数据走完一个闭环）、axe、视觉回归。

顺序合入：1 → 2 → 3 → 4。

## 范围
### 做
- 错题条目读时推导（含读出 `hint_followup` 任务）、变式选题与核对、再练票据、作答接入现有 grade 写入；
- 学习服务内部接口（服务目录 OpenAPI）、BFF 学生端路由与题库自练计量接入、学生端页面（开关默认关）；
- 配置位与合成配置、测试、变异与 lessons。

### 不做
- **契约与迁移**：
  - 不改 `packages/contracts`（插件 API、知识服务、learning-internal、洞察服务）；
  - 不改 `db/migrations`；
  - `retest_task` 不加新原因。
- **掌握与判断**：
  - 不改掌握计算、证据权重、门槛、复测调度、BKT 参数；
  - 不写判断账本，不新增关于学生的陈述。
- **其他任务的范围**：
  - `present_item` 题面契约与真实题面提供者（SIE-02a #2）；
  - 准入决定（4-G，人执行）；
  - 今日计划把再练排进跨科计划（1 月“今日计划跨科编排”另立 spec，本任务只提供 `queue` 接口给它用）；
  - `scheduled / early / decay` 复测与“我其实会”验证题的选题服务（选题组件做成可复用，#9）。
- **题目与内容**：
  - 主观题、拍照上传错题、语文（开关保留）、英语；
  - 提示与讲解（teach）、看答案与解析；
  - 生成新题。
- **人与配置**：
  - 家长端再练视图；
  - 任何人设 Prompt 或话术；
  - 教研数值签字。

## 验收标准（可测试）
- [ ] **错题判定**：合成作答下，满足第 1 节全部条件的作答才成为错题；有以下任一情况的作答不进：C 级、申诉、待复核、拍照、未打标、超出回看期、`partial` 以外的非错判（各有测试）。
- [ ] **闭环与轮次**：
  - 同单元另一道题独立做对，条目闭环；
  - 原题做对、提示后做对、自报“猜的”做对，都不闭环；
  - 再练做错，按 `round_delay_hours` 开下一轮；
  - 做满 `max_rounds` 轮为 `exhausted`；
  - 再练做错不另开条目。
- [ ] **选题**：
  - 只出同主节点、同层次、已准入、未做过、未受限的题；
  - 有不同族候选时不出同族题；
  - 只有同族候选时出同族题并标 `same_family_only`；
  - 没有候选时返回 `unavailable` 加 `empty_reason`，不生成新题；
  - 适配器返回候选集之外的题时，这一轮按不可用处理（变异测试能发现）。
- [ ] **作答**：
  - 只接受有效票据（伪造、过期、跨学生、跨学科都被拒）；
  - `purpose`、`channel` 只能由服务端写入，请求里给了按 400 处理；
  - 作答经现有 grade 路径落库并触发重算，返回内容里没有掌握、置信度、证据 ID、错因；
  - 同 key 重放不写新行。
- [ ] **掌握不受本任务影响**：在同一组合成证据上，有本任务和没有本任务时，掌握记录与复测任务逐字段一致（本任务只读、只经 grade 写）。
- [ ] **计量**：
  - 每次作答都带 `usage_id` 与 `idempotency_key`；
  - 同 key 重试只计一次；
  - 留存态第 6 次被拒；
  - `queue` 与 `next` 不计量；
  - BFF 去掉计量调用的变异能被发现。
- [ ] **授权**：学生本人可用；家长、兄妹、另一户、撤销绑定、停用账号都拿不到（库上与 BFF 用例）。
- [ ] **界面**：
  - 文案过禁用推断词表检查；页面与接口没有错因、掌握度、星态、“粗心”字样；
  - 开关关闭时没有入口，接口返回 404；
  - E2E 用合成数据走完错 → 再练 → 做对 → 闭环。
- [ ] **完成检查**：严格 `make check`、安全审查、契约审查（服务目录 OpenAPI）通过。数据全部合成，不读金标与留出集。

## 涉及的必停项
- **计量与权益（扣次规则）**：PR 3 新增“再练作答按题库自练计次”的口径，必停，PR 首行写 `[NEEDS-HUMAN]`，并跑幂等测试。
- **契约**：默认不改。学习服务内部接口放服务目录（沿用 SIE-02a 先例）。是否并入 `learning-internal`，另起 `[NEEDS-HUMAN]` PR（#3）。
- **迁移 / RLS**：默认不改（读时计算，不新增表）。如果评审选择“落库的错题条目”或“`retest_task` 新原因”（#2），另起迁移草稿，先在副本演练。
- **掌握计算**：不改。只读 `retest_task` 与掌握单元口径。
- **画像可见范围 / 判断**：不新增关于学生的陈述。列表只陈述作答事实，文案待产品审（#6）。
- **不涉及**：人设 Prompt 与语料、禁用推断词表本身、金标数据、新第三方服务或模型供应商、数据出境。

## 待人确认（括号内为默认）
1. **任务包编号**：开发计划 v2.0 的 1 月没有给编号（默认暂用目录名 `practice-loop-stage1`，由人定编号，例如 PLT 或 KGE 系列）。
2. **错题条目要不要落库**：默认读时计算，不新增表、不改迁移。另外两个选择：
   - 新表 `platform.practice_item`：可审计，但要迁移，还要登记删除范围；
   - 给 `retest_task` 加原因 `wrong_followup`：要迁移，并改重算的任务差异逻辑，属于复测调度，必停。
3. **内部接口位置**：默认放 `services/learning/openapi/practice-internal.v0.yaml`，与诊断接口一起，等以后统一并入 `learning-internal`（契约变更另起 PR）。另一个选择是本任务就并入 learning-internal 1.2.0，必停。
4. **计量口径**：默认按题库自练 `meter_practice` 计次，付费与试用不限，留存态每天 5 次，不预留、不结算。另外两个选择：
   - 按 `ai_active_time` 预留、结算分钟，更贴合“AI 学时”，但练题也消耗学时；
   - 平台发起的再练、复测、验证题一律不扣，需要改 0015 与 BFF 计量规则，必停；这个选择与 `mastery-estimate-sprint4` #21 一并定。
5. **同族兜底**：默认不同族优先，没有不同族时才出同族题，并标 `same_family_only`（做对能闭环，但不计入掌握）。另一个选择是只出不同族的题，没有就 `unavailable`：更严格，但真实内容少时，很多错题会无题可练。
6. **展示与看答案**：默认列表只陈述事实，不显示错因与答案、解析。“先练后看答案”等题面契约（`present_item`）确定是否带答案、解析之后再接。文案由产品审，并过禁用推断词表检查。
7. **题型范围**：默认首发只覆盖客观题（验证器或确定性 Rubric），主观题不进再练。另一个选择是放进主观题，但它们判分一律待复核，闭不了环，还要按 AI 资源计量。
8. **诊断错题**：默认诊断会话里的错题也进入再练。另一个选择是只收练习用途的作答，诊断错题留给诊断报告与今日计划。
9. **复测与验证题的选题**：默认本任务只读出 `hint_followup` 任务（与“无提示变式”同义），`scheduled / early / decay / verify_*` 的选题交给“今日计划跨科编排”，复用本任务的选题组件。另一个选择是本任务一并服务所有复测任务，范围会扩大到复测调度的呈现。
10. **家长可见**：默认家长看不到再练列表。家长端 v1（1 月另立）是否显示“本周再练了几道、做对了几道”这类计数，由家长端 spec 定；那是关于学生的陈述，要经判断账本。
11. **“粗心”异议**：默认不单独触发，被异议的作答若判错，已在再练队列里，不加优先级。另一个选择是洞察服务经新接口通知学习服务优先这道题（新接口，契约变更）。
12. **数值签字**：第 7 节全部配置位，由数学与道法教研签字。签字前只在 test / dev 环境用合成值，封测环境不开。
13. **前置依赖的排期**：真实封测要用起来，前提是 `present_item` 题面契约（SIE-02a #2）与准入决定（4-G）已落地。默认本任务先用合成题面与合成准入完成并验收；这两项的负责人与日期由人定。
