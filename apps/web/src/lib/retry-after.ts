// 作答被闸门拒或被限流后的冷却（specs/retry-after-cooldown）：读 BFF 的 retry-after，算出提交按钮禁用多久。
// BFF 被分学科闸门拒时回 503 SUBJECT_BUSY 带 retry-after（秒，services/bff/config/concurrency.json，取值 1–300）；
// 通用限流 429 不带。前端只认 1–300 的整数秒，其他写法（HTTP 日期、小数、0、负数、超范围）一律当没有。
// 冷却只在页面内存里，刷新就没了；BFF 照样兜底拒绝，不靠前端保证。

/** 没有 retry-after 时的冷却秒数（与现在闸门配置的 retry_after_s 相同） */
export const COOLDOWN_DEFAULT_SEC = 2;
/** 冷却上限：配置写大了也不让学生干等 */
export const COOLDOWN_MAX_SEC = 30;

/** retry-after 头 → 秒数；不合口径为 null */
export function parseRetryAfter(v: string | null | undefined): number | null {
  if (typeof v !== 'string' || !/^[0-9]{1,3}$/.test(v.trim())) return null;
  const n = Number(v.trim());
  return n >= 1 && n <= 300 ? n : null;
}

/** 冷却毫秒数：有 retry-after 用它（封顶 30 秒），没有用 2 秒 */
export function cooldownMs(retryAfterSec: number | null | undefined): number {
  const s = typeof retryAfterSec === 'number' && Number.isInteger(retryAfterSec) && retryAfterSec >= 1 ? Math.min(retryAfterSec, COOLDOWN_MAX_SEC) : COOLDOWN_DEFAULT_SEC;
  return s * 1000;
}
