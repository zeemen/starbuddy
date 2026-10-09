// 复测与验证题的学生端接口（specs/retest-delivery-kge05/spec.md 范围第 2、4、5 条）。接口定义 openapi/retest.v0.yaml。
//
// 做法与错题再练（src/practice/service.ts）相同，本人核对、宵禁、题库自练计次、重试都直接复用那里的实现（只有一份）：
//   - 只认学生本人（路由 auth = student：家长会话在任何查询之前 403）；兄妹、另一户、不存在同一个 404（先核对本人再查宵禁、调学习服务）；
//   - 计量（规则 4）：只有作答计次，默认沿用错题再练的口径 platform.meter_practice（题库自练，只计次不扣额度；复测与验证题扣不扣额度
//     待人确认，specs/mastery-estimate-sprint4 #21）；幂等键是 BFF 封装票据时生成的 UUIDv4（一张票据最多扣一次），usage_id 由它派生，
//     与同一个幂等键一起转给学习服务；列表、取题不计次；本地能判的拒绝（格式、学科、本人、票据、作答形状、过期、宵禁）都在计次之前；
//     计次之前另调学习服务的只读预检（/retest/check：条目仍到期、票据未用），不通过不计次（审查意见 2）；
//   - 宵禁：取题与作答在本人核对之后、计次之前挡（402 curfew）；列表不挡；已计过次的票据在宵禁中只找回原结果
//     （/retest/replay：不执行、不封存），找不到仍回 402；
//   - 学习服务调用：结果未知时同一请求重试，整体总时限同再练；确定性拒绝不重试。
// 票据：学习服务的复测票据（rt1）封进不透明的 sbr1 票据（与再练的 sbp1 同一把密钥，前缀与附加数据不同，互相解不开）。
// 转给前端的只有：条目 ID、知识节点与层次、条目类别（复测 / 验证）、状态、题面、判分结论（与待复核标记）、条目新状态（due / done）。
// 不转：掌握、置信度、证据 ID、错因、任务原因、空原因编码、题目引用、学习服务票据（规则 3、11）。任务说明只用前端的固定文案占位。
import { P } from '../http/problems.ts';
import type { Reply } from '../http/routes.ts';
import { pickRich, type RichItem } from '../item-rich.ts';
import { usageIdFor } from '../metering/uuid.ts';
import {
  alreadyCounted, answerFits, countPractice, curfewBlocked, EXPIRY_MARGIN_MS, isDeterministic, isSelf, SUBJECTS, VERDICTS, withRetry,
  type AnswerInput, type AnswerValue,
} from '../practice/service.ts';
import { MAX_SEALED, openTicket, RETEST_SEALED_PREFIX, sealTicket, type ItemFormat } from '../practice/ticket.ts';
import type { Pool } from 'pg';
import type { RetestLearning, RetestOutcome } from './learning-client.ts';

export interface RetestOptions {
  /** 功能开关 retest（缺省关闭：路由不注册） */
  enabled?: boolean;
  /** 学习服务客户端（开关打开但不配时三条路由一律 503 RETEST_UNAVAILABLE） */
  learning?: RetestLearning;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  retryBudgetMs?: number;
  monotonic?: () => number;
  /** 计次等旁路记录（测试断言用）；只收事件名 */
  note?: (event: string) => void;
}

export interface RetestDeps {
  pool: Pool;
  learning: RetestLearning;
  /** 票据封装密钥（与再练同一把 keys.practice；用途靠前缀与附加数据区分） */
  key: Buffer;
  now: () => number;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  retryBudgetMs?: number;
  monotonic?: () => number;
  note?: (event: string) => void;
}

const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}): Reply =>
  ({ status, body: JSON.stringify({ type: `/problems/${code.toLowerCase()}`, title, status, code, ...extra }) });
const fixed = (body: string): Reply => ({ status: (JSON.parse(body) as { status: number }).status, body });
const json = (status: number, v: unknown, headers?: Record<string, string>): Reply =>
  ({ status, body: JSON.stringify(v), ...(headers ? { headers } : {}) });

