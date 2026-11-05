# CI 计费分钟瘦身（2026-11-03）

起因：2026-11-02 至 11-03 两天 7 次 Actions 运行烧掉约 2711 个 job·分钟 ≈ $16.27，
把整个 GitHub 账户当月包含的 $12（约 2000 分钟）额度用光。后果不限于本仓库：账户下
**所有私有仓库**的 Actions 都起不来（job 3 秒失败，注解写 "recent account payments have failed
or your spending limit needs to be increased"），另一个私有仓库 lvban-agent-core 也被连累。
额度 2026-12-01 重置。用户不绑支付方式、不改仓库可见性，所以只能把计费分钟压下来。

## 一、计费口径（先搞清楚，否则会优化错方向）

GitHub 按**每个 job 各自的分钟数**计费，不是整次运行的墙上时间，且**每个 job 单独向上取整到分钟**。
所以“多开并行 job”只买墙上时间，一分钱都省不下来，反而每个 job 多付一份启动开销。

## 二、实测基数（run 41577261661，2026-11-03，23 个 job，约 554–563 计费分钟）

数据来源：Actions API 的 job/step 时长 + 下载各 job 日志、按目标完成标记的时间戳相减。
（注：`/timing` 接口在本账户返回 0，用不了，上面的数字是自己量的。）

make-check 11 组，每组 = 启动开销 + make 时间：

| 组 | job 计费 | make | 其中 *-mutation | 轻量部分 |
|---|---|---|---|---|
| bff-web | 69 | 67.0 | **55.9**（bff-mutation 54.2 + stage-mutation 1.7） | 11.1（web-test 9.7） |
| kge-model | 49 | 48.2 | **47.0**（kge-irt 27.9 + kge-calibrate 19.1） | 1.2 |
| learning-a | 38 | 36.7 | **32.7**（mastery 9.8、model-router 8.7、diagnosis 7.6、practice 6.7） | 4.0 |
| knowledge | 29 | 27.6 | **25.9** | 1.7 |
| aya | 26 | 24.9 | **24.8**（adapter-aya-mutation） | 0.1 |
| learning-b | 19 | 18.7 | **16.5**（7 个） | 2.2 |
| persona | 17 | 15.5 | 0 | 15.5 |
| kge-tools | 17 | 15.4 | **14.5**（dedupe 11.7 + chinese 2.8） | 0.9 |
| insight | 16 | 14.9 | **10.4** | 4.5 |
| contracts | 16 | 14.2 | **11.9**（contract-mutation 8.6 + conformance-mutation 3.3） | 2.3 |
| adapters | 3 | 2.5 | ~0.5 | ~2.0 |
| **合计** | **299** | 285.5 | **约 241** | **约 57** |

db-mutation：12 个分片，每片 21.6 分钟（完全均衡），计费 12×22 = **264**。

启动开销实测：checkout 2–3 秒、setup-python/node ≈1 秒、装 postgresql-16 5–10 秒、
bootstrap-deps.sh **60–96 秒**。每个 make-check job 合计约 1.2 分钟。

## 三、改了什么

按事件切成两份（`-light` 与 `-mutation` 在 Makefile 里用同一个模式互补切分）：

| 事件 | 跑什么 | 计费分钟 |
|---|---|---|
| pull_request | 各组 `-light` | **约 65** |
| push 到 main | 各组 `-light`（**本次改动**，原来是全量 554） | **约 65** |
| workflow_dispatch（默认 scope=mutation） | 各组 `-mutation` + db-mutation | 约 523 |
| workflow_dispatch scope=full | 全量 | 约 563 |

1. `push: branches: [main]` 从全量改成只跑轻量 —— **每次合并 554 → 65，省约 489 分钟（88%）**。
2. 新增 `check-group-<组>-mutation` 与 `check-mutation` 目标（`$(filter %-mutation,…)`），
   手动触发时只跑变异那一份，不再把已经在合并提交上跑过的轻量检查重跑一遍（省约 31 分钟/次）。
   不变式：对每一组 `-light` 的目标 ∪ `-mutation` 的目标 = 整组的目标（已逐组核对，见第五节）。
3. PR 上 `fail-fast: true`：本月 10 次运行里 6 次失败，失败时早停把一次运行从约 65 压到约 30。
   push 与手动触发仍 `fail-fast: false`（要一次看全）。
4. 超时从一律 90 分钟改成：轻量 45、手动触发 90。卡死的 job 会一直烧额度到超时，
   原来一个卡死的组能单独烧掉 90 分钟。

额度账：2000 ÷ 65 ≈ **30 次推送/月**（原来 push 到 main 一次就吃掉 554，4 次见底），
按 22 次推送算还剩约 570 分钟，够一次完整的手动变异扫描。

## 四、代价（明说，没有悄悄降覆盖）

**变异测试不再守护每一次推送与合并。** main 上的每个提交只有 `-light` 把关；
28 个 `*-mutation` 目标与 db-mutation 的 1251 个变异要靠手动触发。
风险：判分、掌握计算、计量加锁、RLS 这类**只有变异测试才发现**的回归，
可能在 main 上躺到下一次手动跑才暴露。

