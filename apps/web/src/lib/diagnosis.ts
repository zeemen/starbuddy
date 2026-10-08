// 数学自适应诊断的 BFF 客户端（SIE-02a；只在服务端调用，浏览器不直连 BFF）。接口见 services/bff/openapi/diagnosis.v0.yaml。
// BFF 只回题面、步骤序号、题型与进度，不回答案、题号、节点、对错；这里再按白名单取一遍字段再交给页面。
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import { isUuid } from './star-map.ts';

export type StepKind = 'adaptive' | 'process' | 'explain';
export interface ItemView { format: 'single' | 'multi' | 'fill'; stem: string; options?: { key: string; text: string }[] }
export interface DiagnosisStep { seq: number; kind: StepKind; item: ItemView; probe_recorded: boolean; step_elapsed_sec: number }
export interface DiagnosisView {
  session_id: string;
  status: 'active' | 'paused' | 'finished' | 'abandoned';
  elapsed_sec: number;
  time_budget_sec: number;
  item_count: number;
  item_limit: number;
  /** 本次是否作答过过程题或讲解题：结束页据此决定说不说“老师会看你的过程”（不是对学生的结论） */
  process_tasks: boolean;
  step: DiagnosisStep | null;
}
export type Answer = { text: string } | { choice: string[] };

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isKey = (s: unknown): s is string => typeof s === 'string' && UUID_V4.test(s);

/**
 * reason：402 时 BFF 给的原因编码（例如 curfew = 宵禁时段）；409 SESSION_INCONSISTENT 时 reason = grade_changed 表示会话中途届别被改，
 * sessionId 是要“重新开始”（放弃）的那个会话（迁移 0049）
 */
export type Result<T> = { ok: true; value: T } | { ok: false; status: number; code: string | null; reason?: string | null; sessionId?: string | null };

async function call<T>(account: BffSession, student: string, method: 'GET' | 'POST', path: string, body?: unknown): Promise<Result<T>> {
  if (!isUuid(student)) return { ok: false, status: 403, code: 'FORBIDDEN' };
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/diagnosis/sessions${path}`, {
      method,
      headers: { ...(await bffHeaders(account, method !== 'GET')), ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: AbortSignal.timeout(60_000),
    });
  } catch {
    return { ok: false, status: 503, code: null };
  }
  let parsed: unknown = null;
  try {
    parsed = await r.json();
  } catch {
    /* 非 JSON：按失败处理 */
  }
  if (!r.ok) {
    const field = (k: string): string | null => parsed && typeof parsed === 'object' && typeof (parsed as Record<string, unknown>)[k] === 'string'
      ? (parsed as Record<string, string>)[k]! : null;
    return failure(r.status, field('code'), field('reason'), field('session_id'));
  }
  return { ok: true, value: parsed as T };
}

/** 失败结果：session_id 只在“会话中途届别被改”（409 SESSION_INCONSISTENT、reason = grade_changed）且是 UUID 时保留 */
export function failure(status: number, code: string | null, reason: string | null, sessionId: string | null): Result<never> {
  const restart = status === 409 && code === 'SESSION_INCONSISTENT' && reason === 'grade_changed' && isUuid(sessionId ?? '');
  return { ok: false, status, code, reason, ...(restart ? { sessionId } : {}) };
}

/** 会话中途届别被改：返回要“重新开始”的会话 ID（先放弃它，再开新会话）；其余情况 null */
export const restartSession = (r: Result<unknown>): string | null => (!r.ok && r.sessionId ? r.sessionId : null);

/** 只取白名单字段（防止 BFF 以后多给了什么被原样带进页面） */
export function pickView(v: DiagnosisView): DiagnosisView {
  const s = v.step;
  return {
    session_id: v.session_id, status: v.status, elapsed_sec: v.elapsed_sec, time_budget_sec: v.time_budget_sec,
    item_count: v.item_count, item_limit: v.item_limit, process_tasks: v.process_tasks === true,
    step: s === null ? null : {
      seq: s.seq, kind: s.kind, probe_recorded: s.probe_recorded, step_elapsed_sec: s.step_elapsed_sec,
      item: { format: s.item.format, stem: s.item.stem, ...(s.item.options ? { options: s.item.options.map((o) => ({ key: o.key, text: o.text })) } : {}) },
    },
  };
}

/** 宵禁时段（BFF 402 reason = curfew）：前端提示“今天先到这里” */
export const isCurfew = (r: Result<unknown>): boolean => !r.ok && r.status === 402 && r.reason === 'curfew';

export const startDiagnosis = (account: BffSession, student: string, key: string) =>
  call<{ session_id: string; status: string }>(account, student, 'POST', '', { subject: 'aya', idempotency_key: key });

export async function currentDiagnosis(account: BffSession, student: string, session: string): Promise<Result<DiagnosisView>> {
  if (!isUuid(session)) return { ok: false, status: 403, code: 'FORBIDDEN' };
  const r = await call<DiagnosisView>(account, student, 'GET', `/${session}`);
  return r.ok ? { ok: true, value: pickView(r.value) } : r;
}

export const probeDiagnosis = (account: BffSession, student: string, session: string, seq: number, prediction: string, key: string,
  askedAt = new Date()) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/probe`, { seq, prediction, asked_at: askedAt.toISOString(), idempotency_key: key });

export const answerDiagnosis = (account: BffSession, student: string, session: string, seq: number, answer: Answer, work: string | null,
  elapsedSec: number, key: string) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/answer`,
    { seq, answer, elapsed_sec: elapsedSec, idempotency_key: key, ...(work !== null ? { work } : {}) });

/** 放弃（“重新开始”用）：因届别变化放弃的，BFF 退回额度 */
export const abandonDiagnosis = (account: BffSession, student: string, session: string, key: string) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/abandon`, { idempotency_key: key });

export const pauseDiagnosis =(account: BffSession, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/pause`,
    { ...(seq !== null ? { seq, step_elapsed_sec: stepElapsedSec } : {}), idempotency_key: key });
