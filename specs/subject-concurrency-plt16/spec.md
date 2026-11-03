# PLT-16 分学科并发上限与有界排队 · 第一步（BFF 进程内，开关默认关）

2026-10-17 起由 Claude 按用户授权自主推进（开发计划 v2.0 阶段 1 · 5 月“增长工程：队列、缓存、分学科限流”，提前做闸门）。
编号 PLT-16 为仓库排队号。零迁移、不改契约、不改计量逻辑（闸门只放在计量调用之前）、零新依赖。

## 背景
BFF 现有限流都是按路由的窗口计数（数据库 `auth_rate_hit`），没有并发控制。会调模型或适配器重方法的请求（诊断 start / answer / current / probe、再练 next / answer、复测 next / answer）在高峰时一科堆积会拖慢其他科，学习服务单进程、无并发上限。

## 范围
1. `services/bff` 新增通用的进程内并发闸门（信号量 + 有界 FIFO 队列 + 排队超时），按学科码独立计数：
   - 配置 `config/concurrency.json`（照 `shell.json` 约定，`_doc`、`loadXxxConfig`、上下界校验、只有 dev/test 可经环境变量换文件）：每科 `max_inflight`、`max_queue`、`queue_timeout_ms`，合成默认值，标“待人确认”；开关 `STARBUDDY_SUBJECT_GATE`（缺省关，关时行为逐字节不变）。
   - 开关打开时启动告警声明进程内状态需单实例（同 `rest.singleInstance` 先例）。
2. 接入点（都在计量之前，被拒的请求一定没碰过计量）：
   - 诊断 start 在 `reserve` 之前；诊断 answer / current / probe 包住转发（学科按 `aya`）。
   - 再练 next 包住转发；再练 answer 在宵禁检查之后、`countPractice` 之前。
   - 复测 next 包住转发；复测 answer 在 `check` / `countPractice` 之前。
   - 闸门不包住“已预留 / 已计次之后”的那段；已入闸的请求无论成功、失败、超时都在 finally 中释放名额。
3. 被拒：新增问题码 503 `SUBJECT_BUSY`，带 `retry-after`（秒，按配置），与现有 503 `UPSTREAM_UNAVAILABLE` 区分；学生端前端对它沿用现有“稍后再试”的展示（文案占位，不新增人设话术）。
4. 观测：访问日志 note 学科码与闸门结果（admitted / queued / rejected、排队毫秒分档），按学科累加计数；不含学生 ID。
5. 测试：闸门纯函数（并发上限、FIFO、排队超时、释放在异常路径也发生、学科间互不影响）；路由集成（开关关时逐字节不变；超限时 503 SUBJECT_BUSY 带 retry-after 且库里没有预留 / 计次行；名额在上游失败时被释放）；变异接进 `bff-mutation`。
6. `tools/loadtest`：driver 单列 429 / 503 SUBJECT_BUSY 与排队时间；场景可选开启再练与复测；文档说明用 `--fault slow` 观察排队。

## 不做
- 多实例共享的分布式限流（需外部存储，新依赖）；学习服务侧的并发上限；批改（grade）BFF 路由（尚不存在）；缓存；改计量、改重试与熔断参数。

## 验收
- 开关关：所有现有测试与响应不变。开关开：超限请求被拒且不触碰计量；名额总能释放；各科独立；变异全部发现；严格 `make check` 通过。

## 待人确认
1. 各科 `max_inflight` / `max_queue` / `queue_timeout_ms` 取值。
2. 被拒状态码与前端展示（503 `SUBJECT_BUSY` + `retry-after`）。
3. 多实例部署前须改为共享状态或按实例比例配置。
