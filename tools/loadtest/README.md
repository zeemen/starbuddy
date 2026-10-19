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
make cost-dashboard [DASH_USAGE=<消耗聚合行.csv> DASH_RECORDS=<调用记录.jsonl>] [DASH_OUT=<目录>]   # PLT-17 成本仪表盘（只出聚合）
make cost-dashboard-test / cost-dashboard-mutation        # 仪表盘单测与变异（进 make check）
```

成本仪表盘（PLT-17，`specs/cost-dashboard-plt17`）：`loadtest/dashboard.py` 复用 `cost.py` 的额度、价格、token 来源、单次成本、
月成本与分位数，把“学生 × 月 × 资源 × 档位 × 学科（可为未归因）”的消耗聚合行与调用记录汇总成各档 × 各科消耗与使用率、各档模型成本、
按任务的失败 / 多次尝试 / 回退与平均成本、两种学科分摊口径（D5 待产品确认）。防回推：每人一个点；组人数加 ±2 确定性扰动，扰动后 < 22
只写“样本不足”、公开人数取整到 10；分位数相邻两秩插值后粗化，P90 以上不足 5 人不给 P90；按任务调用数 < 20 不给数。
本步只收合成样例（`python -m loadtest dashboard-synth`，固定种子，`loadtest/dashboard_synth.py`）；样例报表 `sample/cost-dashboard.md`；
试点导出见 RUNBOOK 第 4 节与 `sql/`（草案，不是迁移）。

命令行（在 `tools/loadtest` 下，用 `services/learning/.venv/bin/python`）：`python -m loadtest plan|baseline|cost|dashboard|dashboard-synth --help`。
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

### 新场景、SLO 判定与 2 倍峰值阶段（PLT-20，`specs/loadtest-2x-peak-plt20`）

```bash
# 在 tools/loadtest 下（Makefile 的 LOADTEST_ARGS 白名单还没有这些选项，直接调用）
PYTHONDONTWRITEBYTECODE=1 ../../services/learning/.venv/bin/python -m loadtest plan --profile peak2x --journeys all      # 只看计划与 digest
PYTHONDONTWRITEBYTECODE=1 ../../services/learning/.venv/bin/python -m loadtest baseline --stages small --journeys all --out <空目录>
PYTHONDONTWRITEBYTECODE=1 ../../services/learning/.venv/bin/python -m loadtest baseline --profile peak2x --peak-concurrency 20 --i-know \
    --journeys all --subject-gate --out <空目录>
