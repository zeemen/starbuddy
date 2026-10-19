# 2 倍峰值压测报告（本机合成，不是容量结论）

> 本机合成，不是容量结论：负载生成器与全部服务同在一台机器，阶段并发是合成占位；1 倍峰值并发 = 20（命令行覆盖，本机对照占位，不是容量目标）。

## 运行参数

- 种子：`20260930`；阶段：0.5× 10 并发 × 30 s → 1× 20 并发 × 30 s → 2× 40 并发 × 30 s → 2× 保持 40 并发 × 60 s；平均会话时长（推到达率）6.0 s；步骤间平均思考 0.2 s
- 旅程配比：student 0.6、parent 0.25、meter 0.15
- 可选场景（PLT-20）：practice_answer、plan_events、my_plan、star_map_v1、diagnose_more、monthly_plan、weekly_report、profile_pages、prediction
- 到达数：723（student 425、parent 195、meter 103）
- 假供应商延迟：中位 300.0 ms、P90 900.0 ms；故障注入：无
- 请求序列：计划 `926c5578ad7ce092…`，实际 `926c5578ad7ce092…`，一致
- 墙钟 156.84 s；会话排队等待 P95 3567.2 ms

## 各阶段（按到达阶段归属）

| 阶段 | 请求数 | 错误率 | p50 ms | p95 ms | p99 ms | 吞吐 req/s |
|---|---|---|---|---|---|---|
| 0.5×（10 并发） | 882 | 0.00% | 15.0 | 213.8 | 842.9 | 29.4 |
| 1×（20 并发） | 1498 | 0.00% | 15.5 | 236.1 | 696.5 | 49.9 |
| 2×（40 并发） | 3968 | 0.00% | 48.6 | 572.9 | 957.0 | 132.3 |
| 2× 保持（40 并发） | 7270 | 0.00% | 34.4 | 419.9 | 931.2 | 108.8 |

## 2 倍峰值各阶段

延迟、吞吐、错误率按到达阶段归属；连接峰值、慢查询、闸门分档按墙钟所在阶段（交界处可能差一个会话时长）。

| 阶段 | 并发 | 请求数 | 吞吐 req/s | p50 ms | p95 ms | p99 ms | 错误率 | 429 | 503 BUSY | 预期 | 连接峰值（角色:总/活跃/锁等待/等待） | 闸门排队分档 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5× | 10 | 882 | 29.4 | 15.0 | 213.8 | 842.9 | 0.00% | 0 | 0 | 24 | bff_app:10/2/1/1；learning_app:11/1/0/0 | 0×232 |
| 1× | 20 | 1498 | 49.9 | 15.5 | 236.1 | 696.5 | 0.00% | 0 | 0 | 42 | bff_app:8/2/0/0；learning_app:13/2/0/0 | 0×429 |
| 2× | 40 | 3968 | 132.3 | 48.6 | 572.9 | 957.0 | 0.00% | 0 | 0 | 104 | bff_app:16/3/1/1；learning_app:18/3/0/1 | 0×649、lt100×269、lt500×193、lt2000×8 |
| 2× 保持 | 40 | 7270 | 108.8 | 34.4 | 419.9 | 931.2 | 0.00% | 0 | 0 | 220 | bff_app:17/2/1/1；learning_app:18/4/0/0 | 0×2205、lt100×213、lt500×52 |

各阶段慢查询（pg_stat_statements 阶段边界快照相减，按总耗时前三；语句已规范化；角色含 bff_app、learning_app 与 starbuddy_learning——学习服务在 SET LOCAL ROLE starbuddy_learning 下执行的语句记在后者名下，PLT-22 起计入，与 PLT-21 及以前的样例不可直接比较；连接采样仍按登录名）：

