// BFF → 学习服务的今日计划（packages/contracts/openapi/learning-internal.v1.yaml `POST /internal/v1/plan`，契约变更待签字）。
// 非消耗类：不预留、不结算、不重试（失败时这一科显示“今天暂无安排”）。鉴权同诊断：Bearer 服务间令牌、不跟随重定向、带超时。
// 响应只挑出契约规定的字段（kind、target_node_ids、priority、estimated_minutes、rationale_code），其余一律丢弃：
// 即使学习服务多给了 p_mastery、confidence、evidence_ids、learner_context，也不会进 BFF 的响应（规则 11）。

export type PlanSubject = 'aya' | 'wenli' | 'zhixing';
export interface PlanTask { kind: string; target_node_ids: string[]; priority: number; estimated_minutes: number; rationale_code: string }
export interface PlanSection { subject: PlanSubject; status: 'ok' | 'unavailable'; tasks: PlanTask[] }

export interface PlanClient {
  /** 学习服务整体不可用（超时、5xx、断连、响应不合格）时返回 null：调用方把每一科都显示为“今天暂无安排” */
  plan(studentRef: string, subjects: PlanSubject[]): Promise<PlanSection[] | null>;
}

const KINDS = new Set(['diagnose', 'practice', 'review', 'lesson', 'retest', 'mock_exam']);
const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const CODE = /^[a-z0-9_]{1,48}$/;

/** 按契约形状挑字段；任何一条不合格就整科按不可用处理（不猜） */
export function pickSections(body: unknown, asked: PlanSubject[]): PlanSection[] | null {
  if (!body || typeof body !== 'object' || !Array.isArray((body as { subjects?: unknown }).subjects)) return null;
  const out: PlanSection[] = [];
  for (const subject of asked) {
    const raw = ((body as { subjects: unknown[] }).subjects).find((s) => s && typeof s === 'object' && (s as { subject?: unknown }).subject === subject) as
      { status?: unknown; tasks?: unknown } | undefined;
    const tasks: PlanTask[] = [];
    let ok = raw?.status === 'ok' && Array.isArray(raw.tasks) && raw.tasks.length <= 50;
    for (const t of ok ? (raw!.tasks as unknown[]) : []) {
      const x = t as Record<string, unknown>;
      const ids = x['target_node_ids'];
      if (typeof x['kind'] !== 'string' || !KINDS.has(x['kind']) || !Array.isArray(ids) || ids.length > 16
          || !ids.every((n) => typeof n === 'string' && NODE_ID.test(n))
          || typeof x['priority'] !== 'number' || !(x['priority'] >= 0 && x['priority'] <= 1)
          || typeof x['estimated_minutes'] !== 'number' || !Number.isInteger(x['estimated_minutes']) || x['estimated_minutes'] < 1
          || typeof x['rationale_code'] !== 'string' || !CODE.test(x['rationale_code'])) {
        ok = false;
        break;
      }
      tasks.push({ kind: x['kind'], target_node_ids: [...(ids as string[])], priority: x['priority'],
                   estimated_minutes: x['estimated_minutes'], rationale_code: x['rationale_code'] });
    }
    out.push(ok ? { subject, status: 'ok', tasks } : { subject, status: 'unavailable', tasks: [] });
  }
  return out;
}

export interface HttpPlanOptions { baseUrl: string; token: string; timeoutMs: number; fetchImpl?: typeof fetch }

export function httpPlanClient(opts: HttpPlanOptions): PlanClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/internal/v1/plan`;
  return {
    async plan(studentRef, subjects) {
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
        return pickSections(await res.json(), subjects);
      } catch {
        return null;  // 超时、断连、非 JSON：不记消息（可能带出数据）
      }
    },
  };
}