```

- `--journeys a,b|all`（缺省全关；`--profile peak2x` 时缺省取 `config/peak2x.yaml` 的 `journeys`；`all` 不含 `prediction`，要测写
  `all,prediction`）：每个场景接在宿主旅程末尾、随其配比进混合负载，前面各步序号不变，同一种子请求序列可复现；不开时请求序列与以前逐项相同
  （`tests/test_peak2x.py` 钉了两个 digest）。场景与本栈打开的开关（`stack.journey_env`，只在 test 环境）与本栈的实际结果（读源码确认）：

  | 场景 | 宿主 | 步骤 | 打开的开关 | 本栈的结果 |
  |---|---|---|---|---|
  | `practice_answer` | 学生 | 再练、复测作答（票据） | 同 `--with-practice`（自动打开） | 取题 ready 时用下发的真票据（必须 2xx）；没有可作答的题时用派生的同前缀假票据：422 TICKET_INVALID（计次之前；只在写路径没接上时记“预期”，见 PLT-21） |
  | `plan_events` | 学生 | plan_done、plan_skip | BFF `STARBUDDY_TODAY_ORCH`、学习服务 `LEARNING_TODAY_CONFIG=synthetic` + `LEARNING_TODAY_SNAPSHOT=1` | 今日计划带快照任务 ID 时用真 ID（必须 2xx）；没有时用派生 ID：422 INVALID（记“预期”）。打开后学生的今日计划另走跨科编排，行名改为 `today.orch`，不与基线 `today` 比较 |
  | `my_plan` / `monthly_plan` | 学生 / 家长 | my-plan / monthly-plan | `STARBUDDY_MONTHLY_PLAN` | my-plan：本月无快照 200（plan 为空）。monthly-plan：本月无快照要调洞察服务生成，本栈不起洞察服务 → 503 UNAVAILABLE；**只有 BFF 访问日志里核对到 `generate=not_configured` 的才算“预期”**，核对不到的改记为错误 |
  | `star_map_v1` | 学生 | star-map/v1 | `STARBUDDY_STARMAP=starmap_v1` | 没有掌握配置时全中性（200） |
  | `diagnose_more` | 学生 | 续做 → 取题 → 探针 → 放弃 | — | 续做应回 200 且是原会话（暂停的会话恢复，不另预留）；若回了新会话（201 或不同 ID，原会话已结束或过期时）单记 `resumed_new` 并告警，新会话照样放弃。会话不在进行中时探针 409 STEP_NOT_CURRENT（记“预期”） |
  | `weekly_report` / `profile_pages` | 家长 | weekly-report、profile-pages/math | `STARBUDDY_TRUST_UI=weekly,profile` | 本栈没有已生成的周报与档案页：200（report / page 为空），不设预期码 |
  | `prediction`（不进 all） | 家长 | exams/{派生 ID}/prediction | `STARBUDDY_TRUST_UI=reconcile` | BFF 没有接预测客户端（`main.ts` 不设），本栈与任何环境都一律 503 UNAVAILABLE：只测鉴权、档案读取与拒绝路径（记“预期”） |

- 场景核对（报告“可选场景核对”一节）：按步骤给真 ID / 真票据与派生路径各多少次、派生中“预期”占比；派生路径里出现非预期结果、
  真路径里出现非 2xx、诊断续做回了新会话、月度计划 503 未核对到 not_configured，都标**告警**（stdout 的 `extras_alert`）。
- `--run-id <ID>`：运行 ID 进幂等键派生（同一种子多轮演练时键不重复）；不给时 digest 不变。`--max-model-calls N`：驱动器侧模型调用硬上限
  （道法、语文批改按学习服务调用次数累计），到上限即不再发走模型的请求与新旅程，报告注明，退出码 1。
- SLO：`config/slo.yaml`（**待技术负责人签字**）按路由组给 p50 / p95 / p99、错误率、429 与 503 SUBJECT_BUSY 占比的上限；报告每组给
  “通过 / 未达 / 无通过线 / 无样本”（总体与按阶段），边界值算通过。“预期”结果不进任何一项、单列计数；分位数只取 2xx 与 402 业务拒绝的请求。
  过闸门的诊断取题单独成组（`gated_read`，有 503 BUSY 上限）。未签字时**只报告，退出码不因未达失败**；签字（`signed_by`、`signed_at`）后
  `enforce: true` 或 `--slo-strict` 才严格（未签字时 `--slo-strict` 直接退出码 2）。
- `--profile peak2x`：以 `config/peak2x.yaml` 的 `peak_concurrency`（**合成占位 10，待确认**；`--peak-concurrency` 可覆盖，报告注明）为 1 倍，
  0.5× → 1× → 2× 各 `ramp_seconds`，再 2× 保持 `hold_seconds`；可与 `--subject-gate`、`--fault fake-a=slow:800` 组合；超过 20 并发仍须 `--i-know`。
  报告写 `peak2x.md` / `peak2x.json`（首段“本机合成，不是容量结论”），另有“2 倍峰值各阶段”表：吞吐、分位数、错误率、429 / 503、预期、
  连接峰值（pg_stat_activity，按角色：总 / 活跃 / 锁等待 / 活跃中等待）、闸门排队分档，与各阶段慢查询（pg_stat_statements 边界快照相减）。
  延迟按到达阶段归属，连接、慢查询、闸门按墙钟阶段归属。
- PLT-21 起写路径已补上（见下一节）：上表 `practice_answer` 一行的“派生票据 422 记预期”只在写路径没接上时（`--no-write-path` 或缺依赖）成立。

### 写路径（PLT-21，`specs/loadtest-write-path-plt21`，`loadtest/writepath.py`）

打开再练与复测（`--with-practice` 或 `practice_answer`）时缺省接上写路径，再练 / 复测取题回 ready 与真票据，作答经判分落库、计次：

- 合成题：学习服务再练合成题面夹具里的 12 道客观题（SYNP-*），写进阿鸦内容包的合成副本（真实内容包原样 + 这些题的答案、来源已核、Q2、admitted；
  经阿鸦自己的 `indexPack` 校验），阿鸦另起一个进程用它（`AYA_CONTENT_PACK`），generate_variation 与 grade
  都走它的合成题路径（验证器，确定性判分）；
  - 替代内容包只在候选版开关打开时允许，所以合成阿鸦进程带 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`。**副作用**：这个进程同时接受 1.1.0
    候选版请求（含 present_item），只限该进程（其余适配器、学习服务、BFF 都不开）；学习服务对它仍按 1.0.0 发请求，题面来源仍是合成夹具；
  - 合成副本的准入决定记录（`admission_decisions`）是**合成占位摘要，不是真实准入依据**（导出编号 `SYN-EXPORT-LT-PLACEHOLDER`、全零 sha256），
    条数与副本里带审核依据的题数一致；真实内容包已有准入决定记录时拒绝生成副本（不与真实依据混用）；
  - `e2e_cohort.stack.adapters` 一次起三科，原阿鸦进程照起但不再收到请求（那是 e2e-cohort 的共用接口，本任务不改）；
