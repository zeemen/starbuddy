// 数学自适应诊断的学生端接口（SIE-02a，specs/sie-02a-math-diagnosis/spec.md 设计要点 1、5）。接口定义 openapi/diagnosis.v0.yaml。
//
// 只认学生本人：家长、其他学生、学生不存在一律 403（与 2-L 事件接口同一套错误码）；未登录 401 在 server.ts。
// 返回给前端的只有题面、步骤序号、题型、进度与会话状态：不含答案、题号、节点、选题理由、对错（规则 3、11）。
//
// 计量（规则 4，沿用 0009 / 0015 的计量函数，不改规则；安全审查 B1）：
//   - 开始：以学生身份预留 diagnosis_full（method = diagnose，幂等键 = 请求的幂等键，usage_id 由键派生），再请学习服务建会话；
//     学习服务确定拒绝 → 释放；结果未知 → 同一请求重试，仍不确定就不结算、不释放（留给过期任务）；
//   - 第一道题判分成功 → 立即结算（预留有效期 10 分钟，远短于一次诊断；之后放弃也不退）；之后每次作答再结算一次是幂等的；
//   - 已有未结束的会话（暂停后续做）：不预留，直接续做同一个会话（不另占额度）；续做当场到上限结束也结算（S10）；
//   - 放弃：只有还没有任何已作答的题时才释放；答过题的放弃按已结算处理（再结算一次，幂等）。
//   - 第一道题在预留过期（开始后 10 分钟）之后才交：按现有规则“宁可少扣”不结算（lessons 待人确认 #3：有效期调长或按段计量）。
// 宵禁（安全审查 B2）：续做、取题、探针、作答入口以学生本人读 platform.student_time_guard（0015 已授 BFF 按 RLS 读），
//   按 platform.meter_in_curfew 的同一口径（北京时间，缺省 22:00–06:00，跨午夜）判断；宵禁中先请学习服务把会话置为暂停，
//   再回 402 reason = curfew。时钟与计量函数同一时间轴（deps.now，测试里注入；0015 没给 BFF 只读的宵禁函数，lessons 待人确认）。
// 探针：BFF 以学生身份调 2-L 的 insight.record_confidence_probe，幂等键由（会话、序号）派生（安全审查 S3：并发提交只写一行，
//   后到的内容不同时 23505，按派生键请学习服务找回同一条），把探针记到步骤；
// 作答落库后按 2-L 的 insight.link_probe_attempt 关联（尽力而为：时间窗外、已关联时不影响作答）。
import type { Pool } from 'pg';
import { withAccount } from '../metering/db.ts';
import { meteringFingerprint } from '../metering/fingerprint.ts';
import { release, reserve, settle } from '../metering/meter.ts';
import { usageIdFor, uuidv5 } from '../metering/uuid.ts';
import { isUuid } from '../star-map/query.ts';
import type { DiagnosisLearning, DiagOutcome, SessionView } from './learning-client.ts';

export interface DiagnosisDeps {
  pool: Pool;
  learning: DiagnosisLearning;
  sleep?: (ms: number) => Promise<void>;
  retryDelaysMs?: number[];
  /** 计量与关联的旁路记录（测试断言用）；只收事件名 */
  note?: (event: string) => void;
  /** 与数据库计量时钟同一时间轴（宵禁判断用；测试里注入） */
  now?: () => number;
}

export interface Reply { status: number; body: unknown; headers?: Record<string, string> }

const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}): Reply =>
  ({ status, body: { type: `/problems/${code.toLowerCase()}`, title, status, code, ...extra } });
export const FORBIDDEN = problem(403, 'FORBIDDEN', 'not allowed');
const INVALID = problem(422, 'INVALID', 'invalid request');
const KEY_REUSED = problem(409, 'IDEMPOTENCY_KEY_REUSED', 'idempotency key reused with different content');
const UNAVAILABLE = problem(503, 'UPSTREAM_UNAVAILABLE', 'diagnosis service unavailable, retry with the same request');

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const CHOICE_KEY = /^[A-Z]$/;
export const PREDICTIONS = ['yes', 'no', 'unsure'] as const;
const MAX_FILL = 200;
const MAX_WORK = 20000;

