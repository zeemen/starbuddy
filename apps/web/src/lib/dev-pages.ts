// 开发专用页面（page.dev.tsx：探针演示、数学诊断演示、符号样张）的运行时关卡（H2）。
// 构建时的关卡在 next.config.ts（STARBUDDY_DEV_PAGES=1 且 dev / test 构建才有这些页面）；运行时再核对一次：
// 环境仍是 dev / test，且请求的 Host 是本机地址（挡 DNS 重绑定：恶意网页把自己的域名解析到 127.0.0.1，浏览器会当成同源）。
// 身份不在这里：演示页与正式页面一样只认真实会话（src/lib/session.ts）。
type Env = Record<string, string | undefined>;

export function isLoopbackHost(host: string | null | undefined): boolean {
  if (!host) return false;
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0];
  return ['127.0.0.1', 'localhost', '[::1]'].includes(name ?? '');
}

export const devRuntimeAllowed = (host: string | null | undefined, env: Env = process.env): boolean =>
  ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '') && isLoopbackHost(host);
