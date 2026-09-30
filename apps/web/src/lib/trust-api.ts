// 信任界面（冲刺 5-H）的 BFF 客户端（接口 services/bff/openapi/trust.v0.yaml，SIE-03 PR③）：只在服务端调用
// （服务端组件、server action、路由处理器），浏览器不直连 BFF。
// 每个调用先过前端开关（src/shell/features.ts）：开关关时不发任何请求，直接按 404 返回（4-H“信任界面预留”验收）。
// 前端不判断授权、不生成关于学生的文字：BFF 与数据库判断；这里只转交，返回值再由 src/trust/view.ts 挑字段、再断言。
import { trustEnabled, type TrustFeature } from '../shell/features.ts';
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import type { Api } from './shell-api.ts';

const OFF = { ok: false, status: 404, code: 'NOT_FOUND' } as const;

async function call<T>(feature: TrustFeature, s: BffSession, method: 'GET' | 'POST', path: string, body?: unknown): Promise<Api<T>> {
  if (!trustEnabled(feature)) return OFF;
  const write = method !== 'GET';
  const headers: Record<string, string> = await bffHeaders(s, write);
  if (body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', redirect: 'error',
    });
  } catch {
    return { ok: false, status: 503, code: 'UNAVAILABLE' };
  }
  const text = await r.text();
  let json: unknown = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T };
  const code = json && typeof json === 'object' && typeof (json as { code?: unknown }).code === 'string' ? (json as { code: string }).code : 'INTERNAL';
  return { ok: false, status: r.status, code };
}

const S = (student: string) => `/students/${student}`;

// ---------------------------------------------------------------- “为什么”（开关 why）

/**
 * 判断的“为什么”：陈述 + 证据链（不含逐句对话）+ 依据版本。**BFF 还没有这条读取路由**（PR③ 只做了 dispute / reconcile /
 * data_rights；4-H 的 insight.explain 读取只留陈述、丢弃证据链）：路径按 shell.v0 的风格预留，在 BFF 补上之前一律 404，
 * 页面显示“暂时无法获取”（不可见与不存在同一个结果，不暴露判断是否存在）。见 specs/trust-ui-sprint5/lessons.md。
 */
export const fetchWhy = (s: BffSession, student: string, claim: string) => call<unknown>('why', s, 'GET', `${S(student)}/claims/${claim}/explain`);

// ---------------------------------------------------------------- 异议（开关 dispute）

export const raiseDispute = (s: BffSession, student: string, claim: string, kind: string, idempotencyKey: string) =>
  call<{ dispute_id: string; kind: string; next_step: string; verify_by: string | null }>(
    'dispute', s, 'POST', `${S(student)}/claims/${claim}/disputes`, { kind, idempotency_key: idempotencyKey });
export const fetchDisputes = (s: BffSession, student: string) => call<unknown>('dispute', s, 'GET', `${S(student)}/disputes`);

// ---------------------------------------------------------------- 考试（开关 reconcile）

export const submitExamPlan = (s: BffSession, student: string, body: Record<string, unknown>) =>
  call<{ exam_id: string }>('reconcile', s, 'POST', `${S(student)}/exams`, body);
export const submitExamScore = (s: BffSession, student: string, exam: string, rawScore: number) =>
  call<{ score_id: string }>('reconcile', s, 'POST', `${S(student)}/exams/${exam}/score`, { raw_score: rawScore });
export const fetchPrediction = (s: BffSession, student: string, exam: string) => call<unknown>('reconcile', s, 'GET', `${S(student)}/exams/${exam}/prediction`);
export const fetchReconciliation = (s: BffSession, student: string, exam: string) =>
  call<unknown>('reconcile', s, 'GET', `${S(student)}/exams/${exam}/reconciliation`);

// ---------------------------------------------------------------- 家长数据权利（开关 data_rights，只给家长）

export const createExport = (s: BffSession, student: string) =>
  call<{ export_job_id: string; status: string; expires_at: string; download_ref: string }>('data_rights', s, 'POST', `${S(student)}/exports`);
export const fetchErasures = (s: BffSession, student: string) => call<unknown>('data_rights', s, 'GET', `${S(student)}/erasure`);
export const requestErasure = (s: BffSession, student: string) =>
  call<{ request_id: string; confirm_token: string }>('data_rights', s, 'POST', `${S(student)}/erasure`);
export const confirmErasure = (s: BffSession, student: string, request: string, confirmToken: string) =>
  call<{ request_id: string; cooling_off_until: string }>('data_rights', s, 'POST', `${S(student)}/erasure/${request}/confirm`, { confirm_token: confirmToken });
export const cancelErasure = (s: BffSession, student: string, request: string) =>
  call<{ request_id: string; status: string }>('data_rights', s, 'POST', `${S(student)}/erasure/${request}/cancel`);

/** 兑换一次性下载凭据：原样取回导出包（JSON 文本），不解析、不缓存；凭据不对、已用过、过期一律 404 */
export async function downloadExport(s: BffSession, student: string, job: string, downloadRef: string)
  : Promise<{ ok: true; body: string } | { ok: false; status: number }> {
  if (!trustEnabled('data_rights')) return { ok: false, status: 404 };
  const headers: Record<string, string> = { ...(await bffHeaders(s, true)), 'content-type': 'application/json' };
  try {
    const r = await fetch(`${bffBaseUrl()}/bff/v0${S(student)}/exports/${job}/download`, {
      method: 'POST', headers, body: JSON.stringify({ download_ref: downloadRef }), cache: 'no-store', redirect: 'error',
    });
    const body = await r.text();
    return r.ok ? { ok: true, body } : { ok: false, status: r.status };
  } catch {
    return { ok: false, status: 503 };
  }
}
