# 冲刺 1-F 模型路由骨架 · lessons

## 实现要点
- 代码：`services/learning/learning/model_router/`（types、config、breaker、providers、ledger、router）；路由表 `services/learning/config/model_routes.yaml`。
- 出境两道防线：启动校验 `config.parse`（pilot、prod 出现境外、未批准、假协议的供应商，可达 L2 的任务配了境外，都拒绝启动）与运行时 `egress_allowed`（境外只在 dev、test 接 L0）。
  熔断、重试只在过滤后的候选里走；没有允许的备选就 `ROUTE_UNAVAILABLE`。
- 测试 78 条（`services/learning/tests/test_model_router.py`），含每个环境 400 组随机配置 × 30 次随机故障的性质测试；变异 31 个（`make model-router-mutation`）。

## 与 spec 的差异（按默认处理，记在这里）
- 供应商白名单状态多了一个 `test_only`：仓库里的假供应商用它，pilot、prod 只接受 `approved`；假协议的供应商不能标 `approved`。
- staging：审查后按 spec 正文执行，境外供应商只在 dev、test 接 L0；staging 可达 L2 以上的路由只能用已批准的供应商（见待人确认 #5）。
- OpenAI 兼容适配器的测试用 httpx 自带的模拟传输（进程内，不开端口），没有起真正的本机 HTTP 服务；请求头、请求体、错误映射都覆盖到了。
- T5、T6（语音）与 T7（向量）只做配置校验，调用时拒绝（骨架没有这些协议的适配器）。

## 踩过的坑
- 性质测试的随机配置在 pilot、prod 下几乎全被启动校验拒掉，测不到运行时路径：生成器按环境调低“非法项”的比例，并断言至少 40 组配置能通过校验。
- 两个变异一开始漏检：半开状态放行多个试探（补了并发试探测试）、client 错误计入熔断（原测试只调一次，达不到阈值；改为连调 6 次）。

## 审查意见处理（安全审查、契约审查，按默认建议）
- 契约格式：供应商 ID、模型名、提示词模板名按插件 API 契约的正则校验；拼出的 model_version、source_version 再核一次；测试直接读 `plugin-api.v1.0.yaml` 的正则校验路由结果。
- 模型不一致：`RouteResult` 带 `model_mismatch` 与 `reported_model`（只留安全字符、截断 128），调用记录同样保存。
- 出境核对：供应商登记 `allowed_hosts`，构造适配器时核对地址主机名；pilot、prod 必须登记主机名、只接受 https、不允许带用户名密码的地址。
- 锁定快照：pilot、prod 必须显式写带日期或版本号的 `api_name`；版本与名字必须是带引号的字符串（YAML 会把 1.10 读成 1.1）。
- 熔断：半开试探改为令牌制，只有持有试探令牌的请求能结算或释放；适配器抛出任何意外异常都归为 `bad_response`，保证释放名额；取消与意外异常也写调用记录（outcome = error）。
- staging 可达 L2 以上只用已批准的供应商；境外只在 dev、test 接 L0（撤回实现时的放宽）。
- 其他：学科按平台学科编码校验；不传 deadline_ms 时总时限 180 秒；重试按 0.2 × 2^n 秒退避，429 与 5xx 看 Retry-After（封顶 30 秒）；响应体上限 1 MiB；
  部署入口从 `LEARNING_ENV` 读环境；配置映射只读，`ModelRouter` 构造时再核一次不出境约束；变异脚本只把断言失败算作发现；测试夹具不再出现真实模型名；T2 注明输出不是分数。
- 变异从 16 个增到 31 个；测试从 55 条增到 78 条。

## 待人确认（实现中新增）
1. 熔断状态在单个进程内：学习服务多实例部署时各实例独立熔断，某家供应商故障时每个实例都要先失败 5 次。要不要共享熔断状态（例如放 Redis），等部署形态定了再说。
2. 调用记录里 `usage_id`、幂等键只记 sha256 截断、未加盐：它们是随机 ID，风险低；若要与计量账本对账后仍不可逆，改成带服务端密钥的 HMAC（与 BFF 日志哈希同一件事，见 SIE-01 lessons）。
3. 供应商返回的模型名与配置不一致时：路由照常返回，但结果带 `model_mismatch`。判分类任务（T2、T4）遇到不一致是否拒收或必须转人工复核，属于判分逻辑（必停项），随判分 spec 定。
4. 路由还没有调用方：四科原产品迁入路由层要重跑人设回归与判分评测（ADR 04 增补第 10 条），各科另起任务；点点一致性评测（`specs/diandian-consistency-r2-sprint3/`）可以直接用 test 环境的路由。
5. staging 是否像 dev、test 一样允许境外供应商处理合成数据（ADR 09 第 5 条允许）：默认不允许，staging 只用境内供应商。
6. 供应商的备案编号或 ADR 15 准入依据与 `docs/compliance/vendors.md` 的自动对照（CI 核对路由表里的供应商都在登记表里且已批准）：等第一家真实供应商登记时一起做。
7. 计量相关留给计量 v2：失败尝试的用量没有记录；超时后换备选，供应商那边可能计费两次；同一幂等键重复调用 `route()` 在路由层不去重（由调用方的计量预留保证）。
8. 数据级别由调用方自报，路由不核实内容；错误上报工具不采集局部变量（写进 README 部署要求）。