const exactKeys = (o: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean =>
  required.every((k) => k in o) && Object.keys(o).every((k) => required.includes(k) || optional.includes(k));
const isKey = (v: unknown): v is string => typeof v === 'string' && UUID_V4.test(v);
const isInt = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;

// ---------- 请求体 ----------
export interface StartInput { subject: 'aya'; idempotency_key: string }
export interface ProbeInput { seq: number; prediction: (typeof PREDICTIONS)[number]; asked_at: string; idempotency_key: string }
export interface AnswerInput { seq: number; answer: { text: string } | { choice: string[] }; work?: string; elapsed_sec: number;
                               idempotency_key: string }
export interface PauseInput { seq: number | null; step_elapsed_sec: number | null; idempotency_key: string }

export function parseStart(b: Record<string, unknown>): StartInput | null {
  if (!exactKeys(b, ['subject', 'idempotency_key']) || b['subject'] !== 'aya' || !isKey(b['idempotency_key'])) return null;
  return { subject: 'aya', idempotency_key: b['idempotency_key'] };
}

export function parseProbe(b: Record<string, unknown>): ProbeInput | null {
  if (!exactKeys(b, ['seq', 'prediction', 'asked_at', 'idempotency_key'])) return null;
  const { seq, prediction, asked_at: asked, idempotency_key: key } = b;
  if (!isInt(seq, 1, 100) || !(PREDICTIONS as readonly unknown[]).includes(prediction) || !isKey(key)) return null;
  if (typeof asked !== 'string' || !RFC3339.test(asked) || !Number.isFinite(Date.parse(asked))) return null;
  return { seq, prediction: prediction as ProbeInput['prediction'], asked_at: asked, idempotency_key: key };
}

function parseAnswerValue(a: unknown): AnswerInput['answer'] | null {
  if (a === null || typeof a !== 'object' || Array.isArray(a)) return null;
  const o = a as Record<string, unknown>;
  if (exactKeys(o, ['text'])) {
    const t = o['text'];
    return typeof t === 'string' && t.trim() !== '' && t.length <= MAX_FILL ? { text: t } : null;
  }
  if (exactKeys(o, ['choice'])) {
    const c = o['choice'];
    if (!Array.isArray(c) || c.length < 1 || c.length > 8 || !c.every((k) => typeof k === 'string' && CHOICE_KEY.test(k))) return null;
    return new Set(c).size === c.length ? { choice: c as string[] } : null;
  }
  return null;
}

export function parseAnswer(b: Record<string, unknown>): AnswerInput | null {
  if (!exactKeys(b, ['seq', 'answer', 'elapsed_sec', 'idempotency_key'], ['work'])) return null;
  const answer = parseAnswerValue(b['answer']);
  const work = b['work'];
  if (!answer || !isInt(b['seq'], 1, 100) || !isInt(b['elapsed_sec'], 0, 7200) || !isKey(b['idempotency_key'])) return null;
  if (work !== undefined && (typeof work !== 'string' || work.trim() === '' || work.length > MAX_WORK)) return null;
  return { seq: b['seq'], answer, elapsed_sec: b['elapsed_sec'], idempotency_key: b['idempotency_key'],
           ...(typeof work === 'string' ? { work } : {}) };
}

export function parsePause(b: Record<string, unknown>): PauseInput | null {
  if (!exactKeys(b, ['idempotency_key'], ['seq', 'step_elapsed_sec']) || !isKey(b['idempotency_key'])) return null;
  const seq = b['seq'] === undefined ? null : b['seq'];
  const el = b['step_elapsed_sec'] === undefined ? null : b['step_elapsed_sec'];
  if ((seq !== null && !isInt(seq, 1, 100)) || (el !== null && !isInt(el, 0, 7200))) return null;
  return { seq: seq as number | null, step_elapsed_sec: el as number | null, idempotency_key: b['idempotency_key'] };
}

export function parseAbandon(b: Record<string, unknown>): string | null {
  return exactKeys(b, ['idempotency_key']) && isKey(b['idempotency_key']) ? b['idempotency_key'] : null;
}

// ---------- 身份与归属 ----------
async function isSelf(pool: Pool, account: string, student: string): Promise<boolean> {
  if (!isUuid(account) || !isUuid(student)) return false;
  return withAccount(pool, account, async (c) =>
    (await c.query<{ ok: boolean }>('SELECT platform.is_self_student($1::uuid) AS ok', [student])).rows[0]!.ok === true);
}

/** 会话归属：经 0027 的 RLS（只有学生本人读得到自己的会话）；读不到与不存在一样处理 */
async function ownsSession(pool: Pool, account: string, student: string, session: string): Promise<boolean> {
  if (!isUuid(session) || !(await isSelf(pool, account, student))) return false;
  return withAccount(pool, account, async (c) =>
    (await c.query('SELECT 1 FROM platform.diagnosis_session WHERE id = $1::uuid AND student_id = $2::uuid', [session, student]))
      .rowCount === 1);
}

async function openSessionId(pool: Pool, account: string, student: string): Promise<string | null> {
  return withAccount(pool, account, async (c) => {
    const { rows } = await c.query<{ id: string }>(
      `SELECT id FROM platform.diagnosis_session WHERE student_id = $1::uuid AND subject = 'aya' AND status IN ('active', 'paused')`,
      [student]);
    return rows[0]?.id ?? null;
  });
}

// ---------- 宵禁（安全审查 B2） ----------
/** 与 platform.meter_in_curfew 同一口径：北京时间；家长设定的时段或缺省 22:00–06:00；start > end 表示跨午夜 */
export async function inCurfew(pool: Pool, account: string, student: string, atMs: number): Promise<boolean> {
  return withAccount(pool, account, async (c) => {
    const { rows } = await c.query<{ curfew: boolean }>(
      `SELECT CASE WHEN s < e THEN t >= s AND t < e ELSE t >= s OR t < e END AS curfew
         FROM (SELECT ($2::timestamptz AT TIME ZONE 'Asia/Shanghai')::time AS t,
                      coalesce(g.curfew_start, time '22:00') AS s, coalesce(g.curfew_end, time '06:00') AS e
                 FROM (SELECT 1) one LEFT JOIN platform.student_time_guard g ON g.student_id = $1::uuid) x`,
      [student, new Date(atMs).toISOString()]);
    return rows[0]!.curfew === true;
  });
}

export const CURFEW = problem(402, 'QUOTA_UNAVAILABLE', 'not available during curfew hours', { reason: 'curfew' });

/** 宵禁中：先请学习服务把会话置为暂停（尽力而为），再回 402 curfew；不在宵禁回 null */
async function curfewGate(deps: DiagnosisDeps, account: string, student: string, session: string | null): Promise<Reply | null> {
  if (!(await inCurfew(deps.pool, account, student, (deps.now ?? Date.now)()))) return null;
  if (session !== null) {
    const p = await withRetry(deps, () => deps.learning.pause(student, session, null, null));
    deps.note?.(p.kind === 'ok' ? 'curfew_paused' : `curfew_pause_${p.kind}`);
  }
  return CURFEW;
}

// ---------- 学习服务调用：结果未知时同一请求重试 ----------
const DEFAULT_DELAYS = [300, 1000, 2000];
async function withRetry<T>(deps: DiagnosisDeps, fn: () => Promise<DiagOutcome<T>>): Promise<DiagOutcome<T>> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  let out = await fn();
  for (const d of deps.retryDelaysMs ?? DEFAULT_DELAYS) {
    if (out.kind !== 'unknown') break;
    await sleep(d);
    out = await fn();
  }
  return out;
}