- 登记与准入：阿鸦内容登记脚本的 `registrationSql`（`node/aya-register.mts`）登记为 pending_review，两名合成 staff（新建；ID 已存在即停下）
  经发布闸门签准入并执行（`sign_admission` → `change_admission`）；合成地图草稿（两个知识节点、层次 apply、题目族与打标）经两人签字与
  `publish_map_version` 发布，以学习服务角色绑到阿鸦内容包的草稿内容版本；之后两名 staff 停用。不用复制模式绕过守卫；每段 SQL 先核对是本机
  Unix socket 上的一次性库（`starbuddy_test*`）；
- 知识服务：起真知识服务（`services/knowledge/.venv`，一次性登录角色只继承 `starbuddy_kge_read`，只读这个一次性库，只配学习服务的合成令牌）；
  学习服务按 `packages/contracts` 知识服务 OpenAPI 严格校验它的响应（不另写替身）；
- 掌握配置（PLT-22，`specs/loadtest-mastery-path-plt22`）：学习服务以 `LEARNING_MASTERY_CONFIG_AYA` 加载压测专用副本
  `tools/loadtest/config/mastery.synthetic.json`——由学习服务单测用的 `services/learning/config/mastery/synthetic-test.json` 原样复制，
  **数值一个未改**，只改了 `$comment`（注明压测专用、与单测配置解耦、数值待人确认）；`synthetic: true`、未签字，**参数是测试占位，不代表任何
  学科口径**。学习服务原文件不动；`tests/test_mastery_path.py` 核对两份除注释外逐项相同（单测配置改了会提醒人决定是否同步）。学习服务的
  加载器只在 `LEARNING_ENV=test` 接受合成或未签字配置；本工具起栈前再核对一遍（synthetic、未签字、学科 aya）；
  未签字的配置下状态机最高只到“稳定”，不会点亮星。同步重算的时间预算本栈不设，取服务缺省（从 `learning.config` 读，读不到时报告标“假设”）；
- `--no-mastery`（对照）：写路径照接，但学习服务不加载掌握配置——作答后不重算、不生成复测任务（复测列表没有到期条目），报告只给
  作答 = 计次 = 作答证据三项与“单元重算次数 / 复测任务数应为 0”。用来和同参数开掌握的运行比较闸门 503 次数与作答延迟；
