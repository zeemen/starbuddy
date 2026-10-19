# 2 倍峰值压测报告（本机合成，不是容量结论）

> 本机合成，不是容量结论：负载生成器与全部服务同在一台机器，阶段并发是合成占位；1 倍峰值并发 = 20（命令行覆盖，本机对照占位，不是容量目标）。

## 运行参数

- 种子：`20260930`；阶段：0.5× 10 并发 × 30 s → 1× 20 并发 × 30 s → 2× 40 并发 × 30 s → 2× 保持 40 并发 × 60 s；平均会话时长（推到达率）6.0 s；步骤间平均思考 0.2 s
- 旅程配比：student 0.6、parent 0.25、meter 0.15
- 可选场景（PLT-20）：practice_answer、plan_events、my_plan、star_map_v1、diagnose_more、monthly_plan、weekly_report、profile_pages、prediction
- 到达数：723（student 425、parent 195、meter 103）
- 假供应商延迟：中位 300.0 ms、P90 900.0 ms；故障注入：无
- 请求序列：计划 `926c5578ad7ce092…`，实际 `926c5578ad7ce092…`，一致
- 墙钟 156.33 s；会话排队等待 P95 1107.5 ms

## 各阶段（按到达阶段归属）

| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |
|---|---|---|---|---|---|---|
| 0.5×（10 并发） | 882 | 0.0 | 15.0 | 221.5 | 742.3 | 29.4 |
| 1×（20 并发） | 1498 | 0.0 | 14.1 | 205.3 | 689.6 | 49.9 |
| 2×（40 并发） | 3968 | 0.0 | 25.6 | 353.9 | 776.7 | 132.3 |
| 2× 保持（40 并发） | 7270 | 0.0 | 20.8 | 289.0 | 861.3 | 109.6 |

## 2 倍峰值各阶段

延迟、吞吐、错误率按到达阶段归属；连接峰值、慢查询、闸门分档按墙钟所在阶段（交界处可能差一个会话时长）。

| 阶段 | 并发 | 请求数 | 吞吐 req/s | p50 ms | p95 ms | p99 ms | 错误率 | 429 | 503 BUSY | 预期 | 连接峰值（角色:总/活跃/锁等待/等待） | 闸门排队分档 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5× | 10 | 882 | 29.4 | 15.0 | 221.5 | 742.3 | 0.0 | 0 | 0 | 24 | bff_app:7/1/0/0；learning_app:12/2/0/0 | 0×232 |
| 1× | 20 | 1498 | 49.9 | 14.1 | 205.3 | 689.6 | 0.0 | 0 | 0 | 42 | bff_app:9/2/0/1；learning_app:13/1/0/0 | 0×429 |
| 2× | 40 | 3968 | 132.3 | 25.6 | 353.9 | 776.7 | 0.0 | 0 | 0 | 104 | bff_app:17/2/0/1；learning_app:16/3/0/0 | 0×1067、lt100×121、lt500×23 |
| 2× 保持 | 40 | 7270 | 109.6 | 20.8 | 289.0 | 861.3 | 0.0 | 0 | 0 | 220 | bff_app:16/2/0/0；learning_app:16/2/0/0 | 0×2357、lt100×21 |

各阶段慢查询（pg_stat_statements 阶段边界快照相减，按总耗时前三；语句已规范化）：

| 阶段 | 角色 | 调用 | 总 ms | 平均 ms | 语句（截断） |
|---|---|---|---|---|---|
| 0.5× | bff_app | 126 | 653.0 | 5.18 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 0.5× | bff_app | 232 | 548.9 | 2.37 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 0.5× | bff_app | 137 | 392.2 | 2.86 | `SELECT platform.meter_settle($1, $2) AS r` |
| 1× | bff_app | 426 | 974.5 | 2.29 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 1× | bff_app | 213 | 660.4 | 3.1 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 1× | bff_app | 241 | 480.6 | 1.99 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× | bff_app | 1188 | 2918.6 | 2.46 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× | bff_app | 591 | 2182.2 | 3.69 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× | bff_app | 675 | 1535.1 | 2.27 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× 保持 | bff_app | 2369 | 5601.1 | 2.36 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× 保持 | bff_app | 1079 | 3416.8 | 3.17 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× 保持 | bff_app | 1278 | 2375.8 | 1.86 | `SELECT platform.meter_settle($1, $2) AS r` |

## 各接口

