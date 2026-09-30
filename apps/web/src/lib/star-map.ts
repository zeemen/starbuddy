// 星图数据：类型与 BFF 客户端（只在服务端调用；浏览器不直连 BFF、数据库或学习服务，spec 第 1 节）。
// 前端只用 BFF 给的 display / retained / needs_verification，不自己推算掌握状态（规则 2）。
// 响应结构见 services/bff/openapi/star-map.v0.yaml。身份只来自会话（H2）。
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
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

/** 以会话身份读星图（会话无效 401 → 去登录；未授权与不存在同一个 404） */
export async function fetchStarMap(studentId: string, session: BffSession | null): Promise<StarMapResult> {
  if (!session) return { kind: 'unauthenticated' };
  if (!isUuid(studentId)) return { kind: 'not-found' };
  const r = await fetch(`${bffBaseUrl()}/bff/v0/students/${studentId}/star-map`, {
    headers: await bffHeaders(session, false),
    cache: 'no-store',
  });
  if (r.status === 401) return { kind: 'unauthenticated' };
  if (r.status === 404) return { kind: 'not-found' };
  if (!r.ok) throw new Error(`star map request failed: ${r.status}`);
  return { kind: 'ok', map: (await r.json()) as StarMap };
}
