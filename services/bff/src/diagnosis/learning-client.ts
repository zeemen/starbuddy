// BFF → 学习服务的诊断会话内部接口（services/learning/openapi/diagnosis-internal.v0.yaml）。
// 鉴权照现有服务间做法（src/metering/learning-client.ts）：Bearer 服务间令牌、不跟随重定向、带超时。
// 响应归三类：ok（2xx）、failure（确定没有执行：4xx，且 retryable 不为真）、unknown（5xx、超时、断连、retryable）。
// 结果未知时调用方用同一个请求重试：学习服务每一步都幂等（开始按幂等键；探针、作答、暂停按会话与序号）。

export interface Problem { code?: string; status?: number; retryable?: boolean; [k: string]: unknown }

export type DiagOutcome<T> =
  | { kind: 'ok'; status: number; body: T; replayed: boolean }
  | { kind: 'failure'; status: number; problem: Problem }
  | { kind: 'unknown'; reason: string };

export interface StartBody { session_id: string; usage_id: string; status: string; created: boolean }
export interface StepView {
  seq: number;
  kind: 'adaptive' | 'process' | 'explain';
  subject: string;
  item_id: string;
  item: { format: 'single' | 'multi' | 'fill'; stem: string; options?: { key: string; text: string }[] };
  probe_recorded: boolean;
  step_elapsed_sec: number;
}
export interface SessionView {
  session_id: string;
  status: 'active' | 'paused' | 'finished' | 'abandoned';
  state: string;
  usage_id: string;
  elapsed_sec: number;
  time_budget_sec: number;
  item_count: number;
  item_limit: number;
  /** 本次是否作答过过程题或讲解题（前端结束页据此选文案；不是对学生的结论） */
  process_tasks: boolean;
  step: StepView | null;
}
export interface AnswerBody { attempt_id: string; probe_id: string | null; status: string; usage_id: string }

export interface DiagnosisLearning {
  start(studentRef: string, subject: string, usageId: string, key: string): Promise<DiagOutcome<StartBody>>;
  current(studentRef: string, sessionId: string): Promise<DiagOutcome<SessionView>>;
  /** probe_id（刚写好的探针）与 probe_key（同一步已有探针时按派生键找回）二选一 */
  probe(studentRef: string, sessionId: string, seq: number, ref: { probe_id: string } | { probe_key: string }):
    Promise<DiagOutcome<{ status: string }>>;
  answer(studentRef: string, sessionId: string,
         body: { seq: number; answer: unknown; work?: string; elapsed_sec: number; idempotency_key: string }): Promise<DiagOutcome<AnswerBody>>;
  pause(studentRef: string, sessionId: string, seq: number | null, stepElapsedSec: number | null): Promise<DiagOutcome<{ status: string }>>;
  /** item_count：已作答题数（没答过题才退额度） */
  abandon(studentRef: string, sessionId: string): Promise<DiagOutcome<{ status: string; usage_id: string; item_count: number }>>;
}

export function classify<T>(status: number, body: unknown, replayed: boolean): DiagOutcome<T> {
  if (status >= 200 && status < 300) return { kind: 'ok', status, body: body as T, replayed };
  const problem: Problem = body !== null && typeof body === 'object' ? (body as Problem) : {};
  if (status >= 500 || status === 408 || status === 429 || problem.retryable === true) {
    return { kind: 'unknown', reason: `${status} ${problem.code ?? ''}`.trim() };
  }
  return { kind: 'failure', status, problem };
}

export interface HttpDiagnosisOptions {
  baseUrl: string;
  token: string;
  fetchImpl?: typeof fetch;
  /** 作答要经验证器判分：比学习服务调适配器的判分超时（10 秒，重试一次）多留余量 */
  timeoutMs?: number;
}

export function httpDiagnosisLearning(opts: HttpDiagnosisOptions): DiagnosisLearning {
  const doFetch = opts.fetchImpl ?? fetch;
  const base = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/diagnosis/sessions`;
  const call = async <T>(method: 'GET' | 'POST', path: string, body?: unknown, extra: Record<string, string> = {}): Promise<DiagOutcome<T>> => {
    let res: Response;
    try {
      res = await doFetch(`${base}${path}`, {
        method,
        headers: { authorization: `Bearer ${opts.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}), ...extra },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(opts.timeoutMs ?? 30_000),
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
  const sid = (s: string) => `/${encodeURIComponent(s)}`;
  return {
    start: (student_ref, subject, usage_id, idempotency_key) => call('POST', '', { student_ref, subject, usage_id, idempotency_key }),
    // 学生 ID 走请求头，不进 URL（访问日志、代理日志里不留学生 ID；契约审查建议 9）
    current: (student_ref, session) => call('GET', sid(session), undefined, { 'x-student-ref': student_ref }),
    probe: (student_ref, session, seq, ref) => call('POST', `${sid(session)}/probe`, { student_ref, seq, ...ref }),
    answer: (student_ref, session, b) => call('POST', `${sid(session)}/answer`, { student_ref, ...b }),
    pause: (student_ref, session, seq, step_elapsed_sec) => call('POST', `${sid(session)}/pause`, {
      student_ref, ...(seq !== null ? { seq } : {}), ...(step_elapsed_sec !== null ? { step_elapsed_sec } : {}) }),
    abandon: (student_ref, session) => call('POST', `${sid(session)}/abandon`, { student_ref }),
  };
}
