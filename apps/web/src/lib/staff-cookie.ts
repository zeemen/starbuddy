// 审核台 staff 会话 cookie 的名字与属性（纯函数，单测可直接引用；读写 cookie 在 review-session.ts 与 server action 里）。
// 正式环境用 __Host- 前缀（要求 Secure、Path=/、不设 Domain）；本地 http 开发与测试（STARBUDDY_ENV 为 dev / test）用不带前缀的名字。
type Env = Record<string, string | undefined>;

const isLocalEnv = (env: Env): boolean => ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

export const staffCookieName = (env: Env = process.env): string => (isLocalEnv(env) ? 'sb_staff' : '__Host-sb_staff');

/** 会话 cookie 的属性：有效期与库里的会话绝对时长一致（12 小时） */
export const staffCookieOptions = (env: Env = process.env) =>
  ({ httpOnly: true, sameSite: 'strict', path: '/', secure: !isLocalEnv(env), maxAge: 12 * 60 * 60 }) as const;

export const isSessionToken = (s: string | undefined): s is string => typeof s === 'string' && /^[0-9a-f]{64}$/.test(s);
