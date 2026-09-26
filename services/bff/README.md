# services/bff · TS BFF

两个模块：计量 `src/metering/`（2-E，`specs/metering-impl-sprint2/spec.md`），以及星图读模块 `src/star-map/`（3-H，`specs/star-map-sprint3/spec.md`）。登录、会话、支付都不在这里。

- 运行时：Node 22（原生运行 TS，与阿鸦适配器同一工具链）；数据库客户端 `pg`（MIT）。
- 计量流程：`meteredCall()`——以当前账号预留 → 用同一个 key 调学习服务 → 结算或释放；结果未知时同 key 退避重试。
  数据库侧的授权、加锁、幂等都在迁移 `0009` 的函数里，BFF 只是调用方（设计文档 `docs/design/metering-schema.md` 第 4、5 节）。
- 定时任务：`runExpiry()`、`runRenewal()`，连接池的登录角色只继承 `starbuddy_scheduler`。
- 规范化 JSON 与学习服务逐字节一致：共享向量 `packages/contracts/testdata/canonical-vectors.json`。
- 星图：`GET /bff/v0/students/{id}/star-map`（`node:http`，`src/star-map/main.ts`，只监听 127.0.0.1）。
  - 接口定义在 `openapi/star-map.v0.yaml`；三态映射只在 `src/star-map/display-state.ts` 一处。
  - 授权完全交给 RLS；未授权与不存在返回同一个 404。
  - 身份来源是开发桩 `x-dev-account-id`，只在 `STARBUDDY_DEV_AUTH=1`、监听 loopback、请求来自 loopback 时生效。4-H 替换。

## 测试
```bash
cd services/bff && npm ci
make bff-test       # 类型检查 + 单测 + Gate 0 六类场景（一次性 Postgres，多连接）
make bff-mutation   # 去掉加锁或并发判断，断言集成测试能发现
```
