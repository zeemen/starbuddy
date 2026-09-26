// BFF 的计量流程（设计文档第 5 节时序）：授权与预留 → 用同一个 key 调学习服务 → 结算或释放。
//   确定成功 → meter_settle；确定失败 → meter_release；
//   结果未知 → 用同一个 key 退避重试，直到确定或用完预算（预留剩余时间的一半）；仍不确定就不结算，留给过期任务。
// 同 key 重试（existing）：reserved 继续同一笔；settled 拿学习服务的重放结果，不再扣；
// released 返回“之前已失败”；expired 仍用原 key 拿重放结果，不扣费（spec 待人确认 #6）。
import type { Pool } from 'pg';
import { withAccount } from './db.ts';
import { meteringFingerprint, type MeteredMethod } from './fingerprint.ts';
import type { LearningCall, LearningOutcome, Problem } from './learning-client.ts';
import { noopMetrics, type Metrics } from './metrics.ts';
import { isUuidV4, usageIdFor } from './uuid.ts';

export type ResourceType =
  | 'diagnosis_screen' | 'diagnosis_full' | 'mock_exam' | 'mistake_analysis'
  | 'lesson_whiteboard' | 'voice_realtime' | 'ai_active_time';

export interface ReserveInput {
  studentId: string;
  resourceType: ResourceType;
  idempotencyKey: string;
  requestFingerprint: string;
  quantity: number;
  contentFingerprint?: string | undefined;
  sourceMethod: MeteredMethod;
}

export type ReserveResult =
  | { outcome: 'reserved' | 'dedup'; usage_id: string; state: 'reserved'; reserved_qty: number; expires_at: string;
      remaining?: number; dedup_of?: string }
  | { outcome: 'existing'; usage_id: string; state: 'reserved' | 'settled' | 'released' | 'expired';
      reserved_qty: number; settled_qty: number | null; expires_at: string; dedup_of: string | null }
  | { outcome: 'insufficient'; remaining: number }
  | { outcome: 'conflict' | 'dedup_in_progress' | 'no_entitlement' };

export type TerminalResult = { outcome: 'settled' | 'released' | 'conflict'; usage_id: string; state: string; idempotent?: boolean };

export async function reserve(pool: Pool, accountId: string, input: ReserveInput): Promise<ReserveResult> {
  return withAccount(pool, accountId, async (c) => {
    const { rows } = await c.query(
      'SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r',
      [input.studentId, input.resourceType, input.idempotencyKey, input.requestFingerprint, input.quantity,
       input.contentFingerprint ?? null, input.sourceMethod]);
    return rows[0].r as ReserveResult;
  });
}

export async function settle(pool: Pool, accountId: string, usageId: string, settledQty: number): Promise<TerminalResult> {
  return withAccount(pool, accountId, async (c) => {
    const { rows } = await c.query('SELECT platform.meter_settle($1, $2) AS r', [usageId, settledQty]);
    return rows[0].r as TerminalResult;
  });
}

export async function release(pool: Pool, accountId: string, usageId: string, reason: string): Promise<TerminalResult> {
  return withAccount(pool, accountId, async (c) => {
    const { rows } = await c.query('SELECT platform.meter_release($1, $2) AS r', [usageId, reason]);
    return rows[0].r as TerminalResult;
  });
}

export interface MeteredCallInput {
  accountId: string;          // 已通过 BFF 鉴权的当前账号（鉴权中间件不在本冲刺）
  studentId: string;
  resourceType: ResourceType;
  method: MeteredMethod;
  idempotencyKey: string;     // 前端为每次用户动作生成的 UUIDv4，重试时原样复用
  body: Record<string, unknown>;  // 发给学习服务的请求体（契约的 GradeRequest / DiagnoseRequest）
  quantity?: number;
  contentFingerprint?: string;
}

export interface MeterDeps {
  pool: Pool;
  learning: LearningCall;
  metrics?: Metrics;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;             // 与数据库计量时钟同一时间轴（测试里注入）
  retryDelaysMs?: number[];       // 结果未知时的退避（spec 待人确认 #8）
}

export type MeteredCallResult =
  | { status: 'ok'; usageId: string; charged: boolean; replay: boolean; dedup: boolean; result: unknown }
  | { status: 'failed'; usageId: string; httpStatus: number; problem: Problem }
  | { status: 'failed_previously'; usageId: string }
  | { status: 'expired_retry_with_new_key'; usageId: string; problem: Problem }
  | { status: 'unknown'; usageId: string }
  | { status: 'insufficient'; remaining: number }
  | { status: 'conflict' | 'dedup_in_progress' | 'no_entitlement' };

