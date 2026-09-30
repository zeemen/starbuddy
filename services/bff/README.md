# services/bff · TS BFF

模块：计量 `src/metering/`（2-E，`specs/metering-impl-sprint2/spec.md`）、星图读模块 `src/star-map/`（3-H，`specs/star-map-sprint3/spec.md`）、建档 `src/onboarding/`（SIE-01）、行为事件与探针 `src/events/`（2-L）。登录、会话、支付都不在这里。

- 运行时：Node 22（原生运行 TS，与阿鸦适配器同一工具链）；数据库客户端 `pg`（MIT）。
- 计量流程：`meteredCall()`——以当前账号预留 → 用同一个 key 调学习服务 → 结算或释放；结果未知时同 key 退避重试。
  数据库侧的授权、加锁、幂等都在迁移 `0009` 的函数里，BFF 只是调用方（设计文档 `docs/design/metering-schema.md` 第 4、5 节）。
- 定时任务：`runExpiry()`、`runRenewal()`，连接池的登录角色只继承 `starbuddy_scheduler`。
- 规范化 JSON 与学习服务逐字节一致：共享向量 `packages/contracts/testdata/canonical-vectors.json`。
- 星图：`GET /bff/v0/students/{id}/star-map`（`node:http`，`src/star-map/main.ts`，只监听 127.0.0.1）。
  - 接口定义在 `openapi/star-map.v0.yaml`；三态映射只在 `src/star-map/display-state.ts` 一处。
  - 授权完全交给 RLS；未授权与不存在返回同一个 404。
  - 身份来源是开发桩 `x-dev-account-id`，只在 `STARBUDDY_DEV_AUTH=1`、监听 loopback、请求来自 loopback 时生效。4-H 替换。
- 行为事件与“能做对吗”探针（冲刺 2-L，`specs/behavior-events-sprint2/spec.md`）：`src/events/`，同一个服务、同一套身份。
  - `POST /bff/v0/students/{id}/events`、`POST …/probes`、`POST …/probes/{probe_id}/attempt`；接口定义在 `openapi/events.v0.yaml`。
  - 只写不读，只回记录 ID；BFF 先校验请求体（未知键、自由文本一律 422），再调迁移 0025 的库函数（只认学生本人，家长、其他学生 403）。
  - 不消耗额度，不走计量预留；日志只记路由、状态码、耗时与学生 ID 的哈希，不记 payload。
- 数学自适应诊断（SIE-02a，`specs/sie-02a-math-diagnosis/spec.md`）：`src/diagnosis/`，同一个服务、同一套身份，只认学生本人。
  - `…/diagnosis/sessions`（开始 / 续做）、`…/sessions/{id}`（当前题）、`…/{id}/probe|answer|pause|abandon`；接口定义在 `openapi/diagnosis.v0.yaml`。
  - 开始预留 `diagnosis_full`、第一道题判分成功即结算、没答题就放弃或确定失败才释放、结果未知不释放；续做不另占额度。
    宵禁时段（按 `platform.student_time_guard` 与缺省 22:00–06:00）续做、取题、探针、作答回 402 `curfew` 并暂停会话。
    探针经 2-L 库函数写入（幂等键由会话与步骤派生）并在作答后关联。
  - 学习服务地址与令牌：`BFF_LEARNING_URL`、`BFF_LEARNING_TOKEN`（不设时诊断接口 503）。

## 测试
```bash
cd services/bff && npm ci
make bff-test       # 类型检查 + 单测 + Gate 0 六类场景（一次性 Postgres，多连接）
make bff-mutation   # 去掉加锁或并发判断，断言集成测试能发现
```
