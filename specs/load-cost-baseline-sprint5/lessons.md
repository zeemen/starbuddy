# 冲刺 5 压测基线、单模型失效降级、成本测算 · lessons

## 实现与 spec 的对照

| spec | 实现 | 说明 |
|---|---|---|
| 1 压测基线 | `tools/loadtest/`（arrivals、scenarios、driver、stack、dbstats、meter、report）；`make loadtest-baseline` | 本机一次性栈：一次性 Postgres + 假供应商 + 三科适配器 + 学习服务 + BFF；泊松到达、固定种子、分阶段（small 默认；full = 10 → 50 → 100 → 200 需 `--i-know`） |
| 1 指标 | p50 / p95 / p99、错误率、吞吐（接口 × 阶段）；连接峰值（pg_stat_activity）；慢查询前十（pg_stat_statements，只在临时实例） | 应用侧池内排队看不到，只能从接口延迟间接看 |
| 1 计量零泄漏 | `meter.leak_report`：快进时钟 + 过期任务后，无 reserved、终态流水对得上、对账视图零行、额度桶未结预留 0 | 复用计量的对账视图口径（同 BFF harness `ledgerConsistent`） |
| 1 可复现 | 请求序列（到达、步骤、幂等键）digest；计划与实际比对；`test_arrivals.py` | 执行时完成顺序不同，比较按（到达、步骤）排序 |
| 2 故障注入 | `faults.py` + `fake_provider.py`（HTTP）+ `faulty_behavior`（进程内 FakeProvider） | **没有改 `model_router/providers.py`**：FakeProvider 本来接受注入的行为 |
| 2 降级断言 | `services/learning/tests/test_degradation.py`；清单 `loadtest/degradation.py` | 路由层：5 条路由 × 5 种故障 × {主失效, 全失效 + 恢复}；端到端（经计量驱动桩运行 BFF meteredCall）：道法、语文批改 × {主失效, 全失效 → 结果未知 → 过期回收, 恢复, 超时 → 同键重试, 备选乱答}，数学不受影响；规则 3 五字段；清单完整性静态核对。其余故障类型（5xx 比例、限流、挂起超时）只在路由层覆盖 |
| 3 成本模型 | `loadtest/cost.py` + `config/`；`make cost-report`；`test_cost.py` 手算核对 | 额度只读解析迁移 0015 的 `v12-pilot@1` |
| 4 执行手册 | `tools/loadtest/RUNBOOK.md` | 预发前提、步骤、停止条件；真实成本重算只导 token 与元数据 |

## 审查修正（契约审查、安全审查，用户授权按默认建议）

- H1：批改经计量驱动桩运行 BFF meteredCall（见偏离 #1）；降级断言改为“结果未知 → 不结算不释放 → 快进过有效期 → 过期 → settled 不变、reserved = 0”；
  过期后同键重试只查不执行；更正了“经 replay 查无后释放”的错误描述。
- M1：降级端到端每条出结果的用例都断言规则 3 五字段（复用 e2e_cohort 的 GRADE_FIELDS 与 check_grade_rows）。
- M2：乱答转人工断言 verdict = needs_review 或总分为空、有分维度只来自 verifier / rubric_rule、证据里没有模型定位片段与分数字段。
- M3 / 安全 M2：待人确认 D10。L1：额度解析核对行数、重复与四档资源集合；画像引用不存在的资源或任务直接报错。
  L2：成本报告“未计入”注明失败尝试的计费 token。L3 / 安全 L7：端到端补超时（结果未知 → 同键重试 → 只写一次只扣一次）。
- 安全 L2：Makefile 路径拒空格与单引号、LOADTEST_STAGES 限 small|full|数字、LOADTEST_ARGS 白名单，变量经环境变量传入；
  L3：不给输出目录用 mkdtemp，给了则拒符号链接与他人目录，写文件 O_NOFOLLOW；L4：价格表供应商与模型必须是“代号”格式（签字价也一样）；
  L5：准备 SQL 与写库前核对 inet_server_addr() 为空（只接受 Unix socket）；L6：README 注明 /_control 与驱动桩接口无鉴权、只在本机。

## 偏离 spec 之处

1. **批改没有 BFF HTTP 入口**：壳里还没有批改路由（BFF 只有计量库 `meteredCall`）。按契约审查 H1，批改经计量驱动桩
   `tools/loadtest/node/meter-harness.mts` **直接运行 BFF 的 meteredCall**（import BFF 源码，不改）：结果未知（5xx / 408 / 429 /
   retryable / 超时）按 1/2/4/8 s 退避同键重试（预算为预留剩余时间一半，测试与压测可按比例缩短），仍未知不结算不释放、留给过期任务；
   existing 按 state 处理（released 直接返回、expired 只查不执行）并核对 usage_id；指纹用 BFF 的 meteringFingerprint / itemFingerprint。
   唯一差别：BFF 的 httpLearningClient 发 `/internal/v0/grade`，驱动桩把同一请求体包成 v1 信封发 `/internal/v1/grade`（本栈适配器按
   契约 1.0.0 启动），响应仍由 BFF 的 classifyResponse 归类。BFF 批改路由合入后改为经 BFF HTTP。
   （更正：本任务初版把“BFF 经 replay 查无后释放预留”写进了清单与说明，那是错的：BFF 在结果未知时不释放，只在预留过期后同键重试时
   调 replay，只查不执行。已更正 degradation.py、README 与本文件。）