| 阶段 | 角色 | 调用 | 总 ms | 平均 ms | 语句（截断） |
|---|---|---|---|---|---|
| 0.5× | bff_app | 126 | 710.0 | 5.64 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 0.5× | bff_app | 232 | 563.7 | 2.43 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 0.5× | bff_app | 137 | 484.8 | 3.54 | `SELECT platform.meter_settle($1, $2) AS r` |
| 1× | bff_app | 426 | 983.7 | 2.31 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 1× | bff_app | 213 | 707.9 | 3.32 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 1× | bff_app | 241 | 451.0 | 1.87 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× | bff_app | 1096 | 3303.6 | 3.01 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× | bff_app | 574 | 2722.3 | 4.74 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× | bff_app | 641 | 1879.4 | 2.93 | `SELECT platform.meter_settle($1, $2) AS r` |
| 2× 保持 | bff_app | 2461 | 6841.5 | 2.78 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2× 保持 | bff_app | 1096 | 4012.0 | 3.66 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 2× 保持 | starbuddy_learning | 4130 | 3650.0 | 0.88 | `SELECT a.attempt_id::text, e.evidence_id::text, a.item_id::text, a.occurred_at, e.recorded_at, a.hint_level_used, a.self_report, e.source_method, e.evidence_kind, e.evidence_level, e.map_version_id::t` |

## 各接口

