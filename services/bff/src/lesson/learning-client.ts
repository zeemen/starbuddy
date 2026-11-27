// BFF → 学习服务的讲评课内部接口（services/learning/openapi/lesson-internal.v0.yaml）。
// 鉴权与响应归类照诊断与再练客户端：Bearer 会话令牌（LEARNING_SESSION_TOKEN）、不跟随重定向、带超时；
// ok（2xx）、failure（确定没有执行：4xx 且 retryable 不为真）、unknown（5xx、超时、断连、retryable）。
// 学生 ID 只进请求体，不进 URL。
import { classify, type DiagOutcome } from '../diagnosis/learning-client.ts';

export type LessonOutcome<T> = DiagOutcome<T>;

export interface OpenRequest {
  student_ref: string; subject: string; lesson_ref: { lesson_id: string; lesson_version: string }; skill_node: string; item_ref?: string;
  usage_id: string; idempotency_key: string;
}
export interface OpenBody {
  session_id: string; usage_id: string; dsl_version: string; dsl: unknown; script: unknown; ticket: string; expires_at: string;
  question_limit: number; question_count: number;
}
export interface AskRequest { student_ref: string; ticket: string; question: unknown; idempotency_key: string }
export type AskBody =
  | { outcome: 'answered'; text: string; question_seq: number; questions_left: number }
  | { outcome: 'no_answer'; reason: 'not_available' | 'limit'; question_seq: number | null; questions_left: number };

export interface LessonLearning {
  open(body: OpenRequest, timeoutMs?: number): Promise<LessonOutcome<OpenBody>>;
  ask(body: AskRequest, timeoutMs?: number): Promise<LessonOutcome<AskBody>>;
}

export interface HttpLessonOptions {
  baseUrl: string;
  token: string;
  fetchImpl?: typeof fetch;
  /** 学习服务调适配器的超时是 10 秒（重试一次，总预算 21 秒）：这里多留余量 */
  timeoutMs?: number;
}

export function httpLessonLearning(opts: HttpLessonOptions): LessonLearning {
  const doFetch = opts.fetchImpl ?? fetch;
  const base = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/lesson`;
  const call = async <T>(path: string, body: object, cap?: number): Promise<LessonOutcome<T>> => {
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
    return classify<T>(res.status, parsed, res.headers.get('idempotent-replayed') === 'true');
  };
  return {
    open: (b, t) => call<OpenBody>('open', b, t),
    ask: (b, t) => call<AskBody>('ask', b, t),
  };
}
