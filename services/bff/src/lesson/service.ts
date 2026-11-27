// 讲评课会话的学生端接口（specs/stage-lesson-session 第二节第 1、4 条，PR ③b-4b；接口定义 openapi/lesson.v0.yaml）。
//
// 只认学生本人：家长会话在任何查询之前 403（路由 auth = student）；兄妹、另一户、学生不存在一律同一个 404（specs/not-self-404）。
// 返回给前端的只有讲解 DSL、台词稿（只有字幕台词，不含预制答疑）、不透明票据、过期时间与提问余量，以及每一问的回答文字或“不答”：
// 都是公共层内容，不含关于学生的陈述（规则 11）；提问与回答不落库、不进日志（规格第二节第 5 条）。
//
// 计量（规则 4；规格第五节第 1、2 条，计量规则 [NEEDS-HUMAN]）：
//   - 打开：以学生身份预留一次 lesson_whiteboard（method = teach，数量 1，幂等键 = 请求的幂等键，usage_id 由键派生），
//     再请学习服务建会话、取讲评课。学习服务确定拒绝 → 释放；结果未知 → 同一请求重试，仍不确定就不结算、不释放（留给过期任务）；
//     成功下发 → 立即结算这一次（与诊断“第一题判分成功即结算”同一口径）。额度不足、基础档、宵禁等由 meter_reserve 判定（402）。
//   - 同一幂等键再打开：预留已在（reserved / settled）就不再预留，学习服务按同一个会话重放、再结算一次是幂等的；
//     预留已释放或已过期 → 409，换一个键重新打开。
//   - 课内提问不扣次、不预留：BFF 先核票据（学生、未过期），转学习服务；学习服务按会话的那一笔预留放行（platform.lesson_session_ask），
//     只累计会话上的提问次数。宵禁中不问（402 reason = curfew），不调学习服务。
import type { Pool } from 'pg';
import { CURFEW, NOT_FOUND, withRetry } from '../diagnosis/service.ts';
import { P } from '../http/problems.ts';
import { withAccount } from '../metering/db.ts';
import { canonical, sha256Hex } from '../metering/canonical.ts';
import { inCurfew } from '../metering/curfew.ts';
import { release, reserve, settle } from '../metering/meter.ts';
import { usageIdFor } from '../metering/uuid.ts';
import { isUuid } from '../star-map/query.ts';
import type { AskBody, LessonLearning } from './learning-client.ts';
import { openLessonTicket, sealLessonTicket } from './ticket.ts';

export interface LessonOptions {
  /** 功能开关 lesson（缺省关闭：路由不注册） */
  enabled?: boolean;
  /** 学习服务客户端（开关打开但不配时两条路由一律 503 LESSON_UNAVAILABLE） */
  learning?: LessonLearning;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  retryBudgetMs?: number;
  monotonic?: () => number;
  note?: (event: string) => void;
}

export interface LessonDeps {
  pool: Pool;
  learning: LessonLearning;
  /** 票据封装密钥（keys.lesson） */
  key: Buffer;
  now: () => number;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  retryBudgetMs?: number;
  monotonic?: () => number;
  /** 计量的旁路记录（测试断言用）；只收事件名 */
  note?: (event: string) => void;
}

export interface Reply { status: number; body: unknown; headers?: Record<string, string> }

const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}): Reply =>
  ({ status, body: { type: `/problems/${code.toLowerCase()}`, title, status, code, ...extra } });
/** 请求体不合格：与其他路由逐字节相同（P.INVALID；H6 身份注入用例要求未知键一律是这一个 422） */
export const INVALID: Reply = { status: 422, body: JSON.parse(P.INVALID) as unknown };
export const LESSON_UNAVAILABLE = problem(503, 'LESSON_UNAVAILABLE', 'lessons are not available');
const UNAVAILABLE = problem(503, 'UPSTREAM_UNAVAILABLE', 'lesson service unavailable, retry with the same request');
const KEY_REUSED = problem(409, 'IDEMPOTENCY_KEY_REUSED', 'idempotency key reused with different content');
const NOT_AVAILABLE = problem(422, 'LESSON_NOT_AVAILABLE', 'this lesson is not available');
const TICKET_INVALID = problem(422, 'TICKET_INVALID', 'lesson ticket is invalid');
const TICKET_EXPIRED = problem(410, 'TICKET_EXPIRED', 'the lesson has expired; open it again');
const LESSON_CLOSED = problem(409, 'LESSON_CLOSED', 'this lesson can no longer take questions; open it again');

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LESSON_ID = /^[a-z][a-z0-9_.-]{0,63}$/;
const SEMVER = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;
const REF = /^aya:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const STAGE_ID = /^[a-z][a-z0-9_]{0,31}$/;
const ANCHOR_KINDS = ['element', 'slot', 'step'] as const;
/** “再讲一遍”在前端本地重放，不进这里（规格 stage-point-to-ask-answer 第五节） */
const INTENTS = ['why_step', 'what_is', 'where_from'] as const;

