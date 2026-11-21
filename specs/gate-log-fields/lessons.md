# 闸门日志字段补进白名单、定时汇总 · 实现记录

- 2026-11-20 用户“按建议，默认值都同意，开始实现”：间隔 60 秒；白名单直接加四个字段（请安全负责人知情）；IaC 日志索引这次不改。

## 做了什么
- **白名单**：`gate_result`（原名 `gate`，11-21 改名）、`gate_subject`、`gate_queue_ms`、`gate_reason` 加进 BFF `src/observability/log.ts` 的 `LOG_FIELDS`，
  同步到学习、洞察、知识服务三份 `obs.py`（`deploy/tests/test_obs_shared.py` 核对四份逐项相同）。
- **定时汇总**：`src/gate/subject-gate.ts` 新增 `startGateStatsLog`（间隔 `GATE_STATS_INTERVAL_MS` = 60 秒，定时器 `unref()`）与 `statsForLog`；
  `src/main.ts` 在闸门建好之后启动，写 `emit('info', { event: 'subject_gate_stats', counts })`。闸门关时不建闸门，也不启动汇总。
- **测试** `tests/gate-log-fields.test.ts`：
  - 静态检查：源码里两参数的 `note('键', …)` 与闸门 `observe?.('键', …)` 的键都在 `LOG_FIELDS` 或 `HASHED_FIELDS` 里；
  - 真实 `format` 输出里有闸门四个字段；
  - 定时汇总：注入定时器，间隔 60 秒、启动时不立即写、`counts` 三科各八项计数、没有学生信息、停止后停掉定时器。
- **变异** 3 条“闸门日志：”（白名单漏 `gate_subject`、汇总不带计数、汇总按原名写计数），全部被发现。

## 与 spec 的差异
1. **`rejected_per_student` 在日志里改名 `rejected_own_limit`**：写测试时发现，日志的嵌套字段规则（`FORBIDDEN_KEY`）把以 `_student` 结尾的键
   当成学生标识整项丢掉，原名写进 `counts` 会少这一项。只在写日志时改名（`statsForLog`），`stats()` 与现有测试不变。
   这条规则本身是对的（防止嵌套结构里带学生标识），不放宽。
2. 静态检查只扫两参数写法；单参数的 `deps.note?.('事件名')`（再练、复测、诊断的计量过程记录）是另一条通路，不在这次范围。

## 验证
- 本机六项全部通过：`make check-fast`、`make deploy-check`（含 `test_obs_shared.py`）、`make bff-test`（类型检查 + 单测 + 集成）、
  `make learning-test`（4031 项，2 项预期失败标记）、`make insight-test`、`make knowledge-test`。
- `BFFMUT_FILTER=闸门日志：` 的 3 条变异全部被发现。

## 待人确认
1. **安全负责人知情**：日志白名单（安全审查 B）多了四个字段，取值全是代码里的固定枚举：`gate_result` 是 admitted / queued / rejected / bypassed，
   `gate_subject` 是学科码，`gate_queue_ms` 是 0 / lt100 / lt500 / lt2000 / ge2000，`gate_reason` 是被拒原因编码。
2. **日志索引**（spec 待人确认第 3 条的另一选项）：要在 SLS 按闸门字段建索引时，改 `deploy/iac/check_iac.py` 的 `LOG_INDEX_FIELDS` 与 IaC，按约定 apply 前给 plan 摘要。