export const NOT_FOUND = fixed(P.NOT_FOUND);
export const INVALID = fixed(P.INVALID);
const KEY_REUSED = fixed(P.KEY_REUSED);
export const RETEST_UNAVAILABLE = problem(503, 'RETEST_UNAVAILABLE', 'retest is not available right now');
const UPSTREAM_UNAVAILABLE = problem(503, 'UPSTREAM_UNAVAILABLE', 'retest service unavailable, retry with the same request');
const TICKET_INVALID = problem(422, 'TICKET_INVALID', 'this item is no longer valid; fetch a new one');
const TICKET_EXPIRED = problem(410, 'TICKET_EXPIRED', 'this item has expired; fetch a new one');
const INVALID_ANSWER = problem(422, 'INVALID_ANSWER', 'answer does not fit this item');
const NOT_DUE = problem(409, 'RETEST_NOT_DUE', 'this entry is not open right now');
const TICKET_USED = problem(409, 'TICKET_USED', 'this item was already answered; fetch a new one');
const KEY_SEALED = problem(409, 'KEY_SEALED', 'this item can no longer be answered; fetch a new one');
const quota = (reason: string) => problem(402, 'QUOTA_UNAVAILABLE', 'no practice available right now', { reason });

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const CHOICE_KEY = /^[A-Za-z0-9]{1,8}$/;
const MAX_FILL = 200;
const LEVELS = ['know', 'understand', 'apply', 'synthesize'];
export const KINDS = ['retest', 'verify'] as const;
export const ENTRY_STATUSES = ['due', 'unavailable'] as const;
export const AFTER_STATUSES = ['due', 'done'] as const;

const exactKeys = (o: Record<string, unknown>, keys: readonly string[]): boolean =>
  keys.every((k) => k in o) && Object.keys(o).every((k) => keys.includes(k));
const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const isTime = (v: unknown): v is string => typeof v === 'string' && RFC3339.test(v) && Number.isFinite(Date.parse(v));
const oneOf = (xs: readonly string[], v: unknown): v is string => typeof v === 'string' && xs.includes(v);

// ---------- 请求体 ----------
function parseAnswerValue(a: unknown): AnswerValue | null {
  if (!isRecord(a)) return null;
  if (exactKeys(a, ['text'])) {
    const t = a['text'];
    return typeof t === 'string' && t.trim() !== '' && t.length <= MAX_FILL ? { text: t } : null;
  }
  if (exactKeys(a, ['choice'])) {
    const c = a['choice'];
    if (!Array.isArray(c) || c.length < 1 || c.length > 8 || !c.every((k) => typeof k === 'string' && CHOICE_KEY.test(k))) return null;
    return new Set(c).size === c.length ? { choice: c as string[] } : null;
  }
  return null;
}

/** 作答请求体：只收票据（sbr1）与作答。幂等键在票据里；usage_id、purpose、channel、学生、数量一律拒收 */
export function parseRetestAnswer(b: Record<string, unknown>): AnswerInput | null {
  if (!exactKeys(b, ['ticket', 'answer'])) return null;
  const { ticket } = b;
  const answer = parseAnswerValue(b['answer']);
  if (!answer || typeof ticket !== 'string' || !ticket.startsWith(RETEST_SEALED_PREFIX) || ticket.length > MAX_SEALED) return null;
  return { ticket, answer };
}

// ---------- 学习服务结果的映射 ----------
const fromUnknown = (out: RetestOutcome<unknown>): Reply => (isDeterministic(out) ? RETEST_UNAVAILABLE : UPSTREAM_UNAVAILABLE);

export type RetestOp = 'queue' | 'next' | 'answer';

/** 学习服务的确定失败 → 学生端（只给编码，不带明细）；票据、作答类的编码只在作答时有意义 */
export function fromRetestFailure(f: { status: number; problem: { code?: string } }, op: RetestOp): Reply {
  const code = f.problem.code ?? '';
  if (f.status === 404) return NOT_FOUND;                                       // ENTRY_NOT_FOUND：与不存在同一个 404
  if (f.status === 400 && code === 'SUBJECT_NOT_SUPPORTED') return NOT_FOUND;    // 学科没开
  if (f.status === 409 && code === 'RETEST_NOT_DUE') return op === 'queue' ? RETEST_UNAVAILABLE : NOT_DUE;
  if (op !== 'answer') return RETEST_UNAVAILABLE;
  if (f.status === 403) return TICKET_INVALID;
  if (f.status === 410) return TICKET_EXPIRED;
  if (f.status === 409 && (code === 'TICKET_USED' || code === 'IDEMPOTENCY_CONFLICT')) return TICKET_USED;
  if (f.status === 409 && code === 'IDEMPOTENCY_KEY_SEALED') return KEY_SEALED;
  if (f.status === 422 && code === 'INVALID_ANSWER') return INVALID_ANSWER;
  return RETEST_UNAVAILABLE;
}

