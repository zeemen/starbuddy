// 内部成员测试运营的 BFF 客户端（接口 services/bff/openapi/internal-test.v0.yaml）：只在服务端调用（服务端组件与 server action）。
// 每个调用先过前端开关（./features.ts）：开关关时不发任何请求，直接按 404 返回。全文件只有 call 里一处 fetch。
// 前端不判断授权：BFF 与数据库判断，这里只把结果码原样交回。反馈文本只放进请求体，不打日志。不计量（反馈与退出不是消耗类请求）。
import { bffBaseUrl, bffHeaders, type BffSession } from '../lib/bff.ts';
import { isUuid } from '../lib/star-map.ts';
import { internalTestEnabled } from './features.ts';
import type { FeedbackCategory } from './routes.ts';

export type InternalTestApi<T> = { ok: true; value: T } | { ok: false; status: number; code: string };

const OFF = { ok: false, status: 404, code: 'NOT_FOUND' } as const;
const TIMEOUT_MS = 15_000;

async function call<T>(s: BffSession, student: string, method: 'GET' | 'POST', path: '/feedback' | '/internal-test' | '/internal-test/exit',
                       body?: unknown): Promise<InternalTestApi<T>> {
  if (!internalTestEnabled()) return OFF;
  if (!isUuid(student)) return OFF;
  const headers: Record<string, string> = await bffHeaders(s, method !== 'GET');
  if (body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 503, code: 'UNAVAILABLE' };
  }
  let json: unknown = null;
  try { json = JSON.parse(await r.text()); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T };
  const code = json && typeof json === 'object' && typeof (json as Record<string, unknown>)['code'] === 'string'
    ? (json as Record<string, string>)['code']! : 'INTERNAL';
  return { ok: false, status: r.status, code };
}

export interface FeedbackInput { category: FeedbackCategory; body: string; route_template: string | null }

/** 学生本人或有效绑定家长提交一条反馈（来源由 BFF 按会话决定） */
export const postFeedback = (s: BffSession, student: string, input: FeedbackInput) =>
  call<{ status: 'recorded' }>(s, student, 'POST', '/feedback', input);

/** 家长读孩子是否已退出内部测试 */
export const fetchInternalTestStatus = (s: BffSession, student: string) =>
  call<{ student_id: string; exited: boolean }>(s, student, 'GET', '/internal-test');

/** 家长让孩子退出内部测试（确认页勾选之后才调） */
export const postInternalTestExit = (s: BffSession, student: string) =>
  call<{ status: 'exited'; newly_exited: boolean }>(s, student, 'POST', '/internal-test/exit', { confirm: true });