| 接口 | 请求数 | 错误率 | 业务拒绝 | 429 限流 | 503 SUBJECT_BUSY | 预期 | p50 ms | p95 ms | p99 ms | 吞吐 req/s | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BFF POST /auth/otp/request | 620 | 0.00% | 0 | 0 | 0 | 0 | 9.7 | 31.5 | 64.6 | 4.0 | 202×620 |
| BFF POST /auth/otp/verify | 620 | 0.00% | 0 | 0 | 0 | 0 | 8.6 | 28.4 | 52.8 | 4.0 | 200×620 |
| BFF POST /students/{id}/student-session | 425 | 0.00% | 0 | 0 | 0 | 0 | 10.8 | 29.6 | 42.7 | 2.7 | 200×425 |
| BFF GET /students/{id}/today（跨科编排 + 快照，plan_events 场景；不与基线 today 比较） | 425 | 0.00% | 0 | 0 | 0 | 0 | 107.5 | 237.9 | 286.7 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions | 425 | 0.00% | 0 | 0 | 0 | 0 | 52.1 | 219.2 | 355.7 | 2.7 | 201×425 |
| BFF GET /students/{id}/diagnosis/sessions/{sid} | 425 | 0.00% | 0 | 0 | 0 | 0 | 39.4 | 211.0 | 343.8 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/answer | 425 | 0.00% | 0 | 0 | 0 | 0 | 193.1 | 539.8 | 688.5 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/pause | 425 | 0.00% | 0 | 0 | 0 | 0 | 33.1 | 85.9 | 118.1 | 2.7 | 200×425 |
| BFF meteredCall → 学习服务 grade（数学，验证器） | 425 | 0.00% | 0 | 0 | 0 | 0 | 140.8 | 338.0 | 401.4 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（道法，模型 T2） | 425 | 0.00% | 0 | 0 | 0 | 0 | 473.0 | 1484.0 | 1896.1 | 2.7 | ok×425 |
| BFF meteredCall → 学习服务 grade（语文作文，模型 T4） | 425 | 0.00% | 0 | 0 | 0 | 0 | 503.7 | 1325.2 | 1939.7 | 2.7 | ok×425 |
| BFF GET /students/{id}/star-map（学生） | 425 | 0.00% | 0 | 0 | 0 | 0 | 11.6 | 32.6 | 49.3 | 2.7 | 200×425 |
| BFF GET /me（家长首页） | 195 | 0.00% | 0 | 0 | 0 | 0 | 10.5 | 33.1 | 48.3 | 1.2 | 200×195 |
| BFF GET /students/{id}/star-map（家长） | 195 | 0.00% | 0 | 0 | 0 | 0 | 13.3 | 36.8 | 51.4 | 1.2 | 200×195 |
| BFF GET /students/{id}/balance（家长） | 195 | 0.00% | 0 | 0 | 0 | 0 | 22.9 | 51.1 | 98.3 | 1.2 | 200×195 |
| BFF GET /students/{id}/enrollments（家长） | 195 | 0.00% | 0 | 0 | 0 | 0 | 18.7 | 43.7 | 66.7 | 1.2 | 200×195 |
| BFF GET /students/{id}/practice/math（再练列表） | 425 | 0.00% | 0 | 0 | 0 | 0 | 46.3 | 122.1 | 150.2 | 2.7 | 200×425 |
| BFF POST …/practice/math/entries/{eid}/next（再练取题，经闸门） | 425 | 0.00% | 0 | 0 | 0 | 0 | 100.8 | 312.8 | 613.5 | 2.7 | 200×425 |
| BFF GET /students/{id}/retest/math（复测列表） | 425 | 0.00% | 0 | 0 | 0 | 0 | 53.7 | 120.7 | 167.7 | 2.7 | 200×425 |
| BFF POST …/retest/math/entries/{eid}/next（复测取题，经闸门） | 425 | 0.00% | 0 | 0 | 0 | 0 | 98.4 | 326.9 | 498.1 | 2.7 | 200×425 |
| BFF POST …/practice/math/answer（再练作答，票据） | 425 | 0.00% | 0 | 0 | 0 | 0 | 227.0 | 658.9 | 872.4 | 2.7 | 200×425 |
| BFF POST …/retest/math/answer（复测作答，票据） | 425 | 0.00% | 0 | 0 | 0 | 0 | 291.8 | 720.3 | 902.1 | 2.7 | 200×425 |
| BFF POST /students/{id}/events（plan_done） | 425 | 0.00% | 0 | 0 | 0 | 0 | 10.6 | 29.5 | 60.2 | 2.7 | 201×425 |
| BFF POST /students/{id}/events（plan_skip） | 425 | 0.00% | 0 | 0 | 0 | 0 | 11.0 | 29.4 | 51.3 | 2.7 | 201×425 |
| BFF GET /students/{id}/my-plan（学生） | 425 | 0.00% | 0 | 0 | 0 | 0 | 9.8 | 31.4 | 57.3 | 2.7 | 200×425 |
| BFF GET /students/{id}/star-map/v1（学生） | 425 | 0.00% | 0 | 0 | 0 | 0 | 40.6 | 87.5 | 113.8 | 2.7 | 200×425 |
| BFF POST /students/{id}/diagnosis/sessions（续做） | 425 | 0.00% | 0 | 0 | 0 | 0 | 43.1 | 176.1 | 387.6 | 2.7 | 200×425 |
| BFF GET …/diagnosis/sessions/{sid}（续做后取题） | 425 | 0.00% | 0 | 0 | 0 | 0 | 37.0 | 181.0 | 245.5 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/probe | 425 | 0.00% | 0 | 0 | 0 | 0 | 64.4 | 201.7 | 337.9 | 2.7 | 200×425 |
| BFF POST …/diagnosis/sessions/{sid}/abandon | 425 | 0.00% | 0 | 0 | 0 | 0 | 38.0 | 94.8 | 155.6 | 2.7 | 200×425 |
| BFF GET /students/{id}/monthly-plan（家长） | 195 | 0.00% | 0 | 0 | 0 | 195 | 11.6 | 32.8 | 52.8 | 1.2 | 503 UNAVAILABLE×195 |
| BFF GET /students/{id}/weekly-report（家长） | 195 | 0.00% | 0 | 0 | 0 | 0 | 10.7 | 28.3 | 47.5 | 1.2 | 200×195 |
| BFF GET /students/{id}/profile-pages/math（家长） | 195 | 0.00% | 0 | 0 | 0 | 0 | 13.4 | 33.7 | 46.4 | 1.2 | 200×195 |
| BFF GET …/exams/{exam_id}/prediction（家长） | 195 | 0.00% | 0 | 0 | 0 | 195 | 10.5 | 33.9 | 46.5 | 1.2 | 503 UNAVAILABLE×195 |
| 计量 meter_reserve ×2（同键并发） | 206 | 0.00% | 0 | 0 | 0 | 0 | 10.0 | 44.0 | 56.7 | 1.3 | outcome:existing×103、outcome:reserved×103 |
| 计量 meter_settle | 103 | 0.00% | 0 | 0 | 0 | 0 | 7.0 | 20.5 | 24.6 | 0.7 | outcome:settled×103 |
| 计量 meter_settle（重复，幂等） | 103 | 0.00% | 0 | 0 | 0 | 0 | 3.3 | 16.7 | 36.8 | 0.7 | outcome:settled×103 |
| 计量 meter_reserve | 103 | 0.00% | 0 | 0 | 0 | 0 | 9.2 | 23.5 | 34.6 | 0.7 | outcome:reserved×103 |
| 计量 meter_release | 103 | 0.00% | 0 | 0 | 0 | 0 | 5.5 | 28.1 | 42.7 | 0.7 | outcome:released×103 |

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

