# 诊断作答提交被拒后的冷却 · 实现记录

- 2026-11-24 用户“按建议，默认值都同意，开始实现”：不限次、每次都冷却；冷却时长同再练、复测、探针；冷却期间计时照常。

## 做了什么
- `apps/web/src/lib/retry-after.ts`：新增 `retryableFailure`（503 `SUBJECT_BUSY`、429、断连 / 超时的 503 无错误码为可重试），由原来
  `src/diagnosis/probe-retry.ts` 的 `probeRetryable` 挪过来改名，探针与作答共用；探针行为不变。
- `src/lib/diagnosis.ts`：新增 `AnswerOutcome` 类型（server action 与组件共用）。
- `src/app/dev/diagnosis/actions.ts` 的 `answerAction`：先成功、再宵禁、再届别变化，最后只在可重试的失败时多返回 `retryable`、`retryAfterSec`。
- `src/diagnosis/Diagnosis.tsx`：作答用第二个 `useCooldown`；可重试失败时显示已有文案 `notSaved`、按 `cooldownMs(retryAfterSec)` 冷却提交按钮
  （禁用、`data-cooldown="on"`），作答与过程不清、不自动重发；冷却中按 Enter 也不提交；换题（`show`）时清冷却；“暂停”不受冷却影响。
- 测试：`tests/diagnosis-answer-cooldown.test.ts`（action 分支顺序、组件接线）；`tests/diagnosis-probe-retry.test.ts` 改引用 `retryableFailure`。

## 与 spec 的差异
- 无。端到端照 spec 不加（诊断那一套 BFF 没开闸门，见 `specs/diagnosis-probe-retry/lessons.md`）；诊断上学生端入口时，在那一套上补探针与作答两条被拒用例。

## 待人确认
1. 作答成功后读下一题被闸门拒时，状态行显示的是“这次没有提交上，请再点一次。”——其实已经交上了；学生再点一次，学习服务按“内容相同”回原结果、
   再读题，结果正确。要改成更准确的说法需要新文案（过禁语与人设评审），请产品定是否要改、改成什么。