2. **计量驱动桩与计量旅程各用一个 bff_app 池（max 20）**，会多占数据库连接；报告里注明。
3. **样例报告是小规模**（small：5 → 10 并发 × 10 s），报告首行注明；完整分阶段只在 `make loadtest-baseline` 的参数里支持，未在开发期运行
   （本机另有长时间检查，按要求控制规模）。200 并发下学习服务与 BFF 各 10 的连接池是否成为瓶颈，**尚无数据**。
4. **降级行为**：规格里的“批改转人工排队”“讲解固定提示”“诊断暂停可恢复”当前代码里没有可测对象（见待人确认 D1–D3），
   按默认“宁可不答不乱答”断言现有行为：不给分、不写证据；BFF 归为结果未知、不结算不释放，预留过期回收（不扣次）。
5. **token 数是估算**：假供应商按提示字符数 / 1.5 估算（不是供应商计数）；人设评测录制目录 `evals/persona/recordings` 目前为空，
   没有可用的 token 数（`diandian` 的留出集按规则 14 未读）。T1、T9 没有任何学科方法调用，token 全用可配置分布。

## 待人确认

- **D1 批改主备全失效的用户可见行为**（各科负责人）：现状是学习服务回 5xx，不写任何证据与判分；BFF 归为结果未知，退避重试后
  不结算不释放，预留过期回收（不扣次）；过期后同键重试只查不执行，replay 查无即封存，恢复后不会“补判”，学生换新键重交。
  过期前（批改预留 10 分钟）同一道题的内容指纹处于 dedup_in_progress，学生立刻重交同一题会被拒（BFF 现有规则，未改）。规格写的“human_review_required / 排队”没有实现：
  要不要做“先收下作答、排队、恢复后或人工补判”（涉及判分与计量规则，必停项）？默认不做，维持“不答”。
- **D2 讲解主备全失效**：讲解（teach）三科都未实现，无可测对象；默认“固定提示‘讲解暂不可用’，不生成内容，计量回滚”，讲解实现时按此补断言。
- **D3 诊断**：现有诊断（数学自适应、语文客观、道法过程模式）都不调模型，模型失效不影响；“诊断暂停可恢复”留给将来模型参与诊断时断言。
- **D4 性能通过线**（spec #1）：本任务只出基线，不设通过线；p95 目标由技术负责人定。
- **D5 成本口径**：成本表是“单科口径”——假设学生整月额度都用在这一科；多科同时在读时额度如何分摊到科（按时长？按次数？）待产品确认。
  使用画像里的比例（作文占批改 30%、主观题 50%、每分钟 1 轮对话、每轮 1 次守卫、语音全计入本科）全是假设。
- **D6 使用分布**（spec #5）：默认 30% / 60% / 100% 三档等权，P50 落在 60% 档、P90 落在 100% 档；三档等权下 P90 等于满额，偏保守。
- **D7 价格表**（spec #4）：示例价，签字后由人填入并重算。
- **D8 预发接入**：RUNBOOK 第 1.2 节第 3 步——驱动器连接预发的方式（地址与服务间令牌来源）本任务不实现，需人批准后另起任务。
- **D10 转人工复核的批改是否扣次**（产品、计量负责人；契约审查 M3、安全审查 M2）：备选“乱答”或主观题转人工时，学习服务返回 200，
  BFF 按成功结算扣 1 次（`test_e2e_backup_garbage_goes_to_human_review_not_auto_score` 断言的现状）。学生拿到的是“待复核”而不是结果，
  是否应不扣或复核后再扣属于计量规则（必停项）；默认维持现状。
- **D9 熔断多实例**：熔断状态在进程内（模型路由 lessons 待人确认 #1），多实例部署时每个实例各自熔断，恢复时间与“主失效”下的请求分布会不同；本机压测是单实例。

## 过程记录

- 端到端降级测试只覆盖“整家不可用”“慢响应导致超时（结果未知）”“备选乱答”三类；5xx 比例、限流、挂起超时只在路由层覆盖（契约审查 L3、安全审查 L7）。
- BFF 角色没有 `platform.meter_now()` 的执行权限：计量驱动桩的时钟由调用方以管理员连接读取后经 `/clock-sync` 传入，不给 BFF 角色加权限。

- `pg_stat_statements.track` 在库未加载前用 `ALTER SYSTEM` 设置会失败，只设 `shared_preload_libraries` 即可（默认 top）。
- 部分结算（预留 5、结算 3）是合法的：零泄漏核对不能用“预留 = 结算 + 释放 + 过期”，改为“每笔一个终态、终态流水与预留对得上”。
- 报告里的供应商一律用代号（代号A / B / X）：`test_cost.py` 扫描样例里的 `fake-a`、`model:` 等标识。
- 计量时钟替换只在临时测试库（同 apps/web/scripts/e2e.sh），否则夜里压测会撞宵禁。
- `dbstats.enable_stat_statements` 借用了 `pgharness.EphemeralPG` 的内部方法重启临时实例；pgharness 改动时要一起看。
