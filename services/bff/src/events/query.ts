// 行为事件与“能做对吗”探针（冲刺 2-L，specs/behavior-events-sprint2/spec.md 设计要点 2）：BFF 只做请求体校验并转发到库函数，
// 身份用当前账号（withAccount，与建档同一做法）。授权（只认学生本人）、payload 白名单、时间窗、幂等全在迁移 0025 的函数里，
// 这里的校验是第一道：未知键、自由文本、类型不对一律 422，不送进库。
//   - 不消耗额度，不走计量预留（spec 设计要点 2）；
//   - 行为事件、探针只给 Agent 用：本模块只写不读，响应只回记录 ID，不回显 payload（规则 11、12）；
//   - 42501 无权 → forbidden（403）；22023 等取值不合要求 → invalid（422）；23505 同一幂等键内容不同、探针已关联别的作答 → conflict（409，
//     由 server.ts 按接口给不同的错误码）；54000 24 小时写入上限（事件 2000、探针 500，0025）→ too_many（429）；
//   - 库函数返回 (id, replayed)：同一幂等键重放返回原记录并标 replayed，server.ts 据此回 200 与响应头 idempotent-replayed: true。
import type { Pool, PoolClient } from 'pg';
import { withAccount } from '../metering/db.ts';
import { isUuid } from '../star-map/query.ts';

export type WriteOutcome<T> = { kind: 'ok'; value: T } | { kind: 'forbidden' } | { kind: 'invalid' } | { kind: 'conflict' } | { kind: 'too_many' };

/** 学生端只收这四种；appeal、exit_after_errors 由后端与编排层写（spec 设计要点 2） */
export const EVENT_KINDS = ['session_start', 'session_end', 'plan_done', 'plan_skip'] as const;
export type EventKind = (typeof EVENT_KINDS)[number];
export const SESSION_ENTRIES = ['today', 'star_map', 'subject', 'onboarding', 'other'] as const;
export const PREDICTIONS = ['yes', 'no', 'unsure'] as const;

export interface EventInput {
  kind: EventKind;
  payload: Record<string, string | number>;
  occurred_at: string;
  idempotency_key: string;
}

export interface ProbeInput {
  subject: string;
  item_id: string;
  prediction: (typeof PREDICTIONS)[number];
  asked_at: string;
  idempotency_key: string;
}

// RFC 3339 带时区（Z 或 ±hh:mm）；秒以下最多 6 位（与 timestamptz 精度一致）
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
// 与 0025 的 payload 白名单同一写法（小写十六进制，不限版本）
const PLAN_TASK_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// 与 platform.skill_local_id 同一字符集
const ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const SUBJECT = /^[a-z]{2,16}$/;

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
/** 键集合必须恰好是 keys（未知键、缺键都算不合要求） */
const exactKeys = (o: Record<string, unknown>, keys: readonly string[]): boolean => {
  const got = Object.keys(o).sort();
  const want = [...keys].sort();
  return got.length === want.length && got.every((k, i) => k === want[i]);
};
const isTimestamp = (v: unknown): v is string => typeof v === 'string' && RFC3339.test(v) && Number.isFinite(Date.parse(v));
const isKey = (v: unknown): v is string => typeof v === 'string' && UUID_V4.test(v);
const oneOf = <T extends string>(v: unknown, set: readonly T[]): v is T => typeof v === 'string' && (set as readonly string[]).includes(v);

/** 按 0025 的白名单校验 payload：每种事件只收固定的键，取值有界；不收自由文本 */
export function validPayload(kind: EventKind, p: unknown): p is Record<string, string | number> {
  if (!isObject(p)) return false;
  switch (kind) {
    case 'session_start':
      return exactKeys(p, ['entry']) && oneOf(p['entry'], SESSION_ENTRIES);
    case 'session_end': {
      const d = p['duration_sec'];
      return exactKeys(p, ['duration_sec']) && typeof d === 'number' && Number.isInteger(d) && d >= 0 && d <= 86400;
    }
    case 'plan_done':
    case 'plan_skip':
      return exactKeys(p, ['plan_task_id']) && typeof p['plan_task_id'] === 'string' && PLAN_TASK_ID.test(p['plan_task_id']);
  }
}

