// 调学习服务的写入接口（packages/contracts/openapi/learning-internal.v0.yaml），并把响应归成三类：
//   success：写入成功；failure：确定没有写入（retryable = false）；unknown：结果未知（retryable = true、5xx、超时、断连）。
// 结果未知时调用方必须用同一个 idempotency_key 重试，不能直接回滚（契约与学习服务 1-C 的约定）。
// 5xx 一律算结果未知，即使 Problem 写着 retryable = false：学习服务在提交阶段出错时也会返回 500 INTERNAL，
// 而那时事务可能已经提交（契约守护 H2；契约的澄清记入 v1.0 评审清单 N18）。宁可重试，也不释放后让前端换 key 重复写入。
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
  // replayed：学习服务按同一个 key 返回了已存的结果（响应头 idempotent-replayed），这次没有重新执行
  | { kind: 'success'; status: number; body: unknown; replayed: boolean }
  | { kind: 'failure'; status: number; problem: Problem }
  | { kind: 'unknown'; reason: string };

export type LearningCall = (method: MeteredMethod, body: Record<string, unknown>) => Promise<LearningOutcome>;

export function classifyResponse(status: number, body: unknown, replayed = false): LearningOutcome {
  if (status >= 200 && status < 300) return { kind: 'success', status, body, replayed };
  const problem: Problem = body !== null && typeof body === 'object' ? (body as Problem) : {};
  if (status >= 500 || status === 408 || status === 429 || problem.retryable === true) {
    return { kind: 'unknown', reason: `${status} ${problem.code ?? ''}`.trim() };
  }
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
        redirect: 'error',  // 不跟随重定向：POST 被改成 GET 后的 200 不能当成写入成功
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
    return classifyResponse(res.status, parsed, res.headers.get('idempotent-replayed') === 'true');
  };
}

// 只查询的重放接口（learning-internal.v1.yaml，评审 N17；specs/plugin-api-v1.0-step2）。预留过期后用它代替原请求：
//   found：学习服务有这个 key 的成功结果；not_found：没有记录或之前失败过，学习服务已封存这个 key（之后同 key 的原请求 409）；
//   busy：同 key 的请求还在执行；unknown：网络或 5xx，结果未知。参数与原请求对不上时学习服务同样返回 404，但不封存。
export type ReplayOutcome =
  | { kind: 'found'; body: unknown }
  | { kind: 'not_found'; problem: Problem }
  | { kind: 'busy' }
  | { kind: 'unknown'; reason: string };

export type LearningReplay = (method: MeteredMethod, idempotencyKey: string, studentRef: string, usageId: string) => Promise<ReplayOutcome>;

export function classifyReplay(status: number, body: unknown): ReplayOutcome {
  const problem: Problem = body !== null && typeof body === 'object' ? (body as Problem) : {};
  if (status === 200) return { kind: 'found', body };
  if (status === 404 && problem.code === 'REPLAY_NOT_FOUND') return { kind: 'not_found', problem };
  if (status === 409 && problem.code === 'BUSY') return { kind: 'busy' };
  // 其余（400、401、没有 code 的 404 如路由不存在、5xx）都不能当作“已封存”：按结果未知处理，留给过期任务与人工排查
  return { kind: 'unknown', reason: `${status} ${problem.code ?? ''}`.trim() };
}

export function httpLearningReplay(opts: HttpLearningOptions): LearningReplay {
  const doFetch = opts.fetchImpl ?? fetch;
  return async (method, key, studentRef, usageId) => {
    const q = new URLSearchParams({ student_ref: studentRef, usage_id: usageId });
    let res: Response;
    try {
      res = await doFetch(
        `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/replay/${encodeURIComponent(method)}/${encodeURIComponent(key)}?${q}`, {
          method: 'GET',
          headers: { authorization: `Bearer ${opts.token}` },
          signal: AbortSignal.timeout(5_000),
          redirect: 'error',
        });
    } catch (err) {
      return { kind: 'unknown', reason: err instanceof Error ? err.name : 'fetch failed' };
    }
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      return { kind: 'unknown', reason: `${res.status} unreadable body` };
    }
    return classifyReplay(res.status, parsed);
  };
}
