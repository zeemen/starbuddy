// 统一壳 H4 页面的 BFF 客户端（接口 services/bff/openapi/shell.v0.yaml）：只在服务端调用（服务端组件与 server action）。
// 浏览器不直连 BFF。身份只来自会话（HttpOnly cookie 转交成 x-sb-session）；改变状态的请求另带 Origin 与 CSRF 令牌（bffHeaders）。
// 前端不判断授权、不算额度与时长：数值全部由 BFF 给（BFF 再取自数据库），这里只转交。
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';

export type Api<T> = { ok: true; value: T } | { ok: false; status: number; code: string };

async function call<T>(s: BffSession, method: 'GET' | 'POST' | 'PUT', path: string, body?: unknown): Promise<Api<T>> {
  const write = method !== 'GET';
  const headers: Record<string, string> = await bffHeaders(s, write);
  if (body !== undefined) headers['content-type'] = 'application/json';
  const r = await fetch(`${bffBaseUrl()}/bff/v0${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', redirect: 'error',
  });
  const text = await r.text();
  let json: unknown = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T };
  const code = json && typeof json === 'object' && typeof (json as { code?: unknown }).code === 'string' ? (json as { code: string }).code : 'INTERNAL';
  return { ok: false, status: r.status, code };
}

// ---------------------------------------------------------------- 类型（与 shell.v0.yaml 一致）

export interface ClaimStatement { kind: 'claim'; claim_id: string; text: string; confidence_band: 'few_evidence' | 'moderate' | 'strong' }
export type Rationale = null | { kind: 'content'; code: string } | ClaimStatement;
export interface TodayTask { kind: string; node_id: string | null; node_name: string | null; priority: number; estimated_minutes: number; rationale: Rationale }
export interface TodaySection { subject: string; status: 'ok' | 'unavailable'; tasks: TodayTask[] }
/** 跨科有序列表的一条（PLT-11；开关 today_orch 打开且是学生本人时才有） */
export interface OrderedTask {
  subject: string; kind: string; node_id: string | null; node_name: string | null; estimated_minutes: number;
  source: 'retest' | 'practice' | 'plan'; entry_id: string | null; rationale: Rationale;
}
export interface OrderedToday { items: OrderedTask[]; truncated_count: number; partial: boolean }
/** ordered：没有这个键 = BFF 开关关（与开关上线前相同）；null = 学习服务暂不可用 */
export interface Today { student_id: string; subjects: TodaySection[]; ordered?: OrderedToday | null }

export interface EnrollmentRow { subject: string; is_core: boolean; catalog_status: 'beta' | 'live'; open: boolean; enrollment: 'active' | 'paused' | null }
export interface Enrollments {
  student_id: string;
  /** 当前年级在 7–9 时为真；为假时只列已开通、已暂停的学科，只能暂停（BFF 新选回 409 GRADE_OUT_OF_RANGE） */
  can_enroll: boolean;
  rules: { core_subjects: string | null; elective_slots: string | null; max_active_subjects: string | null } | null;
  subjects: EnrollmentRow[];
}

export interface Balance {
  student_id: string;
  state: 'trialing' | 'active' | 'grace' | 'retained' | 'none';
  as_of: string;
  trial_ends_at: string | null;
  trial_days_left: number | null;
  practice_remaining_today: number | null;
  /** 留存态每天自练次数（BFF 从单处配置下发，前端不写死） */
  practice_daily_limit: number;
  time: {
    plan_daily_cap: number; daily_cap: number; minutes_used_today: number; minutes_remaining_today: number; curfew_start: string;
    curfew_end: string; in_curfew: boolean; minutes_until_curfew: number; curfew_near: boolean; daily_cap_reached: boolean;
  };
  balances: { resource_type: string; unit: string; remaining: number; period_end: string; sources?: { source: string; remaining: number; period_end: string }[] }[];
}

export interface TimeGuard {
  student_id: string; daily_cap_minutes: number | null; plan_daily_cap: number; effective_daily_cap: number; custom_curfew: boolean;
  curfew_start: string; curfew_end: string; default_curfew: { start: string; end: string };
}

export interface RestView { enabled: boolean; show: boolean; continuous_minutes: number; threshold_minutes: number; suggest_break_minutes: number }

export interface Outcome { outcome: string; slot?: 'core' | 'elective'; limit?: number | null; daily_cap?: number; plan_cap?: number }

// ---------------------------------------------------------------- 读

export const fetchToday = (s: BffSession, student: string) => call<Today>(s, 'GET', `/students/${student}/today`);
export const fetchEnrollments = (s: BffSession, student: string) => call<Enrollments>(s, 'GET', `/students/${student}/enrollments`);
export const fetchBalance = (s: BffSession, student: string) => call<Balance>(s, 'GET', `/students/${student}/balance`);
export const fetchTimeGuard = (s: BffSession, student: string) => call<TimeGuard>(s, 'GET', `/students/${student}/time-guard`);
export const fetchRest = (s: BffSession, student: string) => call<RestView>(s, 'GET', `/students/${student}/rest-reminder`);

// ---------------------------------------------------------------- 写（server action 里用；CSRF 已由 actionSession 核对）

export const ackRest = (s: BffSession, student: string) => call<RestView>(s, 'POST', `/students/${student}/rest-reminder/ack`);
export const enrollSubject = (s: BffSession, student: string, subject: string) => call<Outcome>(s, 'POST', `/students/${student}/enrollments/${subject}`);
export const pauseSubject = (s: BffSession, student: string, subject: string) => call<Outcome>(s, 'POST', `/students/${student}/enrollments/${subject}/pause`);
export const setTimeGuard = (s: BffSession, student: string, body: { daily_cap_minutes: number | null; curfew_start: string | null; curfew_end: string | null }) =>
  call<Outcome>(s, 'PUT', `/students/${student}/time-guard`, body);
export const createStudent = (s: BffSession, body: { grade: number; region: string; birth_year_month: string | null }) =>
  call<{ student_id: string }>(s, 'POST', '/students', body);
export const recordConsent = (s: BffSession, student: string, consentType: string, documentVersion: string) =>
  call<{ status: string }>(s, 'POST', `/students/${student}/consents`, { consent_type: consentType, document_version: documentVersion, decision: 'granted' });

/** 学科码（URL 用目录的学科码，spec 第 5 节） */
export const SUBJECT_CODE = /^(math|chinese|politics|english)$/;
