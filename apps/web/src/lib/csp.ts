// 安全响应头（spec 第 16 节，H2）：纯函数，src/proxy.ts 调用，单测直接引用。
// CSP：每个请求一个 nonce；脚本与样式只认 'self' 与本次的 nonce（脚本另加 'strict-dynamic'），不允许 'unsafe-inline'；
// 生产构建不允许 'unsafe-eval'（只有 next dev 需要，React 开发版用 eval 还原报错栈）。没有任何外部来源，iframe 一律不许。
// HSTS：只在 staging、pilot（与以后的生产）发送，max-age 一年（≥ 180 天）；本地 http 开发与测试不发（浏览器会记住，影响本机其他端口）。
type Env = Record<string, string | undefined>;

export function contentSecurityPolicy(nonce: string, opts: { dev?: boolean } = {}): string {
  if (!/^[A-Za-z0-9+/=_-]{16,}$/.test(nonce)) throw new Error('nonce must be base64, at least 16 characters');
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${opts.dev ? " 'unsafe-eval'" : ''}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' data:",
    "connect-src 'self'",
    "font-src 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join('; ');
}

export const HSTS = 'max-age=31536000; includeSubDomains';

/** 只在部署到 https 的环境发送 HSTS */
export const hstsEnabled = (env: Env = process.env): boolean => ['staging', 'pilot', 'production'].includes(env['STARBUDDY_ENV'] ?? '');

/** 每个请求一个 16 字节随机 nonce（base64） */
export function newNonce(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return btoa(String.fromCharCode(...b));
}
