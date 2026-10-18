# 2 倍峰值压测报告（本机合成，不是容量结论）

> 本机合成，不是容量结论：负载生成器与全部服务同在一台机器，阶段并发是合成占位；1 倍峰值并发 = 20（命令行覆盖，本机对照占位，不是容量目标）。

## 运行参数

- 种子：`20260930`；阶段：0.5× 10 并发 × 30 s → 1× 20 并发 × 30 s → 2× 40 并发 × 30 s → 2× 保持 40 并发 × 60 s；平均会话时长（推到达率）6.0 s；步骤间平均思考 0.2 s
- 旅程配比：student 0.6、parent 0.25、meter 0.15
- 可选场景（PLT-20）：practice_answer、plan_events、my_plan、star_map_v1、diagnose_more、monthly_plan、weekly_report、profile_pages、prediction
- 到达数：723（parent 195、student 425、meter 103）
- 假供应商延迟：中位 300.0 ms、P90 900.0 ms；故障注入：无
- 请求序列：计划 `926c5578ad7ce092…`，实际 `926c5578ad7ce092…`，一致
- 墙钟 156.22 s；会话排队等待 P95 232.9 ms

## 各阶段（按到达阶段归属）

| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |
|---|---|---|---|---|---|---|
| 0.5×（10 并发） | 882 | 0.1 | 11.6 | 199.6 | 664.2 | 29.4 |
| 1×（20 并发） | 1498 | 0.1 | 11.3 | 200.1 | 690.5 | 49.9 |
| 2×（40 并发） | 3968 | 0.1 | 16.2 | 276.1 | 789.9 | 132.3 |
| 2× 保持（40 并发） | 7270 | 0.1 | 14.2 | 251.4 | 820.1 | 109.8 |

## 2 倍峰值各阶段

延迟、吞吐、错误率按到达阶段归属；连接峰值、慢查询、闸门分档按墙钟所在阶段（交界处可能差一个会话时长）。

| 阶段 | 并发 | 请求数 | 吞吐 req/s | p50 ms | p95 ms | p99 ms | 错误率 | 429 | 503 BUSY | 预期 | 连接峰值（角色:总/活跃/锁等待/等待） | 闸门排队分档 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5× | 10 | 882 | 29.4 | 11.6 | 199.6 | 664.2 | 0.1 | 0 | 0 | 78 | bff_app:10/1/0/0；learning_app:10/1/0/0 | 0×186 |
| 1× | 20 | 1498 | 49.9 | 11.3 | 200.1 | 690.5 | 0.1 | 0 | 0 | 136 | bff_app:10/1/0/0；learning_app:10/2/0/0 | 0×348 |
| 2× | 40 | 3968 | 132.3 | 16.2 | 276.1 | 789.9 | 0.1 | 0 | 0 | 354 | bff_app:18/1/0/0；learning_app:10/2/0/0 | 0×964、lt100×1 |
| 2× 保持 | 40 | 7270 | 109.8 | 14.2 | 251.4 | 820.1 | 0.1 | 0 | 0 | 672 | bff_app:17/3/0/0；learning_app:10/3/0/0 | 0×1901 |

各阶段慢查询（pg_stat_statements 阶段边界快照相减，按总耗时前三；语句已规范化）：

| 阶段 | 角色 | 调用 | 总 ms | 平均 ms | 语句（截断） |
|---|---|---|---|---|---|
| 0.5× | bff_app | 126 | 684.2 | 5.43 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 0.5× | bff_app | 186 | 446.8 | 2.4 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 0.5× | bff_app | 138 | 394.9 | 2.86 | `SELECT platform.meter_settle($1, $2) AS r` |
| 1× | bff_app | 344 | 833.9 | 2.42 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 1× | bff_app | 213 | 751.9 | 3.53 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 1× | bff_app | 239 | 579.6 | 2.43 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× | bff_app | 942 | 2497.2 | 2.65 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× | bff_app | 589 | 2457.0 | 4.17 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× | bff_app | 674 | 1635.1 | 2.43 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× 保持 | bff_app | 1893 | 4913.2 | 2.6 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× 保持 | bff_app | 1081 | 3613.4 | 3.34 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× 保持 | bff_app | 1280 | 2441.6 | 1.91 | `SELECT platform.meter_settle($1, $2) AS r` |

