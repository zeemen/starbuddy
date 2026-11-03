# PLT-20 2 倍峰值压测 · 第一步（补场景、SLO 判定、本机 2 倍阶段；pilot 执行另起）

2026-10-18 起由 Claude 按用户授权自主推进（开发计划 v2.0 阶段 1 · 5 月“2 倍峰值压测”；完整方案 v1.1 容量一节“按 2 倍突发规划”；W13 技术债 T38）。
编号 PLT-20 为仓库排队号。零迁移、不改契约、不改服务逻辑与计量、零新依赖；只在本机一次性合成栈上运行，不接触 pilot 或生产。

## 现状（只读摸底）
- `tools/loadtest` 场景：学生（登录、today、诊断 start/current/answer/pause、三科批改经 meter-harness、star-map）、家长（me、孩子 star-map / balance / enrollments）、计量（reserve / settle / release）；`--with-practice` 只到 practice / retest 的列表与 next。
- 已上线未覆盖：再练与复测 answer；今日计划快照 plan_done / plan_skip（经 `POST /students/{id}/events`）；monthly-plan、my-plan；star-map v1；weekly-report 及同表 profile-pages、first-report、prediction；诊断 probe、abandon。知识服务无 BFF 读路由；模考无路由。
- 峰值：完整方案按 100 万注册推算高峰 7,500 在线、约 37.5 次/秒，2 倍突发约 75 次/秒——不是 5 月实际规模。通过线（p95 ≤ 800 ms、批改 ≤ 30 s）仍是建议值未签字；基线只跑过 small 档（10 并发约 11 req/s、p95 408 ms）。

## 范围（`tools/loadtest`）
1. **补场景**（合成数据，经 BFF 真实路由）：再练与复测的 answer（先种到期条目，作答走票据）；plan_done / plan_skip 事件；monthly-plan、my-plan；star-map v1；weekly-report、profile-pages、prediction；诊断 probe、abandon。每条场景按旅程权重进混合负载；新场景默认关，`--journeys` 或配置启用。
2. **SLO 判定**：新建 `config/slo.yaml`（按路由组：p50 / p95 / p99 上限、错误率上限、429 / 503 SUBJECT_BUSY 分别计），标“待技术负责人签字”；报告每组给“通过 / 未达 / 无通过线”，未签字时退出码不因未达而失败（只在报告里标出），签字后可切严格。
3. **2 倍阶段**：`--profile peak2x`：以配置中的“峰值并发”（合成占位，标待确认）为 1 倍，阶段 0.5× → 1× → 2× → 2×保持，可与 `--subject-gate`、`--fault slow` 组合；超过 20 并发仍须 `--i-know`。报告给每阶段吞吐、分位数、错误率、连接池峰值与等待、慢查询、闸门排队分档。
4. **本机可行性**：在本机合成栈上实际跑一次 small 档全场景与一次 peak2x（以本机可承受的峰值占位，如 1× = 20 并发）作为回归对照，结果写 `sample/peak2x.md`（首段“本机合成，不是容量结论”）。
5. **RUNBOOK**：新增“pilot 2 倍峰值演练”一节，列出必须由人在 pilot 执行的步骤与前提（驱动器接入 pilot 的适配未实现、用 pilot 实测峰值定目标、多实例下熔断与闸门、监控值守与停止条件、计量与真实供应商成本），本步只写方案。
6. **测试**：新场景的请求构造与票据处理单测；SLO 判定纯函数（边界、无通过线、未签字不失败）；peak2x 阶段生成；报告格式与 digest；`loadtest-test`（若已有则扩展）接进 check，实际压测运行不进 check。

## 不做
- 接入 pilot、真实供应商、多实例；改服务、计量、闸门参数；知识服务与模考场景（无路由）。

## 验收
- 新场景在本机合成栈跑通且零泄漏；SLO 判定与阶段生成测试通过；本机 peak2x 样例生成；严格 `make check` 通过。

## 待人确认
1. 通过线（各路由组 p95 / 错误率）签字；未签字前只报告不阻断。
2. “1 倍峰值”取值：按 pilot 实测、按 5 月计划规模（约 1,000 户内测）推算，还是沿用完整方案的 100 万注册推算。
3. pilot 演练的执行人、时间窗与停止条件；驱动器接入 pilot 的适配（另起，涉及凭据与环境，必停）。
