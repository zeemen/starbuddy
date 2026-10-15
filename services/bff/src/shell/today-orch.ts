// 今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11/spec.md 范围第 2 条）：开关与学习服务客户端。
// 开关 STARBUDDY_TODAY_ORCH=today_orch，只在 dev / test 生效；其余环境设了就拒绝启动（main.ts），缺省关（做法同 src/retest/features.ts）。
// 开关打开时 GET /bff/v0/students/{id}/today 对学生本人只调学习服务 /internal/v1/today（每页只排一次计划）：按科的 sections 用它附带的
// 各科计划构造（与 /internal/v1/plan 同一套挑字段与排序），另返回跨科有序列表 ordered。开关关、家长会话时只调 /internal/v1/plan，
// 响应与开关上线前逐字段一致。家长端不返回 ordered（家长端跨科列表的可见范围待人确认，spec“不做”）。
// 学习服务接口：services/learning/openapi/today-internal.v0.yaml（只读汇总，非消耗类：不预留、不结算、不重试）。
// 鉴权同今日计划：Bearer 服务间令牌、不跟随重定向、带超时。响应只挑出接口规定的字段，任何一条不合格就整张列表按不可用处理（不猜）；
// 学习服务多给的字段（掌握、置信度、证据 ID 等）一律不进 BFF 的响应（规则 11）。
// 今日计划快照（PLT-15 PR2，specs/today-plan-snapshot-plt15 范围第 3 条）：学习服务开了 LEARNING_TODAY_SNAPSHOT 时响应带 snapshot，
// frozen 时每条带 task_id（快照行 ID）；BFF 照样透出给学生本人（前端据此显示“完成”“先跳过”），家长会话本来就没有 ordered。
import { isLocalEnv, type RunEnv } from '../http/config.ts';
import { pickSections, type PlanSection, type PlanSubject } from './plan-client.ts';

export const TODAY_ORCH_FEATURE = 'today_orch';

/** 解析开关；非 dev / test 设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseTodayOrchFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!isLocalEnv(env)) throw new Error('STARBUDDY_TODAY_ORCH 只在 dev / test 可用（今日编排的数值与文案签字之前 staging 以上一律关闭）');
  if (raw.trim() !== TODAY_ORCH_FEATURE) throw new Error(`STARBUDDY_TODAY_ORCH 只认 ${TODAY_ORCH_FEATURE}`);
  return true;
}

export type OrderedSource = 'retest' | 'practice' | 'plan';
export interface OrderedItem {
  subject: PlanSubject; kind: string; node_id: string | null; estimated_minutes: number; source: OrderedSource;
  /** 可去做的条目（复测、再练列表的 entry_id）；计划任务为空 */
  entry_id: string | null;
  /** 计划任务原有的推荐理由编码（BFF 按既有两类机制与白名单处理，不外露）；复测、再练为空 */
  rationale_code: string | null;
  /** 今日计划快照的任务 ID（PLT-15，即 plan_done / plan_skip 的 plan_task_id）：只在 snapshot = frozen 时有，且每条都有 */
  task_id?: string;
}
/** 学习服务的快照状态（PLT-15）：frozen = 条目取自当天快照、每条带 task_id；not_frozen = 没冻结、不带 task_id；
 *  学习服务快照开关关着时没有这个字段（undefined），BFF 响应也不带（与开关上线前逐字段一致） */
export type SnapshotState = 'frozen' | 'not_frozen';
export interface OrderedList { items: OrderedItem[]; truncated_count: number; partial: boolean; snapshot?: SnapshotState }

export interface TodayOrchClient {
  /** 学习服务不可用（超时、非 200、断连）时返回 null：调用方每科“今天暂无安排”、不显示跨科列表；
   *  响应里某一部分不合格时那一部分为 null（按科计划不合格 = 每科暂无安排；跨科列表不合格 = 不显示） */
  today(studentRef: string, subjects: PlanSubject[]): Promise<TodayResult | null>;
}

const KINDS = new Set(['retest', 'verify', 'practice', 'diagnose', 'review', 'lesson', 'mock_exam']);
const SOURCES = new Set<OrderedSource>(['retest', 'practice', 'plan']);
const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const CODE = /^[a-z0-9_]{1,48}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MAX_ITEMS = 200;