## 各接口

| 接口 | 请求数 | 错误率 | 业务拒绝 | 429 限流 | 503 SUBJECT_BUSY | 预期 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BFF POST /auth/otp/request | 620 | 0.0 | 0 | 0 | 0 | 0 | 7.0 | 16.5 | 24.8 | 4.0 | 202×620 |
| BFF POST /auth/otp/verify | 620 | 0.0 | 0 | 0 | 0 | 0 | 7.1 | 16.2 | 27.9 | 4.0 | 200×620 |
| BFF POST /students/{id}/student-session | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.2 | 20.8 | 38.3 | 2.7 | 200×425 |
| BFF GET /students/{id}/today（跨科编排 + 快照，plan_events 场景；不与基线 today 比较） | 425 | 0.0 | 0 | 0 | 0 | 0 | 54.7 | 157.8 | 240.6 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions | 425 | 0.0 | 0 | 0 | 0 | 0 | 26.6 | 72.5 | 122.4 | 2.7 | 201×425 |
| BFF GET /students/{id}/diagnosis/sessions/{sid} | 425 | 0.0 | 0 | 0 | 0 | 0 | 21.2 | 68.2 | 122.0 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/answer | 425 | 0.0 | 0 | 0 | 0 | 0 | 85.6 | 229.3 | 335.9 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/pause | 425 | 0.0 | 0 | 0 | 0 | 0 | 16.8 | 53.0 | 92.6 | 2.7 | 200×425 |
| BFF meteredCall → 学习服务 grade（数学，验证器） | 425 | 0.0 | 0 | 0 | 0 | 0 | 62.8 | 179.2 | 258.5 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（道法，模型 T2） | 425 | 0.0 | 0 | 0 | 0 | 0 | 405.1 | 1324.5 | 1800.8 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（语文作文，模型 T4） | 425 | 0.0 | 0 | 0 | 0 | 0 | 411.0 | 1299.7 | 1838.8 | 2.7 | ok×425 |
| BFF GET /students/{id}/star-map（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.8 | 18.9 | 28.1 | 2.7 | 200×425 |
| BFF GET /me（家长首页） | 195 | 0.0 | 0 | 0 | 0 | 0 | 7.2 | 18.4 | 41.7 | 1.2 | 200×195 |
| BFF GET /students/{id}/star-map（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 9.9 | 22.9 | 33.6 | 1.2 | 200×195 |
| BFF GET /students/{id}/balance（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 10.3 | 23.5 | 63.4 | 1.2 | 200×195 |
| BFF GET /students/{id}/enrollments（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 14.6 | 27.1 | 45.0 | 1.2 | 200×195 |
| BFF GET /students/{id}/practice/math（再练列表） | 425 | 0.0 | 0 | 0 | 0 | 0 | 19.0 | 72.8 | 103.7 | 2.7 | 200×425 |
| BFF POST …/practice/math/entries/{eid}/next（再练取题，经闸门） | 425 | 1.0 | 0 | 0 | 0 | 0 | 14.0 | 32.8 | 46.9 | 2.7 | 503×425 |
| BFF GET /students/{id}/retest/math（复测列表） | 425 | 0.0 | 0 | 0 | 0 | 0 | 19.8 | 64.0 | 92.7 | 2.7 | 200×425 |
| BFF POST …/retest/math/entries/{eid}/next（复测取题，经闸门） | 425 | 1.0 | 0 | 0 | 0 | 0 | 13.8 | 29.9 | 56.6 | 2.7 | 503×425 |
| BFF POST …/practice/math/answer（再练作答，票据） | 425 | 0.0 | 0 | 0 | 0 | 425 | 7.6 | 17.7 | 28.9 | 2.7 | 422 TICKET_INVALID×425 |
| BFF POST …/retest/math/answer（复测作答，票据） | 425 | 0.0 | 0 | 0 | 0 | 425 | 7.3 | 16.8 | 23.8 | 2.7 | 422 TICKET_INVALID×425 |
| BFF POST /students/{id}/events（plan_done） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.6 | 21.2 | 37.0 | 2.7 | 201×425 |
| BFF POST /students/{id}/events（plan_skip） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.4 | 19.8 | 28.4 | 2.7 | 201×425 |
| BFF GET /students/{id}/my-plan（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.2 | 20.8 | 28.5 | 2.7 | 200×425 |
| BFF GET /students/{id}/star-map/v1（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 10.2 | 23.8 | 33.6 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions（续做） | 425 | 0.0 | 0 | 0 | 0 | 0 | 24.6 | 68.1 | 117.9 | 2.7 | 200×425 |
| BFF GET …/diagnosis/sessions/{sid}（续做后取题） | 425 | 0.0 | 0 | 0 | 0 | 0 | 20.9 | 58.9 | 120.8 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/probe | 425 | 0.0 | 0 | 0 | 0 | 0 | 31.9 | 99.4 | 155.3 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/abandon | 425 | 0.0 | 0 | 0 | 0 | 0 | 19.6 | 65.4 | 111.9 | 2.7 | 200×425 |
| BFF GET /students/{id}/monthly-plan（家长） | 195 | 0.0 | 0 | 0 | 0 | 195 | 8.9 | 22.3 | 67.4 | 1.2 | 503 UNAVAILABLE×195 |
| BFF GET /students/{id}/weekly-report（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 8.1 | 17.6 | 25.3 | 1.2 | 200×195 |
| BFF GET /students/{id}/profile-pages/math（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 10.4 | 21.1 | 27.7 | 1.2 | 200×195 |
| BFF GET …/exams/{exam_id}/prediction（家长） | 195 | 0.0 | 0 | 0 | 0 | 195 | 8.0 | 18.3 | 25.4 | 1.2 | 503 UNAVAILABLE×195 |
| 计量 meter_reserve ×2（同键并发） | 206 | 0.0 | 0 | 0 | 0 | 0 | 8.3 | 28.6 | 34.5 | 1.3 | outcome:existing×103、outcome:reserved×103 |
| 计量 meter_settle | 103 | 0.0 | 0 | 0 | 0 | 0 | 5.3 | 15.1 | 35.9 | 0.7 | outcome:settled×103 |
| 计量 meter_settle（重复，幂等） | 103 | 0.0 | 0 | 0 | 0 | 0 | 2.6 | 12.3 | 20.3 | 0.7 | outcome:settled×103 |
| 计量 meter_reserve | 103 | 0.0 | 0 | 0 | 0 | 0 | 7.8 | 19.6 | 28.3 | 0.7 | outcome:reserved×103 |
| 计量 meter_release | 103 | 0.0 | 0 | 0 | 0 | 0 | 5.3 | 15.2 | 28.3 | 0.7 | outcome:released×103 |

