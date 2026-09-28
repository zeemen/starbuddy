# 插件 API v1.0 第二步 · 经验与待人确认

## 交付
- 共享库：`plugin_contract(version)` 按请求的 `contract_version` 选校验版本；1.0.0 仍受 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT` 保护；
  `verify()` 拒绝应答版本与请求不一致、v1.0 下证据 `attempt_id` 不属于本次请求。
- 学习服务：`/internal/v1/grade`、`/internal/v1/diagnose`；重放接口 `GET /internal/v1/replay/...`；`INTERNAL` 改为 `retryable: true`（N18 b）。
- 迁移 0016：`platform.replay_seal`（只追加、只有 learning 角色能读写）与 `ingest_request.usage_id`。
- BFF：`httpLearningReplay`；预留过期（`existing` + `expired`）只调重放接口：200 → 不扣费返回；404 → `expired_retry_with_new_key`；
  `BUSY` 或其他 → 结果未知。写入仍走 v0（待人确认 #3 默认）。
- 阿鸦：同时支持 0.9.0 与 1.0.0，按请求版本应答（能力声明报 0.9.0，待人确认 #2 默认）。

## 经验
1. **封存必须同时拦住“认领”和“提交”两处。** 只在认领时查封存不够：持有者租约过期、仍在调适配器时，重放看到的是“没有成功记录”并封存；
   持有者回来提交就成了一次重放说“没有”的执行。现在 `claim` 与 `persist` 都先拿 (method, key) 的咨询锁再查封存，
   封存后 `persist` 按 `LeaseLost` 退回，原请求得到 409 `IDEMPOTENCY_KEY_SEALED`（测试 `test_holder_with_expired_lease_cannot_commit_after_seal`，去掉守卫即失败）。
2. **BFF 只把带 `REPLAY_NOT_FOUND` 的 404 当作已封存。** 路由不存在、网关的 404、400、401、5xx 都按结果未知，否则前端换新 key 后原 key 仍可能执行，造成重复写入。
3. **参数不匹配不封存。** 否则任何持有服务令牌的调用方都能用猜到的 key 封掉别人的请求；同时与“没有记录”返回同样的 404，不透露 key 是否存在。
4. 旧的 `ingest_request` 行（0016 之前）`usage_id` 为空：重放只核对学生——成功的照常返回结果，失败的照常封存。
   最初按“usage_id 对不上”处理，结果是 404 却不封存，BFF 让前端换新 key 后原 key 仍可能执行（安全审查必须改 2）。
6. v0 契约的错误码枚举里没有 `IDEMPOTENCY_KEY_SEALED`：v0 路径被封存时返回 409 `IDEMPOTENCY_CONFLICT`（不可重试），v1 路径返回 `IDEMPOTENCY_KEY_SEALED`；
   不改 v0 契约（安全审查必须改 3，默认做法）。
7. 封存表按操作分开写 RLS 策略（SELECT、INSERT），并挡住 TRUNCATE；DELETE 只靠不授权（学生档案删除时要级联）。
8. 阿鸦自己也有候选版开关（`allowCandidate`），关着时声明 1.0.0 的请求返回 `CONTRACT_VERSION_UNSUPPORTED`，与学习服务形成两道闸。
9. 覆盖缺口：BFF 侧“封存 → 晚到原请求被拒”只用假学习服务验证；真实学习服务与数据库的组合在 `services/learning/tests/test_v1_replay.py` 单独验证，没有两服务联跑的冒烟测试。
5. 指标 `reexecuted_without_reservation` 已改为 `sealed_after_expiry`：过期后不再发原请求，N17 缺口关闭。

## 待人确认
1. 迁移 0016 由人先在副本演练再执行（必停项），**必须先于本版本学习服务上线**：v0、v1 写入都要查封存表，
   学习服务启动时检查 `platform.replay_seal`，不存在就拒绝启动（安全审查必须改 1）。
2. BFF 切到 `/internal/v1` 写入的时间点：默认 v1.0 签字后另开小任务。
3. 封存记录的保留期：目前随学生档案级联删除，不单独清理；是否需要按时间清理（例如 30 天后 key 不会再被重试）待定。
4. 重放是 GET，key、`student_ref`、`usage_id` 在查询串里：部署时学习服务关闭 uvicorn 访问日志（`--no-access-log`），中间不经会记 URL 的代理；
   下次契约改版时考虑改成 POST（安全审查建议 5）。
5. 阿鸦 v1.0 只认 `text` 与 `choice` 作答；v1.0 允许的 `latex` / `mhchem` / `semantic` 返回 422（契约里阿鸦 diagnose 的 v1.0 示例用的正是 `latex`）。
   `latex` 能否按现有符号验证器解析，待教研与负责人决定。
6. 阿鸦的默认契约版本用启动参数 `--contract-version 1.0.0 --allow-candidate-contract` 切换（一致性测试的 CLI 不向适配器进程传环境变量）；
   1.0.0 那条一致性测试不写覆盖率报告，避免 Gate 0 重复计数。v1.0 新增的 `MODE_NOT_SUPPORTED`、`UNAUTHENTICATED` 编码暂未启用。