因此约定（已写进 check.yml 的注释）：改动 `db/migrations`、判分与掌握计算、
计量与权益、RLS 与权限策略时，合并前必须手动跑一次 `scope=mutation`。
这与 CLAUDE.md“必须停下来请人确认的变更”覆盖的范围基本重合。

PR 上 `fail-fast: true` 的代价：一次只看到第一个失败的组，修完要再推一次才看到下一个。

## 五、验证到哪一步（**没有任何一项经过 CI**）

Actions 被停到 2026-12-01，所以下面都是本机验证：

- `actionlint v1.7.7` 对 check.yml 零告警（exit 0）。未装 shellcheck，`run:` 块的 shell
  没经过 shellcheck。
- 逐组核对 `-light` ∪ `-mutation` = 整组，11 组全部一致，28 个变异目标一个不少
  （用 `make -np` 读实际先决条件比较，不是看字面）。
- 工作流能生成的 33 个目标名（11 组 × light/mutation/full）全部能解析，无 "No rule to make target"。
  `check`、`check-core`、`check-light`、`check-fast` 不受影响。
- `make check-group-adapters-mutation` 真跑了一遍（本机无依赖，非严格模式正常打印“跳过”）。
- 按 GitHub 的 `bash --noprofile --norc -eo pipefail` 模拟了那段选后缀的 shell，
  SUITE=light/mutation/full 三种都 rc=0、后缀正确。
  （踩过一个坑：原来写 `[ "$SUITE" != full ] && suffix=…`，`bash -e` 下 SUITE=full 时
  整行返回 1，会把这一步直接判失败。已改成 if。）

**只能等额度恢复后才能真正验证的部分：**
1. `-light` 与 `-mutation` 在 CI 上是否真的各自通过（`-light` 这一份从未在 CI 上跑过——
   上一次改动合并后的两次 PR 运行都被计费拦在 3 秒失败）。
2. 上面那张“约 65 / 约 523 分钟”的预估（按实测基数算的，没实跑过）。
3. `timeout-minutes` 的表达式、`inputs.scope`、顶层 `env.SUITE` 在真实事件下的取值。
4. `fail-fast: true` 在 PR 上的实际早停效果。

## 六、考虑过但没做

- **依赖缓存**（原以为是大头，实测不是）：bootstrap-deps.sh 每个 job 只 60–96 秒，
  11 个 job 合计约 13 分钟，占 554 的 2.4%。依赖树很小（npm 90 个包、Python 约 110 个包），
  缓存 12 个目录的恢复时间和重装差不多，还多一套缓存键要维护。不做。
- **减少 db-mutation 分片**：脚本自己已按 `nproc` 并行（`DBMUT_JOBS`），12 片买到的是
  墙上时间（21 分钟 vs 单机 229 分钟），计费只比单机多约 30 分钟的启动开销。
  这一份现在只在发布前手动跑，那时等 21 分钟比等 3.8 小时值得。保留 12 片。
- **`paths-ignore`（只改文档时不跑）**：不安全也不划算。CI 实际会读 `docs/` 下几乎每个子目录
  （personas 87 处、design 77、mapping 34、subjects 16、compliance 14、plan 10、adr 1），
  所以 `docs/**` 不能整体忽略；能证明没人读的只有 `docs/archive/**` 和 `.claude/**`，省不下几次。
  更要紧的是：`paths-ignore` 会让被跳过的 PR 永远不上报必需状态检查，PR 直接卡住合并。不做。
- **把小组合并成更少的 job**（轻量模式下 6 个组各只干 ≤3 分钟，启动开销占 13/65）：
  轻量下省约 8 分钟，但这几组在变异模式下恰恰是最贵的（kge-model 48、knowledge 28、aya 26），
  合并会把 120 分钟串成一个 job，墙上时间无法接受。要两种事件用两套分组，需要动态矩阵，
  在没法跑 CI 的当下不值得冒这个风险。

## 七、下一步（最大的结构性机会，需要另起任务 + 人拍板）

**把各服务的变异测试脚本改成并行。** `db/tests/mutations.sh` 早就按 `nproc` 并行了，
但 `services/bff/tests/mutations.sh` 等是严格顺序的 `for` 循环（每个变异起一个一次性
Postgres + 跑一遍集成测试，最多重试 3 次）。标准 runner 是 4 核，所以：

- bff-mutation：494 个变异 54 分钟 → 4 路并行理论上约 15 分钟
- 变异那一份整体 241 分钟 → 可能压到 80–100 分钟

**但有真实风险，不能盲改**：bff 的那些变异是**并发时序相关**的
（脚本开头自己写了“并发相关的失败依赖时序……每个变异最多跑 3 次，任何一次失败就算被发现”）。
4 个变异同时抢 4 个核，时序会变，可能让某个变异从“被发现”变成“漏检”——
那是**悄悄降覆盖**，比省钱严重得多。要做必须先写 spec、先在额度恢复后用对照跑法验证
（同一批变异顺序跑一遍、并行跑一遍，核对“被发现/漏检”的名单完全一致）。

另一个方向：**按改动路径决定跑哪些组**（只改 services/knowledge 就只跑 knowledge 那几个），
默认回退成全跑以保证不漏。省得多，但映射写错就会静默漏检，同样需要 spec 与人确认。