const isRecord = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const exactKeys = (o: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean =>
  required.every((k) => k in o) && Object.keys(o).every((k) => required.includes(k) || optional.includes(k));
const isInt = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;

// ---------- 请求体 ----------
export interface OpenInput {
  subject: 'aya'; lesson_ref: { lesson_id: string; lesson_version: string }; skill_node: string; item_ref?: string; idempotency_key: string;
}
export interface Question { cursor: number; anchor: { kind: (typeof ANCHOR_KINDS)[number]; id: string }; intent: (typeof INTENTS)[number] }
export interface AskInput { ticket: string; question: Question; idempotency_key: string }

/** 打开：学科（只有 aya）、讲评课引用、节点、题（可选）与幂等键；不收 usage_id、数量、资源类型、answer_submitted（由平台决定） */
export function parseOpen(b: Record<string, unknown>): OpenInput | null {
  if (!exactKeys(b, ['subject', 'lesson_ref', 'skill_node', 'idempotency_key'], ['item_ref'])) return null;
  const { subject, lesson_ref: ref, skill_node: node, item_ref: item, idempotency_key: key } = b;
  if (subject !== 'aya' || typeof key !== 'string' || !UUID_V4.test(key)) return null;
  if (!isRecord(ref) || !exactKeys(ref, ['lesson_id', 'lesson_version'])) return null;
  const { lesson_id: id, lesson_version: ver } = ref;
  if (typeof id !== 'string' || !LESSON_ID.test(id) || typeof ver !== 'string' || !SEMVER.test(ver)) return null;
  if (typeof node !== 'string' || !REF.test(node) || (item !== undefined && (typeof item !== 'string' || !REF.test(item)))) return null;
  return { subject: 'aya', lesson_ref: { lesson_id: id, lesson_version: ver }, skill_node: node,
           ...(typeof item === 'string' ? { item_ref: item } : {}), idempotency_key: key };
}

/** 提问：票据、第一阶段的结构化提问（cursor、anchor、intent，不收自由文字）、这一问的幂等键（同一问重试带同一个键） */
export function parseAsk(b: Record<string, unknown>): AskInput | null {
  if (!exactKeys(b, ['ticket', 'question', 'idempotency_key'])) return null;
  const { ticket, question: q, idempotency_key: key } = b;
  if (typeof ticket !== 'string' || ticket === '' || typeof key !== 'string' || !UUID_V4.test(key)) return null;
  if (!isRecord(q) || !exactKeys(q, ['cursor', 'anchor', 'intent']) || !isInt(q['cursor'], 1, 100)) return null;
  const a = q['anchor'];
  if (!isRecord(a) || !exactKeys(a, ['kind', 'id']) || !(ANCHOR_KINDS as readonly unknown[]).includes(a['kind'])
      || typeof a['id'] !== 'string' || !STAGE_ID.test(a['id'])) return null;
  if (!(INTENTS as readonly unknown[]).includes(q['intent'])) return null;
  return { ticket, idempotency_key: key, question: { cursor: q['cursor'], anchor: { kind: a['kind'] as Question['anchor']['kind'], id: a['id'] },
                                                     intent: q['intent'] as Question['intent'] } };
}

// ---------- 身份 ----------
async function isSelf(pool: Pool, account: string, student: string): Promise<boolean> {
  if (!isUuid(account) || !isUuid(student)) return false;
  return withAccount(pool, account, async (c) =>
    (await c.query<{ ok: boolean }>('SELECT platform.is_self_student($1::uuid) AS ok', [student])).rows[0]!.ok === true);
}

/** 计量指纹：同一幂等键换一节课（或换节点、换题）是 409 IDEMPOTENCY_KEY_REUSED。与 meteringFingerprint 同一规范化（canonical + SHA-256） */
export function lessonFingerprint(student: string, input: OpenInput): string {
  return sha256Hex(canonical({ method: 'teach', body: { context: { subject: input.subject, student_ref: student }, mode: 'lesson',
                                                        lesson_ref: input.lesson_ref, skill_node: input.skill_node,
                                                        item_ref: input.item_ref ?? null } }));
}

const replayHeader = (replayed: boolean) => ({ 'idempotent-replayed': replayed ? 'true' : 'false' });

async function settleQuietly(deps: LessonDeps, account: string, usage: string): Promise<void> {
  // 预留已过期或已释放时不结算（宁可少扣，同诊断）
  const s = await settle(deps.pool, account, usage, 1);
  deps.note?.(s.outcome === 'settled' ? (s.idempotent ? 'settle_replayed' : 'settled') : `settle_${s.state}`);
}

async function releaseQuietly(deps: LessonDeps, account: string, usage: string, reason: string): Promise<void> {
  const r = await release(deps.pool, account, usage, reason);
  deps.note?.(r.outcome === 'released' ? (r.idempotent ? 'release_replayed' : 'released') : `release_${r.state}`);
}

// ---------- 打开讲评课 ----------
export async function openLesson(deps: LessonDeps, account: string, student: string, input: OpenInput): Promise<Reply> {
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const key = input.idempotency_key;
  const usage = usageIdFor(key);
  const res = await reserve(deps.pool, account, { studentId: student, resourceType: 'lesson_whiteboard', idempotencyKey: key,
                                                   requestFingerprint: lessonFingerprint(student, input), quantity: 1, sourceMethod: 'teach' });
  switch (res.outcome) {
    case 'conflict':
      return KEY_REUSED;
    case 'insufficient': case 'no_entitlement': case 'daily_cap_reached': case 'curfew': case 'curfew_soon':
    case 'retained_no_ai': case 'dedup_in_progress':
      return problem(402, 'QUOTA_UNAVAILABLE', 'no lesson quota available right now', { reason: res.outcome });
  }
  if (res.outcome === 'existing' && res.state === 'released') return problem(409, 'KEY_FAILED_PREVIOUSLY', 'open again with a new key');
  if (res.outcome === 'existing' && res.state === 'expired') return problem(409, 'KEY_EXPIRED', 'open again with a new key');
  const holding = res.outcome === 'reserved' || res.outcome === 'dedup' || (res.outcome === 'existing' && res.state === 'reserved');
  deps.note?.(res.outcome === 'existing' ? `reserve_existing_${res.state}` : 'reserved');
  const r = await withRetry(deps, () => deps.learning.open({
    student_ref: student, subject: input.subject, lesson_ref: input.lesson_ref, skill_node: input.skill_node,
    ...(input.item_ref !== undefined ? { item_ref: input.item_ref } : {}), usage_id: usage, idempotency_key: key }));
  if (r.kind === 'unknown') return UNAVAILABLE;  // 不释放：结果未知，同一个键重试会找回同一笔预留与同一个会话
  if (r.kind === 'failure') {
    if (holding) await releaseQuietly(deps, account, usage, 'upstream_rejected');
    if (r.status === 404) return NOT_FOUND;
    if (r.status === 501) return LESSON_UNAVAILABLE;
    if (r.status === 409 && r.problem.code === 'IDEMPOTENCY_CONFLICT') return KEY_REUSED;
    if (r.status === 409) return problem(409, 'KEY_FAILED_PREVIOUSLY', 'open again with a new key');
    return NOT_AVAILABLE;  // LESSON_NOT_AVAILABLE 与其他确定拒绝（学习服务回 400 说明 BFF 的请求不合格，按这节课打不开处理）
  }
  const o = r.body;
  if (o.usage_id !== usage || !isUuid(o.session_id) || typeof o.ticket !== 'string' || !Number.isFinite(Date.parse(o.expires_at))) {
    // 学习服务的会话挂在另一笔预留上（不应发生）：不下发、不结算，留给过期任务
    deps.note?.('usage_mismatch');
    return UNAVAILABLE;
  }
  await settleQuietly(deps, account, usage);  // 讲评课内容成功下发即结算这一次（规格第五节第 2 条）
  const sealed = sealLessonTicket(deps.key, student, { ticket: o.ticket, sessionId: o.session_id, expiresAt: o.expires_at });
  return { status: 200, headers: replayHeader(r.replayed),
           body: { dsl_version: o.dsl_version, dsl: o.dsl, script: o.script, ticket: sealed, expires_at: o.expires_at,
                   questions_left: Math.max(0, o.question_limit - o.question_count) } };
}

// ---------- 课内提问 ----------
export async function askLesson(deps: LessonDeps, account: string, student: string, input: AskInput): Promise<Reply> {
  if (!(await isSelf(deps.pool, account, student))) return NOT_FOUND;
  const curfew = await inCurfew(deps.pool, account, student);
  if (curfew === 'forbidden') return NOT_FOUND;
  if (curfew) return CURFEW;
  const t = openLessonTicket(deps.key, student, input.ticket);
  if (t === null) return TICKET_INVALID;
  if (Date.parse(t.expiresAt) <= deps.now()) return TICKET_EXPIRED;  // 本地拒绝：不调学习服务
  const r = await withRetry(deps, () => deps.learning.ask({ student_ref: student, ticket: t.ticket, question: input.question,
                                                             idempotency_key: input.idempotency_key }));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') {
    const code = r.problem.code ?? '';
    if (r.status === 404) return NOT_FOUND;
    if (r.status === 410) return TICKET_EXPIRED;
    if (r.status === 501) return LESSON_UNAVAILABLE;
    if (r.status === 409) return LESSON_CLOSED;  // LESSON_NOT_OPEN、IDEMPOTENCY_KEY_SEALED（预留已释放或已过期）
    if (r.status === 422 && code === 'TICKET_INVALID') return TICKET_INVALID;
    return INVALID;  // QUESTION_NOT_ANSWERABLE（指着还没出现的东西）与请求不合格
  }
  return { status: 200, headers: replayHeader(r.replayed), body: answerView(r.body) };
}

/** 回答只转结果、文字或原因、余量（第几问不给前端） */
function answerView(a: AskBody): Record<string, unknown> {
  return a.outcome === 'answered' ? { outcome: 'answered', text: a.text, questions_left: a.questions_left }
    : { outcome: 'no_answer', reason: a.reason, questions_left: a.questions_left };
}
