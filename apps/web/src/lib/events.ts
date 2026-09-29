// 行为事件与探针的 BFF 客户端（冲刺 2-L；只在服务端调用，浏览器不直连 BFF）。接口见 services/bff/openapi/events.v0.yaml。
// 只写不读：BFF 只回记录 ID，前端也不展示任何事件或探针（只给 Agent 用，spec 设计要点 4）。
import type { Prediction } from '../probe/copy.ts';
import { bffBaseUrl, isUuid } from './star-map.ts';

/** 演示页用的合成题（db/seeds/dev/star-map-synthetic.sql 里的合成道法题，不是教研内容） */
export const DEMO_PROBE_ITEM = { subject: 'zhixing', item_id: 'SYN-ZX-ITEM-01' } as const;

export type SessionEvent =
  | { kind: 'session_start'; payload: { entry: 'today' | 'star_map' | 'subject' | 'onboarding' | 'other' } }
  | { kind: 'session_end'; payload: { duration_sec: number } };

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isKey = (s: unknown): s is string => typeof s === 'string' && UUID_V4.test(s);

async function post(account: string, student: string, sub: string, body: unknown): Promise<{ ok: boolean; status: number }> {
  if (!isUuid(student)) return { ok: false, status: 403 };
  const r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/${sub}`, {
    method: 'POST',
    headers: { 'x-dev-account-id': account, 'content-type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
    signal: AbortSignal.timeout(10_000),
  });
  await r.body?.cancel();  // 只关心是否写入；不读、不转交记录 ID
  return { ok: r.ok, status: r.status };
}

export const postSessionEvent = (account: string, student: string, e: SessionEvent, key: string, occurredAt = new Date()) =>
  post(account, student, 'events', { kind: e.kind, payload: e.payload, occurred_at: occurredAt.toISOString(), idempotency_key: key });

export const postProbe = (account: string, student: string, item: { subject: string; item_id: string }, prediction: Prediction, key: string,
  askedAt = new Date()) =>
  post(account, student, 'probes', { subject: item.subject, item_id: item.item_id, prediction, asked_at: askedAt.toISOString(), idempotency_key: key });