const replayHeader = (replayed: boolean) => ({ 'idempotent-replayed': replayed ? 'true' : 'false' });

// ---------- 响应白名单 ----------
/** 条目：只挑这些字段；空原因编码、任务原因与任何多出来的字段不转 */
export function pickRetestEntry(e: unknown): Record<string, unknown> | null {
  if (!isRecord(e)) return null;
  const { entry_id, node_id, cognitive_level, kind, status } = e;
  if (typeof entry_id !== 'string' || !UUID.test(entry_id) || typeof node_id !== 'string' || node_id === '' || node_id.length > 128
      || !oneOf(LEVELS, cognitive_level) || !oneOf(KINDS, kind) || !oneOf(ENTRY_STATUSES, status)) {
    return null;
  }
  return { entry_id, node_id, cognitive_level, kind, status };
}

interface Ready {
  entry_id: string; kind: string; item: { format: ItemFormat; stem: string; options?: { key: string; text: string }[]; rich?: RichItem };
  ticket: string; expires_at: string;
}

export function readRetestReady(v: Record<string, unknown>, entry: string): Ready | null {
  const { entry_id, kind, item, ticket, expires_at } = v;
  if (entry_id !== entry || !oneOf(KINDS, kind) || typeof ticket !== 'string' || ticket === '' || ticket.length > 1024
      || !isTime(expires_at) || !isRecord(item)) {
    return null;
  }
  const { format, stem, options } = item;
  if ((format !== 'single' && format !== 'multi' && format !== 'fill') || typeof stem !== 'string' || stem === '') return null;
  let opts: { key: string; text: string }[] | undefined;
  if (options !== undefined) {
    if (!Array.isArray(options) || !options.every((o) => isRecord(o) && typeof o['key'] === 'string' && CHOICE_KEY.test(o['key'])
                                                      && typeof o['text'] === 'string')) return null;
    opts = options.map((o) => ({ key: (o as { key: string }).key, text: (o as { text: string }).text }));
  }
  if ((format === 'fill') !== (opts === undefined || opts.length === 0)) return null;
  let rich: RichItem | undefined;
  if (item['rich'] !== undefined) {
    rich = pickRich(item['rich'], (opts ?? []).map((o) => o.key)) ?? undefined;
  }
  return { entry_id: entry, kind, ticket, expires_at,
           item: { format, stem, ...(opts && opts.length > 0 ? { options: opts } : {}), ...(rich ? { rich } : {}) } };
}

/** 作答结果：判分结论（契约枚举，来自验证器或 Rubric 的 grade 摘要）、待复核标记、条目新状态（due / done）；其余一律不转 */
export function pickRetestAnswer(v: unknown): Record<string, unknown> | null {
  if (!isRecord(v) || !(VERDICTS as readonly unknown[]).includes(v['verdict']) || !isRecord(v['entry'])) return null;
  const { entry_id, status } = v['entry'];
  if (typeof entry_id !== 'string' || !UUID.test(entry_id) || !oneOf(AFTER_STATUSES, status)) return null;
  if (v['pending_review'] !== undefined && v['pending_review'] !== true) return null;
  return { verdict: v['verdict'], ...(v['pending_review'] === true ? { pending_review: true } : {}), entry: { entry_id, status } };
}

// ---------- 三个操作 ----------
export async function retestQueue(deps: RetestDeps, account: string, student: string, code: string): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const r = await withRetry(deps, (ms) => deps.learning.queue(student, subject, ms));
  if (r.kind === 'unknown') return fromUnknown(r);
  if (r.kind === 'failure') return fromRetestFailure(r, 'queue');
  const body = r.body;
  if (!isRecord(body) || !Array.isArray(body['entries'])) return UPSTREAM_UNAVAILABLE;
  const entries = body['entries'].map(pickRetestEntry);
  if (entries.some((e) => e === null)) return UPSTREAM_UNAVAILABLE;  // 形状不对整页不给
  return json(200, { subject: code, entries });
}