## 可选场景核对

真路径（真 ID / 真票据）必须 2xx；派生 / 预期路径必须全部是预期结果——任一不满足即告警。

| 接口 | 真路径 | 派生路径 | 派生中预期占比 | 真路径非 2xx | 派生非预期 | 告警 |
|---|---|---|---|---|---|---|
| BFF POST …/diagnosis/sessions/{sid}/abandon | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST …/diagnosis/sessions/{sid}/probe | 425 | 0 | — | 0 | 0 | 否 |
| BFF GET …/diagnosis/sessions/{sid}（续做后取题） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST /students/{id}/diagnosis/sessions（续做） | 425 | 0 | — | 0 | 0 | 否 |
| BFF GET /students/{id}/my-plan（学生） | 425 | 0 | — | 0 | 0 | 否 |
| BFF GET /students/{id}/monthly-plan（家长） | 0 | 195 | 100.0% | 0 | 0 | 否 |
| BFF GET …/exams/{exam_id}/prediction（家长） | 0 | 195 | 100.0% | 0 | 0 | 否 |
| BFF GET /students/{id}/profile-pages/math（家长） | 195 | 0 | — | 0 | 0 | 否 |
| BFF GET /students/{id}/weekly-report（家长） | 195 | 0 | — | 0 | 0 | 否 |
| BFF POST /students/{id}/events（plan_done） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST /students/{id}/events（plan_skip） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST …/practice/math/answer（再练作答，票据） | 0 | 425 | 100.0% | 0 | 0 | 否 |
| BFF POST …/retest/math/answer（复测作答，票据） | 0 | 425 | 100.0% | 0 | 0 | 否 |
| BFF GET /students/{id}/star-map/v1（学生） | 425 | 0 | — | 0 | 0 | 否 |

