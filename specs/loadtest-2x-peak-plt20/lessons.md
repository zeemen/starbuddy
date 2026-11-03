# PLT-20 2 倍峰值压测第一步 · 实现记录

2026-10-18 起由 Claude 按用户授权自主推进；零迁移、不改契约、不改服务逻辑与计量、零新依赖；只在本机一次性合成栈运行。
提交首行 `[NEEDS-HUMAN]`：SLO 通过线与 1 倍峰值为占位未签字；RUNBOOK 1A pilot 演练涉及真实计费、多实例与试点用户隔离，须人批准与执行。

## 交付
- 可选场景（默认关，`--journeys`）：再练 / 复测作答、plan_done / plan_skip 事件、monthly-plan、my-plan、star-map v1、weekly-report、profile-pages、prediction（默认不进 all）、诊断 probe / abandon / 续做。不开时请求序列 digest 与旧版逐字节相同（两组钉值测试）。
- “预期结果”分类（按状态码 + 问题码，只在本机栈）：本栈无知识服务与合成地图，作答只走到 BFF 票据校验（派生票据 → 422 TICKET_INVALID，计次之前）；月度计划 503 只在 BFF 访问日志为 `generate=not_configured` 时算预期，核对不到改记错误；按步骤报告真路径与派生路径次数与预期占比，异常时告警（`extras_alert`）。请求体经 BFF 源码解析函数校验（有 node 时）。
- SLO：`config/slo.yaml`（待技术负责人签字），按组判“通过 / 未达 / 无通过线”；分位数只取 2xx 与 402 业务拒绝，预期结果单列；过闸门的诊断读单列 `gated_read`；开 plan_events 时 today 改标 `today.orch` 单列。未签字只报告不影响退出码，`--slo-strict` 未签字时退出码 2。
- `--profile peak2x`：0.5× → 1× → 2× → 2× 保持，1× 取 `config/peak2x.yaml` 占位（confirmed: false）；超过 20 并发须 `--i-know`；每阶段分位数、错误率、429 / 503、连接峰值、慢查询（pg_stat_statements，本机）、闸门排队。`--run-id` 进入幂等键派生；`--max-model-calls` 按学习服务调用次数硬上限。
- RUNBOOK 1A“pilot 2 倍峰值演练”（只写方案）：前提、步骤、停止条件（含 402 异常、驱动器硬上限、监控值守判定）、pilot 关闭预期分类、只读观测角色、假供应商不得上共享网络、每轮换种子或 run-id。
- 测试：tools/loadtest/tests 203 通过；`loadtest-test` 接进 check；本机样例 `sample/peak2x.md`。

## 审查意见的处理（按默认）
- 安全（无阻断）：诊断续做核对原会话、run-id、收窄预期、pilot 护栏与停止条件、只读观测、实际状态码写进 README。
- 契约阻断：新测试未进 check、本机样例缺失 → 加 `loadtest-test` 并实跑生成样例；建议：SLO 分位数剔除预期样本、闸门诊断读改组、today.orch 单列、预测只测拒绝路径、真 / 派生路径计数、digest 钉值。

## 本机实跑结果（2026-10-18，合成，不是容量结论）
- small 档全场景（`--journeys all,prediction --subject-gate`）：394 请求，零泄漏，请求序列一致，`extras_alert` 否。
- peak2x（1× = 20 并发，本机占位）：13,618 请求，零泄漏，序列一致；各阶段 p95 约 200 / 200 / 276 / 251 ms，2× 保持吞吐约 110 req/s，429 与 503 SUBJECT_BUSY 均为 0，连接峰值 bff_app 18、learning_app 10，无锁等待。样例见 `tools/loadtest/sample/peak2x.md`。
- **发现**：再练 / 复测取题（`--with-practice` 既有步骤）在本栈全部回 503（各 425 次），原因与作答只到票据校验相同——本栈不起知识服务、没有合成地图与题。这使 student_write 组“未达”（错误率约 0.2%）；未签字不影响退出码。未把它们改记“预期”，留给 PLT-21 补齐写路径后消除。

## 教训
- 极快的预期拒绝会拉低延迟分位数，SLO 必须剔除。
- 通用问题码（422 INVALID、503 UNAVAILABLE）当“预期”会吞掉真实错误，须用旁证（访问日志字段、真路径计数）收窄。
- “不开新场景时 digest 不变”的测试必须钉住旧值，新代码比新代码不构成回归。

## 待人确认
1. SLO 各组通过线签字（含新组 gated_read、today_orch 的占位值）。
2. “1 倍峰值”取值：pilot 实测、5 月内测规模推算，还是完整方案的 100 万注册推算。
3. 是否另起任务补合成地图、假知识服务、能判合成题的适配器与到期条目，让作答真正落库计次（建议做，否则 2 倍峰值下最重的写路径测不到）；否则 spec 措辞改为“只到票据校验”。
4. “预期结果”不计入错误率与 SLO 的口径；`extras_alert` 是否让退出码失败（现只报告）；严格模式是否也看“2× 保持”阶段判定。
5. RUNBOOK 1A：执行人、时间窗、阶段时长（每阶段 ≥ 300 秒、保持 ≥ 900 秒）、停止阈值、成本上限；`--max-model-calls` 按学习服务调用计数是否够；驱动器接入 pilot 另起（涉及凭据与环境，必停）。
6. `loadtest-baseline` 的参数白名单是否加入本次新选项（现直接调用）。

## 决定记录（2026-10-18，用户“按建议确认”）
- 待人确认按现行默认执行：SLO 与 1 倍峰值保持占位、未签字只报告不阻断；另起任务补合成地图、假知识服务、合成判题适配器与到期条目，让作答真正落库计次（排队号另定）；“预期结果”不计入错误率与 SLO，`extras_alert` 暂只报告；RUNBOOK 1A 建议值作为草案，pilot 演练与驱动器接入 pilot 另起必停任务；`loadtest-baseline` 白名单暂不扩。
