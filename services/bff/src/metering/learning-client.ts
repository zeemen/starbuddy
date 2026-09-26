// 调学习服务的写入接口（packages/contracts/openapi/learning-internal.v0.yaml），并把响应归成三类：
//   success：写入成功；failure：确定没有写入（retryable = false）；unknown：结果未知（retryable = true、5xx、超时、断连）。
// 结果未知时调用方必须用同一个 idempotency_key 重试，不能直接回滚（契约与学习服务 1-C 的约定）。
import type { MeteredMethod } from './fingerprint.ts';

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  retryable?: boolean;
  [k: string]: unknown;
}

export type LearningOutcome =
  | { kind: 'success'; status: number; body: unknown }
  | { kind: 'failure'; status: number; problem: Problem }
  | { kind: 'unknown'; reason: string };

export type LearningCall = (method: MeteredMethod, body: Record<string, unknown>) => Promise<LearningOutcome>;

export function classifyResponse(status: number, body: unknown): LearningOutcome {
  if (status >= 200 && status < 300) return { kind: 'success', status, body };
  const problem: Problem = body !== null && typeof body === 'object' ? (body as Problem) : {};
  if (problem.retryable === true) return { kind: 'unknown', reason: `retryable ${status} ${problem.code ?? ''}`.trim() };
  if (problem.retryable === false) return { kind: 'failure', status, problem };
  // 没有 Problem 体：5xx、408、429 视为结果未知，其余 4xx 视为确定失败
  if (status >= 500 || status === 408 || status === 429) return { kind: 'unknown', reason: `http ${status}` };
  return { kind: 'failure', status, problem };
}

export interface HttpLearningOptions {
  baseUrl: string;
  token: string;
  // 比学习服务调适配器的超时（grade 10 秒、diagnose 30 秒）各多 5 秒
  timeoutMs?: Partial<Record<MeteredMethod, number>>;
  fetchImpl?: typeof fetch;
}

export function httpLearningClient(opts: HttpLearningOptions): LearningCall {
  const timeouts: Record<MeteredMethod, number> = { grade: 15_000, diagnose: 35_000, ...opts.timeoutMs };
  const doFetch = opts.fetchImpl ?? fetch;
  return async (method, body) => {
    let res: Response;
    try {
      res = await doFetch(`${opts.baseUrl.replace(/\/$/, '')}/internal/v0/${method}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${opts.token}` },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeouts[method]),
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
    return classifyResponse(res.status, parsed);
  };
}
