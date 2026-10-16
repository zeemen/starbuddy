# tools/loadtest · 压测基线、单模型失效降级、成本测算

冲刺 5（`specs/load-cost-baseline-sprint5/spec.md`）。**只在本机一次性栈上跑**（规则 9），只用合成数据（规则 8）；
不引入新依赖（Python asyncio + httpx + asyncpg，均为 `services/learning/.venv` 已有）。预发压测与真实成本重算见 `RUNBOOK.md`。

## 用法

```bash
make loadtest-baseline                                   # 小规模（small：5 → 10 并发 × 10 s，约 40 秒），报告写到 $TMPDIR/starbuddy-loadtest
make loadtest-baseline LOADTEST_STAGES=full LOADTEST_ARGS=--i-know   # spec 的 10 → 50 → 100 → 200 并发 × 各 120 s（占满本机，执行人另跑）
make loadtest-baseline LOADTEST_STAGES='10:60,20:60' LOADTEST_OUT=<目录> LOADTEST_ARGS='--fault fake-a=unavailable'
make degradation-test                                    # 故障注入 + 降级断言（路由层 + 端到端，约 30 秒）
make cost-report [COST_RECORDS=<调用记录.jsonl>] [COST_OUT=<目录>]   # 科目 × 会员档单月 P50 / P90（合成估算、示例价）
```

命令行（在 `tools/loadtest` 下，用 `services/learning/.venv/bin/python`）：`python -m loadtest plan|baseline|cost --help`。
`--fault <供应商>=<模式>[:参数]`：模式 `unavailable`、`timeout`、`error_ratio:0.3`、`slow:800`（毫秒）、`rate_limited`；供应商是路由表 test 节的
`fake-a`（主）、`fake-b`（备）、`fake-x`（模拟境外）。报告里只出现合成代号（代号A / B / X），不写供应商 ID 与模型标识。

### 分学科并发闸门（PLT-16，`specs/subject-concurrency-plt16`）

```bash
make loadtest-baseline LOADTEST_ARGS='--subject-gate --with-practice --fault fake-a=slow:800'
python -m loadtest baseline --stages 10:20 --subject-gate --with-practice --gate-config <调小 max_inflight 的一份配置> --fault fake-a=slow:800
```

- `--subject-gate`：BFF 以 `STARBUDDY_SUBJECT_GATE=subject_gate` 启动（缺省用 `services/bff/config/concurrency.json` 的合成默认值；
  `--gate-config` 换一份同格式配置，经 `BFF_CONCURRENCY_CONFIG`，只在本栈的 `STARBUDDY_ENV=test` 有效，不在 Makefile 白名单里）。
  不加时 BFF 与以前逐字节相同，报告只注明“未打开”。
- `--with-practice`：学生旅程在星图之后另走数学的再练与复测各一次列表、一次取题（取题经闸门）；BFF 打开再练与复测路由，
  学习服务用合成配置与合成题面（票据密钥每次启动随机生成）。没有到期条目时用派生的条目 ID 取题（学习服务回 404，请求照样经过闸门，记为预期）。
  不加时旅程与请求序列（digest）和以前逐项相同。
- 报告：“各接口”表单列 **429 限流** 与 **503 SUBJECT_BUSY** 两列（二者仍算错误，错误率口径不变）；另有“分学科并发闸门”一节，
  按学科给立即放行 / 排队后放行 / 被拒 / 绕过（已计量的重试）次数、被拒原因（full 队列满 / timeout 排队超时 / per_student 本学生这一科已到上限 /
  aborted 请求方断开）与**排队毫秒分档**（来自 BFF 访问日志的 `gate_*` 枚举字段，
  不含学生 ID；会话级的“会话排队等待”是驱动器自己的并发上限，与闸门无关）。
- 用 `--fault fake-a=slow:800` 观察排队：慢的是走模型的调用（道法、语文批改经计量驱动桩，不经 BFF 闸门），它们与诊断、再练、复测共用
  单进程的学习服务与数据库池，把经闸门的请求一起拖慢，名额占用变长，排队与 SUBJECT_BUSY 才会出现；本栈的诊断与再练题面是合成的、不调模型，
  所以默认上限（每科 8）下往往看不到排队，这时用 `--gate-config` 把 `max_inflight` 调小（例如 1–2）、`queue_timeout_ms` 调短再跑。
  数字只作本机对照，不代表生产容量与上限取值（取值待人确认）。

假供应商的 `/_control/faults`、`/_control/stats` 与计量驱动桩的接口**没有鉴权**：只监听 127.0.0.1、只在本机一次性栈里存在，
不得暴露到任何共享网络（预发故障演练见 RUNBOOK 第 2 节）。

依赖：`services/learning/.venv`、本机 Postgres 16 服务端、`node`，`adapters/{aya,wenli,zhixing}` 与 `services/bff` 的 `npm ci`。缺依赖时跳过（`CONTRACT_STRICT=1` 失败）。

## 组成（`loadtest/`）

