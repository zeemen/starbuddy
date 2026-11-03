// 家庭会话 cookie 的名字与属性（纯函数，单测直接引用；读写在 shell-session.ts 与 server action 里）。
// spec 第 2 节：__Host-sb_session，HttpOnly; Secure; SameSite=Lax; Path=/，不设 Domain；
// 本地 http 开发与测试（STARBUDDY_ENV 为 dev / test）用不带前缀的名字、不带 Secure（浏览器不给 http 页面存 Secure cookie）。
// CSRF 令牌（与会话绑定，BFF 派生）存在同样属性的另一个 cookie 里；页面把它放进表单的隐藏字段，
// server action 核对“表单 = cookie”后转交 BFF（双重提交）。两个 cookie 浏览器脚本都读不到。
type Env = Record<string, string | undefined>;

const isLocalEnv = (env: Env): boolean => ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

export const sessionCookieName = (env: Env = process.env): string => (isLocalEnv(env) ? 'sb_session' : '__Host-sb_session');
export const csrfCookieName = (env: Env = process.env): string => (isLocalEnv(env) ? 'sb_csrf' : '__Host-sb_csrf');

/** 有效期与库里的会话绝对时长一致（7 天，services/bff/config/auth.json）；过期以库里为准 */
export const SESSION_MAX_AGE = 7 * 24 * 60 * 60;

export const sessionCookieOptions = (env: Env = process.env) =>
  ({ httpOnly: true, sameSite: 'lax', path: '/', secure: !isLocalEnv(env), maxAge: SESSION_MAX_AGE }) as const;

export const isHex64 = (s: string | undefined | null): s is string => typeof s === 'string' && /^[0-9a-f]{64}$/.test(s);

/** 手机号：界面只收中国大陆 11 位手机号或完整的 E.164（测试用 +999 合成号码），统一成 E.164 */
export function normalizePhone(input: string): string | null {
  const s = input.replace(/[\s-]/g, '');
  if (/^1[3-9][0-9]{9}$/.test(s)) return `+86${s}`;
  if (/^\+[1-9][0-9]{6,14}$/.test(s)) return s;
  return null;
}