/** 学习服务的确定失败映射到学生端：会话 / 步骤 / 状态类冲突原样给出编码（不带明细）；其余 422 */
function fromFailure(p: { status: number; problem: { code?: string } }): Reply {
  const code = p.problem.code ?? '';
  if (p.status === 404) return FORBIDDEN;
  if (p.status === 501) return problem(503, 'DIAGNOSIS_UNAVAILABLE', 'diagnosis is not available');
  if (p.status === 409 && ['STEP_NOT_CURRENT', 'INVALID_STATE', 'SESSION_INCONSISTENT'].includes(code)) {
    return problem(409, code, 'the session has moved on; fetch the current step');
  }
  if (p.status === 409 && code === 'ANSWER_CONFLICT') return problem(409, code, 'this step was already answered with a different answer');
  if (p.status === 409) return KEY_REUSED;
  if (code === 'NO_TARGETS') return problem(422, 'NO_TARGETS', 'no diagnosis available for this grade yet');
  return INVALID;
}

const replayHeader = (replayed: boolean) => ({ 'idempotent-replayed': replayed ? 'true' : 'false' });

async function settleQuietly(deps: DiagnosisDeps, account: string, usage: string): Promise<void> {
  // 会话结束才结算；预留已过期或已释放时不结算（宁可少扣，同 meteredCall）
  const s = await settle(deps.pool, account, usage, 1);
  deps.note?.(s.outcome === 'settled' ? (s.idempotent ? 'settle_replayed' : 'settled') : `settle_${s.state}`);
}

