// 家庭登录与会话的 BFF 客户端（接口 services/bff/openapi/shell.v0.yaml）：只在服务端调用（服务端组件与 server action）。
// 浏览器只和本前端通信，不直连 BFF。前端不判断身份与授权：只把 HttpOnly cookie 里的会话令牌放进 x-sb-session 转交，
// 改变状态的请求再带上浏览器请求的 Origin 与（核对过“表单 = cookie”的）CSRF 令牌。
import { cookies } from 'next/headers';
import { bffBaseUrl, requestMeta } from './bff.ts';
import { csrfCookieName, isHex64, sessionCookieName } from './session-cookie.ts';

export interface Problem { status: number; code: string }
export type Result<T> = { ok: true; value: T } | { ok: false; problem: Problem };

export interface IssuedSession { account_kind: 'guardian' | 'student'; session_token: string; csrf_token: string; student_id?: string }
/** 家长的孩子列表：grade 是当前年级（BFF 按届别推算，迁移 0045）；不在 7–9 时为 null（界面显示“不在初中年级”） */
export type Me = { account_kind: 'guardian'; children: { student_id: string; grade: number | null }[] }
  | { account_kind: 'student'; student_id: string | null; grade?: number | null };

/** 当前请求带的会话令牌与 CSRF 令牌（格式不对当作没有）；是否有效由 BFF 解析 */
export async function currentSession(): Promise<{ token: string; csrf: string | null } | null> {
  const jar = await cookies();
  const token = jar.get(sessionCookieName())?.value;
  const csrf = jar.get(csrfCookieName())?.value;
  return isHex64(token) ? { token, csrf: isHex64(csrf) ? csrf : null } : null;
}

async function call<T>(method: 'GET' | 'POST', path: string, o: { token?: string; csrf?: string; body?: unknown } = {}): Promise<Result<T>> {
  const hdr: Record<string, string> = method === 'POST' ? await requestMeta() : {};
  if (o.token) hdr['x-sb-session'] = o.token;
  if (o.csrf) hdr['x-csrf-token'] = o.csrf;
  if (o.body !== undefined) hdr['content-type'] = 'application/json';
  const r = await fetch(`${bffBaseUrl()}/bff/v0${path}`, {
    method, headers: hdr, body: o.body === undefined ? undefined : JSON.stringify(o.body), cache: 'no-store', redirect: 'error',
  });
  const text = await r.text();
  let json: unknown = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON：按错误处理 */ }
  if (r.ok) return { ok: true, value: json as T };
  const code = json && typeof json === 'object' && typeof (json as { code?: unknown }).code === 'string' ? (json as { code: string }).code : 'INTERNAL';
  return { ok: false, problem: { status: r.status, code } };
}

export const requestLoginCode = (phone: string) => call<{ status: string }>('POST', '/auth/otp/request', { body: { phone } });

export const verifyLoginCode = (phone: string, code: string, prevToken?: string) =>
  call<IssuedSession>('POST', '/auth/otp/verify', { body: { phone, code }, ...(prevToken ? { token: prevToken } : {}) });

export const whoAmI = (token: string) => call<Me>('GET', '/me', { token });

export const logout = (token: string, csrf: string) => call<{ status: string }>('POST', '/auth/logout', { token, csrf });

export const openStudentMode = (token: string, csrf: string, studentId: string) =>
  call<IssuedSession>('POST', `/students/${studentId}/student-session`, { token, csrf });
