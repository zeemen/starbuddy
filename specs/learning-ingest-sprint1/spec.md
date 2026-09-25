# 学习服务 · 冲刺 1-C：学习证据与推断的写入接口

状态：**草稿，等人确认后才开始实现**（CLAUDE.md：先 spec，确认后再动手）。确认人、日期：＿＿＿

关联：
- 开发计划 冲刺 1-C：“evidence_event、attempt、error_attribution 写入接口；事件幂等键”，验收是“重复事件不重复入库”
- ADR 01（只有学习服务能写学习者模型）、ADR 03（显式状态机）、ADR 05（分区与幂等键）、ADR 06（计量）
- 契约 `packages/contracts/openapi/plugin-api.v0.9.yaml`
- 学习者 Schema v0.9（`db/migrations/0001`–`0006`），以及 `specs/learner-schema-v0.9/spec.md` 待人确认 #2、#12、#13
- 阿鸦适配器 `specs/adapter-aya-sprint1/lessons.md` 待人确认 #6、#7、#8

## 目标
BFF 发来一次作答或诊断请求，学习服务要做三件事：
1. 调用对应学科的适配器；
2. 按契约和平台规则核对适配器的结果；
3. 在**一个事务里、幂等地**把证据和推断写进 `platform.*`。

写入的表包括：`evidence_event`、`attempt`、`grade_result`、`grade_dimension`、`error_attribution`、`skill_observation`。

完成后，冲刺 1-D 的阿鸦链路就是真正的端到端：BFF → 学习服务 → 阿鸦适配器 → 平台库。现在阿鸦端到端测试里模拟落库的 `adapters/aya/tests/e2e/persist.ts` 可以删掉。

## 范围

### 做
1. **服务骨架** `services/learning/`：Python 3.11+、FastAPI、asyncpg，测试用 pytest。依赖与许可证见待人确认 #1。
2. **两个内部接口**：只供 BFF 调用，用服务间令牌鉴权。
   - `POST /internal/v0/grade`：请求体就是契约的 `GradeRequest`。
   - `POST /internal/v0/diagnose`：请求体就是契约的 `DiagnoseRequest`，只支持带 `attempts` 的同步诊断。
   - 两个接口都返回平台摘要：`attempt_id`（或多个）、`grade_result_id`、`evidence_ids`、`verdict`、`semantics`、`human_review_required`，**不原样转发**适配器的响应。
   - 接口定义写成 OpenAPI，放在哪里见待人确认 #2。
3. **调用适配器**：
   - 按 `context.subject` 路由到适配器地址，地址与令牌来自配置。
   - 超时：grade 10 秒、diagnose 30 秒。
   - 只在连接失败，或适配器返回 `retryable: true` 时，用**同一个** `idempotency_key` 重试一次。
   - 适配器返回 202（异步任务）时，本冲刺返回“不支持”，不落库。异步任务是冲刺 3 英语的事。
4. **结果核对**。任何一条不通过，就整体拒绝，一行都不写，并记录告警。核对项：
   - 按契约 schema 校验响应：直接加载契约 YAML，和阿鸦适配器用同一份。
   - **证据 ID 闭合**：`result` 里引用的每个 evidence_id，都必须在本次响应的 `evidence` 里，或在请求的 `prior_evidence` 里。
   - **学科一致**：证据的 `item_ref`、`skill_node` 和推断的 `skill_node`，命名空间都等于 `context.subject`。
   - **已登记**：题目和能力点都在 `platform.item`、`platform.skill_node` 里。
   - **准入以平台为准**：证据的 `admission` 按 `platform.item.admission` 重写，只能往更严的方向改。
   - **效力以平台为准**：`semantics` 按全部证据的准入状态重新判定。适配器声明的只能更严，不能更松（契约 PluginResultBase 平台侧规则）。判定为 practice 时，补上 `human_review_required` 和 `content_not_admitted`。
   - **学生以请求为准**：学生只取请求里 BFF 给的 `context.student_ref`，不读适配器响应里的任何学生信息。