| 文件 | 作用 |
|---|---|
| `arrivals.py` | 固定种子的泊松到达、分阶段加压（到达率 = 并发数 / 平均会话时长）、旅程配比、思考时间；幂等键由（种子、到达、步骤）派生；`digest()` 给请求序列的 sha256 |
| `scenarios.py` | 旅程：学生（登录 → 学生模式 → 今日计划 → 数学诊断开始 / 取题 / 作答 / 暂停 → 三科批改 → 星图）、家长（登录 → 首页 → 孩子的星图 / 额度 / 选科）、计量（同键两路并发预留 → 部分结算 → 重复结算 → 预留后释放） |
| `stack.py` | 本机一次性栈：`pgharness.EphemeralPG`（全部迁移）+ 假供应商 + 三科适配器（复用 `e2e_cohort.stack.adapters`）+ 学习服务（`python -m learning`，路由表 test 节）+ BFF（`node src/main.ts`，STARBUDDY_ENV=test、放宽限流的一份配置、短信捕获桩） |
| `seed.py` | 计量时钟替换（北京时间 10:00 起、可快进，只在测试库）、`v12-pilot@1` 的已发布副本 `loadtest-plan@1`、合成家庭（+999 号码、同意、选科、经 `meter_open_subscription` 开通） |
| `driver.py` | 负载驱动：每阶段会话并发上限、真实 BFF 会话、批改经计量驱动桩运行 BFF 的 meteredCall（见“已知偏差”）、记录实际请求序列 |
| `fake_provider.py` / `faults.py` | OpenAI 兼容的假供应商（独立进程，延迟为对数正态，token 按字符数估算）；故障注入：整家不可用、超时、5xx 比例、慢响应、限流；同一判定也给进程内 `FakeProvider` 用（不改 `model_router/providers.py`） |
| `dbstats.py` | `pg_stat_activity` 采样（按登录角色的连接峰值）与 `pg_stat_statements` 慢查询前十（只在临时实例打开） |
| `meter.py` | 计量旅程的计量函数调用（与 BFF 同一组函数、同一角色）与零泄漏核对 |
| `harness.py` + `node/meter-harness.mts` | 计量驱动桩：在本机运行 BFF 的 `meteredCall`（批改的计量全走它）；计量时钟由调用方经 `/clock-sync` 对齐 |
| `metrics.py` / `report.py` / `baseline.py` | 分位数（最近秩）、错误率、吞吐；Markdown + JSON 报告；一次压测的编排 |
| `cost.py` + `config/` | 成本模型与报表；`prices.example.yaml`（**示例价**）、`cost_profile.yaml`（使用画像，逐项标来源） |
| `degradation.py` | 降级清单：每科每个方法是否用模型、任务与数据级别、三种情形下的期望行为（断言的唯一来源）；静态扫描适配器源码核对清单完整 |

降级的端到端断言在 `services/learning/tests/test_degradation.py`（也随 `make learning-test` 运行）。

## 报告口径

- 压测报告：各接口与各阶段的 p50 / p95 / p99、错误率（非 2xx 且非 402 业务拒绝，或连接失败）、吞吐；连接峰值；慢查询前十；计量一致性；
  模型调用计数。标注“**本机基线，不代表生产容量**”。只含汇总：无请求 / 响应内容、无学生 ID、无模型标识；
- 可复现：同一种子两次运行的请求序列（到达、步骤、幂等键）逐项一致；报告写计划与实际两个 digest，不一致（有步骤因前置失败被跳过）时退出码 1；
- 零泄漏：压测后快进计量时钟 3 小时、以定时任务角色跑完过期，再核对：本轮合成学生没有停在 reserved 的预留、每笔预留恰好一个终态且流水对得上、
  对账视图（`entitlement_reconciliation`、`reservation_ledger_mismatch`）零行、额度桶未结预留为 0；不满足时退出码 1；
- 调用记录（`call-records.jsonl`）：只留科目、任务、数据级别、结果、token 数、耗时、尝试次数，是成本模型的输入；
- 成本报告：标注“合成估算”“示例价”；token 来源逐项标注（合成回放 / 可配置分布）；英语行“搁置，无数据”。

样例（`sample/`）：小规模（small 档）生成，报告首行注明；`test_cost.py` 核对样例成本报告可由样例调用记录逐字节重生、且样例里没有模型标识。
重生样例（在 `tools/loadtest` 下）：`python -m loadtest baseline --stages small --out sample --scale-note '小规模样例：…'`，
再 `python -m loadtest cost --records sample/call-records.jsonl --out sample`。

## 已知偏差（详见 `specs/load-cost-baseline-sprint5/lessons.md`）

- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩 `node/meter-harness.mts` **直接运行 BFF 的 `meteredCall`**（import BFF 源码，不改它）：
  确定成功结算、确定失败（非 408 / 429 的 4xx 且不 retryable）释放、结果未知（5xx、408、429、retryable、超时）按 1/2/4/8 s 退避同键重试
  （预算为预留剩余时间的一半），仍未知则**不结算不释放**，留给过期任务；同键重试遇到已过期的预留只查不执行（replay）。
  请求指纹与内容指纹用 BFF 的 `meteringFingerprint` / `itemFingerprint`。唯一差别在传输层：BFF 的 `httpLearningClient` 发 `/internal/v0/grade`，
  本栈适配器按契约 1.0.0 启动，驱动桩把同一请求体包成 v1 信封发 `/internal/v1/grade`，响应仍由 BFF 的 `classifyResponse` 归类；
  `--retry-scale` 可按比例缩短退避（口径不变）；
- 负载生成器与被测服务同机，数字只作回归比较；学习服务与 BFF 的连接池上限各 10，计量驱动桩与计量旅程各另用一个 20 的 bff_app 池；
- token 为假供应商按字符数估算（`CHARS_PER_TOKEN = 1.5`），不是供应商计数；T1 / T9 没有任何学科方法调用，全靠假设；
- 再练与复测场景（`--with-practice`）只有列表与取题，没有作答（要有到期条目与票据，合成学生在本栈里多半没有到期条目）；
  闸门的计次前拒绝由 `services/bff/tests/subject-gate.integration.test.ts` 覆盖。