async function releaseQuietly(deps: DiagnosisDeps, account: string, usage: string, reason: string): Promise<void> {
  const r = await release(deps.pool, account, usage, reason);
  deps.note?.(r.outcome === 'released' ? (r.idempotent ? 'release_replayed' : 'released') : `release_${r.state}`);
}

// ---------- 开始 / 续做 ----------
export async function startDiagnosis(deps: DiagnosisDeps, account: string, student: string, input: StartInput): Promise<Reply> {
  if (!(await isSelf(deps.pool, account, student))) return FORBIDDEN;
  const key = input.idempotency_key;
  const usage = usageIdFor(key);
  const open = await openSessionId(deps.pool, account, student);
  if (open !== null) {
    // 续做：宵禁中不续做（会话置为暂停）；同一个会话，不预留（之前开始时已占过额度）
    const blocked = await curfewGate(deps, account, student, open);  // 宵禁：续做
    if (blocked) return blocked;
    const r = await withRetry(deps, () => deps.learning.start(student, input.subject, usage, key));
    if (r.kind === 'unknown') return UNAVAILABLE;
    if (r.kind === 'failure') return fromFailure(r);
    if (r.body.status === 'finished') await settleQuietly(deps, account, r.body.usage_id);  // 续做当场到上限结束也结算（S10）
    return { status: 200, body: { session_id: r.body.session_id, status: r.body.status }, headers: replayHeader(r.replayed) };
  }
  const fingerprint = meteringFingerprint('diagnose', { context: { subject: input.subject, student_ref: student }, mode: 'adaptive_session' });
  const res = await reserve(deps.pool, account, { studentId: student, resourceType: 'diagnosis_full', idempotencyKey: key,
                                                   requestFingerprint: fingerprint, quantity: 1, sourceMethod: 'diagnose' });
  switch (res.outcome) {
    case 'conflict':
      return KEY_REUSED;
    case 'insufficient': case 'no_entitlement': case 'daily_cap_reached': case 'curfew': case 'curfew_soon':
    case 'retained_no_ai': case 'dedup_in_progress':
      return problem(402, 'QUOTA_UNAVAILABLE', 'no diagnosis quota available right now', { reason: res.outcome });
  }
  if (res.outcome === 'existing' && res.state === 'released') return problem(409, 'KEY_FAILED_PREVIOUSLY', 'start again with a new key');
  if (res.outcome === 'existing' && res.state === 'expired') return problem(409, 'KEY_EXPIRED', 'start again with a new key');
  const holding = res.outcome === 'reserved' || res.outcome === 'dedup' || (res.outcome === 'existing' && res.state === 'reserved');
  deps.note?.(res.outcome === 'existing' ? `reserve_existing_${res.state}` : 'reserved');
  const r = await withRetry(deps, () => deps.learning.start(student, input.subject, usage, key));
  if (r.kind === 'unknown') return UNAVAILABLE;  // 不释放：结果未知，同一个键重试会找回同一笔预留与同一个会话
  if (r.kind === 'failure') {
    if (holding) await releaseQuietly(deps, account, usage, 'upstream_rejected');
    return fromFailure(r);
  }
  if (holding && r.body.usage_id !== usage) {
    await releaseQuietly(deps, account, usage, 'session_already_open');  // 并发开始：续做了已有会话，这笔不用
  } else if (r.body.status === 'finished') {
    await settleQuietly(deps, account, usage);
  }
  return { status: r.body.created ? 201 : 200, body: { session_id: r.body.session_id, status: r.body.status },
           headers: replayHeader(r.replayed) };
}

