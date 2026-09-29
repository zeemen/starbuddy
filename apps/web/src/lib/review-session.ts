// 审核台的 staff 会话（PR 4，specs/release-gate-sprint4/pr4-staff-login.md）：只在服务端调用。
// 会话令牌放在 httpOnly、SameSite=Strict 的 cookie 里；前端不判断身份，只把令牌转交审核台后端（请求头 x-review-session），
// 由库函数 platform.staff_session_resolve 解析。
import { cookies } from 'next/headers';
import { isSessionToken, staffCookieName } from './staff-cookie.ts';

export { isSessionToken, staffCookieName, staffCookieOptions } from './staff-cookie.ts';

/** 当前请求带的会话令牌（格式不对当作没有）；是否有效由后端解析 */
export async function currentSession(): Promise<string | null> {
  const v = (await cookies()).get(staffCookieName())?.value;
  return isSessionToken(v) ? v : null;
}
