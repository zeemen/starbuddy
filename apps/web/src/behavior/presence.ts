// 学生壳的会话起止（SIE-05 PR2，specs/sie05-silent-metrics/spec.md 范围第 4 条）：纯函数，单测直接引用；客户端组件也引用这里的常量，
// 所以这里不引用 node:crypto 与 next/headers（签名、读写 cookie、转交 BFF 在 src/behavior/presence-server.ts 与 /s/{id}/presence 路由）。
// “一次会话”跨越壳内多页：壳的导航是整页跳转，页面本身记不住上一页，又不许用浏览器存储（tests/static.test.ts），所以会话状态放在一个
// 带 HMAC 签名的 HttpOnly cookie 里（浏览器脚本读不到，改动即验签失败）。状态只有：学生、开始与最近活动时间、入口类别、开始事件是否已记下、
// 开始与结束事件的幂等键；不记哪一页、停了多久（迁移 0025 注释、安全审查 H1：时间戳只用于降负触发，不做作息分析）。
// 写进库的只有两条事件：session_start（entry 只取入口类别）与 session_end（duration_sec）。
//   - 超时：最近一次活动之后 IDLE_SEC 没有活动，算这次会话结束（页面开着时由壳的计时器发结束；页面关了就在下次进入时补记）；
//   - 结束事件的发生时间是最近一次活动的时间，时长 = 最近活动 − 开始；超过 0025 的一天时间窗就不补记（静默丢弃，不重试）；
//   - 开始与结束事件的幂等键在会话开始时就定下：开始事件没记下时，下一次报告用同一个键、同一发生时间重试（BFF 按幂等键去重）。

/** 无活动多久算会话结束（秒）。取值待人确认（lessons） */
export const IDLE_SEC = 30 * 60;
/** 页面在有活动时多久向服务端报一次“还在”（秒）：只更新 cookie 里的最近活动时间，不写任何事件 */
export const HEARTBEAT_SEC = 5 * 60;
/** 0025 只收最近 1 天内的发生时间；留 10 分钟余量，超出的事件不补记 */
export const MAX_AGE_SEC = 24 * 60 * 60;
const SAFE_AGE_SEC = MAX_AGE_SEC - 10 * 60;

export const ENTRIES = ['today', 'star_map', 'subject', 'other'] as const;
export type Entry = (typeof ENTRIES)[number];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** 会话入口：只按壳内页面的类别取（今日 / 星图 / 科目 / 其他），不记具体哪一页 */
export function entryOf(path: string, student: string): Entry {
  if (!UUID.test(student)) return 'other';
  const base = `/s/${student}/`;
  if (!path.startsWith(base)) return 'other';
  const rest = path.slice(base.length);
  if (rest === 'today') return 'today';
  if (rest === 'star-map') return 'star_map';
  if (rest === 'learn' || rest.startsWith('learn/')) return 'subject';
  return 'other';
}

export const isEntry = (v: unknown): v is Entry => typeof v === 'string' && (ENTRIES as readonly string[]).includes(v);

/** 会话状态（时间为 Unix 秒） */
export interface Presence {
  student: string; start: number; last: number; entry: Entry;
  /** 开始事件是否已记下（没记下时下次报告用同一个 startKey 重试） */
  started: boolean;
  startKey: string; endKey: string;
}

/** 要补记的结束事件：发生时间 = 最近活动，时长 = 最近活动 − 开始（截到 0–86400） */
export interface EndEvent { durationSec: number; at: number; key: string }

/** 这份状态能补记结束吗：开始事件已记下、同一学生、最近活动在 0025 的时间窗内（时间在未来的也不补记） */
export function endOf(prev: Presence | null, student: string, now: number, at: number = prev?.last ?? 0): EndEvent | null {
  if (!prev || !prev.started || prev.student !== student) return null;
  if (prev.last > now || at > now || at < prev.start || now - at > SAFE_AGE_SEC) return null;
  return { durationSec: Math.min(MAX_AGE_SEC, Math.max(0, at - prev.start)), at, key: prev.endKey };
}

/** 会话还在吗：同一学生、最近活动在 IDLE_SEC 内、开始不超过（一天 − 余量）、时间不在未来 */
export function alive(prev: Presence | null, student: string, now: number): prev is Presence {
  return prev !== null && prev.student === student && prev.start <= prev.last && prev.last <= now && now - prev.last <= IDLE_SEC
    && now - prev.start <= SAFE_AGE_SEC;
}

export type Step =
  | { kind: 'continue'; next: Presence }
  | { kind: 'retry_start'; next: Presence }
  | { kind: 'restart'; end: EndEvent | null };

/**
 * 壳的页面报“在”（ping）：会话还在 → 只更新最近活动（开始事件还没记下时，用原来的幂等键与发生时间重试一次）；
 * 否则 → 先补记上一段的结束（能补记时），再开始新会话。touch（离开页面时）只更新还在、且已记下开始的会话。
 */
export function decide(prev: Presence | null, student: string, now: number): Step {
  if (alive(prev, student, now)) return { kind: prev.started ? 'continue' : 'retry_start', next: { ...prev, last: now } };
  return { kind: 'restart', end: endOf(prev, student, now) };
}
