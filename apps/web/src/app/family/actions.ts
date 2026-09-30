'use server';
// 家长首页的写操作（H1）：在本设备打开学生模式、退出登录。
// 双重提交：表单隐藏字段里的 CSRF 令牌必须等于 CSRF cookie（常量时间比较），再连同浏览器的 Origin 转交 BFF，
// BFF 还要核对它与会话绑定。打开学生模式成功后，本设备的会话 cookie 换成学生会话（家长会话在库里同时作废）。
import { timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import type { FormResult } from '../../auth/ResultForm.tsx';
import { csrfCookieName, sessionCookieName, sessionCookieOptions } from '../../lib/session-cookie.ts';
import { currentSession, logout, openStudentMode } from '../../lib/shell-auth.ts';
import { isUuid } from '../../lib/star-map.ts';

const text = (f: FormData, k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string) : '');

/** 表单里的 CSRF 令牌与 cookie 一致才返回会话 */
async function sessionFor(f: FormData): Promise<{ token: string; csrf: string } | null> {
  const s = await currentSession();
  const posted = Buffer.from(text(f, 'csrf'), 'utf8');
  if (!s || !s.csrf) return null;
  const expected = Buffer.from(s.csrf, 'utf8');
  if (posted.length !== expected.length || !timingSafeEqual(posted, expected)) return null;
  return { token: s.token, csrf: s.csrf };
}

async function clearCookies(): Promise<void> {
  const jar = await cookies();
  // 删除要带与写入时相同的属性（__Host- 前缀要求 Secure、Path=/，否则浏览器不认这次删除）
  jar.set(sessionCookieName(), '', { ...sessionCookieOptions(), maxAge: 0 });
  jar.set(csrfCookieName(), '', { ...sessionCookieOptions(), maxAge: 0 });
}

export async function openStudentModeAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await sessionFor(f);
  if (!s) return { code: 'CSRF' };
  const student = text(f, 'student');
  if (!isUuid(student)) return { code: 'NOT_FOUND' };
  const r = await openStudentMode(s.token, s.csrf, student);
  if (!r.ok) {
    if (r.problem.code === 'UNAUTHENTICATED') redirect('/login');
    return { code: r.problem.code };
  }
  const jar = await cookies();
  jar.set(sessionCookieName(), r.value.session_token, sessionCookieOptions());
  jar.set(csrfCookieName(), r.value.csrf_token, sessionCookieOptions());
  redirect('/student');
}

/** 退出（家长退出登录、孩子退出学生模式）：撤销库里的会话，删 cookie，回登录页 */
export async function logoutAction(_: FormResult | null, f: FormData): Promise<FormResult> {
  const s = await sessionFor(f);
  if (!s) return { code: 'CSRF' };
  await logout(s.token, s.csrf);
  await clearCookies();
  redirect('/login');
}
