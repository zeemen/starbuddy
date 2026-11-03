// 错题再练（specs/practice-loop-stage1 PR 4）的 BFF 客户端（接口 services/bff/openapi/practice.v0.yaml）：只在服务端调用
// （服务端组件与 server action），浏览器不直连 BFF，前端也不直连学习服务（学习服务的内部接口只认 BFF 的服务间令牌）。
// 每个调用先过前端开关（src/practice/features.ts）：开关关时不发任何请求，直接按 404 返回。全文件只有 call 里一处 fetch。
// 前端不判断授权、不计次、不判分：BFF 与数据库判断；返回值再由 src/practice/view.ts 按白名单挑字段。
// 作答请求体只有 {ticket, answer}：幂等键在 BFF 封装的不透明票据里，usage_id、purpose、channel 都不由前端给。
import { isPracticeSubject, practiceEnabled } from '../practice/features.ts';
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import type { Answer } from './diagnosis.ts';
import { isUuid } from './star-map.ts';

/** reason：402 时 BFF 给的原因编码（curfew、no_entitlement、daily_limit_reached） */
export type PracticeApi<T> = { ok: true; value: T } | { ok: false; status: number; code: string; reason: string | null };

const OFF = { ok: false, status: 404, code: 'NOT_FOUND', reason: null } as const;

/** BFF 整体总时限 45 秒（含重试），这里多留余量 */
const TIMEOUT_MS = 60_000;

async function call<T>(s: BffSession, student: string, subject: string, method: 'GET' | 'POST', tail: string, body?: unknown): Promise<PracticeApi<T>> {
  if (!practiceEnabled()) return OFF;
  if (!isUuid(student) || !isPracticeSubject(subject)) return OFF;
  const headers: Record<string, string> = await bffHeaders(s, method !== 'GET');
  if (body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/practice/${subject}${tail}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 503, code: 'UNAVAILABLE', reason: null };
  }
  let json: unknown = null;
  try { json = JSON.parse(await r.text()); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T };
  const field = (k: string): string | null => json && typeof json === 'object' && typeof (json as Record<string, unknown>)[k] === 'string'
    ? (json as Record<string, string>)[k]! : null;
  return { ok: false, status: r.status, code: field('code') ?? 'INTERNAL', reason: field('reason') };
}

/** 一科的错题条目（只读，不计次，宵禁不挡） */
export const fetchPracticeQueue = (s: BffSession, student: string, subject: string) => call<unknown>(s, student, subject, 'GET', '');

/** 为一个开着的条目取一道再练题（不计次；宵禁中 402 curfew） */
export function nextPracticeItem(s: BffSession, student: string, subject: string, entry: string): Promise<PracticeApi<unknown>> {
  if (!isUuid(entry)) return Promise.resolve(OFF);
  return call<unknown>(s, student, subject, 'POST', `/entries/${entry}/next`);
}

/** 提交作答（BFF 按题库自练计次一次；同一张票据重试不再计次） */
export const answerPractice = (s: BffSession, student: string, subject: string, ticket: string, answer: Answer) =>
  call<unknown>(s, student, subject, 'POST', '/answer', { ticket, answer });
