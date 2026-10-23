# 调度器（services/scheduler）

PLT-13 第一步（`specs/scheduler-plt13/spec.md`，默认设计第 1–7 条，全部待人确认）：只负责“按时间表调用已有入口”。
不监听端口、不写任何业务表、不新增授权、不引入新依赖（ADR 10：表队列 + 定时任务）。只用于 `SCHEDULER_ENV=dev / test` 的本机库，不接生产调度（规则 9）。

## 安装与运行

```
cd services/scheduler && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
```

锁文件直接引用洞察服务的锁（同一组包、同一版本）：启动核对复用审核台命令行的 `local_only` / `check_server_local`，其导入链需要这组包。
子进程用各服务自己的 venv（`services/insight/.venv`、`services/learning/.venv`）。

```
SCHEDULER_ENV=dev <各身份的变量> .venv/bin/python -m scheduler                 # 常驻
... .venv/bin/python -m scheduler --once meter-expire                            # 立刻跑一个任务一轮
... .venv/bin/python -m scheduler --dry-run [--once <任务>]                      # 只核对并列计划，不连库
```

测试与变异：`make scheduler-test`、`make scheduler-mutation`（均已接进 `make check`）。

## 时间表（`config/schedule.synthetic.yaml`，Asia/Shanghai，合成默认值）

| 任务 | 时间表 | 身份 | 子进程 |
|---|---|---|---|
| meter-expire | every 5m | scheduler | `insight.scheduled meter-expire`（`platform.meter_expire_due`） |
| meter-renew | hourly :07 | scheduler | `insight.scheduled meter-renew`（`platform.meter_renew_due`） |
| erasures | hourly :17 | scheduler | `insight.scheduled erasures`（`platform.execute_due_erasures`） |
| freeze | hourly :01 | scheduler | `insight.scheduled freeze`（`insight.freeze_due_predictions`） |
| sweep | daily 02:30 | insight | `insight.jobs sweep` |
| mastery-sweep | daily 02:30 | learning | `learning.jobs mastery-sweep` |
| expire-affairs | daily 02:30 | kge | `review_console expire-affairs` |
| spot-check | daily 02:30 | scheduler | `insight.scheduled spot-check`（`platform.review_sample_spot_checks`，比例 0.1，回看 26 小时） |
| metrics | daily 03:20（频率待人确认，SIE-05） | insight | `insight.jobs metrics --all --skip persistence`（中圈指标静默重算，只写 agent 可见的指标判断；暂不重算学习持续性，与迁移 0025 的关系待人确认） |
| weekly | weekly mon 03:00（启动时补跑一次） | insight | `insight.jobs weekly`（上一周的周报） |

表达式：`every <N>m`（N 整除 1440）、`hourly[ :MM]`、`daily HH:MM`、`weekly <周几> HH:MM`。

## 身份与环境白名单

| 身份 | 可调用的子命令 | 运行环境变量 | 连接串变量 | 可声明的附加变量（显式白名单） | 子进程“拒绝”退出码 |
|---|---|---|---|---|---|
| scheduler | meter-expire、meter-renew、erasures、freeze、spot-check | `SCHEDULER_ENV` | `SCHEDULER_DATABASE_URL`（登录角色只继承 starbuddy_scheduler） | 无 | 2 |
| insight | sweep、weekly、metrics | `INSIGHT_ENV` | `INSIGHT_DATABASE_URL` | `INSIGHT_POLICY`、`INSIGHT_WEEKLY_POLICY`、`INSIGHT_WEEKLY_CHECK` | 2 |
| learning | mastery-sweep | `LEARNING_ENV` | `LEARNING_DATABASE_URL` | 前缀 `LEARNING_MASTERY_CONFIG_*` | 2 |
| kge | expire-affairs | `REVIEW_CONSOLE_ENV` | `REVIEW_CONSOLE_KGE_DATABASE_URL` | 无 | 1、2 |

未列出的子命令（判断生成 `sync`、`admission-recompute`、审核台 `serve` 等）一律拒绝启动。

子进程环境 = 固定的 `PATH=/usr/bin:/bin`、`PYTHONDONTWRITEBYTECODE`、`PYTHONIOENCODING`、`LANG` + 该身份的两个变量 + 任务声明的附加变量。
附加变量只认上表的精确名或登记的前缀，不按“前缀 + 黑词”放行；名字以 `_URL`、`_DSN`、`_DATABASE_URL` 结尾的变量只认本身份的连接串变量，
通配也匹配不到这类名字（如 `LEARNING_MASTERY_CONFIG_X_URL`）。
审核台命令行（review_console）以 `SystemExit("说明")` 拒绝时退出码是 1：kge 身份下 1 与 2 都按 `refused` 记（调度器退出码 2）。

传给子进程的连接串追加 `statement_timeout`（任务超时的 90%，默认 540 秒）：asyncpg 把连接串里不认识的查询参数当作服务端设置，
四个子命令都用这一个连接串连库，所以不用改子命令。

## 运行记录与告警

每轮一行 JSON 日志（标准输出）：`job`、`outcome`（ok / partial / refused / crashed / timeout / bad_output / lock_error / lock_lost /
skipped_locked / skipped_running / dry_run）、`exit_code`、`child_exit`、开始与结束时间、`counts`（子进程最后一行 JSON 的计数，
清洗后不含学生 ID）、`reason`（子进程标准错误末行：去掉连接串与 `user=…`，UUID 换成 `uuid`，只留 `[A-Za-z0-9_ .:()-]`，SQLSTATE 保留）。
非 0 退出、超时（任务级默认 10 分钟）、锁丢失、`stale_estimate > 0`、上一轮未结束一律 `level: alert`。

## 互斥、探活与补跑

- 互斥：按该任务身份的连接取 `pg_try_advisory_lock`（键由任务名派生），取不到记 `skipped_locked`。**会话级咨询锁要求直连 Postgres**：
  经事务级连接池（如 PgBouncer transaction 模式）转发时锁会失效，部署时不得走这类连接池。
- 子进程运行期间每 15 秒对锁连接 `SELECT 1` 探活；连接丢失（锁随之释放）立即杀掉子进程组，记 `lock_lost`。
- 启动补跑只给周报（`catch_up: true`，配置只允许 weekly）：调度器启动时立即跑一次，已生成的周由子命令幂等兜底；其他任务不补跑，错过的轮次也不补。
- 健康监听（LT-03，`scheduler/health.py`，接口定义 `openapi/health.v0.yaml`）：常驻模式下设了 `SCHEDULER_HEALTH_PORT` 才起（`SCHEDULER_HOST`
  缺省 127.0.0.1，staging 另收内网 IPv4）；`GET /healthz` 存活、`GET /readyz` 就绪（循环在跑、收到 SIGTERM 即未就绪，且能用
  `SCHEDULER_DATABASE_URL` 连库并核对服务端）；无需鉴权、只回状态、不记日志。配置不合格拒绝启动（退出码 2）。

## 与 BFF 的关系

`services/bff/src/metering/jobs.ts` 的 `runExpiry` / `runRenewal`（计量过期与续期的一次性入口）已由本调度器的 `meter-expire`、`meter-renew`
取代，只供 BFF 自己的测试使用；本步不改 BFF。
