# 压测基线报告（本机基线，不代表生产容量）

> 小规模样例：small 档（5 → 10 并发 × 各 10 s，种子 20260930）在开发机上生成，只演示报告口径；完整分阶段 10 → 50 → 100 → 200 用 make loadtest-baseline LOADTEST_STAGES=full LOADTEST_ARGS=--i-know，由执行人在本机另跑。

## 运行参数

- 种子：`20260930`；阶段：5 并发 × 10 s → 10 并发 × 10 s；平均会话时长（推到达率）6.0 s；步骤间平均思考 0.2 s
- 旅程配比：student 0.6、parent 0.25、meter 0.15
- 到达数：22（parent 10、student 10、meter 2）
- 假供应商延迟：中位 300.0 ms、P90 900.0 ms；故障注入：无
- 请求序列：计划 `3cd4b8e5ba08775a…`，实际 `3cd4b8e5ba08775a…`，一致
- 墙钟 21.16 s；会话排队等待 P95 0.0 ms

## 各阶段（按到达阶段归属）

| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |
|---|---|---|---|---|---|---|
| 5 并发 | 66 | 0.0 | 25.2 | 747.6 | 1516.2 | 6.6 |
| 10 并发 | 126 | 0.0 | 24.3 | 408.4 | 1148.1 | 11.3 |

## 各接口

| 接口 | 请求数 | 错误率 | 业务拒绝 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |
|---|---|---|---|---|---|---|---|---|
| BFF POST /auth/otp/request | 20 | 0.0 | 0 | 16.9 | 45.0 | 45.0 | 0.9 | 202×20 |
| BFF POST /auth/otp/verify | 20 | 0.0 | 0 | 10.7 | 19.3 | 26.6 | 0.9 | 200×20 |
| BFF POST /students/{id}/student-session | 10 | 0.0 | 0 | 13.6 | 77.2 | 77.2 | 0.5 | 200×10 |
| BFF GET /students/{id}/today | 10 | 0.0 | 0 | 60.5 | 790.0 | 790.0 | 0.5 | 200×10 |
| BFF POST /students/{id}/diagnosis/sessions | 10 | 0.0 | 0 | 39.2 | 110.4 | 110.4 | 0.5 | 201×10 |
| BFF GET /students/{id}/diagnosis/sessions/{sid} | 10 | 0.0 | 0 | 26.8 | 63.3 | 63.3 | 0.5 | 200×10 |
| BFF POST …/diagnosis/sessions/{sid}/answer | 10 | 0.0 | 0 | 106.3 | 300.7 | 300.7 | 0.5 | 200×10 |
| BFF POST …/diagnosis/sessions/{sid}/pause | 10 | 0.0 | 0 | 19.3 | 28.7 | 28.7 | 0.5 | 200×10 |
| BFF meteredCall → 学习服务 grade（数学，验证器） | 10 | 0.0 | 0 | 75.9 | 268.2 | 268.2 | 0.5 | ok×10 |
| BFF meteredCall → 学习服务 grade（道法，模型 T2） | 10 | 0.0 | 0 | 408.4 | 1516.2 | 1516.2 | 0.5 | ok×10 |
| BFF meteredCall → 学习服务 grade（语文作文，模型 T4） | 10 | 0.0 | 0 | 538.9 | 1499.0 | 1499.0 | 0.5 | ok×10 |
| BFF GET /students/{id}/star-map（学生） | 10 | 0.0 | 0 | 19.4 | 25.3 | 25.3 | 0.5 | 200×10 |
| BFF GET /me（家长首页） | 10 | 0.0 | 0 | 14.2 | 32.7 | 32.7 | 0.5 | 200×10 |
| BFF GET /students/{id}/star-map（家长） | 10 | 0.0 | 0 | 21.7 | 91.9 | 91.9 | 0.5 | 200×10 |
| BFF GET /students/{id}/balance（家长） | 10 | 0.0 | 0 | 19.5 | 43.4 | 43.4 | 0.5 | 200×10 |
| BFF GET /students/{id}/enrollments（家长） | 10 | 0.0 | 0 | 23.0 | 32.3 | 32.3 | 0.5 | 200×10 |
| 计量 meter_reserve ×2（同键并发） | 4 | 0.0 | 0 | 25.2 | 29.7 | 29.7 | 0.2 | outcome:existing×2、outcome:reserved×2 |
| 计量 meter_settle | 2 | 0.0 | 0 | 10.5 | 25.2 | 25.2 | 0.1 | outcome:settled×2 |
| 计量 meter_settle（重复，幂等） | 2 | 0.0 | 0 | 4.1 | 6.9 | 6.9 | 0.1 | outcome:settled×2 |
| 计量 meter_reserve | 2 | 0.0 | 0 | 20.8 | 28.2 | 28.2 | 0.1 | outcome:reserved×2 |
| 计量 meter_release | 2 | 0.0 | 0 | 11.1 | 24.6 | 24.6 | 0.1 | outcome:released×2 |

