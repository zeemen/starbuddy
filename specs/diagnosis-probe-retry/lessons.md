# 诊断探针提交失败的处理 · 实现记录

- 2026-11-22 用户“按建议，默认值都同意，开始实现”：可重试的留在原处冷却后再给一次机会；每步只多给一次；冷却时长同再练、复测。

## 做了什么
- `apps/web/src/diagnosis/probe-retry.ts`（新）：`probeRetryable`（503 `SUBJECT_BUSY`、429、断连 / 超时的 503 无错误码为可重试）与
  `nextAfterProbe`（recorded / curfew / cooldown / skip；宵禁优先；可重试且这一步没重试过才冷却）。
- `src/lib/diagnosis.ts`：失败返回带上 `retryAfterSec`（只认合口径的 `retry-after`，同再练）。
- `src/app/dev/diagnosis/actions.ts` 的 `probeAction`：只在可重试的失败时多返回 `retryable`、`retryAfterSec`。
- `src/probe/ConfidenceProbe.tsx`：可选 `cooling` 属性，冷却时三个选项与“跳过”都禁用，选项按钮带 `data-cooldown`。
- `src/diagnosis/Diagnosis.tsx`：`onPredict` 按 `nextAfterProbe` 分支；冷却时探针留在原处、显示已有文案 `notSaved`、按 `retry-after` 冷却（复用
  `useCooldown` 与 `cooldownMs`），不记跳过、不自动重发；第二次可重试失败或不可重试失败照旧记跳过、进入作答。跳过时清掉提示。
- 测试 `tests/diagnosis-probe-retry.test.ts`（失败分类、下一步、action 与组件接线）；`tests/diagnosis.test.ts` 数宵禁分支的断言改为两种写法分别计数。

## 与 spec 的差异
- **没有加“提交被拒”的端到端**：诊断的端到端（`e2e/diagnosis.spec.ts`）跑在第一套 BFF 上，后面是真实学习服务，这一套没开分学科闸门，造不出被拒；
  为它打开闸门会影响同一套里的其他用例。“被拒”这条路径改由单测覆盖（纯函数 + 接线的静态检查）；原有诊断、探针端到端 5 项照常通过
  （含“家长打开演示页提交被拒”——那是 403，不在可重试之列，行为不变）。诊断上学生端入口时，建议在那一套上补被拒的端到端。

## 验证
- 前端单测 278 项通过；`e2e/diagnosis.spec.ts`、`e2e/probe.spec.ts` 5 项通过；`make check-fast` 通过。

## 待人确认
- 无新增。