- 种子（PLT-22 起）：每个合成学生压测前经学习服务 grade 写入路径串行做两道——先做对一道 N2 的题（复测参照题），再做错一道 N1 的题
  （再练首轮间隔 0 小时，立即到期）。**工具不写任务表**（PLT-21 手工种入 `verify_in_platform` 的做法已去掉）：复测任务只由落库后的同步重算生成。
  合成配置下一道题的置信度（质量 / (质量 + k)）低于复核下限，重算当即给两个单元各排一条 `decay` 任务，到期 = 最后一条计入证据的记录时间
  （KGE-04 现有口径，**没有用时间注入**；学习服务进程也没有可从外部注入的时钟）。复测列表按到期时间排序，先做的 N2 排前面，驱动器取第一条
  到期条目 → 复测落在 N2，不与再练（N1）撞同一单元；N1 那条 decay 在再练作答后由重算撤销（若复测也落在 N1，已签发的复测票据会因任务被撤销
  回 409 RETEST_NOT_DUE——这是服务的正确行为，先做对 N2 的顺序就是为了避开它）。压测中复测作答把 N2 推到两族独立做对、到“执行”时，
  重算排出复测轮（rung0），间隔 7200 s，压测窗口内不到期，只计数。种子之后核对：全部任务能证明由重算写出、每名学生至少一条开着的到期任务，
  否则立即失败（`--no-mastery` 时不核对）。种子只用于准备数据：**不经 BFF 与计量**（不预留、不结算、不计次），不计入压测指标；
  种子只发给 127.0.0.1 上本栈的学习服务，判分结论必须正好是 practice:incorrect × n、retest:correct × n，否则立即失败；
- 驱动器：写路径接上时派生条目（404）的“预期”自动停用，取题一律记真 / 派生路径，派生的照发、按错误计；**取题没拿到真票据（上游非 2xx、
  闸门 503 或没有可作答的题）时不再用派生票据去作答**（PLT-22 审查）：这一步不发、记为跳过，另计“上游未取到（未发作答）”，不计入写路径一致性。
  因此上游一有失败，请求序列 digest 就与计划不一致（同前置失败的跳过）；写路径没接上时照 PLT-20（派生票据照发、422 记预期）；