// ---------- 当前题 ----------
export function publicView(v: SessionView): Record<string, unknown> {
  const step = v.step === null ? null : {
    seq: v.step.seq, kind: v.step.kind, probe_recorded: v.step.probe_recorded, step_elapsed_sec: v.step.step_elapsed_sec,
    item: { format: v.step.item.format, stem: v.step.item.stem,
            ...(v.step.item.options ? { options: v.step.item.options.map((o) => ({ key: o.key, text: o.text })) } : {}) },
  };
  return { session_id: v.session_id, status: v.status, elapsed_sec: v.elapsed_sec, time_budget_sec: v.time_budget_sec,
           item_count: v.item_count, item_limit: v.item_limit, process_tasks: v.process_tasks === true, step };
}

/** 会话结束的计量：结束 → 结算；放弃 → 没答过题才释放，答过题按已结算处理（再结算一次是幂等的） */
async function settleIfDone(deps: DiagnosisDeps, account: string, v: { status: string; usage_id: string; item_count: number }): Promise<void> {
  if (v.status === 'finished') await settleQuietly(deps, account, v.usage_id);
  if (v.status === 'abandoned') {
    if (v.item_count > 0) await settleQuietly(deps, account, v.usage_id);
    else await releaseQuietly(deps, account, v.usage_id, 'abandoned');
  }
}

