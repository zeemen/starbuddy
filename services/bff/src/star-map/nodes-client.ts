// BFF → 学习服务的节点星态（packages/contracts/openapi/learning-internal.v1.yaml `POST /internal/v1/star-map/nodes`，1.1.0 草稿内追加，待签字）。
// 只读、非计量、不重试。鉴权同今日计划：Bearer 服务间令牌（BFF_LEARNING_TOKEN）、不跟随重定向、短超时（config/shell.json star_map.timeout_ms）。
// 学习服务完全相信 BFF 给的 student_ref（lessons PR 1 给 PR 2 的约束 1）：调用方必须先在 withAccount 里确认可读，student_ref 只取已校验的路径参数。
// 响应只挑出契约规定的字段（node_id、state、star_lit、basis_id），形状或一致性任何一处不合格就整科按不可用处理（不猜）。
// 不记录响应体、不缓存（每次请求现取；没有跨账号的共享状态）。

import type { MasteryState } from './display-state.ts';

export type StarMapSubject = 'aya' | 'wenli' | 'zhixing';
export interface StarNode { node_id: string; state: MasteryState; star_lit: boolean; basis_id: string | null }

export type StarNodesOutcome =
  | { kind: 'ok'; mapVersion: string; nodes: StarNode[] }
  /** 该科没有已发布地图（“地图建设中”） */
  | { kind: 'no_map' }
  /** 409 MASTERY_UNAVAILABLE：地图已发布但没有可用掌握配置（星全部中性，lessons PR 2） */
  | { kind: 'mastery_unavailable' }
  /** 超时、断连、其他状态码、响应不合格（星全部中性） */
  | { kind: 'unavailable' };

export interface StarMapNodesClient {
  nodes(studentRef: string, subject: StarMapSubject): Promise<StarNodesOutcome>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const MAP_VERSION = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;
const STATES: readonly MasteryState[] = ['unseen', 'understand', 'execute', 'stable', 'transfer', 'retain'];
const NODE_KEYS = 'basis_id,node_id,star_lit,state';
const BODY_KEYS = 'map_version,nodes,status,subject';

/** 按契约形状挑字段并核对一致性（basis 为空必是 unseen；star_lit 当且仅当 transfer / retain；no_map 不带版本与星）；不合格返回 unavailable */
export function pickNodes(body: unknown, subject: StarMapSubject): StarNodesOutcome {
  const bad: StarNodesOutcome = { kind: 'unavailable' };
  if (!body || typeof body !== 'object' || Array.isArray(body)) return bad;
  const b = body as Record<string, unknown>;
  if (Object.keys(b).sort().join(',') !== BODY_KEYS || b['subject'] !== subject || !Array.isArray(b['nodes'])) return bad;
  if (b['status'] === 'no_map') return b['map_version'] === null && b['nodes'].length === 0 ? { kind: 'no_map' } : bad;
  if (b['status'] !== 'ok' || typeof b['map_version'] !== 'string' || !MAP_VERSION.test(b['map_version']) || b['nodes'].length > 5000) return bad;
  const nodes: StarNode[] = [];
  const seen = new Set<string>();
  for (const raw of b['nodes'] as unknown[]) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return bad;
    const n = raw as Record<string, unknown>;
    const state = n['state'];
    const basis = n['basis_id'];
    if (Object.keys(n).sort().join(',') !== NODE_KEYS || typeof n['node_id'] !== 'string' || !NODE_ID.test(n['node_id']) || seen.has(n['node_id'])
        || typeof state !== 'string' || !(STATES as readonly string[]).includes(state) || typeof n['star_lit'] !== 'boolean'
        || (basis !== null && (typeof basis !== 'string' || !UUID.test(basis)))
        || (basis === null && state !== 'unseen') || n['star_lit'] !== (state === 'transfer' || state === 'retain')) {
      return bad;
    }
    seen.add(n['node_id']);
    nodes.push({ node_id: n['node_id'], state: state as MasteryState, star_lit: n['star_lit'], basis_id: basis as string | null });
  }
  return { kind: 'ok', mapVersion: b['map_version'], nodes };
}

/** 响应体上限：5000 颗星每颗约 130 字节，留足余量；超过即整科不可用（先看 content-length，再在流式读取中累计） */
export const MAX_BODY_BYTES = 2 * 1024 * 1024;

/** 按上限读 JSON：超限、非 JSON 返回 undefined（不保留已读内容） */
export async function readCappedJson(res: Response, cap = MAX_BODY_BYTES): Promise<unknown> {
  const declared = Number(res.headers.get('content-length') ?? NaN);
  if (Number.isFinite(declared) && declared > cap) {
    await res.body?.cancel();
    return undefined;
  }
  if (!res.body) return undefined;
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > cap) {
      await reader.cancel();
      return undefined;
    }
    chunks.push(value);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
  } catch {
    return undefined;
  }
}

export interface HttpStarMapOptions { baseUrl: string; token: string; timeoutMs: number; fetchImpl?: typeof fetch; maxBodyBytes?: number }

export function httpStarMapClient(opts: HttpStarMapOptions): StarMapNodesClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/star-map/nodes`;
  return {
    async nodes(studentRef, subject) {
      try {
        const res = await doFetch(url, {
          method: 'POST',
          headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
          body: JSON.stringify({ request_id: crypto.randomUUID(), caller: 'bff', student_ref: studentRef, subject }),
          signal: AbortSignal.timeout(opts.timeoutMs),
          redirect: 'error',
        });
        if (res.status === 409) {
          const p = (await readCappedJson(res, 64 * 1024)) as { code?: unknown } | undefined;
          return p?.code === 'MASTERY_UNAVAILABLE' ? { kind: 'mastery_unavailable' } : { kind: 'unavailable' };
        }
        if (res.status !== 200) {
          await res.body?.cancel();
          return { kind: 'unavailable' };
        }
        return pickNodes(await readCappedJson(res, opts.maxBodyBytes ?? MAX_BODY_BYTES), subject);
      } catch {
        return { kind: 'unavailable' };  // 超时、断连、非 JSON：不记消息与响应体（可能带出数据）
      }
    },
  };
}