- 报告“写路径（PLT-21 / PLT-22 掌握路径）”一节（只取本轮合成学生、压测前后增量）：
  - 作答（驱动器记的真票据 2xx）= 合成题计次增量（`platform.practice_log` 中经幂等键关联到 SYNP-* 作答的行）= 作答证据行增量（SYNP-* 的作答证据，每次作答一行）
    = 触发的单元重算次数（PLT-22）；
  - 重算的触发口径（按服务实际规则）：每次 grade / diagnose **新落库**提交后（重放不触发），该科有掌握配置时同步重算这次作答涉及的单元——只按主节点，
    单元 = 当前已发布地图里打了标的题的主节点 × 层次（`learning/service.py` `_recompute_mastery` → `mastery/recompute.py` `after_ingest`，
    时间预算 2 s，超时记 failed 交给对账）。本栈只有 SYNP-* 在合成地图里打了标：学生旅程的数学批改与诊断也触发 after_ingest，但不涉及任何单元。
    所以每次合成题作答恰好重算一个单元。单元重算次数取重算专用的“读当前记录”语句（`mastery/store.py` `current_estimate`，服务里只此一处）在
    `pg_stat_statements` 里的调用数（阶段边界快照相减）；重算在 `SET LOCAL ROLE starbuddy_learning` 下执行，按语句文本计、不按登录角色过滤。
    一致性的触发条件逐条说明：
    - **仍触发、仍算一次**：计入不了掌握的证据（同族重做 repeat_family、判分置信度低于下限、提示后做对、待复核、C 级）——after_ingest 照样
      对作答的主节点单元重算，重放后不写新记录（变化低于写入阈值），但“读当前记录”照样执行一次；
    - **不触发**：幂等重放（同一幂等键的第二次请求直接回原结果）、落库失败（判分或证据写入出错，作答也不算 2xx）、该科没有掌握配置
      （`--no-mastery`）、题目在当前已发布地图里没有打标（学生旅程的真实阿鸦题、诊断题）；
    - **多算一次**：同一单元并发写撞唯一索引（23505）或更正守卫（23514）时整单元事务回滚、重试一次，会多读一次当前记录（日志里看不到），
      表现为“作答 < 单元重算次数”而判不通过；同步预算超时（`mastery_after_ingest_timeout`）时已开始的单元也已读过，计入重算次数、另记失败；
  - 复测任务全部来自重算，**推断 + 直接证据**：
    - 推断（逐条）：复测轮（scheduled / early）的 `scheduled_by_estimate_id` 指向同学生同单元、**同一事务**写出的掌握记录，且到期等于其
      `next_retest_at`；其他原因（decay 等）要求同单元有同一事务写出的记录。“同一事务”靠两个触发器把时间戳置为事务开始的 `now()`：迁移 0005 的
      `guard_inference_insert`（`mastery_estimate.computed_at`）与迁移 0037 的 `retest_task_guard`（`retest_task.created_at`）；压测后只读核对
      两个触发器存在且启用。都不满足的记为“不能证明”（应为 0；PLT-21 手工种入的那种行就是这一类）；
    - 直接证据：`pg_stat_statements` 里所有写 `platform.retest_task` 的语句（INSERT / UPDATE）——只能是学习服务重算 / 自述验证的那几条
      语句文本（`writepath.SERVICE_TASK_WRITES`，测试核对它们出自 `mastery/recompute.py`）且由 `starbuddy_learning` 执行；其他角色或其他语句
      的调用数应为 0。pg_stat_statements 压测前清零一次，所以种子阶段在清零前取一次、压测后再取一次，两次相加；
  - 测到与没测到的：decay 任务在后续作答后由重算**撤销**（cancelled，置信度回到下限以上），不是“通过 / 失败”关闭；KGE 复测轮的关闭
    （passed / failed）与靠复测轮通过的晋级（执行 → 稳定）在压测窗口内没有测到（rung0 间隔 7200 s），只测到了 unseen → understand → execute；
  - 重算失败 / 超时：学习服务日志里 `mastery_after_ingest_timeout`、`mastery_after_ingest_failed`、`mastery_recompute_failed` 的条数（只读 event 字段），应为 0；
  - 掌握重算耗时（“掌握重算”小节，总体与各墙钟阶段 p50 / p95 / 最大）：数据库事务提交时间戳（`track_commit_timestamp`，只在一次性实例随
    pg_stat_statements 同一次重启打开）——“单元事务”= 重算事务开始（`computed_at`）→ 提交；“作答后重算”= 这名学生最近一次合成题作答证据的
    落库提交 → 重算事务提交（含 after_ingest 预读地图与证据的只读事务）。只覆盖写出新记录的重算（变化低于写入阈值的不写记录、不在样本里），
    报告并列单元重算次数与写出记录数；服务源码不改、不加日志；
  - 门槛状态变化：写出的记录里“前一状态 → 新状态”计数（没有前一条记作“无”）；
  - “计次无对应作答证据”与“作答证据无对应计次”都为 0，全部计次 = 合成题计次 + 无对应作答证据的计次（没有计次关联到合成题以外的作答）；
  - 口径说明：题库自练是“开始即计数、没有释放”（BFF 先 `meter_practice` 再调学习服务，`specs/metering-v2` 偏差 13），计次之后学习服务失败会留下
    “计次无对应作答证据”——这是设计口径，不是计量故障；本机无故障注入时应为 0，出现即判不通过；
  - 不通过时退出码 1；另给各环节耗时（学习服务、知识服务、阿鸦各自访问日志的方法名、状态码、耗时；压测期间；学习服务作答环节已含同步掌握重算）；
  - 口径变化（PLT-22 审查）：“慢查询前十 / 各阶段慢查询”的角色过滤加入 `starbuddy_learning`——pg_stat_statements 记的是执行时的当前角色，
    学习服务在 `SET LOCAL ROLE starbuddy_learning` 下执行的语句以前不在表里；PLT-22 起计入，与 PLT-21 及以前的样例不可直接比较。
    连接采样（pg_stat_activity）仍按登录名（`learning_app`、`bff_app`）；
  - 报告里的错误率、429 / 503 占比改为百分比两位小数（以前 0.0073 显示为 0.0）；
  - 2× 阶段数学闸门 503 SUBJECT_BUSY 的增量可能来自同步重算拉长了作答占用闸门的时间，用 `--no-mastery` 同参数对照；
- 缺 `services/knowledge/.venv` 时照旧（报告注明“未接上”与原因）；`--no-write-path` 显式关（对照 PLT-20）；`--require-write-path`
  要求写路径必须接上，没接上（或没打开再练与复测）退出码失败，用于确认写路径确实被测到；
- 报告的 Markdown 由写出的 JSON 读回后渲染：`sample/peak2x.json` 重渲染与 `sample/peak2x.md` 逐字节相等（`tests/test_write_path.py`、
  `tests/test_mastery_path.py` 核对；后者另核对样例含掌握测量、没有手工任务）。