## 数据库

连接（pg_stat_activity 每 0.5 s 采样的峰值）：

| 登录角色 | 最大连接 | 最大活跃 | 最大锁等待 |
|---|---|---|---|
| bff_app | 6 | 1 | 0 |
| learning_app | 4 | 1 | 0 |

应用侧池上限：学习服务 asyncpg 池 max 10（learning/app.py）、BFF pg 池 max 10（services/bff/src/main.ts）；批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20

慢查询前十（pg_stat_statements，按总耗时；语句已规范化）：

| # | 角色 | 调用 | 总 ms | 平均 ms | 最大 ms | 语句（截断） |
|---|---|---|---|---|---|---|
| 1 | bff_app | 46 | 409.6 | 8.9 | 48.7 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2 | bff_app | 44 | 173.4 | 3.94 | 22.3 | `SELECT platform.meter_settle($1::uuid, $2::numeric)` |
| 3 | bff_app | 20 | 120.0 | 6.0 | 42.2 | `SELECT e.subject::text AS subject, e.status AS enrollment_status, n.local_id, n.label_code, m.state::text AS state, m.confidence::text AS confidence FROM platform.subject_enrollment e JOIN platform.sk` |
| 4 | bff_app | 40 | 97.6 | 2.44 | 7.5 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 5 | bff_app | 10 | 82.5 | 8.25 | 23.8 | `SELECT resource_type, unit, source, period_end, remaining FROM platform.entitlement_balance WHERE student_id = $1 ORDER BY resource_type, source, period_end` |
| 6 | bff_app | 110 | 47.2 | 0.43 | 2.8 | `SELECT account_id, account_kind::text AS account_kind, origin FROM platform.auth_session_resolve($1)` |
| 7 | bff_app | 120 | 32.4 | 0.27 | 3.0 | `SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok` |
| 8 | bff_app | 20 | 23.9 | 1.19 | 4.8 | `SELECT outcome, session_token FROM platform.auth_otp_login($1, $2, $3, $4, $5)` |
| 9 | bff_app | 2 | 20.1 | 10.07 | 15.8 | `SELECT platform.meter_release($1::uuid, $2)` |
| 10 | bff_app | 10 | 19.1 | 1.91 | 8.1 | `SELECT platform.auth_issue_student_session($1, $2, $3, $4) AS token` |

## 计量一致性（压测后）

- 快进计量时钟 3 hours 并跑过期任务，回收 0 笔；
- 预留 44 笔：结算 42、释放 2、过期 0、仍在预留 0；流水不平 0；对账视图 0 行；额度桶未结预留量 0
- 结论：**零泄漏**

## 模型调用（调用记录元数据）

- 共 20 次；按科目与任务：wenli/T4 10、zhixing/T2 10
- 结果：ok 20

## 说明

- 分位数为最近秩法；错误 = 非 2xx 且非 402 业务拒绝，或连接失败；
- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩直接运行 BFF 的 meteredCall（耗时含预留、同键退避重试与结算）；
- 负载生成器、学习服务、BFF、适配器、假供应商与数据库同在一台机器，互相争用 CPU；数字只作回归比较的基线；
- 本机基线，不代表生产容量。
