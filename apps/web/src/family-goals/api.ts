// 家长诉求的 BFF 客户端（接口 services/bff/openapi/family-goals.v0.yaml）：只在服务端调用（服务端组件与 server action），浏览器不直连 BFF。
// 每个调用先过前端开关（./features.ts）：开关关时不发任何请求，直接按 404 返回。全文件只有 call 里一处 fetch。
// 前端不判断授权与取值规则：BFF 与数据库判断，这里只把结果码原样交回；返回值再由 ./view.ts 按白名单挑字段。不计量。
import { bffBaseUrl, bffHeaders, type BffSession } from '../lib/bff.ts';
import { isUuid } from '../lib/star-map.ts';
import { familyGoalsEnabled } from './features.ts';
import type { GoalBody } from './form.ts';

export type GoalsApi<T> = { ok: true; value: T } | { ok: false; status: number; code: string };

const OFF = { ok: false, status: 404, code: 'NOT_FOUND' } as const;
const TIMEOUT_MS = 15_000;

async function call<T>(s: BffSession, student: string, method: 'GET' | 'PUT', body?: GoalBody): Promise<GoalsApi<T>> {
  if (!familyGoalsEnabled()) return OFF;
  if (!isUuid(student)) return OFF;
  const headers: Record<string, string> = await bffHeaders(s, method !== 'GET');
  if (body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/family-goals`, {
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

/** 家长本人写的当前诉求与历史，以及表单需要的事实（只读） */
export const fetchFamilyGoals = (s: BffSession, student: string) => call<unknown>(s, student, 'GET');

/** 提交一版诉求（整体替换本人的当前版；旧版保留） */
export const putFamilyGoals = (s: BffSession, student: string, body: GoalBody) => call<{ version: number }>(s, student, 'PUT', body);