## 写路径（PLT-21 / PLT-22 掌握路径）

- 合成地图 aya 1.0.0（经两名合成 staff 签字发布）：2 个单元（层次 apply）、12 道合成题（经准入闸门升为 admitted，打标审核通过）；真知识服务只读这个一次性库，响应由学习服务按契约校验；判分走阿鸦合成题路径（验证器，确定性；合成阿鸦进程打开了候选版开关，只限该进程）；合成内容包的准入决定记录是**合成占位摘要，不是真实准入依据**
- 种子（压测前准备数据：经学习服务 grade 写入路径，**不经计量**——不预留、不结算、不计次，不计入压测指标）：425 名学生、850 道作答（practice:incorrect×425、retest:correct×425）；复测任务 850 条（decay:open×850），**工具不写任务表**，任务只由学习服务落库后的掌握重算生成（推断能证明由重算写出 850、不能证明 0）
- 掌握配置：`tools/loadtest/config/mastery.synthetic.json`（syn-0.1.0，**压测专用合成副本、未签字，只在 LEARNING_ENV=test 加载；数值待人确认，不代表任何学科口径**）；作答落库后同步重算（预算 2.0 s（learning.config 缺省））；复测第一轮（rung0）间隔 7200 s
- 派生条目的“预期”已停用（派生的照发、按错误计）；取题没拿到真票据时**不发作答**，单列“上游未取到（未发作答）”：再练 0、复测 0（这些步骤记为跳过，不计入一致性）

| 核对项 | 值 |
|---|---|
| 作答（真票据 2xx） | 850 |
| 真票据 / 派生票据 | 850 / 0（真路径占比 100.0%） |
| 合成题计次增量（practice_log，经幂等键关联到合成题作答） / 全部计次增量 | 850 / 850 |
| 作答证据行增量（每次作答一行） | 850 |
| 触发的单元重算次数（pg_stat_statements） | 850 |
| 重算失败 / 超时（学习服务日志） | mastery_after_ingest_failed×0、mastery_after_ingest_timeout×0、mastery_recompute_failed×0 |
| 复测任务（压测后）：总数 / 推断由重算写出 / 不能证明 / 开着且已到期 | 1275 / 1275 / 0 / 0 |
| 复测任务（压测后）按原因:状态 | decay:cancelled×850、scheduled:open×425 |
| 推断依赖的触发器（0005 guard_inference_insert、0037 retest_task_guard） | mastery_estimate.guard_inference_insert:启用、retest_task.retest_task_guard:启用 |
| 直接证据：任务表写入（pg_stat_statements，种子 + 压测）服务重算语句 / 非服务写入 | 2125 / 0 |
| 证据行总增量（含判分轨迹） / 每次作答 | 1700 / 2.0 |
| 计次无对应作答证据 / 作答证据无对应计次（增量） | 0 / 0 |
| 结论 | **通过** |

口径：作答 = 合成题计次增量 = 作答证据行增量 = 触发的单元重算次数，且两类“无对应”都为 0、全部计次 = 合成题计次 + 无对应作答证据的计次。题库自练是“开始即计数、没有释放”（BFF 先计次再调学习服务），计次之后学习服务失败会留下“计次无对应作答证据”——这是设计口径，不是计量故障；本机无故障注入时应为 0，出现即判不通过。

重算触发口径：每次 grade / diagnose 新落库提交后（重放不触发），阿鸦有掌握配置，同步重算这次作答涉及的单元（只按主节点）；本栈只有合成题在合成地图里打了标，学生旅程的数学批改与诊断也触发但不涉及单元，所以每次合成题作答恰好重算一个单元（不计入的证据——如同族重做、低置信——照样触发重算；撞唯一索引重试会多读一次，表现为不相等，详见 README）。复测任务来源是**推断 + 直接证据**：推断 = 复测轮的 scheduled_by_estimate_id 指向同一事务写出的掌握记录（其他原因：同单元有同一事务写出的记录；依赖上表两个触发器把时间戳置为 now()）；直接证据 = pg_stat_statements 里写任务表的语句只有学习服务重算的那几条。decay 任务在作答后由重算**撤销**（cancelled），不是关闭；复测轮的关闭与晋级（passed / failed）在压测窗口内没有测到（rung0 间隔见上）。

