// 调学习服务的写入接口（packages/contracts/openapi/learning-internal.v1.yaml，2026-10-20 签字为 1.1.0；LT-04 起由 v0 切到 v1），
// 并把响应归成三类：
//   success：写入成功；failure：确定没有写入（retryable = false）；unknown：结果未知（retryable = true、5xx、超时、断连）。
// 结果未知时调用方必须用同一个 idempotency_key 重试，不能直接回滚（契约与学习服务 1-C 的约定）。
// 5xx 一律算结果未知，即使 Problem 写着 retryable = false：学习服务在提交阶段出错时也会返回 500 INTERNAL，
// 而那时事务可能已经提交（契约守护 H2；契约的澄清记入 v1.0 评审清单 N18）。宁可重试，也不释放后让前端换 key 重复写入。
// v1 的请求体是信封 {request, ingest}：request 是插件契约 1.0.0 的 GradeRequest / DiagnoseRequest（context.contract_version 缺省时
// 由这里补成 1.0.0；调用方给了别的版本抛 TypeError，meteredCall 在预留前就核对），ingest 只有输入通道 keypad——用途与证据等级由
// 学习服务推导（直接写入只能是 practice），BFF 不透传前端字段。
// 学习服务写入前按计量账本核对 usage_id + 幂等键（迁移 0058），对不上 409 IDEMPOTENCY_KEY_SEALED（确定失败，retryable = false）。
// 计量指纹（fingerprint.ts）仍按调用方给的插件请求体在预留前算，算法不变；信封只在发送这一步包上。
import type { MeteredMethod } from './fingerprint.ts';

export const LEARNING_CONTRACT_VERSION = '1.0.0';

/** 调用方给的插件契约版本只能缺省或是 1.0.0：别的版本（0.9.0、候选版 1.1.0 等）是调用方的错，抛 TypeError。
 *  meteredCall 在预留之前调用它（不扣额度、不留预留）；信封这一步再核一次，防止绕过 meteredCall 的调用把别的版本悄悄改写成 1.0.0。 */
export function assertLearningContractVersion(context: unknown): void {
  const v = context !== null && typeof context === 'object' ? (context as Record<string, unknown>)['contract_version'] : undefined;
  if (v !== undefined && v !== LEARNING_CONTRACT_VERSION) {
    throw new TypeError(`context.contract_version must be ${LEARNING_CONTRACT_VERSION} or omitted`);
  }
}

/** learning-internal v1 的写入信封：插件请求（契约版本缺省时补 1.0.0）+ 平台侧作答上下文（键盘作答；用途与等级由学习服务推导） */
export function ingestEnvelope(body: Record<string, unknown>): { request: Record<string, unknown>; ingest: { channel: 'keypad' } } {
  const context = (body['context'] ?? {}) as Record<string, unknown>;
  assertLearningContractVersion(context);
  return { request: { ...body, context: { ...context, contract_version: LEARNING_CONTRACT_VERSION } }, ingest: { channel: 'keypad' } };
}

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
    // 在 try 之外包信封：契约版本不对是调用方的错（TypeError 抛给调用方），不能被当成“断连、结果未知”吞掉
    const payload = JSON.stringify(ingestEnvelope(body));
    let res: Response;
    try {
      res = await doFetch(`${opts.baseUrl.replace(/\/$/, '')}/internal/v1/${method}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${opts.token}` },
        body: payload,
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
