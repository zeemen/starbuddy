# PLT-13 调度器第一步 · 实现记录

2026-10-12 起由 Claude 按用户授权推进；调度频率涉及计量、删除执行、掌握对账，提交首行 `[NEEDS-HUMAN]`。

## 调研要点（依据）
- 仓库此前没有常驻调度；各定时工作是“库函数 + 一次性入口”由人手动跑，或在请求路径上读时现算。
- 各任务所需库身份不同（scheduler / learning / insight / kge），按规则一个登录角色只能继承一个应用角色，不能合并成一个连接。
- ADR 10 定阶段 0–1 用 Postgres 自带能力；SIE、KGE 分册写的“Redis + Arq”与已采纳的 ADR 10 冲突，以 ADR 10 为准。

## 交付
- `services/scheduler`（无 HTTP 端口，不写业务表、不新增授权、无新依赖，锁文件引用洞察服务的锁）：
  - 时间表 `config/schedule.synthetic.yaml`（Asia/Shanghai；`every Nm`、`hourly[:MM]`、`daily HH:MM`、`weekly <周几> HH:MM`），`next_run` 纯函数。
  - 四个身份固定子进程模块、连接变量与允许子命令；附加环境变量按身份显式白名单（insight：`INSIGHT_POLICY`、`INSIGHT_WEEKLY_POLICY`、`INSIGHT_WEEKLY_CHECK`；learning：前缀 `LEARNING_MASTERY_CONFIG_`；scheduler、kge：无），`_URL/_DSN/_DATABASE_URL` 结尾的非本身份变量一律拒绝。
  - 运行器：子进程只拿本身份变量；`pg_try_advisory_lock` 互斥（取不到记 `skipped_locked`）；运行期间每 15 秒探活锁连接，丢失即杀子进程组（`lock_lost`）；连接串追加 `statement_timeout`（任务超时 90%）；超时 SIGTERM→5 秒后 SIGKILL；退出码映射 ok / partial / refused / crashed / timeout / bad_output；每轮一行 JSON 日志，计数与 stderr 末行经清洗。
  - 启动核对：`SCHEDULER_ENV` 与各身份 `*_ENV` 只收 dev/test，所有连接串本机且连上后 `check_server_local`。`--once`、`--dry-run`。
- insight：`python -m insight.scheduled freeze|erasures|meter-expire|meter-renew|spot-check`、`python -m insight.jobs weekly`（只调已有函数，不改库函数语义）。
- 测试与变异：调度器 204 项测试、变异 70 个；洞察服务 205 个变异，全部发现；9 个任务在一次性库上经 `--once` 端到端验证效果与幂等（含掌握对账写入与第二轮无新增）、并发互斥、锁连接断开、测试反序运行。

## 审查意见的处理（按默认）
- 安全阻断：附加变量“前缀 + 黑词”可把其他身份连接串传给子进程 → 改为按身份显式白名单。
- 契约阻断：lessons 缺失（本文件）；掌握对账端到端只证明调起 → 补合成数据，断言写入与幂等。
- 建议：锁探活与语句超时；stderr 与计数清洗；kge 身份拒绝映射为 refused；端到端去掉顺序依赖；weekly 只在周参数核对处报“周参数错”；周报启动补跑；抽检多学科变异；README 写明直连 Postgres 与 BFF `jobs.ts` 已被取代。

## 与 spec 的偏差
1. 周报启动时补跑一次（只有 weekly，`catch_up`），其他任务错过不补跑。
2. weekly 不触发学情档案页编译（`profile_pages=None`），等于关掉 KGE-04 第二步 PR 4“每周触发编译”的路径。

## 待人确认
1. **时间表全部为默认值（必停，需签字）**：计量预留过期每 5 分钟、批量 50；订阅续期每小时 :07；删除执行每小时 :17；预测冻结每小时 :01；每天 02:30 并发跑判断到期扫描、掌握对账、时政下架、判分抽检（比例 0.1、回看 26 小时，只抽 aya / zhixing / wenli）；周报每周一 03:00（生成上一周）。
2. 周报口径：用户规格写“周日生成”，0036 只收已结束的周，现按“周一生成上一周”。
3. 周报不再触发学情档案页编译（偏差 2），是否另配学习服务令牌恢复。
4. 探活间隔 15 秒、单次超时 5 秒；语句超时取任务超时的 90%；任务超时默认 10 分钟。
5. kge 身份把审核台的退出码 1 当作“拒绝”。
6. 会话级咨询锁要求直连 Postgres，经事务级连接池会失效。
7. 父进程环境里持有四个身份的连接串（spec 默认设计 2 接受，只要求不合并成一个连接）；子进程只拿本身份变量。
8. 删除执行：库函数把全部到期删除放在一个事务里，任一学生核对失败整批回滚、之后每小时都失败；调度器只记 refused 告警，没有“已超 24 小时”的逾期监控（需新授权，另起）。
9. BFF `services/bff/src/metering/jobs.ts` 的 `runExpiry` / `runRenewal` 与 insight `_meter_loop` 语义一致，后者多 `max_batches` 上限（默认 10000，到上限退出码 1）；前者只供 BFF 测试，以后防止两边漂移。
10. 调度器 venv 复用洞察服务整套依赖（因复用审核台本机核对，导入链需要 FastAPI）；可改为抽出纯标准库的本机核对模块。
11. 新环境变量名：`SCHEDULER_ENV`、`SCHEDULER_DATABASE_URL`、`INSIGHT_WEEKLY_POLICY`、`INSIGHT_WEEKLY_CHECK`。
12. 另起任务（迁移，必停）：会话、验证码、限流计数清理函数；分区维护 `ensure_monthly_partitions` 授权；运行记录表；月度计划每月 1 日批量生成（需“有权益学生清单”入口）；SIE-03 触发评估与下发（规则未签字）。

## 过程记录
- 子代理两次命令被保护钩子拦下（误带 `rm -rf /dev/null`；环境变量名含 TOKEN），均未执行。