const DEFAULT_DELAYS = [1000, 2000, 4000, 8000];

export async function meteredCall(deps: MeterDeps, input: MeteredCallInput): Promise<MeteredCallResult> {
  if (!isUuidV4(input.idempotencyKey)) throw new TypeError('idempotencyKey must be a lowercase UUIDv4');
  const metrics = deps.metrics ?? noopMetrics;
  const usageId = usageIdFor(input.idempotencyKey);
  const body = structuredClone(input.body);
  const context = { ...(body['context'] as Record<string, unknown> | undefined), idempotency_key: input.idempotencyKey, usage_id: usageId };
  body['context'] = context;

  const r = await reserve(deps.pool, input.accountId, {
    studentId: input.studentId,
    resourceType: input.resourceType,
    idempotencyKey: input.idempotencyKey,
    requestFingerprint: meteringFingerprint(input.method, body),
    quantity: input.quantity ?? 1,
    contentFingerprint: input.contentFingerprint,
    sourceMethod: input.method,
  });
  switch (r.outcome) {
    case 'insufficient':
      return { status: 'insufficient', remaining: r.remaining };
    case 'conflict':
    case 'dedup_in_progress':
    case 'no_entitlement':
      return { status: r.outcome };
  }
  if (r.usage_id !== usageId) throw new Error('usage_id derived by the database does not match the BFF');

  if (r.outcome === 'existing' && r.state !== 'reserved') {
    if (r.state === 'released') return { status: 'failed_previously', usageId };
    // settled：之前已经扣过；expired：过期不扣费。两种都用原 key 拿学习服务的重放结果，不再预留
    const replay = await callWithRetry(deps, input.method, body, r.expires_at, metrics);
    if (replay.kind === 'success') {
      if (r.state === 'expired') metrics.increment('replay_after_expiry');
      return { status: 'ok', usageId, charged: r.state === 'settled', replay: true, dedup: r.dedup_of !== null, result: replay.body };
    }
    if (replay.kind === 'failure') {
      return r.state === 'expired'
        ? { status: 'expired_retry_with_new_key', usageId, problem: replay.problem }
        : { status: 'failed', usageId, httpStatus: replay.status, problem: replay.problem };
    }
    metrics.increment('result_unknown');
    return { status: 'unknown', usageId };
  }

  // 新预留、复看预留，或同 key 找回的仍在预留中的那一笔
  const reservedQty = r.reserved_qty;
  const dedup = r.outcome === 'dedup' || (r.outcome === 'existing' && r.dedup_of !== null);
  const outcome = await callWithRetry(deps, input.method, body, r.expires_at, metrics);
  if (outcome.kind === 'success') {
    const s = await settle(deps.pool, input.accountId, usageId, reservedQty);
    if (s.outcome === 'settled') {
      return { status: 'ok', usageId, charged: reservedQty > 0, replay: false, dedup, result: outcome.body };
    }
    // 结算前预留已被过期任务处理：宁可少扣，不报错（设计文档场景 5）
    metrics.increment('late_success_after_expiry');
    return { status: 'ok', usageId, charged: false, replay: false, dedup, result: outcome.body };
  }
  if (outcome.kind === 'failure') {
    await release(deps.pool, input.accountId, usageId, 'upstream_rejected');
    return { status: 'failed', usageId, httpStatus: outcome.status, problem: outcome.problem };
  }
  metrics.increment('result_unknown');
  return { status: 'unknown', usageId };
}

// 结果未知时用同一个请求体（同一个 key、同一个 usage_id）退避重试；预算为预留剩余时间的一半
async function callWithRetry(deps: MeterDeps, method: MeteredMethod, body: Record<string, unknown>, expiresAt: string,
                             metrics: Metrics): Promise<LearningOutcome> {
  const now = deps.now ?? Date.now;
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const delays = deps.retryDelaysMs ?? DEFAULT_DELAYS;
  const start = now();
  const budgetEnd = start + Math.max(0, (Date.parse(expiresAt) - start) / 2);
  let outcome = await deps.learning(method, body);
  for (const delay of delays) {
    if (outcome.kind !== 'unknown' || now() + delay > budgetEnd) break;
    await sleep(delay);
    metrics.increment('learning_retry');
    outcome = await deps.learning(method, body);
  }
  return outcome;
}