| 接口 | 请求数 | 错误率 | 业务拒绝 | 429 限流 | 503 SUBJECT_BUSY | 预期 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BFF POST /auth/otp/request | 620 | 0.0 | 0 | 0 | 0 | 0 | 6.6 | 18.2 | 31.8 | 4.0 | 202×620 |
| BFF POST /auth/otp/verify | 620 | 0.0 | 0 | 0 | 0 | 0 | 6.6 | 15.5 | 25.0 | 4.0 | 200×620 |
| BFF POST /students/{id}/student-session | 425 | 0.0 | 0 | 0 | 0 | 0 | 7.5 | 18.1 | 29.1 | 2.7 | 200×425 |
| BFF GET /students/{id}/today（跨科编排 + 快照，plan_events 场景；不与基线 today 比较） | 425 | 0.0 | 0 | 0 | 0 | 0 | 66.6 | 157.3 | 203.6 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions | 425 | 0.0 | 0 | 0 | 0 | 0 | 31.7 | 93.6 | 174.7 | 2.7 | 201×425 |
| BFF GET /students/{id}/diagnosis/sessions/{sid} | 425 | 0.0 | 0 | 0 | 0 | 0 | 22.6 | 79.7 | 142.8 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/answer | 425 | 0.0 | 0 | 0 | 0 | 0 | 100.5 | 273.0 | 351.8 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/pause | 425 | 0.0 | 0 | 0 | 0 | 0 | 19.2 | 53.2 | 77.6 | 2.7 | 200×425 |
| BFF meteredCall → 学习服务 grade（数学，验证器） | 425 | 0.0 | 0 | 0 | 0 | 0 | 73.0 | 183.3 | 259.1 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（道法，模型 T2） | 425 | 0.0 | 0 | 0 | 0 | 0 | 418.1 | 1293.4 | 1789.4 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（语文作文，模型 T4） | 425 | 0.0 | 0 | 0 | 0 | 0 | 409.2 | 1239.6 | 1860.7 | 2.7 | ok×425 |
| BFF GET /students/{id}/star-map（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.3 | 19.2 | 42.5 | 2.7 | 200×425 |
| BFF GET /me（家长首页） | 195 | 0.0 | 0 | 0 | 0 | 0 | 7.2 | 18.0 | 27.1 | 1.2 | 200×195 |
| BFF GET /students/{id}/star-map（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 9.3 | 21.8 | 37.2 | 1.2 | 200×195 |
| BFF GET /students/{id}/balance（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 9.9 | 22.5 | 32.7 | 1.2 | 200×195 |
| BFF GET /students/{id}/enrollments（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 14.3 | 30.0 | 54.6 | 1.2 | 200×195 |
| BFF GET /students/{id}/practice/math（再练列表） | 425 | 0.0 | 0 | 0 | 0 | 0 | 24.1 | 67.3 | 84.7 | 2.7 | 200×425 |
| BFF POST …/practice/math/entries/{eid}/next（再练取题，经闸门） | 425 | 0.0 | 0 | 0 | 0 | 0 | 60.0 | 145.3 | 223.4 | 2.7 | 200×425 |
| BFF GET /students/{id}/retest/math（复测列表） | 425 | 0.0 | 0 | 0 | 0 | 0 | 28.8 | 83.1 | 117.2 | 2.7 | 200×425 |
| BFF POST …/retest/math/entries/{eid}/next（复测取题，经闸门） | 425 | 0.0 | 0 | 0 | 0 | 0 | 62.3 | 164.3 | 206.1 | 2.7 | 200×425 |
| BFF POST …/practice/math/answer（再练作答，票据） | 425 | 0.0 | 0 | 0 | 0 | 0 | 103.5 | 309.6 | 415.9 | 2.7 | 200×425 |
| BFF POST …/retest/math/answer（复测作答，票据） | 425 | 0.0 | 0 | 0 | 0 | 0 | 132.4 | 373.2 | 494.7 | 2.7 | 200×425 |
| BFF POST /students/{id}/events（plan_done） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.0 | 19.2 | 27.2 | 2.7 | 201×425 |
| BFF POST /students/{id}/events（plan_skip） | 425 | 0.0 | 0 | 0 | 0 | 0 | 8.0 | 18.5 | 27.8 | 2.7 | 201×425 |
| BFF GET /students/{id}/my-plan（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 7.5 | 20.8 | 34.3 | 2.7 | 200×425 |
| BFF GET /students/{id}/star-map/v1（学生） | 425 | 0.0 | 0 | 0 | 0 | 0 | 20.9 | 58.3 | 79.0 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions（续做） | 425 | 0.0 | 0 | 0 | 0 | 0 | 27.4 | 73.9 | 116.5 | 2.7 | 200×425 |
| BFF GET …/diagnosis/sessions/{sid}（续做后取题） | 425 | 0.0 | 0 | 0 | 0 | 0 | 23.5 | 70.4 | 100.0 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/probe | 425 | 0.0 | 0 | 0 | 0 | 0 | 36.9 | 94.7 | 149.3 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/abandon | 425 | 0.0 | 0 | 0 | 0 | 0 | 22.8 | 61.9 | 88.9 | 2.7 | 200×425 |
| BFF GET /students/{id}/monthly-plan（家长） | 195 | 0.0 | 0 | 0 | 0 | 195 | 8.6 | 22.3 | 43.5 | 1.2 | 503 UNAVAILABLE×195 |
| BFF GET /students/{id}/weekly-report（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 7.8 | 21.9 | 37.0 | 1.2 | 200×195 |
| BFF GET /students/{id}/profile-pages/math（家长） | 195 | 0.0 | 0 | 0 | 0 | 0 | 10.2 | 24.1 | 62.2 | 1.2 | 200×195 |
| BFF GET …/exams/{exam_id}/prediction（家长） | 195 | 0.0 | 0 | 0 | 0 | 195 | 7.5 | 19.7 | 36.1 | 1.2 | 503 UNAVAILABLE×195 |
| 计量 meter_reserve ×2（同键并发） | 206 | 0.0 | 0 | 0 | 0 | 0 | 7.3 | 18.8 | 23.4 | 1.3 | outcome:existing×103、outcome:reserved×103 |
| 计量 meter_settle | 103 | 0.0 | 0 | 0 | 0 | 0 | 5.5 | 13.5 | 18.3 | 0.7 | outcome:settled×103 |
| 计量 meter_settle（重复，幂等） | 103 | 0.0 | 0 | 0 | 0 | 0 | 2.4 | 11.9 | 24.2 | 0.7 | outcome:settled×103 |
| 计量 meter_reserve | 103 | 0.0 | 0 | 0 | 0 | 0 | 6.7 | 23.8 | 32.1 | 0.7 | outcome:reserved×103 |
| 计量 meter_release | 103 | 0.0 | 0 | 0 | 0 | 0 | 4.5 | 13.4 | 16.2 | 0.7 | outcome:released×103 |

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
| BFF POST …/practice/math/answer（再练作答，票据） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST …/practice/math/entries/{eid}/next（再练取题，经闸门） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST …/retest/math/answer（复测作答，票据） | 425 | 0 | — | 0 | 0 | 否 |
| BFF POST …/retest/math/entries/{eid}/next（复测取题，经闸门） | 425 | 0 | — | 0 | 0 | 否 |
| BFF GET /students/{id}/star-map/v1（学生） | 425 | 0 | — | 0 | 0 | 否 |