export async function retestNext(deps: RetestDeps, account: string, student: string, code: string, entry: string): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：取题
  if (blocked) return blocked;
  const r = await withRetry(deps, (ms) => deps.learning.next(student, subject, entry, ms));
  if (r.kind === 'unknown') return fromUnknown(r);
  if (r.kind === 'failure') return fromRetestFailure(r, 'next');
  const body = r.body;
  if (isRecord(body) && body['status'] === 'unavailable') return json(200, { status: 'unavailable' });  // 空原因编码不转
  if (!isRecord(body) || body['status'] !== 'ready') return UPSTREAM_UNAVAILABLE;
  const ready = readRetestReady(body, entry);
  if (!ready) return UPSTREAM_UNAVAILABLE;
  const sealed = sealTicket(deps.key, student, {
    ticket: ready.ticket, subject, entryId: ready.entry_id, expiresAt: ready.expires_at, format: ready.item.format,
    optionKeys: (ready.item.options ?? []).map((o) => o.key),
  }, 'retest');
  // 题目引用（item_ref）、学习服务票据不转给前端
  return json(200, { status: 'ready', entry_id: ready.entry_id, kind: ready.kind, item: ready.item, ticket: sealed, expires_at: ready.expires_at });
}

export async function retestAnswer(deps: RetestDeps, account: string, student: string, code: string, input: AnswerInput): Promise<Reply> {
  const subject = SUBJECTS[code];
  if (subject === undefined) return NOT_FOUND;
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  // 本地能判的拒绝都在计次之前（不扣冤枉次）
  const t = openTicket(deps.key, student, input.ticket, 'retest');
  if (!t || t.subject !== subject) return TICKET_INVALID;
  if (!answerFits(t.format, t.optionKeys, input.answer)) return INVALID_ANSWER;
  const key = t.idempotencyKey;  // 一张票据一个键（BFF 封装时生成）：计次与学习服务都用它
  const counted = await alreadyCounted(deps.pool, account, student, key);  // 这张票据已经计过次（上一次结果未知或要找回结果）
  if (deps.now() >= Date.parse(t.expiresAt) - EXPIRY_MARGIN_MS && !counted) {
    deps.note?.('ticket_expired_local');
    return TICKET_EXPIRED;
  }
  const blocked = await curfewBlocked(deps, account, student);  // 宵禁：作答（计次之前）
  if (blocked) {
    if (!counted) return blocked;
    // 宵禁中、这张票据已计过次：只找回原结果（学习服务只读接口，不执行、不封存，不会产生新作答）；找不到仍回宵禁（审查意见 2）
    deps.note?.('retest_curfew_replay');
    const rp = await withRetry(deps, (ms) => deps.learning.replay(student, subject, { ticket: t.ticket, idempotency_key: key,
                                                                                       usage_id: usageIdFor(key) }, ms));
    if (rp.kind !== 'ok') return blocked;
    const found = pickRetestAnswer(rp.body);
    if (!found || (found['entry'] as { entry_id: string }).entry_id !== t.entryId) return UPSTREAM_UNAVAILABLE;
    return json(200, found, replayHeader(true));
  }
  if (!counted) {
    // 计次之前的只读预检（审查意见 2）：条目已变、票据已用、学习服务那边已过期等，在这里就拒绝，不扣冤枉次。
    // 已计过次的键不预检（同一张票据重试要找回原结果，学习服务按幂等处理）
    const pre = await withRetry(deps, (ms) => deps.learning.check(student, subject, t.ticket, ms));
    if (pre.kind === 'unknown') return fromUnknown(pre);
    if (pre.kind === 'failure') {
      deps.note?.('retest_precheck_rejected');
      return fromRetestFailure(pre, 'answer');
    }
  }
  const usage = usageIdFor(key);
  const m = await countPractice(deps.pool, account, student, key);  // 默认沿用题库自练的计次口径（待人确认）
  if (m === 'forbidden') return NOT_FOUND;
  if (m === 'invalid') return INVALID;
  switch (m.outcome) {
    case 'conflict':
      return KEY_REUSED;
    case 'no_entitlement': case 'daily_limit_reached':
      return quota(m.outcome);
    case 'allowed': case 'existing':
      break;
    default:
      throw new Error('unexpected meter_practice outcome');
  }
  if (m.usage_id !== usage) throw new Error('usage_id derived by the database does not match the BFF');
  deps.note?.(m.outcome === 'allowed' ? 'retest_counted' : 'retest_count_existing');
  const r = await withRetry(deps, (ms) => deps.learning.answer(student, subject, { ticket: t.ticket, answer: input.answer, idempotency_key: key,
                                                                                    usage_id: usage }, ms));
  if (r.kind === 'unknown') return fromUnknown(r);
  if (r.kind === 'failure') return fromRetestFailure(r, 'answer');
  const out = pickRetestAnswer(r.body);
  if (!out || (out['entry'] as { entry_id: string }).entry_id !== t.entryId) return UPSTREAM_UNAVAILABLE;
  return json(200, out, replayHeader(r.replayed));
}
