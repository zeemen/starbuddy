// 请求代理（Next 16 的 proxy，原 middleware；spec 第 16 节，H2）：为每个页面请求生成 CSP nonce。
// 把 CSP 同时放进请求头（Next 渲染时从中取出 nonce，加到自己的脚本与内联样式上）与响应头；staging、pilot 另发 HSTS。
// 其余固定的安全头（nosniff、X-Frame-Options、Referrer-Policy、Permissions-Policy）在 next.config.ts，对静态资源也生效。
// 只做头部，不判断身份与授权（身份只在 BFF 一处解析）。
import { NextResponse, type NextRequest } from 'next/server';
import { contentSecurityPolicy, HSTS, hstsEnabled, newNonce } from './lib/csp.ts';

export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const csp = contentSecurityPolicy(nonce, { dev: process.env.NODE_ENV === 'development' });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  if (hstsEnabled()) response.headers.set('Strict-Transport-Security', HSTS);
  return response;
}

export const config = {
  // 静态资源（带哈希、不含内联脚本）不需要 nonce；其余一律经过这里
  matcher: [{ source: '/((?!_next/static|_next/image|favicon.ico).*)' }],
};