核对标记：diagnose.resume:resumed_same ×425

无告警

## 写路径（PLT-21）

- 合成地图 aya 1.0.0（经两名合成 staff 签字发布）：2 个单元（层次 apply）、12 道合成题（经准入闸门升为 admitted，打标审核通过）；真知识服务只读这个一次性库，响应由学习服务按契约校验；判分走阿鸦合成题路径（验证器，确定性；合成阿鸦进程打开了候选版开关，只限该进程）；合成内容包的准入决定记录是**合成占位摘要，不是真实准入依据**
- 到期条目种子（压测前准备数据：经学习服务 grade 写入路径，**不经计量**——不预留、不结算、不计次，不计入压测指标）：425 名学生、850 道作答（practice:incorrect×425、retest:correct×425）、425 条已到期的平台内验证任务（**手工种入，不是 KGE 调度**）
- 派生票据与派生条目的“预期”已停用：取题与作答一律按真路径要求 2xx（派生的照发、按错误计）

| 核对项 | 值 |
|---|---|
| 作答（真票据 2xx） | 850 |
| 真票据 / 派生票据 | 850 / 0（真路径占比 100.0%） |
| 合成题计次增量（practice_log，经幂等键关联到合成题作答） / 全部计次增量 | 850 / 850 |
| 作答证据行增量（每次作答一行） | 850 |
| 证据行总增量（含判分轨迹） / 每次作答 | 1700 / 2.0 |
| 计次无对应作答证据 / 作答证据无对应计次（增量） | 0 / 0 |
| 结论 | **通过** |

