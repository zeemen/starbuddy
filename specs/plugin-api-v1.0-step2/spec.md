# 插件 API v1.0 第二步：学习服务双版本、重放接口、阿鸦切到 v1.0、BFF 用重放

状态：**草稿 v0.1，待确认**（2026-09-28）
任务包：开发计划 v2.0 冲刺 1（W3–W4）A 流“三份 OpenAPI 与三个 Schema 冻结 v1.0；消费者驱动契约测试进 CI”的插件 API 部分；
承接 `specs/plugin-api-v1.0/`（第一步已交付候选版，lessons“第二步”）。
关联：`packages/contracts/openapi/plugin-api.v1.0.yaml`、`learning-internal.v1.yaml`；评审清单 `specs/contract-conformance-sprint1/v1.0-review.md`（N17、N18）；
`services/learning/`、`adapters/aya/`、`services/bff/src/metering/`；CLAUDE.md 规则 1、3、4。

> 契约的使用方改动、计量逻辑（BFF 重放后的结算）属必停项。PR 首行写 [NEEDS-HUMAN]。v1.0 仍是候选版：本任务让系统**能**跑 v1.0，
> 但生产切换以评审签字为准；签字前共享库的候选版开关（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT`）只在测试与演练环境打开。

## 目标
迁移期内学习服务同时接受 v0.9 与 v1.0，阿鸦能按 v1.0 通过一致性测试，BFF 在预留过期后改用只查询的重放接口，不再出现“没有预留的执行”。

## 范围
### 做
1. **共享库**（`packages/contracts/python/starbuddy_contract`）：
   - 同时加载两个版本，按请求的 `context.contract_version` 选校验版本（不再只靠环境变量）；1.0.0 仍受候选版开关保护；
   - 跨文件引用按文件 URI 解析（第一步已有，补单测）。
2. **学习服务**（`services/learning`）：
   - 新增 `/internal/v1/grade`、`/internal/v1/diagnose`，请求按 v1.0 校验，转发给适配器时带 `contract_version: 1.0.0`；`/internal/v0/*` 保留到迁移期结束（W6）；
   - 重放接口 `GET /internal/v1/replay/{method}/{idempotency_key}?student_ref=&usage_id=`：只查不执行；没有记录或失败过 → 404 并**封存** key（新表或新列，迁移，必停项）；
     之后以该 key 到达的原请求返回 409 `IDEMPOTENCY_KEY_SEALED`，不调适配器、不写任何行；`student_ref` / `usage_id` 不匹配与不存在同样 404，不封存；
   - N18 b：提交阶段出错与 `INTERNAL` 改为 `retryable: true`（结果未知）；
   - 作答类证据必须带 `attempt_id` 并与请求的作答一致（v1.0 的平台侧校验）；证据等级、通道、情境由平台补齐的列等冲刺 1-C 加列后再接，本任务只保留位置。
3. **阿鸦适配器**（`adapters/aya`）：
   - `loadContract` 按文件 URI 同时登记两份服务契约，ajv 能解析 v1.0 的跨文件引用；
   - 同时支持 0.9.0 与 1.0.0（按请求的 `contract_version` 应答，能力声明报当前默认版本）；v1.0 下作答类证据带 `attempt_id`；
   - 一致性测试按 1.0.0 跑通（`make conformance` 加一条）。
4. **BFF**（`services/bff/src/metering`）：
   - 学习服务客户端支持 v1 路径；预留过期（`existing` 且 `expired`）时先调重放接口：200 → 用摘要、不扣费；404 → 用新 key 重新预留；409 `BUSY` → 结果未知；
   - 迁移期 5xx 语义：v0 路径也按“5xx 一律结果未知”处理（已如此实现，补测试）；
   - 集成测试覆盖：过期后重放拿到结果不扣费；过期后原请求晚到被封存拒绝；重放参数不匹配不封存。
5. **检查**：新增一条检查，断言阿鸦的契约加载能解析 v1.0 的全部请求与响应 schema（契约审查“没有检查覆盖”）。

### 不做
- 证据加列（冲刺 1-C，另起 spec）；知识服务、洞察服务的实现；删除 v0.9（W6 另起清理任务）。
- 不改人设、不调用模型。

## 验收标准（可测试）
- [ ] 学习服务：同一作答分别按 v0.9、v1.0 请求都能写入；v1.0 请求缺 `attempt_id` 被拒；集成测试通过。
- [ ] 重放：存在 → 200 且不写行；不存在 → 404 且 key 被封存，随后原请求 409 `IDEMPOTENCY_KEY_SEALED` 且适配器调用次数为 0；参数不匹配 → 404 且不封存。
- [ ] 阿鸦按 1.0.0 与 0.9.0 都通过一致性测试；ajv 加载 v1.0 的检查通过。
- [ ] BFF：过期后重放拿到结果不扣费；404 后新 key 重新预留；幂等与回滚测试全绿（含并发）。
- [ ] 数据库迁移（封存表）up → down → up 通过，变异测试覆盖封存规则。
- [ ] 严格 `make check` 通过。

## 待人确认
1. 封存记录放学习服务自己的表（默认，`platform.replay_seal`，只有 learning 角色能写）还是复用 `usage_reservation`？
2. 阿鸦能力声明在迁移期报哪个版本？默认报 0.9.0，按请求的 `contract_version` 应答，v1.0 签字后改报 1.0.0。
3. BFF 何时切到 `/internal/v1`：默认本任务只接重放接口，写入仍走 v0，签字后另开小任务切换。
