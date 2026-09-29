// 开发身份桩：写入 httpOnly cookie 后跳回星图页（学生、家长的合成账号；审核台用真实 staff 登录，PR 4）。只在开发开关打开时存在，只接受合成账号。4-H 删除。
// Host 必须是本机地址（挡 DNS 重绑定）；cookie 8 小时过期。仍是 GET 登录（登录 CSRF 只能把浏览器登成合成账号），随 4-H 删除。
import { NextResponse, type NextRequest } from 'next/server';
import { SYNTHETIC_ACCOUNTS } from '../../../dev/accounts.ts';
import { DEV_COOKIE, DEV_COOKIE_MAX_AGE, devAuthEnabled, isLoopbackHost, safeNext } from '../../../lib/dev-auth.ts';

export const dynamic = 'force-dynamic';

const COOKIE = { httpOnly: true, sameSite: 'strict', path: '/', secure: false, maxAge: DEV_COOKIE_MAX_AGE } as const;

export function GET(req: NextRequest) {
  if (!devAuthEnabled() || !isLoopbackHost(req.headers.get('host'))) return new NextResponse('Not Found', { status: 404 });
  const account = req.nextUrl.searchParams.get('account') ?? '';
  if (!SYNTHETIC_ACCOUNTS.some((a) => a.account_id === account)) {
    return new NextResponse('Bad Request', { status: 400 });
  }
  // 相对地址跳转：不信任 Host 头拼出的绝对地址（也避免 127.0.0.1 与 localhost 之间丢 cookie）
  const res = new NextResponse(null, { status: 303, headers: { Location: safeNext(req.nextUrl.searchParams.get('next')) } });
  res.cookies.set(DEV_COOKIE, account, COOKIE);
  res.headers.set('Cache-Control', 'no-store');
  return res;
}
