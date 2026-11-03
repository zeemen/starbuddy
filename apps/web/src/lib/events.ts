// 行为事件与探针的 BFF 客户端（冲刺 2-L；只在服务端调用，浏览器不直连 BFF）。接口见 services/bff/openapi/events.v0.yaml。
// 只写不读：BFF 只回记录 ID，前端也不展示任何事件或探针（只给 Agent 用，spec 设计要点 4）。
import type { Prediction } from '../probe/copy.ts';
import { bffBaseUrl, bffHeaders, type BffSession } from './bff.ts';
import { isUuid } from './star-map.ts';

/** 演示页用的合成题（db/seeds/dev/star-map-synthetic.sql 里的合成道法题，不是教研内容） */
export const DEMO_PROBE_ITEM = { subject: 'zhixing', item_id: 'SYN-ZX-ITEM-01' } as const;

export type SessionEvent =
  | { kind: 'session_start'; payload: { entry: 'today' | 'star_map' | 'subject' | 'onboarding' | 'other' } }
  | { kind: 'session_end'; payload: { duration_sec: number } };
/** 今日计划“完成”“先跳过”（PLT-15 PR2）：只由今日页的 server action 写（src/app/s/[studentId]/actions.ts）；
 *  plan_done 只表示学生自己点了完成，不是核实过的完成（规则 6） */
export type PlanEvent = { kind: 'plan_done' | 'plan_skip'; payload: { plan_task_id: string } };
export type BehaviorEvent = SessionEvent | PlanEvent;

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const isKey = (s: unknown): s is string => typeof s === 'string' && UUID_V4.test(s);

async function post(account: BffSession, student: string, sub: string, body: unknown): Promise<{ ok: boolean; status: number }> {
  if (!isUuid(student)) return { ok: false, status: 403 };
  const r = await fetch(`${bffBaseUrl()}/bff/v0/students/${student}/${sub}`, {
    method: 'POST',
    headers: { ...(await bffHeaders(account, true)), 'content-type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
    signal: AbortSignal.timeout(10_000),
  });
  await r.body?.cancel();  // 只关心是否写入；不读、不转交记录 ID
  return { ok: r.ok, status: r.status };
}

export const postSessionEvent = (account: BffSession, student: string, e: BehaviorEvent, key: string, occurredAt = new Date()) =>
  post(account, student, 'events', { kind: e.kind, payload: e.payload, occurred_at: occurredAt.toISOString(), idempotency_key: key });

/** 按事件类别与结果类别（2xx / 4xx / 5xx / network）累计的写入次数；只在本进程内，日志不含学生 ID 与幂等键 */
const writeCounts = new Map<string, number>();
function countWrite(kind: string, result: string): void {
  const k = `${kind}:${result}`;
  const n = (writeCounts.get(k) ?? 0) + 1;
  writeCounts.set(k, n);
  console.info(`behavior_event write kind=${kind} result=${result} count=${n}`);
}
export const behaviorWriteCounts = (): ReadonlyMap<string, number> => writeCounts;

/**
 * 学生壳真实页面用（SIE-05 PR2）：对学生静默——BFF 不可达、超时都当“没写入”，不抛错、不重试（调用方也不重试）；
 * 409（同一幂等键已记过）算已记下。服务端按结果类别计数写日志。BFF 地址配置错误不吞掉（照常抛出）。
 */
export async function postEventQuietly(account: BffSession, student: string, e: BehaviorEvent, key: string,
  occurredAt = new Date()): Promise<boolean> {
  bffBaseUrl();  // 配置错误在这里抛出，不当作“没写入”
  try {
    const r = await postSessionEvent(account, student, e, key, occurredAt);
    // 403（不是本人）与 422（取值不合要求，含任务 ID 不属于该生当天快照）单独计，其余按类别（审查建议；不带学生 ID 与任务 ID）
    countWrite(e.kind, r.status === 403 || r.status === 422 ? String(r.status) : `${Math.floor(r.status / 100)}xx`);
    return r.ok || r.status === 409;
  } catch {
    countWrite(e.kind, 'network');
    return false;
  }
}

export const postProbe = (account: BffSession, student: string, item: { subject: string; item_id: string }, prediction: Prediction, key: string,
  askedAt = new Date()) =>
  post(account, student, 'probes', { subject: item.subject, item_id: item.item_id, prediction, asked_at: askedAt.toISOString(), idempotency_key: key });
