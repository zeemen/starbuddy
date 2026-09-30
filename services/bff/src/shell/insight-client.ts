// BFF → 洞察服务：只调 insight.explain 取一条判断的 RenderedStatement（packages/contracts/openapi/insight-service.v0.9.yaml；
// spec 第 8 节，待人确认 #5 的默认）。caller = bff，带 actor（学生本人，或家长 + guardian_student_link），洞察服务再核对一次授权（规则 5）。
// BFF 只保留 statement，丢弃证据链（证据链是冲刺 5“为什么”的内容）。超时、非 200、形状不对一律返回 null：该处不显示文字（规则 11）。
// 4-H 不调 insight.record / dispute / reconcile / export / erase（这些随冲刺 5，路由表里也没有）。

export type Audience = 'student' | 'guardian';
export type ConfidenceBand = 'few_evidence' | 'moderate' | 'strong';

export interface RenderedStatement {
  claim_id: string;
  template_key: string;
  template_version: number;
  status: 'hypothesis' | 'established' | 'to_verify';
  display: 'solid' | 'dashed';
  text: string;
  confidence_band: ConfidenceBand;
}

export interface ExplainRequest {
  studentRef: string;
  audience: Audience;
  /** 当前登录账号（学生本人或家长） */
  actorRef: string;
  /** 家长访问时的绑定关系 */
  linkId?: string;
  claimId: string;
}

export interface InsightClient {
  explain(req: ExplainRequest, timeoutMs: number): Promise<RenderedStatement | null>;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TEMPLATE_KEY = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$/;
const BANDS = new Set(['few_evidence', 'moderate', 'strong']);

/** 按契约的 RenderedStatement 形状核对；只返回这七个字段 */
export function pickStatement(v: unknown): RenderedStatement | null {
  if (!v || typeof v !== 'object') return null;
  const s = v as Record<string, unknown>;
  const status = s['status'];
  const display = s['display'];
  if (typeof s['claim_id'] !== 'string' || !UUID.test(s['claim_id']) || typeof s['template_key'] !== 'string' || !TEMPLATE_KEY.test(s['template_key'])
      || typeof s['template_version'] !== 'number' || !Number.isInteger(s['template_version']) || s['template_version'] < 1
      || (status !== 'hypothesis' && status !== 'established' && status !== 'to_verify')
      || (display !== 'solid' && display !== 'dashed') || (status === 'to_verify') !== (display === 'dashed')
      || typeof s['text'] !== 'string' || s['text'].length < 1 || s['text'].length > 300
      || typeof s['confidence_band'] !== 'string' || !BANDS.has(s['confidence_band'])) {
    return null;
  }
  return { claim_id: s['claim_id'], template_key: s['template_key'], template_version: s['template_version'], status, display,
           text: s['text'], confidence_band: s['confidence_band'] as ConfidenceBand };
}

export function explainBody(req: ExplainRequest, requestId: string): Record<string, unknown> {
  const actor: Record<string, string> = { role: req.audience, actor_ref: req.actorRef };
  if (req.audience === 'guardian' && req.linkId) actor['link_id'] = req.linkId;
  return {
    context: { request_id: requestId, contract_version: '0.9.0', caller: 'bff', data_class: 'L2', student_ref: req.studentRef,
               audience: req.audience, actor },
    claim_id: req.claimId,
  };
}

export interface HttpInsightOptions { baseUrl: string; token: string; fetchImpl?: typeof fetch }

export function httpInsightClient(opts: HttpInsightOptions): InsightClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/v0/insight.explain`;
  return {
    async explain(req, timeoutMs) {
      try {
        const res = await doFetch(url, {
          method: 'POST',
          headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
          body: JSON.stringify(explainBody(req, crypto.randomUUID())),
          signal: AbortSignal.timeout(timeoutMs),
          redirect: 'error',
        });
        if (res.status !== 200) {
          await res.body?.cancel();
          return null;
        }
        const body = (await res.json()) as { result?: { statement?: unknown } };
        return pickStatement(body?.result?.statement);
      } catch {
        return null;
      }
    },
  };
}