核对标记：diagnose.resume:resumed_same ×425

无告警

注：开了 plan_events，学生的今日计划另走跨科编排与快照，行名“today.orch”，不与基线的 today 比较。

## SLO 判定（config/slo.yaml）

- 状态：**待技术负责人签字**；判定方式：只报告（未达不影响退出码）；边界值算通过；样本按到达阶段归属

| 路由组 | 请求数 | 判定 | p50 ms | p95 ms | p99 ms | 错误率 | 429 占比 | 503 BUSY 占比 |
|---|---|---|---|---|---|---|---|---|
| login | 1665 | 通过 | 7.3 / ≤300.0 | 17.8 / ≤800.0 | 28.3 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| student_read | 2125 | 通过 | 12.4 / ≤300.0 | 47.6 / ≤800.0 | 84.0 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| today_orch | 425 | 通过 | 54.7 / ≤400.0 | 157.8 / ≤800.0 | 240.6 / ≤2500.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| gated_read | 850 | 通过 | 21.2 / ≤300.0 | 62.7 / ≤800.0 | 121.6 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 |
| student_write | 4250 | **未达** | 21.8 / ≤400.0 | 111.9 / ≤800.0 | 196.8 / ≤2500.0 | 0.2 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 |
| grade | 1275 | 通过 | 263.4 / — | 1123.2 / ≤30000.0 | 1780.5 / — | 0.0 / ≤0.0 | 0.0 / — | 0.0 / — |
| parent_read | 1170 | 通过 | 10.2 / ≤300.0 | 22.9 / ≤800.0 | 32.9 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| metering | 618 | 通过 | 6.3 / — | 22.2 / ≤800.0 | 34.0 / — | 0.0 / ≤0.0 | 0.0 / — | 0.0 / — |

按阶段：

| 路由组 | 0.5× | 1× | 2× | 2× 保持 |
|---|---|---|---|---|
| login | 通过（105） | 通过（183） | 通过（479） | 通过（898） |
| student_read | 通过（135） | 通过（235） | 通过（625） | 通过（1130） |
| today_orch | 通过（27） | 通过（47） | 通过（125） | 通过（226） |
| gated_read | 通过（54） | 通过（94） | 通过（250） | 通过（452） |
| student_write | **未达**（270） | **未达**（470） | **未达**（1250） | **未达**（2260） |
| grade | 通过（81） | 通过（141） | 通过（375） | 通过（678） |
| parent_read | 通过（72） | 通过（126） | 通过（312） | 通过（660） |
| metering | 通过（60） | 通过（66） | 通过（198） | 通过（294） |

## 分学科并发闸门（BFF 访问日志汇总）

