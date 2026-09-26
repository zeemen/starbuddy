// 开发身份桩：写入 httpOnly cookie 后跳回星图页。只在 STARBUDDY_DEV_AUTH=1 时存在，只接受合成账号。4-H 删除。
import { NextResponse, type NextRequest } from 'next/server';
import { SYNTHETIC_ACCOUNTS } from '../../../dev/accounts.ts';
import { DEV_COOKIE, devAuthEnabled, safeNext } from '../../../lib/dev-auth.ts';

export const dynamic = 'force-dynamic';

export function GET(req: NextRequest) {
  if (!devAuthEnabled()) return new NextResponse('Not Found', { status: 404 });
  const account = req.nextUrl.searchParams.get('account') ?? '';
  if (!SYNTHETIC_ACCOUNTS.some((a) => a.account_id === account)) {
    return new NextResponse('Bad Request', { status: 400 });
  }
  // 相对地址跳转：不信任 Host 头拼出的绝对地址（也避免 127.0.0.1 与 localhost 之间丢 cookie）
  const res = new NextResponse(null, { status: 303, headers: { Location: safeNext(req.nextUrl.searchParams.get('next')) } });
  res.cookies.set(DEV_COOKIE, account, { httpOnly: true, sameSite: 'strict', path: '/', secure: false });
  res.headers.set('Cache-Control', 'no-store');
  return res;
}