5. **幂等**。这是冲刺 1-C 的验收核心，也是阿鸦 lessons #6 提出的硬性要求：
   - 新增一张**不分区**的请求幂等表，主键是 `(method, idempotency_key)`。表里记录：
     - 请求体指纹（不含 `request_id`）；
     - 学生；
     - 状态：`pending`、`succeeded` 或 `failed`；
     - 平台生成的 `attempt_id`；
     - 结果摘要。
   - 各种情况下的行为：
     | 情况 | 行为 |
     |---|---|
     | 同 key、同指纹，已成功 | 直接返回存下的结果摘要，**不再调用适配器、不写任何行** |
     | 同 key、不同指纹 | 返回 409 `IDEMPOTENCY_CONFLICT` |
     | 同 key 并发到达 | 只有一个请求执行，其余等它完成后拿到同一结果 |
     | 同 key 处于 `pending`，但持有者已超时（例如进程在中途崩溃） | 由新请求接管、重新执行。适配器的证据 ID 是确定性派生的，重做结果相同 |
   - 新增一张**不分区**的证据 ID 登记表，主键是 `evidence_id`。它补上分区表上做不到的“证据 ID 全局唯一”（schema 待人确认 #12）：
     - 同一个 evidence_id 再来一次且内容相同 → 跳过；
     - 内容不同 → 拒绝并告警。
   - **attempt_id 由平台生成**：第一次收到请求时生成，存进幂等表，重试时沿用（阿鸦 lessons #7）。
     - BFF 如果在请求里带了 `attempt_id`，就用 BFF 的。
     - 转发给适配器的请求里带上这个 attempt_id。
6. **字段映射**。平台列 ← 契约字段，按学科通用。阿鸦端到端的 `persist.ts` 已经验证过这套映射：
   | 表 | 来源 |
   |---|---|
   | `evidence_event` | 响应 `evidence[]`。另加 `student_id` = `context.student_ref`，`source_method`，`request_id`，`request_idempotency_key` |
   | `attempt` | 请求 `attempt` / `attempts[i]` 加平台 attempt_id。`response_evidence_id` = 该作答对应的 `response_*` 类证据 |
   | `grade_result` / `grade_dimension` | 响应 `result` 与信封字段。`evidence_ids`、`source_version`、`model_version`、`confidence`、`human_review_required`、`review_reasons`、`semantics` 原样保存（规则 3） |
   | `error_attribution` | `result.error_attributions[]` |
   | `skill_observation` | `result.observations[]` |
   | 不写 | `mastery_estimate`：属于冲刺 4-C 的掌握估计 |

   **作答与证据的对应**：diagnose 响应里，哪条证据属于哪道作答，靠 `item_ref` 加 `occurred_at` 匹配，不依赖证据在数组里的顺序（契约守护 N6）。一道作答匹配到零条或多条 `response_*` 证据时，整体拒绝。
7. **写入身份**：数据库登录角色只继承 `starbuddy_learning`，每个事务 `SET LOCAL ROLE starbuddy_learning`。连接串来自配置，**只连开发库或测试库**（规则 9）。
8. **测试**：
   - 单测覆盖核对规则，每条都有正例和反例。
   - 集成测试复用本地一次性 Postgres，做法同 `db/tests/run.sh`。
   - 阿鸦端到端改为走学习服务：阿鸦适配器以真实 HTTP 进程运行，学习服务调用它，再查库断言。
   - 新增 Makefile 目标 `learning-test`，并接入 `make check`。

### 不做
- 计量的预留、结算、回滚（冲刺 2-E）。学习服务只校验消耗类请求带了 `usage_id`，并把它原样传给适配器；结算由 BFF 按学习服务的返回结果做（ADR 06）。
- 统一复核队列（冲刺 2-G）：本冲刺**不写** `review_task`，见待人确认 #4。
- 掌握估计与复测调度（冲刺 4-C）。
- 完整的编排状态机（ADR 03）：本冲刺只有“调适配器 → 核对 → 落库”一步，幂等表里的状态就是这一步的状态。
- 异步任务（202 + job），以及 `paper`、`process` 两种诊断模式。
- 给统一壳用的查询接口（冲刺 3-H 星图、4-H 统一壳）。本冲刺的“可查询”由测试直接查库证明。
- `teach`、`plan`、`report`、`generate_variation` 的落库。

## 验收标准（可测试）
- [ ] **重复不入库**：以下场景各有测试，重试之后行数不变，返回同一结果：
  - 同一个 `idempotency_key` 顺序重试；
  - 20 个并发请求用同一个 key；
  - 首次执行在落库事务中途失败（事务回滚后重试）；
  - 持有者超时后由新请求接管。
