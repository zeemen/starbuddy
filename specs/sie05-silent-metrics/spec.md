# SIE-05 中圈指标静默采集补齐 · 第一步（接通原料、定时重算、效度检验覆盖全部静默指标）

2026-10-14 起由 Claude 按用户授权自主推进（开发计划 v2.0 阶段 1 · 3 月“中圈指标静默采集”，为 Gate 1“至少两个中圈指标通过效度检验”提前准备）。
编号 SIE-05 为仓库排队号。零迁移、不改契约、不改判断模板与可见范围、不改指标口径。

## 现状（只读摸底结论）
- SIE-02b 已有 8 个静默指标纯函数（`services/insight/insight/metrics.py`），结果写 `insight.claim`（`target_type='metric'`、`visibility={agent}`、状态最多“假设”），入口 `python -m insight.jobs metrics --all|--student`。
- 缺口：
  1. 指标重算没进调度（`services/scheduler/scheduler/config.py` 的 insight 身份只放行 `sweep`、`weekly`）。
  2. `plan_done` / `plan_skip` 全仓没有写入方；`session_start` / `session_end` 只有开发页 `apps/web/src/app/dev/probe` 在写。计划完成率、学习持续性在真实使用中没有原料。
  3. Gate 1 效度检验（`tools/gate1-evidence`）只覆盖 4 个指标；合成队列不产出探针、计划事件、错因，自评校准、计划完成率、持续性、审题类错因占比无法检验。

## 范围
### PR1 后端：定时重算与效度检验覆盖
1. 调度器：insight 身份放行 `metrics` 子命令，新增每日一次的“中圈指标静默重算”作业（`--all`），频率写在配置里标“待人确认”；沿用 PLT-13 的身份、环境变量、拒绝退出码与日志口径。
2. `tools/gate1-evidence`：
   - 合成队列补齐原料：做题前探针（yes/no/unsure）、计划事件（plan_done / plan_skip，按 plan_task_id 去重取最后一次）、会话事件、审题类错因归因；全部合成、可复现（固定种子）。
   - 效度检验覆盖全部 8 个静默指标；每个指标标明结果变量（沿用现有 outcome 口径，新增指标的结果变量写进配置并标“待签字”）；样本不足的指标报“样本不足”，不报通过。
   - 输出 `sample/metrics.csv` 列不变；新增指标出现在同一文件。
3. 测试与变异：调度配置单测（metrics 作业存在、身份正确、未放行的子命令仍拒绝）；效度检验新增指标的正反例；变异接进现有 `scheduler-mutation`、`gate1-mutation`。

### PR2 前端：真实页面写入行为事件
4. 学生壳（`apps/web/src/app/s/[studentId]/`）进入时写 `session_start`（`entry` 取 today / star_map / subject / other），离开或超时写 `session_end`（`duration_sec`）；今日计划任务的“完成”“跳过”写 `plan_done` / `plan_skip`（`plan_task_id` 取今日计划已有的任务 ID）。
   - 只经 BFF 现有事件接口（`services/bff/src/events/`），不新增接口、不改 payload 白名单；失败静默不打扰学生，不重试风暴（同一事件幂等键去重）。
   - 不记录页面停留细节、不做作息分析（迁移 0025 注释与安全审查 H1）；家长端不写。
5. 测试：单测与端到端（合成学生）覆盖四类事件写入、越权学生 403、重复点击只记一次；axe 不退化。

## 不做
- 迁移（`attempt.self_report` 读权限、“主动发起”字段、计划完成落库、`retest_task` 读权限）；`self_initiated_sessions`、“难题坚持时长”、“检查习惯”等未实现指标；任何对学生、家长的展示；改指标口径、判断模板、可见范围、禁用推断词表；改效度门槛（|r|≥0.3、至少 2 个指标，仍待签字）。

## 验收
- PR1：调度器能按配置调起指标重算；效度检验报告覆盖 8 个指标且合成队列上可复现；变异全部发现；严格 `make check` 通过。
- PR2：真实学生页产生四类行为事件、去重与越权正确；严格 `make check` 通过。

## 待人确认（先写下，实施中补充）
1. 指标重算频率（默认每日一次）。
2. 新增指标的效度结果变量。
3. 学习持续性用会话时间戳是否与 0025“时间戳只用于降负触发，不做作息分析”冲突：本步只按“活跃日”计数，不看时段。