口径：作答 = 合成题计次增量 = 作答证据行增量，且两类“无对应”都为 0、全部计次 = 合成题计次 + 无对应作答证据的计次。题库自练是“开始即计数、没有释放”（BFF 先计次再调学习服务），计次之后学习服务失败会留下“计次无对应作答证据”——这是设计口径，不是计量故障；本机无故障注入时应为 0，出现即判不通过。

各环节耗时（各服务自己的访问日志，只取方法名、状态码、耗时；压测期间，不含种子）：

| 环节 | 次数 | 非 2xx | p50 ms | p95 ms | 最大 ms |
|---|---|---|---|---|---|
| 学习服务 再练取题（含知识服务与 generate_variation） | 425 | 0 | 45.0 | 120.0 | 1073.0 |
| 学习服务 复测取题（同上） | 425 | 0 | 47.0 | 122.0 | 177.0 |
| 知识服务 item.similar | 850 | 0 | 6.0 | 13.0 | 26.0 |
| 阿鸦 generate_variation（选题） | 850 | 0 | 0.0 | 1.0 | 43.0 |
| 学习服务 复测作答预检（计次之前） | 425 | 0 | 14.0 | 54.0 | 110.0 |
| 学习服务 再练作答（验票、判分、证据落库） | 425 | 0 | 86.0 | 277.0 | 509.0 |
| 学习服务 复测作答（同上） | 425 | 0 | 88.0 | 283.0 | 448.0 |
| 阿鸦 grade（验证器；含学生旅程的数学批改） | 1700 | 0 | 0.0 | 1.0 | 6.0 |

BFF 侧（含计次与转发）见“各接口”表的再练 / 复测取题、作答行；掌握更新：本栈不设掌握配置，作答后不重算（未测）。

注：开了 plan_events，学生的今日计划另走跨科编排与快照，行名“today.orch”，不与基线的 today 比较。

## SLO 判定（config/slo.yaml）

- 状态：**待技术负责人签字**；判定方式：只报告（未达不影响退出码）；边界值算通过；样本按到达阶段归属

| 路由组 | 请求数 | 判定 | p50 ms | p95 ms | p99 ms | 错误率 | 429 占比 | 503 BUSY 占比 |
|---|---|---|---|---|---|---|---|---|
| login | 1665 | 通过 | 6.9 / ≤300.0 | 16.9 / ≤800.0 | 30.7 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| student_read | 2125 | 通过 | 15.4 / ≤300.0 | 61.9 / ≤800.0 | 95.0 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| today_orch | 425 | 通过 | 66.6 / ≤400.0 | 157.3 / ≤800.0 | 203.6 / ≤2500.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| gated_read | 850 | 通过 | 22.9 / ≤300.0 | 73.4 / ≤800.0 | 142.1 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 |
| student_write | 5100 | 通过 | 41.3 / ≤400.0 | 204.1 / ≤800.0 | 348.5 / ≤2500.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 |
| grade | 1275 | 通过 | 275.0 / — | 1145.1 / ≤30000.0 | 1789.4 / — | 0.0 / ≤0.0 | 0.0 / — | 0.0 / — |
| parent_read | 1170 | 通过 | 9.7 / ≤300.0 | 23.2 / ≤800.0 | 37.2 / ≤2000.0 | 0.0 / ≤0.0 | 0.0 / ≤0.0 | 0.0 / — |
| metering | 618 | 通过 | 6.0 / — | 18.3 / ≤800.0 | 26.8 / — | 0.0 / ≤0.0 | 0.0 / — | 0.0 / — |

按阶段：

| 路由组 | 0.5× | 1× | 2× | 2× 保持 |
|---|---|---|---|---|
| login | 通过（105） | 通过（183） | 通过（479） | 通过（898） |
| student_read | 通过（135） | 通过（235） | 通过（625） | 通过（1130） |
| today_orch | 通过（27） | 通过（47） | 通过（125） | 通过（226） |
| gated_read | 通过（54） | 通过（94） | 通过（250） | 通过（452） |
| student_write | 通过（324） | 通过（564） | 通过（1500） | 通过（2712） |
| grade | 通过（81） | 通过（141） | 通过（375） | 通过（678） |
| parent_read | 通过（72） | 通过（126） | 通过（312） | 通过（660） |
| metering | 通过（60） | 通过（66） | 通过（198） | 通过（294） |

