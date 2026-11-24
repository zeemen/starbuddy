// 诊断“能做对吗”探针提交失败时怎么办（specs/diagnosis-probe-retry，2026-11-22 用户确认默认值）：
//   - 可重试的失败（BFF 分学科闸门被拒 503 SUBJECT_BUSY、限流 429、断连 / 超时）：探针留在原处，按 retry-after 冷却后再给一次机会；
//     不自动重发。BFF 的探针幂等键由（会话、序号）派生，同一步再提交只写一行；
//   - 每一步只多给一次机会：第二次仍是可重试的失败，照旧记成跳过、进入作答，不让学生卡在探针上；
//   - 其他失败（400、403、404、409 等）：维持原来的做法，记成跳过、进入作答。宵禁另有处理（停在“今天先到这里”）。
// 失败分类与作答共用 src/lib/retry-after.ts 的 retryableFailure（specs/diagnosis-answer-cooldown）。
// 纯函数，组件（Diagnosis.tsx）按返回值走分支；单测在 tests/diagnosis-probe-retry.test.ts。

/** 探针提交的结果（server action 的返回） */
export interface ProbeOutcome { ok: boolean; curfew?: boolean; retryable?: boolean; retryAfterSec?: number | null }

export type ProbeNext = 'recorded' | 'curfew' | 'cooldown' | 'skip';

/** 下一步：记上了；宵禁；留在原处冷却（这一步还没重试过）；记成跳过、进入作答 */
export function nextAfterProbe(r: ProbeOutcome, alreadyRetried: boolean): ProbeNext {
  if (r.curfew) return 'curfew';
  if (r.ok) return 'recorded';
  if (r.retryable === true && !alreadyRetried) return 'cooldown';
  return 'skip';
}
