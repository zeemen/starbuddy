// 数学自适应诊断的 BFF 客户端（SIE-02a；只在服务端调用，浏览器不直连 BFF）。接口见 services/bff/openapi/diagnosis.v0.yaml。
// BFF 只回题面、步骤序号、题型与进度，不回答案、题号、节点、对错；这里再按白名单取一遍字段再交给页面。
import { bffBaseUrl, isUuid } from './star-map.ts';

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
  step: DiagnosisStep | null;
}
export type Answer = { text: string } | { choice: string[] };

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isKey = (s: unknown): s is string => typeof s === 'string' && UUID_V4.test(s);

export type Result<T> = { ok: true; value: T } | { ok: false; status: number; code: string | null };

async function call<T>(account: string, student: string, method: 'GET' | 'POST', path: string, body?: unknown): Promise<Result<T>> {
  if (!isUuid(student)) return { ok: false, status: 403, code: 'FORBIDDEN' };
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/diagnosis/sessions${path}`, {
      method,
      headers: { 'x-dev-account-id': account, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
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
    const code = parsed && typeof parsed === 'object' && typeof (parsed as { code?: unknown }).code === 'string'
      ? (parsed as { code: string }).code : null;
    return { ok: false, status: r.status, code };
  }
  return { ok: true, value: parsed as T };
}

/** 只取白名单字段（防止 BFF 以后多给了什么被原样带进页面） */
export function pickView(v: DiagnosisView): DiagnosisView {
  const s = v.step;
  return {
    session_id: v.session_id, status: v.status, elapsed_sec: v.elapsed_sec, time_budget_sec: v.time_budget_sec,
    item_count: v.item_count, item_limit: v.item_limit,
    step: s === null ? null : {
      seq: s.seq, kind: s.kind, probe_recorded: s.probe_recorded, step_elapsed_sec: s.step_elapsed_sec,
      item: { format: s.item.format, stem: s.item.stem, ...(s.item.options ? { options: s.item.options.map((o) => ({ key: o.key, text: o.text })) } : {}) },
    },
  };
}

export const startDiagnosis = (account: string, student: string, key: string) =>
  call<{ session_id: string; status: string }>(account, student, 'POST', '', { subject: 'aya', idempotency_key: key });

export async function currentDiagnosis(account: string, student: string, session: string): Promise<Result<DiagnosisView>> {
  if (!isUuid(session)) return { ok: false, status: 403, code: 'FORBIDDEN' };
  const r = await call<DiagnosisView>(account, student, 'GET', `/${session}`);
  return r.ok ? { ok: true, value: pickView(r.value) } : r;
}

export const probeDiagnosis = (account: string, student: string, session: string, seq: number, prediction: string, key: string,
  askedAt = new Date()) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/probe`, { seq, prediction, asked_at: askedAt.toISOString(), idempotency_key: key });

export const answerDiagnosis = (account: string, student: string, session: string, seq: number, answer: Answer, work: string | null,
  elapsedSec: number, key: string) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/answer`,
    { seq, answer, elapsed_sec: elapsedSec, idempotency_key: key, ...(work !== null ? { work } : {}) });

export const pauseDiagnosis = (account: string, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string) =>
  call<{ status: string }>(account, student, 'POST', `/${session}/pause`,
    { ...(seq !== null ? { seq, step_elapsed_sec: stepElapsedSec } : {}), idempotency_key: key });