## 分学科并发闸门（BFF 访问日志汇总）

| 学科码 | 立即放行 | 排队后放行 | 被拒 | 绕过（已计量的重试） | 被拒原因 | 排队毫秒分档 |
|---|---|---|---|---|---|---|
| aya | 4085 | 165 | 0 | 0 | — | 0×4085、lt100×142、lt500×23 |

## 数据库

连接（pg_stat_activity 每 0.5 s 采样的峰值）：

| 登录角色 | 最大连接 | 最大活跃 | 最大锁等待 |
|---|---|---|---|
| bff_app | 17 | 2 | 0 |
| learning_app | 16 | 3 | 0 |

应用侧池上限：学习服务 asyncpg 池 max 10 + 再练作答串行锁池 max 8（learning/app.py PRACTICE_LOCK_POOL_SIZE，打开再练时）、BFF pg 池 max 10（services/bff/src/main.ts）；批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20

慢查询前十（pg_stat_statements，按总耗时；语句已规范化）：

| # | 角色 | 调用 | 总 ms | 平均 ms | 最大 ms | 语句（截断） |
|---|---|---|---|---|---|---|
| 1 | bff_app | 4215 | 10043.1 | 2.38 | 35.5 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2 | bff_app | 2009 | 6912.4 | 3.44 | 30.4 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 3 | bff_app | 2331 | 4783.7 | 2.05 | 27.4 | `SELECT platform.meter_settle($1, $2) AS r` |
| 4 | bff_app | 10485 | 3990.9 | 0.38 | 14.5 | `SELECT account_id, account_kind::text AS account_kind, origin FROM platform.auth_session_resolve($1)` |
| 5 | bff_app | 11105 | 2537.8 | 0.23 | 7.1 | `SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok` |
| 6 | bff_app | 5950 | 1553.9 | 0.26 | 5.7 | `SELECT platform.is_self_student($1::uuid) AS ok` |
| 7 | bff_app | 850 | 1243.7 | 1.46 | 12.4 | `SELECT id, replayed FROM insight.record_behavior_event($1, $2, $3::jsonb, $4::timestamptz, $5::uuid)` |
| 8 | bff_app | 620 | 750.5 | 1.21 | 5.8 | `SELECT e.subject::text AS subject, e.status AS enrollment_status, n.local_id, n.label_code, m.state::text AS state, m.confidence::text AS confidence FROM platform.subject_enrollment e JOIN platform.sk` |
| 9 | bff_app | 850 | 674.0 | 0.79 | 7.4 | `SELECT platform.meter_practice($1::uuid, $2) AS r` |
| 10 | bff_app | 2550 | 554.5 | 0.22 | 3.1 | `SELECT $3 FROM platform.diagnosis_session WHERE id = $1::uuid AND student_id = $2::uuid` |

## 计量一致性（压测后）

- 快进计量时钟 3 hours 并跑过期任务，回收 0 笔；
- 预留 1906 笔：结算 1803、释放 103、过期 0、仍在预留 0；流水不平 0；对账视图 0 行；额度桶未结预留量 0
- 结论：**零泄漏**

## 模型调用（调用记录元数据）

- 共 850 次；按科目与任务：wenli/T4 425、zhixing/T2 425
- 结果：ok 850

## 说明

- 分位数为最近秩法；错误 = 非 2xx 且非 402 业务拒绝、非“预期”，或连接失败（429 限流与 503 SUBJECT_BUSY 仍算错误，另单列两列）；
- “预期”= 本栈缺依赖时的预期结果（PLT-20 可选场景：没有洞察服务时月度计划与预测的 503 UNAVAILABLE、没有可作答题时派生票据的 422 TICKET_INVALID、快照里没有任务时派生任务 ID 的 422 INVALID 等），不算错误，单列（本次写路径已接上：再练 / 复测的派生条目与派生票据不算预期）；
- 批改没有 BFF HTTP 入口（壳里尚无批改路由）：经计量驱动桩直接运行 BFF 的 meteredCall（耗时含预留、同键退避重试与结算）；
- 负载生成器、学习服务、BFF、适配器、假供应商与数据库同在一台机器，互相争用 CPU；数字只作回归比较的基线；
- 本机合成，不是容量结论。
