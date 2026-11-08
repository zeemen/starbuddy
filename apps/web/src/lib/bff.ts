// BFF 客户端的公共部分（只在服务端调用：服务端组件与 server action；浏览器不直连 BFF，spec 第 1 节）。
// 身份只来自会话（spec 第 4 节，H2）：把 HttpOnly cookie 里的会话令牌放进 x-sb-session 转交；改变状态的请求另带浏览器请求的
// Origin、客户端地址与（核对过“表单 = cookie”的）CSRF 令牌。前端不判断身份与授权，BFF 解析会话、数据库判断授权。

type Env = Record<string, string | undefined>;

export interface BffSession { token: string; csrf: string }

const LOOPBACK = ['127.0.0.1', 'localhost', '[::1]'];
/** 内网地址（RFC 1918 的 IPv4 字面量）；staging、pilot、生产的 BFF 只能在内网 */
const PRIVATE = /^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$/;

/**
 * BFF 地址白名单（spec 第 4 节）：开发与测试（STARBUDDY_ENV 为 dev / test，或没设）只允许本机 loopback 的 http；
 * staging、pilot、生产只允许内网地址（http 或 https）。其余一律拒绝，不连任何公网地址。
 */
export function bffBaseUrl(env: Env = process.env): string {
  const raw = env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787';
  const u = new URL(raw);
  const run = env['STARBUDDY_ENV'] ?? '';
  const local = run === '' || run === 'dev' || run === 'test';
  const ok = local ? u.protocol === 'http:' && LOOPBACK.includes(u.hostname)
    : ['staging', 'pilot', 'production'].includes(run) && (u.protocol === 'http:' || u.protocol === 'https:') && PRIVATE.test(u.hostname);
  if (!ok || u.username || u.password || (u.pathname !== '/' && u.pathname !== '')) {
    throw new Error(local ? 'STARBUDDY_BFF_URL 在开发与测试环境只能指向本机 loopback 的 http'
                          : 'STARBUDDY_BFF_URL 在 staging、pilot、生产只能指向内网地址');
  }
  return u.origin;
}

/**
 * 转交给 BFF 的客户端地址（T46，specs/tech-debt-batch2）：只在 WEB_TRUST_CLIENT_IP=1 时转交，且只取网关覆盖写入的 X-Real-IP；
 * 不取 X-Forwarded-For（第一段可被客户端伪造）。缺省不转交（BFF 在 staging 也默认不信，BFF_TRUST_CLIENT_IP=0）。
 * 打开它是发布检查项：网关必须覆盖写 X-Real-IP，前端只对网关开放（deploy/config/README.md S5）。
 */
export function clientIpFor(get: (name: string) => string | null, env: Env = process.env): string | null {
  if (env['WEB_TRUST_CLIENT_IP'] !== '1') return null;
  const ip = get('x-real-ip')?.trim() ?? '';
  return ip && ip.length <= 64 && /^[0-9A-Fa-f:.]+$/.test(ip) ? ip : null;
}

/** 浏览器请求的 Origin 与客户端地址（转交 BFF 核对；这里不判断身份） */
export async function requestMeta(): Promise<Record<string, string>> {
  // 按需加载：本文件的地址白名单与只读请求在单测里直接引用（Node 解析不了 next/headers 的包内路径）
  const { headers } = await import('next/headers');
  const h = await headers();
  const out: Record<string, string> = {};
  const origin = h.get('origin');
  if (origin) out['origin'] = origin;
  const ip = clientIpFor((n) => h.get(n));
  if (ip) out['x-sb-client-ip'] = ip;
  return out;
}

/** 以会话身份调 BFF 的请求头；改变状态的请求（write）另带 Origin、客户端地址与 CSRF 令牌 */
export async function bffHeaders(s: BffSession, write: boolean): Promise<Record<string, string>> {
  return { ...(write ? await requestMeta() : {}), 'x-sb-session': s.token, ...(write ? { 'x-csrf-token': s.csrf } : {}) };
}
