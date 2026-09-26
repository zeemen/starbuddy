// 星图数据：类型与 BFF 客户端（只在服务端调用；浏览器不直连 BFF、数据库或学习服务，spec 第 1 节）。
// 前端只用 BFF 给的 display / retained / needs_verification，不自己推算掌握状态（规则 2）。
// 响应结构见 services/bff/openapi/star-map.v0.yaml。
export type Display = 'unlit' | 'half' | 'lit';

export interface Star {
  skill: string;
  label_code: string;
  display: Display;
  retained: boolean;
  needs_verification: boolean;
}

export interface Sky {
  subject: string;
  enrollment_status: 'active' | 'paused';
  stars: Star[];
  summary: Record<Display, number>;
}

export interface StarMap {
  student_id: string;
  generated_at: string;
  skies: Sky[];
}

export type StarMapResult = { kind: 'ok'; map: StarMap } | { kind: 'unauthenticated' } | { kind: 'not-found' };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isUuid = (s: string): boolean => UUID_RE.test(s);

/** BFF 地址只能是 loopback 上的 http（开发原型；4-H 定部署拓扑） */
export function bffBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787';
  const u = new URL(raw);
  if (u.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) {
    throw new Error('STARBUDDY_BFF_URL 只能指向本机 loopback（开发原型）');
  }
  return u.origin;
}

export async function fetchStarMap(studentId: string, devAccount: string | null): Promise<StarMapResult> {
  if (!devAccount) return { kind: 'unauthenticated' };
  if (!isUuid(studentId)) return { kind: 'not-found' };
  const r = await fetch(`${bffBaseUrl()}/bff/v0/students/${studentId}/star-map`, {
    headers: { 'x-dev-account-id': devAccount },
    cache: 'no-store',
  });
  if (r.status === 401) return { kind: 'unauthenticated' };
  if (r.status === 404) return { kind: 'not-found' };
  if (!r.ok) throw new Error(`star map request failed: ${r.status}`);
  return { kind: 'ok', map: (await r.json()) as StarMap };
}
