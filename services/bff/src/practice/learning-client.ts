// BFF → 学习服务的错题再练内部接口（services/learning/openapi/practice-internal.v0.yaml）。
// 鉴权与响应归类照诊断客户端（src/diagnosis/learning-client.ts）：Bearer 服务间令牌、不跟随重定向、带超时；
// ok（2xx）、failure（确定没有执行：4xx 且 retryable 不为真）、unknown（5xx、超时、断连、retryable）。
// 学生 ID 只进请求体，不进 URL（访问日志、代理日志里不留学生 ID）。
// 每次调用可另给本次的超时上限（timeoutMs）：调用方按重试的总时限把剩余时间传进来，单次超时取两者较小值。
import { classify, type DiagOutcome } from '../diagnosis/learning-client.ts';

export type PracticeOutcome<T> = DiagOutcome<T>;

export interface PracticeLearning {
  queue(studentRef: string, subject: string, timeoutMs?: number): Promise<PracticeOutcome<unknown>>;
  next(studentRef: string, subject: string, entryId: string, timeoutMs?: number): Promise<PracticeOutcome<unknown>>;
  answer(studentRef: string, subject: string,
         body: { ticket: string; answer: unknown; idempotency_key: string; usage_id: string }, timeoutMs?: number): Promise<PracticeOutcome<unknown>>;
}

export interface HttpPracticeOptions {
  baseUrl: string;
  token: string;
  fetchImpl?: typeof fetch;
  /** 作答要经验证器判分：比学习服务调适配器的判分超时（10 秒，重试一次）多留余量 */
  timeoutMs?: number;
}

export function httpPracticeLearning(opts: HttpPracticeOptions): PracticeLearning {
  const doFetch = opts.fetchImpl ?? fetch;
  const base = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/practice`;
  const call = async (path: string, body: Record<string, unknown>, cap?: number): Promise<PracticeOutcome<unknown>> => {
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
  };
}