| 学科码 | 立即放行 | 排队后放行 | 被拒 | 绕过（已计量的重试） | 被拒原因 | 排队毫秒分档 |
|---|---|---|---|---|---|---|
| aya | 3399 | 1 | 0 | 0 | — | 0×3399、lt100×1 |

## 数据库

连接（pg_stat_activity 每 0.5 s 采样的峰值）：

| 登录角色 | 最大连接 | 最大活跃 | 最大锁等待 |
|---|---|---|---|
| bff_app | 18 | 3 | 0 |
| learning_app | 10 | 3 | 0 |

应用侧池上限：学习服务 asyncpg 池 max 10（learning/app.py）、BFF pg 池 max 10（services/bff/src/main.ts）；批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20

慢查询前十（pg_stat_statements，按总耗时；语句已规范化）：

| # | 角色 | 调用 | 总 ms | 平均 ms | 最大 ms | 语句（截断） |
|---|---|---|---|---|---|---|
| 1 | bff_app | 3365 | 8691.2 | 2.58 | 14.6 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2 | bff_app | 2009 | 7506.4 | 3.74 | 39.0 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 3 | bff_app | 2331 | 5051.3 | 2.17 | 30.8 | `SELECT platform.meter_settle($1, $2) AS r` |
| 4 | bff_app | 10485 | 4473.0 | 0.43 | 11.8 | `SELECT account_id, account_kind::text AS account_kind, origin FROM platform.auth_session_resolve($1)` |
| 5 | bff_app | 11105 | 2622.1 | 0.24 | 7.9 | `SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok` |
| 6 | bff_app | 5950 | 1695.1 | 0.28 | 3.8 | `SELECT platform.is_self_student($1::uuid) AS ok` |
| 7 | bff_app | 850 | 1329.9 | 1.56 | 8.4 | `SELECT id, replayed FROM insight.record_behavior_event($1, $2, $3::jsonb, $4::timestamptz, $5::uuid)` |
| 8 | bff_app | 620 | 815.0 | 1.31 | 8.9 | `SELECT e.subject::text AS subject, e.status AS enrollment_status, n.local_id, n.label_code, m.state::text AS state, m.confidence::text AS confidence FROM platform.subject_enrollment e JOIN platform.sk` |
| 9 | bff_app | 2550 | 598.1 | 0.23 | 2.7 | `SELECT $3 FROM platform.diagnosis_session WHERE id = $1::uuid AND student_id = $2::uuid` |
| 10 | bff_app | 1205 | 561.7 | 0.47 | 7.0 | `SELECT $2 FROM platform.student_profile WHERE id = $1` |

## 计量一致性（压测后）

- 快进计量时钟 3 hours 并跑过期任务，回收 0 笔；
- 预留 1906 笔：结算 1803、释放 103、过期 0、仍在预留 0；流水不平 0；对账视图 0 行；额度桶未结预留量 0
- 结论：**零泄漏**

## 模型调用（调用记录元数据）

- 共 850 次；按科目与任务：wenli/T4 425、zhixing/T2 425
- 结果：ok 850

## 说明

- 分位数为最近秩法；错误 = 非 2xx 且非 402 业务拒绝、非“预期”，或连接失败（429 限流与 503 SUBJECT_BUSY 仍算错误，另单列两列）；
- “预期”= 本栈缺依赖时的预期结果（PLT-20 可选场景：没有洞察服务时月度计划与预测的 503 UNAVAILABLE、没有可作答题时派生票据的 422 TICKET_INVALID、快照里没有任务时派生任务 ID 的 422 INVALID 等），不算错误，单列；
- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩直接运行 BFF 的 meteredCall（耗时含预留、同键退避重试与结算）；
- 负载生成器、学习服务、BFF、适配器、假供应商与数据库同在一台机器，互相争用 CPU；数字只作回归比较的基线；
- 本机合成，不是容量结论。
