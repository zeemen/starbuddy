// BFF → 学习服务的复测与验证题内部接口（services/learning/openapi/retest-internal.v0.yaml）。
// 鉴权与响应归类照错题再练客户端（src/practice/learning-client.ts）：Bearer 服务间令牌、不跟随重定向、带超时；
// ok（2xx）、failure（确定没有执行：4xx 且 retryable 不为真）、unknown（5xx、超时、断连、retryable）。学生 ID 只进请求体，不进 URL。
import { classify } from '../diagnosis/learning-client.ts';
import type { PracticeOutcome } from '../practice/learning-client.ts';

export type RetestOutcome<T> = PracticeOutcome<T>;

export interface RetestLearning {
  queue(studentRef: string, subject: string, timeoutMs?: number): Promise<RetestOutcome<unknown>>;
  next(studentRef: string, subject: string, entryId: string, timeoutMs?: number): Promise<RetestOutcome<unknown>>;
  answer(studentRef: string, subject: string,
         body: { ticket: string; answer: unknown; idempotency_key: string; usage_id: string }, timeoutMs?: number): Promise<RetestOutcome<unknown>>;
  /** 计次前的只读预检（条目仍到期、票据未用）：不通过就不计次（审查意见 2） */
  check(studentRef: string, subject: string, ticket: string, timeoutMs?: number): Promise<RetestOutcome<unknown>>;
  /** 宵禁时只找回已计次的键的原结果（不执行、不封存） */
  replay(studentRef: string, subject: string, body: { ticket: string; idempotency_key: string; usage_id: string },
         timeoutMs?: number): Promise<RetestOutcome<unknown>>;
}

export interface HttpRetestOptions {
  baseUrl: string;
  token: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
}

export function httpRetestLearning(opts: HttpRetestOptions): RetestLearning {
  const doFetch = opts.fetchImpl ?? fetch;
  const base = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/retest`;
  const call = async (path: string, body: Record<string, unknown>, cap?: number): Promise<RetestOutcome<unknown>> => {
    const timeout = Math.max(1, Math.floor(Math.min(opts.timeoutMs ?? 30_000, cap ?? Infinity)));
    let res: Response;
    try {
      res = await doFetch(`${base}/${path}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeout),
        redirect: 'error',
      });
    } catch (err) {
      return { kind: 'unknown', reason: err instanceof Error ? err.name : 'fetch failed' };
    }
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      if (res.ok) return { kind: 'unknown', reason: 'unreadable success body' };
    }
    return classify<unknown>(res.status, parsed, res.headers.get('idempotent-replayed') === 'true');
  };
  return {
    queue: (student_ref, subject, t) => call('queue', { student_ref, subject }, t),
    next: (student_ref, subject, entry_id, t) => call('next', { student_ref, subject, entry_id }, t),
    answer: (student_ref, subject, b, t) => call('answer', { student_ref, subject, ticket: b.ticket, answer: b.answer,
                                                             idempotency_key: b.idempotency_key, usage_id: b.usage_id }, t),
    check: (student_ref, subject, ticket, t) => call('check', { student_ref, subject, ticket }, t),
    replay: (student_ref, subject, b, t) => call('replay', { student_ref, subject, ticket: b.ticket, idempotency_key: b.idempotency_key,
                                                             usage_id: b.usage_id }, t),
  };
}