假供应商的 `/_control/faults`、`/_control/stats` 与计量驱动桩的接口**没有鉴权**：只监听 127.0.0.1、只在本机一次性栈里存在，
不得暴露到任何共享网络（预发故障演练见 RUNBOOK 第 2 节）。

依赖：`services/learning/.venv`、本机 Postgres 16 服务端、`node`，`adapters/{aya,wenli,zhixing}` 与 `services/bff` 的 `npm ci`。缺依赖时跳过（`CONTRACT_STRICT=1` 失败）。
写路径（PLT-21）另要 `services/knowledge/.venv`；缺时只是写路径不接上。

## 组成（`loadtest/`）

| 文件 | 作用 |
|---|---|
| `arrivals.py` | 固定种子的泊松到达、分阶段加压（到达率 = 并发数 / 平均会话时长）、旅程配比、思考时间；幂等键由（种子、到达、步骤）派生；`digest()` 给请求序列的 sha256 |
| `scenarios.py` | 旅程：学生（登录 → 学生模式 → 今日计划 → 数学诊断开始 / 取题 / 作答 / 暂停 → 三科批改 → 星图）、家长（登录 → 首页 → 孩子的星图 / 额度 / 选科）、计量（同键两路并发预留 → 部分结算 → 重复结算 → 预留后释放） |
| `stack.py` | 本机一次性栈：`pgharness.EphemeralPG`（全部迁移）+ 假供应商 + 三科适配器（复用 `e2e_cohort.stack.adapters`）+ 学习服务（`python -m learning`，路由表 test 节）+ BFF（`node src/main.ts`，STARBUDDY_ENV=test、放宽限流的一份配置、短信捕获桩） |
| `seed.py` | 计量时钟替换（北京时间 10:00 起、可快进，只在测试库）、`v12-pilot@1` 的已发布副本 `loadtest-plan@1`、合成家庭（+999 号码、同意、选科、经 `meter_open_subscription` 开通） |
| `driver.py` | 负载驱动：每阶段会话并发上限、真实 BFF 会话、批改经计量驱动桩运行 BFF 的 meteredCall（见“已知偏差”）、记录实际请求序列 |
| `fake_provider.py` / `faults.py` | OpenAI 兼容的假供应商（独立进程，延迟为对数正态，token 按字符数估算）；故障注入：整家不可用、超时、5xx 比例、慢响应、限流；同一判定也给进程内 `FakeProvider` 用（不改 `model_router/providers.py`） |
| `dbstats.py` | `pg_stat_activity` 采样（按登录角色的连接峰值）与 `pg_stat_statements` 慢查询前十（只在临时实例打开；PLT-22 起语句统计含 `starbuddy_learning`，另开 `track_commit_timestamp`） |
| `meter.py` | 计量旅程的计量函数调用（与 BFF 同一组函数、同一角色）与零泄漏核对 |
| `harness.py` + `node/meter-harness.mts` | 计量驱动桩：在本机运行 BFF 的 `meteredCall`（批改的计量全走它）；计量时钟由调用方经 `/clock-sync` 对齐 |
| `metrics.py` / `report.py` / `baseline.py` | 分位数（最近秩）、错误率、吞吐；Markdown + JSON 报告；一次压测的编排 |
| `writepath.py` + `node/aya-register.mts` | PLT-21 写路径：合成内容包、登记 / 准入 / 地图种子 SQL（经闸门）、知识服务环境、到期条目种子、一致性核对与环节耗时（`tests/test_write_path.py`）；PLT-22 合成掌握配置、任务来源核对、单元重算计数、重算耗时与状态变化（`tests/test_mastery_path.py`） |
| `bodies.py` / `slo.py` / `peak.py` / `phases.py` | PLT-20：新场景的请求构造与票据处理；SLO 判定；peak2x 配置与阶段；按阶段的连接、慢查询、闸门汇总（均为纯函数，`tests/test_peak2x.py`） |
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
- 再练与复测场景（`--with-practice`）只有列表与取题；作答在 PLT-20 的 `practice_answer` 场景里；PLT-21 起写路径接上后取题 ready、作答落库计次，
  写路径没接上时只走到 BFF 的票据校验（见上文）；
  闸门的计次前拒绝由 `services/bff/tests/subject-gate.integration.test.ts` 覆盖。
