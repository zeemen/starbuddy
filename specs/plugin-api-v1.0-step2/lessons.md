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
4. 旧的 `ingest_request` 行 `usage_id` 为空：重放拿不到它们的结果，只会得到 404 并封存。迁移期 BFF 写入都带 `usage_id`，影响只在 0016 之前的遗留行。
5. 指标 `reexecuted_without_reservation` 已改为 `sealed_after_expiry`：过期后不再发原请求，N17 缺口关闭。

## 待人确认
1. 迁移 0016 由人先在副本演练再执行（必停项）；执行前学习服务的 v1 路径与重放接口不可用（表不存在时重放返回 500，BFF 按结果未知处理）。
2. BFF 切到 `/internal/v1` 写入的时间点：默认 v1.0 签字后另开小任务。
3. 封存记录的保留期：目前随学生档案级联删除，不单独清理；是否需要按时间清理（例如 30 天后 key 不会再被重试）待定。
