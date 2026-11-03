// 判断先于展示（CLAUDE.md 规则 11；spec 第 8 节）：壳内任何关于学生的文字都只取洞察服务渲染的 RenderedStatement.text，
// 并带 claim_id。BFF 自己只做两件事：
//   1. 在 withAccount 事务里按 RLS（0010 的 claim_front_read）读判断的 ID 与状态——只取 claim_id、目标、模板键与版本、状态、basis_id；
//      另外显式要求“成立或假设”、未到期、可见范围含当前受众（学生看 student、家长看 guardian），再过对外模板白名单；
//   2. 对通过的判断调 insight.explain 取陈述（每页最多 N 条、每会话每分钟限频），核对陈述确实属于这条判断、是实线的成立或假设。
// 任何一步不满足（没有判断、已推翻、已过期、白名单外、洞察服务超时或限频）都返回 null：调用方不显示文字，而不是退回别的说法。
import type { ShellDeps, Query } from '../http/routes.ts';
import { rateKey } from '../http/secrets.ts';
import type { Audience, ConfidenceBand, InsightClient } from './insight-client.ts';
import type { ShellConfig } from './config.ts';

export interface ClaimRef {
  id: string;
  target_id: string;
  template_key: string;
  template_version: number;
  status: 'hypothesis' | 'established';
  basis_id: string | null;
}

export interface ShownStatement { claim_id: string; text: string; confidence_band: ConfidenceBand }

export const templateRef = (key: string, version: number): string => `${key}@${version}`;

/** 目标节点上该受众可见、成立或假设、未到期、模板在白名单里的判断（同一节点按最近更新在前） */
export async function nodeClaims(q: Query, cfg: ShellConfig, student: string, audience: Audience, nodeIds: string[])
  : Promise<Map<string, ClaimRef[]>> {
  const out = new Map<string, ClaimRef[]>();
  if (nodeIds.length === 0 || cfg.externalTemplates.size === 0) return out;
  const { rows } = await q<ClaimRef>(
    `SELECT c.id, c.target_id, c.template_key, c.template_version, c.status::text AS status, c.basis_id
       FROM insight.claim c
      WHERE c.student_id = $1 AND c.target_type = 'node' AND c.target_id = ANY($2::text[])
        AND c.status IN ('hypothesis', 'established') AND $3 = ANY (c.visibility) AND c.expires_at > now()
      ORDER BY c.target_id, c.updated_at DESC, c.id`, [student, [...new Set(nodeIds)], audience]);
  for (const r of rows) {
    if (!cfg.externalTemplates.has(templateRef(r.template_key, r.template_version))) continue;  // 白名单外的判断即使可见也不展示
    const list = out.get(r.target_id) ?? [];
    list.push(r);
    out.set(r.target_id, list);
  }
  return out;
}

/** 家长访问时的绑定关系 ID（洞察服务的 actor.link_id）；学生本人为 undefined */
export async function linkIdFor(q: Query, student: string): Promise<string | undefined> {
  const { rows } = await q<{ id: string }>(
    `SELECT l.id FROM platform.guardian_student_link l
      WHERE l.student_id = $1 AND l.guardian_account_id = platform.current_account_id() AND l.status = 'active'
      ORDER BY l.created_at LIMIT 1`, [student]);
  return rows[0]?.id;
}

/** 一页要取陈述的任务：按顺序尝试它的候选判断，取到第一条合格的陈述为止 */
export interface StatementJob { key: number; claims: ClaimRef[] }
export interface Who { student: string; audience: Audience; actor: string; linkId?: string }

/**
 * 一页的陈述：并行调 insight.explain（并发上限 cfg.explain.concurrency），整页总时限 cfg.explain.pageDeadlineMs，
 * 全页最多 cfg.explain.maxPerPage 次调用；每次调用另记一次每会话每分钟的限频（超了就不取，不报错）。
 * 时限到了还没回来的句子不显示（返回的 Map 里没有它）。
 */
export async function resolveStatements(deps: ShellDeps, cfg: ShellConfig, insight: InsightClient | undefined, sessionToken: string,
                                        who: Who, jobs: StatementJob[]): Promise<Map<number, ShownStatement>> {
  const out = new Map<number, ShownStatement>();
  if (!insight || jobs.length === 0) return out;
  let used = 0;
  let closed = false;
  const rateOk = async (): Promise<boolean> => {
    const rule = deps.config.rate_limits['insight_explain.session.minute'];
    if (!rule) return false;
    const { rows } = await deps.pool.query<{ ok: boolean }>('SELECT platform.auth_rate_hit($1, $2, $3, $4) AS ok',
      ['insight_explain.session.minute', rateKey(deps.keys, 'session', sessionToken), rule.window_seconds, rule.limit]);
    return rows[0]?.ok === true;
  };
  const one = async (claim: ClaimRef): Promise<ShownStatement | null> => {
    if (closed || used >= cfg.explain.maxPerPage) return null;
    used += 1;
    if (!(await rateOk())) return null;
    const st = await insight.explain({ studentRef: who.student, audience: who.audience, actorRef: who.actor,
                                       ...(who.linkId ? { linkId: who.linkId } : {}), claimId: claim.id }, cfg.explain.timeoutMs);
    // 核对洞察服务回来的陈述确实属于这条判断、是实线的成立或假设（白名单在 nodeClaims 一处过滤）
    if (!st || st.claim_id !== claim.id || st.display !== 'solid' || (st.status !== 'hypothesis' && st.status !== 'established')
        || st.template_key !== claim.template_key || st.template_version !== claim.template_version) {
      return null;
    }
    return { claim_id: st.claim_id, text: st.text, confidence_band: st.confidence_band };
  };
  const queue = [...jobs];
  const worker = async (): Promise<void> => {
    for (let job = queue.shift(); job && !closed; job = queue.shift()) {
      for (const claim of job.claims) {
        const st = await one(claim).catch(() => null);
        if (closed) return;
        if (st) {
          out.set(job.key, st);
          break;
        }
      }
    }
  };
  let timer: NodeJS.Timeout | undefined;
  const deadline = new Promise<void>((r) => { timer = setTimeout(r, cfg.explain.pageDeadlineMs); });
  await Promise.race([Promise.all(Array.from({ length: Math.min(cfg.explain.concurrency, jobs.length) }, worker)), deadline]);
  closed = true;  // 时限到了：之后回来的句子一律不放进结果
  clearTimeout(timer);
  return new Map(out);
}