### 掌握重算（KGE-04 after_ingest，合成掌握配置）

- 单元重算次数（压测期间）：850；写出新掌握记录 850 条（变化低于写入阈值的重算不写记录），其中取到提交时间戳的 850 条
- 耗时取数据库事务提交时间戳（track_commit_timestamp，只在一次性实例打开；服务源码不改、不加日志）：“单元事务”= 重算事务开始 → 提交；“作答后重算”= 这次作答的证据落库提交 → 重算事务提交（含预读地图与证据）；只覆盖写出新记录的重算

| 范围 | 单元重算次数 | 写出记录 | 单元事务 p50 ms | p95 ms | 最大 ms | 作答后重算 p50 ms | p95 ms | 最大 ms |
|---|---|---|---|---|---|---|---|---|
| 全程 | 850 | 850 | 10.8 | 37.3 | 78.0 | 32.7 | 105.1 | 197.7 |
| 0.5× | 46 | 46 | 3.3 | 9.4 | 11.8 | 7.3 | 15.5 | 18.6 |
| 1× | 84 | 84 | 3.0 | 10.7 | 21.9 | 7.4 | 26.1 | 61.4 |
| 2× | 221 | 221 | 18.3 | 44.3 | 72.2 | 51.7 | 127.1 | 197.7 |
| 2× 保持 | 499 | 499 | 11.8 | 34.6 | 78.0 | 34.2 | 96.8 | 161.2 |

阶段按墙钟归属（重算次数取阶段边界的 pg_stat_statements 快照相减；记录按 computed_at 落在哪个边界区间）。

门槛状态变化（写出的记录里前一状态 → 新状态）：变化 850 次（understand→execute×425、unseen→understand×425）；状态不变、只更新 p / 置信度的写入 0 次。合成配置未签字：状态机最高只到“稳定”，不会点亮星。

各环节耗时（各服务自己的访问日志，只取方法名、状态码、耗时；压测期间，不含种子）：

| 环节 | 次数 | 非 2xx | p50 ms | p95 ms | 最大 ms |
|---|---|---|---|---|---|
| 学习服务 再练取题（含知识服务与 generate_variation） | 425 | 0 | 77.0 | 189.0 | 1085.0 |
| 学习服务 复测取题（同上） | 425 | 0 | 74.0 | 166.0 | 257.0 |
| 知识服务 item.similar | 850 | 0 | 7.0 | 17.0 | 41.0 |
| 阿鸦 generate_variation（选题） | 850 | 0 | 0.0 | 1.0 | 47.0 |
| 学习服务 复测作答预检（计次之前） | 425 | 0 | 30.0 | 90.0 | 137.0 |
| 学习服务 再练作答（验票、判分、证据落库） | 425 | 0 | 195.0 | 510.0 | 713.0 |
| 学习服务 复测作答（同上） | 425 | 0 | 219.0 | 510.0 | 706.0 |
| 阿鸦 grade（验证器；含学生旅程的数学批改） | 1700 | 0 | 0.0 | 2.0 | 15.0 |

BFF 侧（含计次与转发）见“各接口”表的再练 / 复测取题、作答行；学习服务作答环节的耗时已含同步掌握重算（掌握重算耗时单列见上）；2× 阶段数学闸门 503 SUBJECT_BUSY 的增量可能来自同步重算拉长了作答占用闸门的时间（与 --no-mastery 对照比较，见汇报，未单独归因）。

注：开了 plan_events，学生的今日计划另走跨科编排与快照，行名“today.orch”，不与基线的 today 比较。

## SLO 判定（config/slo.yaml）

- 状态：**待技术负责人签字**；判定方式：只报告（未达不影响退出码）；边界值算通过；样本按到达阶段归属

