# PLT-13 调度器 · 第一步（零迁移：按时间表调用已有入口）

2026-10-12 用户要求继续 PLT-13。编号来源：specs/family-goals-monthly-plan/spec.md:4。
依据：ADR 03（:37、:62、:73）、ADR 10（:32、:73 阶段 0–1 用“表队列 + 定时任务”，Postgres 自带能力）、ADR 06（:39 预留过期释放）、ADR 05（:46 分区）、
SIE 分册 :458-460（每天、每周、每月任务）、用户规格 V1.2 :217、:343。调研见本目录 lessons。
涉及计量、删除执行、复测与掌握对账的调度频率（必停类），提交首行 `[NEEDS-HUMAN]`；频率全部是默认值，待人签字。

## 默认设计（写进 lessons 待人确认，可推翻）
1. **位置**：新建 `services/scheduler`（Python 包，无 HTTP 端口），只负责“按时间表调用已有入口”，自己不写任何业务表、不新增授权。
2. **执行方式**：每个任务以**子进程**调用所属服务已有的一次性 CLI，子进程只拿到该任务身份的环境（scheduler / learning / insight / kge 各自的连接配置，按任务声明的变量白名单传递），
   不合并成一个连接；输出一行 JSON 计数、退出码 0/1/2 沿用既有约定。
3. **时间表**（`services/scheduler/config/schedule.synthetic.yaml`，时区 Asia/Shanghai，全部为默认值）：
   - 每 5 分钟：计量预留过期 `platform.meter_expire_due`
   - 每小时：订阅续期 `meter_renew_due`、删除请求执行 `execute_due_erasures`、考前预测冻结 `freeze_due_predictions`
   - 每天 02:30：判断到期扫描 `insight.jobs sweep`、掌握对账 `learning.jobs mastery-sweep`、时政过期 `expire-affairs`、判分抽检 `review_sample_spot_checks`
   - 每周一 03:00：上一周家长周报（给 `insight.jobs_cli` 补 `weekly` 子命令，调用已有 `jobs.weekly_reports`）
   - 月度计划每月 1 日生成：本步不做（需要“有权益学生清单”的读取入口，另起）
   缺少的 CLI 入口（如 scheduler 身份调用 `meter_expire_due`、`meter_renew_due`、`review_sample_spot_checks`）补在 `services/insight/insight/scheduled.py`（已有 scheduler 身份连接与角色断言）。
4. **互斥与重入**：每个任务运行前取 `pg_try_advisory_lock`（键由任务名派生，按该任务身份的连接取），取不到就跳过本轮并记 `skipped_locked`；同一任务上一轮未结束不重叠。
5. **运行记录与告警**：不建表；每轮运行写一行结构化日志（任务名、开始结束、退出码、计数，不含学生 ID）；退出码非 0、超时（任务级超时默认 10 分钟）、`stale_estimate` 一律记 `alert` 级别日志。
6. **规则 9**：只允许 dev/test（`SCHEDULER_ENV`），其他环境拒绝启动；连接串必须是本机（复用 `check_server_local`）；提供 `--once <任务名>` 与 `--dry-run` 便于测试。
7. 不引入 Redis、Arq、托管队列或任何新依赖（与 ADR 10 一致）。

## 不做
- 新迁移（会话/验证码/限流清理函数、分区维护授权、运行记录表）——另起，必停。
- 月度计划批量生成、SIE-03 触发评估与下发（规则未签字）、判断生成 sync、中圈指标、`admission-recompute`。
- 生产调度部署。

## 验收
- 每个任务可经 `--once` 在一次性库上运行并产生预期效果（预留过期释放、续期、删除执行、冻结、到期扫描、对账、下架、抽检、周报）；重复运行幂等；
- 两个实例并发时同一任务只有一个执行；子进程环境只含白名单变量（测试断言）；非 dev/test 或非本机连接拒绝启动；
- 时间表解析与“下一次运行时间”纯函数测试（含时区、月末、周一）；变异测试；严格 `make check` 通过。