- [ ] 同 key、不同请求体，返回 409，不写任何行。
- [ ] 同一个 evidence_id 再次出现：内容相同则跳过；内容不同则整体拒绝，不写任何行。
- [ ] 核对规则每条都有反例测试，被拒绝时**一行都不写**：契约不合、证据 ID 不闭合、学科不一致、题目未登记、semantics 比平台判定更松。
- [ ] 适配器声明的准入状态比平台登记更松时，落库的是平台的状态；这时 `semantics` 为 practice，并要求复核。
- [ ] 阿鸦端到端测试走“学习服务 → 阿鸦适配器（HTTP）→ 平台库”，查得到 `evidence_event`、`attempt`、`grade_result`（含维度）、`error_attribution`、`skill_observation`，evidence_id 与适配器响应一致。`adapters/aya/tests/e2e/persist.ts` 已删除。
- [ ] 日志和错误详情里不出现学生作答原文和答案，有测试断言这一点（同阿鸦 `leak.test.ts` 的做法）。
- [ ] 新表的 RLS 有正反向测试（规则 5）：BFF 不能读写，学习服务可以写。
- [ ] `make check` 通过；新增依赖的许可证已核（规则 10）。

## 涉及的必停项（见 CLAUDE.md）
**是**。PR 首行写 `[NEEDS-HUMAN]`：
- `db/migrations` 要新增 `0007`，内容是请求幂等表、证据 ID 登记表，以及它们的 RLS 和授权。迁移由人执行。
- 如果内部接口的 OpenAPI 放进 `packages/contracts`（待人确认 #2），属于契约变更。
- 不涉及计量逻辑、人设、第三方服务。

## 依赖与输入
- 已合并：契约 v0.9、学习者 Schema v0.9、阿鸦适配器（`adapters/aya`，冲刺 1-D）。
- ADR 01、03、05、06 还是草稿。本 spec 按它们的建议选项写，签字结果不同时再调整。
- 本机需要 Python 3.11+ 与 PostgreSQL 16。开发环境已具备。

## 待人确认
1. **Python 依赖**（都已查过许可证）：
   | 包 | 许可证 |
   |---|---|
   | fastapi | MIT |
   | pydantic | MIT |
   | uvicorn | BSD-3-Clause |
   | asyncpg | Apache-2.0 |
   | httpx | BSD-3-Clause |
   | jsonschema | MIT |
   | pyyaml | MIT |
   | pytest（开发） | MIT |

   数据库驱动**不用 psycopg 3，因为它是 LGPL-3.0**。按规则 10，LGPL 默认不引入，所以选 asyncpg。是否同意？
2. **BFF ↔ 学习服务的 OpenAPI 放在哪里**：
   - (a) `packages/contracts/openapi/learning-internal.v0.yaml`（默认）。ADR 01 说两者之间用 OpenAPI 契约，放进唯一硬接口目录更一致，但属于必停项。
   - (b) `services/learning/openapi/`，作为服务内部接口，不算契约。
3. **幂等怎么落**：本 spec 新增两张不分区的表，没有沿用 ADR 05 写的 `UNIQUE (idempotency_key, occurred_at)`，原因有两个：
   - 一次请求会产生多条证据；
   - 分区表上的唯一约束只在分区内有效，跨月重试会漏。

   这与 ADR 06 给计量账本单独建 `usage_idempotency` 表的思路一致。确认后回写 ADR 05 与 schema spec 待人确认 #2。
4. **复核任务什么时候建**（阿鸦 lessons #8、aya spec 待人确认 #3）：本冲刺不写 `review_task`，由冲刺 2-G 决定入队规则。
   - 默认建议：原因只有 `content_not_admitted` 的结果不进人工队列。
   - 学习服务按 `verdict = needs_review` 触发复核后的重判，不按 `dimension_unavailable` 触发。
5. **pending 的租约时长**：默认 60 秒，比适配器超时加一次重试更长。超时后由新请求接管。

   接管时如果适配器正在处理第一次调用，会被调用两次。结果相同，也不会重复扣次，因为计量不在这里。可以接受吗？
6. **结果摘要里给多少**：默认只给 ID、verdict、semantics、human_review_required 与错因的跨科大类，不给维度分和原因码。人设层需要更多字段时再加。
7. **幂等记录的保留期**：幂等表里存了请求体指纹与学生 ID，不存作答原文。默认保留 30 天，按 ADR 09 的数据分级再定。
8. **谁来跑内容登记脚本**：阿鸦的登记 SQL 目前是人工执行。学习服务启动时是否自动检查“适配器 capabilities 声明的内容版本已登记”，没登记就拒绝调用？默认只检查、不自动登记。