/** 按接口形状挑字段；任何一条不合格就整张列表按不可用处理 */
export function pickOrdered(body: unknown, asked: PlanSubject[]): OrderedList | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  const raw = b['items'];
  const cut = b['truncated_count'];
  const sources = b['sources'];
  const snapshot = b['snapshot'];
  if (!Array.isArray(raw) || raw.length > MAX_ITEMS || typeof cut !== 'number' || !Number.isInteger(cut) || cut < 0 || !Array.isArray(sources)) return null;
  // 快照状态（PLT-15）：没有这个字段 = 学习服务快照开关关；有就只认 frozen / not_frozen
  if (snapshot !== undefined && snapshot !== 'frozen' && snapshot !== 'not_frozen') return null;
  const frozen = snapshot === 'frozen';
  const taskIds = new Set<string>();
  const items: OrderedItem[] = [];
  for (const t of raw) {
    if (!t || typeof t !== 'object') return null;
    const x = t as Record<string, unknown>;
    const source = x['source'];
    const entry = x['entry_id'];
    const code = x['rationale_code'];
    const node = x['node_id'];
    const minutes = x['estimated_minutes'];
    if (typeof x['subject'] !== 'string' || !(asked as string[]).includes(x['subject'])
        || typeof x['kind'] !== 'string' || !KINDS.has(x['kind'])
        || typeof source !== 'string' || !SOURCES.has(source as OrderedSource)
        || !(node === null || (typeof node === 'string' && NODE_ID.test(node)))
        || typeof minutes !== 'number' || !Number.isInteger(minutes) || minutes < 1 || minutes > 600
        // 复测、再练必须带可去做的条目引用、不带理由编码；计划任务反之
        || (source === 'plan' ? entry !== null || !(code === null || (typeof code === 'string' && CODE.test(code)))
          : typeof entry !== 'string' || !UUID.test(entry) || code !== null)) {
      return null;
    }
    // 任务 ID：frozen 时每条都必须有、是 UUID、互不相同；其余情况一条都不许有（不合格整张列表按不可用处理）
    const task = x['task_id'];
    if (frozen ? typeof task !== 'string' || !UUID.test(task) || taskIds.has(task) : task !== undefined) return null;
    if (frozen) taskIds.add(task as string);
    items.push({ subject: x['subject'] as PlanSubject, kind: x['kind'], node_id: node as string | null, estimated_minutes: minutes,
                 source: source as OrderedSource, entry_id: entry as string | null, rationale_code: code as string | null,
                 ...(frozen ? { task_id: task as string } : {}) });
  }
  // partial 只算“启用了但失败”（source_unavailable）；未启用（source_disabled）不算。认不出的状态从严当失败
  const partial = sources.some((s) => {
    const st = s && typeof s === 'object' ? (s as { status?: unknown }).status : undefined;
    return st !== 'ok' && st !== 'source_disabled';
  });
  return { items, truncated_count: cut, partial, ...(snapshot !== undefined ? { snapshot } : {}) };
}

/** /internal/v1/today 的结果：按科计划（与 /plan 同一套挑字段，pickSections）与跨科列表，两者各自独立判合格 */
export interface TodayResult { sections: PlanSection[] | null; ordered: OrderedList | null }

export function pickToday(body: unknown, asked: PlanSubject[]): TodayResult {
  const plan = body && typeof body === 'object' ? (body as { plan?: unknown }).plan : undefined;
  return { sections: plan ? pickSections(plan, asked) : null, ordered: pickOrdered(body, asked) };
}

export interface HttpTodayOrchOptions { baseUrl: string; token: string; timeoutMs: number; fetchImpl?: typeof fetch }

export function httpTodayOrchClient(opts: HttpTodayOrchOptions): TodayOrchClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/today`;
  return {
    async today(studentRef, subjects) {
      try {
        const res = await doFetch(url, {
          method: 'POST',
          headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
          body: JSON.stringify({ request_id: crypto.randomUUID(), student_ref: studentRef, subjects }),
          signal: AbortSignal.timeout(opts.timeoutMs),
          redirect: 'error',
        });
        if (res.status !== 200) {
          await res.body?.cancel();
          return null;
        }
        return pickToday(await res.json(), subjects);
      } catch {
        return null;  // 超时、断连、非 JSON：不记消息（可能带出数据）
      }
    },
  };
}
