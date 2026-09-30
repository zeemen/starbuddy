'use server';
// 家长登录（spec 第 2 节）：两步——发验证码、核对验证码。server action 自带同源校验（Origin 必须与 Host 一致），
// 另把浏览器的 Origin 原样转交 BFF 再核对一次。登录成功写 HttpOnly 会话 cookie 与 CSRF cookie，进家长首页。
// 失败只回结果码（不区分原因，不回显输入）；验证码只经表单到 BFF，不进 cookie、URL 与日志。
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { csrfCookieName, normalizePhone, sessionCookieName, sessionCookieOptions } from '../../lib/session-cookie.ts';
import { currentSession, requestLoginCode, verifyLoginCode } from '../../lib/shell-auth.ts';

export interface LoginState { step: 'phone' | 'code'; phone: string; code: string }

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string) : '');

export async function loginAction(prev: LoginState | null, f: FormData): Promise<LoginState> {
  const op = text(f, 'op');
  if (op === 'change') return { step: 'phone', phone: '', code: '' };
  const phone = normalizePhone(text(f, 'phone'));
  if (!phone) return { step: 'phone', phone: '', code: 'INVALID_PHONE' };
  if (op === 'send') {
    const r = await requestLoginCode(phone);
    if (!r.ok) return { step: 'phone', phone, code: r.problem.code === 'INVALID' ? 'INVALID_PHONE' : r.problem.code };
    return { step: 'code', phone, code: 'SENT' };
  }
  const code = text(f, 'code').trim();
  if (!/^[0-9]{6}$/.test(code)) return { step: 'code', phone, code: 'LOGIN_FAILED' };
  // 本设备原来的会话（如果有）一并交给 BFF：登录成功后作废（防会话固定）
  const prevSession = await currentSession();
  const r = await verifyLoginCode(phone, code, prevSession?.token);
  if (!r.ok) return { step: 'code', phone, code: r.problem.code };
  const jar = await cookies();
  jar.set(sessionCookieName(), r.value.session_token, sessionCookieOptions());
  jar.set(csrfCookieName(), r.value.csrf_token, sessionCookieOptions());
  redirect('/family');
}