| 路由组 | 请求数 | 判定 | p50 ms | p95 ms | p99 ms | 错误率 | 429 占比 | 503 BUSY 占比 |
|---|---|---|---|---|---|---|---|---|
| login | 1665 | 通过 | 9.6 / ≤300.0 | 30.4 / ≤800.0 | 54.3 / ≤2000.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / — |
| student_read | 2125 | 通过 | 24.1 / ≤300.0 | 97.5 / ≤800.0 | 140.5 / ≤2000.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / — |
| today_orch | 425 | 通过 | 107.5 / ≤400.0 | 237.9 / ≤800.0 | 286.7 / ≤2500.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / — |
| gated_read | 850 | 通过 | 38.3 / ≤300.0 | 187.4 / ≤800.0 | 316.4 / ≤2000.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / ≤1.00% |
| student_write | 5100 | 通过 | 61.0 / ≤400.0 | 436.4 / ≤800.0 | 683.7 / ≤2500.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / ≤1.00% |
| grade | 1275 | 通过 | 343.3 / — | 1205.2 / ≤30000.0 | 1812.5 / — | 0.00% / ≤2.00% | 0.00% / — | 0.00% / — |
| parent_read | 1170 | 通过 | 14.8 / ≤300.0 | 39.0 / ≤800.0 | 54.3 / ≤2000.0 | 0.00% / ≤1.00% | 0.00% / ≤0.50% | 0.00% / — |
| metering | 618 | 通过 | 7.3 / — | 30.7 / ≤800.0 | 53.6 / — | 0.00% / ≤0.10% | 0.00% / — | 0.00% / — |

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
| aya | 3515 | 735 | 0 | 0 | — | 0×3515、lt100×482、lt500×245、lt2000×8 |

## 数据库

连接（pg_stat_activity 每 0.5 s 采样的峰值）：

| 登录角色 | 最大连接 | 最大活跃 | 最大锁等待 |
|---|---|---|---|
| bff_app | 17 | 3 | 1 |
| learning_app | 18 | 4 | 0 |

应用侧池上限：学习服务 asyncpg 池 max 10 + 再练作答串行锁池 max 8（learning/app.py PRACTICE_LOCK_POOL_SIZE，打开再练时）、BFF pg 池 max 10（services/bff/src/main.ts）；批改经计量驱动桩运行 BFF meteredCall，另用 bff_app 池 max 20；计量旅程另用 bff_app 池 max 20

慢查询前十（pg_stat_statements，按总耗时；语句已规范化；角色含 bff_app、learning_app 与 starbuddy_learning——学习服务在 SET LOCAL ROLE starbuddy_learning 下执行的语句记在后者名下，PLT-22 起计入，与 PLT-21 及以前的样例不可直接比较；连接采样仍按登录名）：

| # | 角色 | 调用 | 总 ms | 平均 ms | 最大 ms | 语句（截断） |
|---|---|---|---|---|---|---|
| 1 | bff_app | 4215 | 11692.5 | 2.77 | 20.8 | `SELECT platform.meter_guard_status($1::uuid) AS g` |
| 2 | bff_app | 2009 | 8152.2 | 4.06 | 61.2 | `SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r` |
| 3 | bff_app | 2331 | 5802.7 | 2.49 | 58.6 | `SELECT platform.meter_settle($1, $2) AS r` |
| 4 | starbuddy_learning | 7225 | 5262.7 | 0.73 | 8.2 | `SELECT a.attempt_id::text, e.evidence_id::text, a.item_id::text, a.occurred_at, e.recorded_at, a.hint_level_used, a.self_report, e.source_method, e.evidence_kind, e.evidence_level, e.map_version_id::t` |
| 5 | starbuddy_learning | 6541 | 4783.7 | 0.73 | 8.8 | `INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id, admission, payload, media_ref, source_method, request_id, request_idempotency_key, ev` |
| 6 | bff_app | 10485 | 4503.6 | 0.43 | 17.8 | `SELECT account_id, account_kind::text AS account_kind, origin FROM platform.auth_session_resolve($1)` |
| 7 | bff_app | 11105 | 2873.2 | 0.26 | 7.9 | `SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok` |
| 8 | starbuddy_learning | 2550 | 2524.9 | 0.99 | 16.7 | `INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total, calibrated, confidence, source_version, model_version, verifie` |
| 9 | bff_app | 5950 | 1762.7 | 0.3 | 4.9 | `SELECT platform.is_self_student($1::uuid) AS ok` |
| 10 | bff_app | 195 | 1676.1 | 8.6 | 20.3 | `SELECT resource_type, unit, source, period_end, remaining FROM platform.entitlement_balance WHERE student_id = $1 ORDER BY resource_type, source, period_end` |

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
