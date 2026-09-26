# services/bff · TS BFF

本冲刺（2-E，`specs/metering-impl-sprint2/spec.md`）只有计量模块 `src/metering/`；登录、会话、统一壳接口、支付都不在这里。

- 运行时：Node 22（原生运行 TS，与阿鸦适配器同一工具链）；数据库客户端 `pg`（MIT）。
- 计量流程：`meteredCall()`——以当前账号预留 → 用同一个 key 调学习服务 → 结算或释放；结果未知时同 key 退避重试。
  数据库侧的授权、加锁、幂等都在迁移 `0009` 的函数里，BFF 只是调用方（设计文档 `docs/design/metering-schema.md` 第 4、5 节）。
- 定时任务：`runExpiry()`、`runRenewal()`，连接池的登录角色只继承 `starbuddy_scheduler`。
- 规范化 JSON 与学习服务逐字节一致：共享向量 `packages/contracts/testdata/canonical-vectors.json`。

## 测试
```bash
cd services/bff && npm ci
make bff-test       # 类型检查 + 单测 + Gate 0 六类场景（一次性 Postgres，多连接）
make bff-mutation   # 去掉加锁或并发判断，断言集成测试能发现
```
