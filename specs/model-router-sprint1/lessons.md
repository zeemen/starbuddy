# 冲刺 1-F 模型路由骨架 · lessons

## 实现要点
- 代码：`services/learning/learning/model_router/`（types、config、breaker、providers、ledger、router）；路由表 `services/learning/config/model_routes.yaml`。
- 出境两道防线：启动校验 `config.parse`（pilot、prod 出现境外、未批准、假协议的供应商，可达 L2 的任务配了境外，都拒绝启动）与运行时 `egress_allowed`（境外只在 dev、test、staging 接 L0）。
  熔断、重试只在过滤后的候选里走；没有允许的备选就 `ROUTE_UNAVAILABLE`。
- 测试 55 条（`services/learning/tests/test_model_router.py`），含每个环境 400 组随机配置 × 30 次随机故障的性质测试；变异 16 个（`make model-router-mutation`）。

## 与 spec 的差异（按默认处理，记在这里）
- 供应商白名单状态多了一个 `test_only`：仓库里的假供应商用它，pilot、prod 只接受 `approved`；假协议的供应商不能标 `approved`。
- staging 与 dev、test 一样允许境外供应商接 L0（ADR 09 第 5 条写的是“dev 和 staging 可以用境外服务，只处理 L0”）；spec 正文只写了 dev、test。仓库里 staging 一节为空。
- OpenAI 兼容适配器的测试用 httpx 自带的模拟传输（进程内，不开端口），没有起真正的本机 HTTP 服务；请求头、请求体、错误映射都覆盖到了。
- T5、T6（语音）与 T7（向量）只做配置校验，调用时拒绝（骨架没有这些协议的适配器）。

## 踩过的坑
- 性质测试的随机配置在 pilot、prod 下几乎全被启动校验拒掉，测不到运行时路径：生成器按环境调低“非法项”的比例，并断言至少 40 组配置能通过校验。
- 两个变异一开始漏检：半开状态放行多个试探（补了并发试探测试）、client 错误计入熔断（原测试只调一次，达不到阈值；改为连调 6 次）。

## 待人确认（实现中新增）
1. 熔断状态在单个进程内：学习服务多实例部署时各实例独立熔断，某家供应商故障时每个实例都要先失败 5 次。要不要共享熔断状态（例如放 Redis），等部署形态定了再说。
2. 调用记录里 `usage_id`、幂等键只记 sha256 截断、未加盐：它们是随机 ID，风险低；若要与计量账本对账后仍不可逆，改成带服务端密钥的 HMAC（与 BFF 日志哈希同一件事，见 SIE-01 lessons）。
3. 供应商返回的模型名与配置不一致时只告警（按配置记录 `model_version`）。是否改为直接拒收该结果，等接真实供应商后看各家返回格式再定。
4. 路由还没有调用方：四科原产品迁入路由层要重跑人设回归与判分评测（ADR 04 增补第 10 条），各科另起任务；点点一致性评测（`specs/diandian-consistency-r2-sprint3/`）可以直接用 test 环境的路由。