export async function currentStep(deps: DiagnosisDeps, account: string, student: string, session: string): Promise<Reply> {
  if (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;
  const r = await withRetry(deps, () => deps.learning.current(student, session));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') return fromFailure(r);
  if (r.body.status === 'active') {
    const blocked = await curfewGate(deps, account, student, session);  // 宵禁：取题
    if (blocked) return blocked;
  }
  await settleIfDone(deps, account, r.body);
  return { status: 200, body: publicView(r.body) };
}

// ---------- 探针 ----------
/** 探针的幂等键由（会话、序号）派生：同一步只可能有一条探针（安全审查 S3） */
export const DIAG_PROBE_NAMESPACE = '6c2f9d4e-1b7a-4e53-8f0c-2a9d5e7b3c61';
export const probeKeyFor = (session: string, seq: number): string => uuidv5(DIAG_PROBE_NAMESPACE, `${session}|${seq}`);

export async function submitProbe(deps: DiagnosisDeps, account: string, student: string, session: string, input: ProbeInput): Promise<Reply> {
  if (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;
  const blocked = await curfewGate(deps, account, student, session);  // 宵禁：探针
  if (blocked) return blocked;
  const cur = await withRetry(deps, () => deps.learning.current(student, session));
  if (cur.kind === 'unknown') return UNAVAILABLE;
  if (cur.kind === 'failure') return fromFailure(cur);
  const step = cur.body.step;
  if (cur.body.status !== 'active' || step === null || step.seq !== input.seq) {
    return problem(409, 'STEP_NOT_CURRENT', 'the session has moved on; fetch the current step');
  }
  // 这一步已经记过探针：只认第一次的预测（不再写新的探针行）
  if (step.probe_recorded) return { status: 200, body: { status: 'ok' }, headers: replayHeader(true) };
  const probeKey = probeKeyFor(session, input.seq);
  let ref: { probe_id: string } | { probe_key: string };
  try {
    ref = { probe_id: await withAccount(deps.pool, account, async (c) => (await c.query<{ id: string }>(
      'SELECT id FROM insight.record_confidence_probe($1, $2::platform.subject, $3, $4, $5::timestamptz, $6::uuid)',
      [student, step.subject, step.item_id, input.prediction, input.asked_at, probeKey])).rows[0]!.id) };
  } catch (e) {
    const code = (e as { code?: string }).code ?? '';
    if (code === '42501') return FORBIDDEN;
    // 同一步已有一条探针（并发提交或上一次没记到步骤上）：不另写，按派生键请学习服务记那一条（只认第一次的预测）
    if (code === '23505') ref = { probe_key: probeKey };
    else if (code === '54000') return problem(429, 'TOO_MANY', 'too many writes');
    else if (['22023', '23514', '22P02', '22007', '22008'].includes(code)) return INVALID;
    else throw e;
  }
  const r = await withRetry(deps, () => deps.learning.probe(student, session, input.seq, ref));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') return fromFailure(r);
  return { status: 200, body: { status: 'ok' }, headers: replayHeader(r.replayed) };
}

// ---------- 作答 ----------
export async function submitAnswer(deps: DiagnosisDeps, account: string, student: string, session: string, input: AnswerInput): Promise<Reply> {
  if (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;
  const blocked = await curfewGate(deps, account, student, session);  // 宵禁：作答
  if (blocked) return blocked;
  const r = await withRetry(deps, () => deps.learning.answer(student, session, {
    seq: input.seq, answer: input.answer, elapsed_sec: input.elapsed_sec, idempotency_key: input.idempotency_key,
    ...(input.work !== undefined ? { work: input.work } : {}) }));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') return fromFailure(r);
  if (r.body.probe_id) {
    try {
      await withAccount(deps.pool, account, (c) =>
        c.query('SELECT insight.link_probe_attempt($1, $2::uuid, $3::uuid)', [student, r.body.probe_id, r.body.attempt_id]));
      deps.note?.('probe_linked');
    } catch (e) {
      // 尽力而为：探针写入 2 小时后才作答（暂停跨天）等时间窗外的情况不关联，不影响作答（lessons）
      deps.note?.(`probe_link_failed_${(e as { code?: string }).code ?? 'unknown'}`);
    }
  }
  // 第一道题判分成功即结算（之后放弃不退；再结算是幂等的）：不等会话结束，免得预留先过期（安全审查 B1）
  await settleQuietly(deps, account, r.body.usage_id);
  return { status: 200, body: { status: r.body.status }, headers: replayHeader(r.replayed) };
}

// ---------- 暂停 / 放弃 ----------
export async function pauseSession(deps: DiagnosisDeps, account: string, student: string, session: string, input: PauseInput): Promise<Reply> {
  if (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;
  const r = await withRetry(deps, () => deps.learning.pause(student, session, input.seq, input.step_elapsed_sec));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') return fromFailure(r);
  return { status: 200, body: { status: r.body.status }, headers: replayHeader(r.replayed) };
}

export async function abandonSession(deps: DiagnosisDeps, account: string, student: string, session: string): Promise<Reply> {
  if (!(await ownsSession(deps.pool, account, student, session))) return FORBIDDEN;
  const r = await withRetry(deps, () => deps.learning.abandon(student, session));
  if (r.kind === 'unknown') return UNAVAILABLE;
  if (r.kind === 'failure') return fromFailure(r);
  await settleIfDone(deps, account, r.body);  // 没答过题才释放；答过题不退
  return { status: 200, body: { status: r.body.status }, headers: replayHeader(r.replayed) };
}