/** POST …/events 的请求体：{kind, payload, occurred_at, idempotency_key}，多一个键都不收 */
export function parseEvent(body: Record<string, unknown>): EventInput | null {
  if (!exactKeys(body, ['kind', 'payload', 'occurred_at', 'idempotency_key'])) return null;
  const kind = body['kind'];
  if (!oneOf(kind, EVENT_KINDS) || !validPayload(kind, body['payload'])) return null;
  if (!isTimestamp(body['occurred_at']) || !isKey(body['idempotency_key'])) return null;
  return { kind, payload: body['payload'], occurred_at: body['occurred_at'], idempotency_key: body['idempotency_key'] };
}

/** POST …/probes 的请求体：{subject, item_id, prediction, asked_at, idempotency_key} */
export function parseProbe(body: Record<string, unknown>): ProbeInput | null {
  if (!exactKeys(body, ['subject', 'item_id', 'prediction', 'asked_at', 'idempotency_key'])) return null;
  const { subject, item_id: item, prediction, asked_at: asked, idempotency_key: key } = body;
  if (typeof subject !== 'string' || !SUBJECT.test(subject) || typeof item !== 'string' || !ITEM_ID.test(item)) return null;
  if (!oneOf(prediction, PREDICTIONS) || !isTimestamp(asked) || !isKey(key)) return null;
  return { subject, item_id: item, prediction, asked_at: asked, idempotency_key: key };
}

/** POST …/probes/{id}/attempt 的请求体：{attempt_id} */
export function parseAttempt(body: Record<string, unknown>): string | null {
  const a = body['attempt_id'];
  return exactKeys(body, ['attempt_id']) && typeof a === 'string' && PLAN_TASK_ID.test(a) ? a : null;
}

// 22023：函数判定取值不合要求；22P02 / 22007 / 22008 / 23514 / 23502 / 22003：类型转换或约束（例如学科不是枚举值）
const INVALID_CODES = new Set(['22023', '23514', '22P02', '22007', '22008', '23502', '22003']);

async function run<T>(pool: Pool, account: string, student: string, fn: (c: PoolClient) => Promise<T>): Promise<WriteOutcome<T>> {
  // 路径里的学生 ID 不合法与无权同样处理：不区分“不存在”与“不是你”
  if (!isUuid(student) || !isUuid(account)) return { kind: 'forbidden' };
  try {
    return { kind: 'ok', value: await withAccount(pool, account, fn) };
  } catch (e) {
    const code = (e as { code?: string }).code ?? '';
    if (code === '42501') return { kind: 'forbidden' };
    if (INVALID_CODES.has(code)) return { kind: 'invalid' };
    if (code === '23505') return { kind: 'conflict' };
    if (code === '54000') return { kind: 'too_many' };
    throw e;
  }
}

/** 写入结果：记录 ID，以及这次是不是同一幂等键的重放（重放时库里没有新写任何行） */
export interface Recorded { id: string; replayed: boolean }

/** 写一条行为事件；同一幂等键重放返回原记录 ID 与 replayed=true */
export function recordEvent(pool: Pool, account: string, student: string, e: EventInput): Promise<WriteOutcome<Recorded>> {
  return run(pool, account, student, async (c) => {
    const { rows } = await c.query<Recorded>(
      'SELECT id, replayed FROM insight.record_behavior_event($1, $2, $3::jsonb, $4::timestamptz, $5::uuid)',
      [student, e.kind, JSON.stringify(e.payload), e.occurred_at, e.idempotency_key]);
    return { id: rows[0]!.id, replayed: rows[0]!.replayed === true };
  });
}

/** 写一条探针（学生自述）；同一幂等键重放返回原记录 ID 与 replayed=true */
export function recordProbe(pool: Pool, account: string, student: string, p: ProbeInput): Promise<WriteOutcome<Recorded>> {
  return run(pool, account, student, async (c) => {
    const { rows } = await c.query<Recorded>(
      'SELECT id, replayed FROM insight.record_confidence_probe($1, $2::platform.subject, $3, $4, $5::timestamptz, $6::uuid)',
      [student, p.subject, p.item_id, p.prediction, p.asked_at, p.idempotency_key]);
    return { id: rows[0]!.id, replayed: rows[0]!.replayed === true };
  });
}

/** 把探针与作答关联（作答提交后回填）；同一作答重复关联是幂等的，关联别的作答回冲突 */
export async function linkProbeAttempt(pool: Pool, account: string, student: string, probe: string, attempt: string): Promise<WriteOutcome<null>> {
  if (!PLAN_TASK_ID.test(probe)) return { kind: 'invalid' };
  return run(pool, account, student, async (c) => {
    await c.query('SELECT insight.link_probe_attempt($1, $2::uuid, $3::uuid)', [student, probe, attempt]);
    return null;
  });
}
