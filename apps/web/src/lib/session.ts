// 页面与 server action 取当前会话（H2：开发身份桩删除后，星图、建档、演示页都走这里）。
// - 页面（服务端组件）：pageSession() 读 cookie 里的会话，问 BFF 这是谁（/me）；没有或无效时返回 null，由页面决定去登录页；
// - server action：actionSession(posted) 另核对表单（或客户端组件参数）带来的 CSRF 令牌等于 CSRF cookie（常量时间比较），
//   不一致返回 null；BFF 还会核对它与会话绑定（双重提交），并核对浏览器的 Origin。
import { timingSafeEqual } from 'node:crypto';
import type { BffSession } from './bff.ts';
import { currentSession, whoAmI, type Me } from './shell-auth.ts';

export interface PageSession { session: BffSession; me: Me }

export async function pageSession(): Promise<PageSession | null> {
  const s = await currentSession();
  if (!s) return null;
  const me = await whoAmI(s.token);
  if (!me.ok) return null;
  return { session: { token: s.token, csrf: s.csrf ?? '' }, me: me.value };
}

export async function actionSession(posted: unknown): Promise<BffSession | null> {
  const s = await currentSession();
  if (!s || !s.csrf || typeof posted !== 'string') return null;
  const a = Buffer.from(posted, 'utf8');
  const b = Buffer.from(s.csrf, 'utf8');
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  return { token: s.token, csrf: s.csrf };
}
