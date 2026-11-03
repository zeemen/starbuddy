// SIE-02b 第二步 PR 2：BFF → 洞察服务的两条读取（packages/contracts/openapi/insight-service.v0.9.yaml）：
//   - insight.report：首份画像报告（学生版 / 家长版），每句带 claim_id；
//   - insight.explain：单条判断的结论、证据链与置信度说明（“为什么”与演示页的待验证虚线）。
// caller = bff，带 actor（学生本人，或家长 + guardian_student_link）；洞察服务按 0044 insight.verify_actor 再核对一次授权（规则 5）。
// BFF 只转发 result（契约 insight.report 描述，specs/sie-02b-claims-first-report/lessons.md 第二步 PR 1）：信封外层的 confidence、
// evidence_ids、source_version、model_version、human_review_required 只在这里核对，source_version 与是否要人工复核只记进访问日志，
// 一律不交给前端。证据链里逐条的 evidence_id 同样不转发（前端“为什么”页只用时间、来源、等级、节点四个字段）。
// 结果按契约逐字段核对：多一个键、少一个键、长度或编码不合，整份当作不可信（报告不出、解释不可用），不截断、不挑着转。
// 报告另核节键与句子模板类别对应（templateSection）；解释返回前按受众与状态裁剪（explainForAudience）。响应体有约 1 MB 上限（readCapped）。
// 本模块不产生任何关于学生的文字。
import type { Audience, ConfidenceBand, RenderedStatement } from '../shell/insight-client.ts';
import { pickStatement } from '../shell/insight-client.ts';
import { envelopeOk, type Who } from './clients.ts';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TEMPLATE_KEY = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$/;
const NODE = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
/** 报告的策略版本：只收这个字符集（访问日志也只在合格时记） */
export const POLICY_VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const BANDS: readonly string[] = ['few_evidence', 'moderate', 'strong'];
const SOURCES: readonly string[] = ['objective', 'self', 'guardian', 'inferred'];
const SECTION_KEYS: readonly string[] = ['node_mastery', 'node_transfer'];
/** 与契约同一组上限（每节 200 句、全报告 400 个判断 ID、节至多 2 个、证据链至多 200 条） */
export const REPORT_LIMITS = { sections: 2, sentencesPerSection: 200, claimIds: 400, notice: 200, title: 80, connective: 60, text: 300, chain: 200 } as const;

const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown, max: number): v is string => typeof v === 'string' && v.length >= 1 && v.length <= max && v.trim() !== '';
const isIso = (v: unknown): v is string => typeof v === 'string' && ISO.test(v) && Number.isFinite(Date.parse(v));
/** 键恰好是 required ∪ optional 的子集，且 required 都在 */
const keysOk = (o: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean =>
  required.every((k) => k in o) && Object.keys(o).every((k) => required.includes(k) || optional.includes(k));

// ---------------------------------------------------------------- 客户端

/** 信封外层里 BFF 只留这两项记访问日志（不给前端） */
export interface EnvelopeNote { source_version: string; human_review_required: boolean }
export type ReadOutcome = { kind: 'ok'; result: unknown; envelope: EnvelopeNote } | { kind: 'not_found' } | { kind: 'unavailable' };

export interface ExplainWho extends Who { claimId: string }

export interface InsightReadClient {
  /** insight.report：200 → ok（result 原样，调用方再按契约挑）；404 → not_found（无权、没有报告、检查不过）；其余 → unavailable */
  report(who: Who, timeoutMs: number): Promise<ReadOutcome>;
  /** insight.explain：同上 */
  explain(who: ExplainWho, timeoutMs: number): Promise<ReadOutcome>;
}

export function readContext(who: Who, requestId: string): Record<string, unknown> {
  const actor: Record<string, string> = { role: who.audience, actor_ref: who.actorRef };
  if (who.audience === 'guardian' && who.linkId) actor['link_id'] = who.linkId;
  return { request_id: requestId, contract_version: '0.9.0', caller: 'bff', data_class: 'L2', student_ref: who.studentRef, audience: who.audience, actor };
}
export const reportBody = (who: Who, requestId: string): Record<string, unknown> => ({ context: readContext(who, requestId) });
export const explainReadBody = (who: ExplainWho, requestId: string): Record<string, unknown> =>
  ({ context: readContext(who, requestId), claim_id: who.claimId });

/** 洞察服务响应体上限（约 1 MB）：报告至多 400 句、证据链至多 200 条，正常远小于此；超了按不可用处理 */
export const MAX_READ_BYTES = 1_048_576;

export interface HttpReadOptions { baseUrl: string; token: string; fetchImpl?: typeof fetch; /** 测试用；缺省 MAX_READ_BYTES */ maxBytes?: number }

/**
 * 有上限地读响应体：先看 content-length（不是十进制数字或超限直接 cancel），再流式累计，超限立即 cancel；
 * 超限返回 null（调用方按 unavailable 处理）。UTF-8 不合法时抛错（同样落到 unavailable）。
 */
export async function readCapped(res: Response, max: number): Promise<string | null> {
  const len = res.headers.get('content-length');
  if (len !== null && (!/^\d{1,15}$/.test(len.trim()) || Number(len.trim()) > max)) {
    await res.body?.cancel().catch(() => undefined);
    return null;
  }
  if (!res.body) return '';
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
}

export function httpInsightReadClient(opts: HttpReadOptions): InsightReadClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const max = opts.maxBytes ?? MAX_READ_BYTES;
  const base = opts.baseUrl.replace(/\/$/, '');
  const post = async (tool: 'insight.report' | 'insight.explain', body: Record<string, unknown>, timeoutMs: number): Promise<ReadOutcome> => {
    try {
      const res = await doFetch(`${base}/v0/${tool}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
        redirect: 'error',
      });
      if (res.status !== 200) {
        await res.body?.cancel();
        return res.status === 404 ? { kind: 'not_found' } : { kind: 'unavailable' };
      }
      const raw = await readCapped(res, max);
      if (raw === null) return { kind: 'unavailable' };
      const env = JSON.parse(raw) as Record<string, unknown>;
      if (!envelopeOk(env) || !('result' in env)) return { kind: 'unavailable' };
      return { kind: 'ok', result: env['result'],
               envelope: { source_version: String(env['source_version']), human_review_required: env['human_review_required'] === true } };
    } catch {
      return { kind: 'unavailable' };
    }
  };
  return {
    report: (who, timeoutMs) => post('insight.report', reportBody(who, crypto.randomUUID()), timeoutMs),
    explain: (who, timeoutMs) => post('insight.explain', explainReadBody(who, crypto.randomUUID()), timeoutMs),
  };
}

// ---------------------------------------------------------------- 首份报告：按契约 InsightReportResponse.result 核对

export interface ReportSentence {
  claim_id: string; template_key: string; template_version: number; status: 'hypothesis' | 'established'; display: 'solid'; text: string;
  label?: 'preliminary'; confidence_band?: ConfidenceBand;
}
export interface ReportSection { key: 'node_mastery' | 'node_transfer'; connective: string; sentences: ReportSentence[] }
export interface FirstReport {
  format: 'sie-first-report/0.1'; audience: Audience; policy_version: string; notice: string; title: string;
  sections: ReportSection[]; claim_ids: string[];
}

function pickSentence(v: unknown, audience: Audience): ReportSentence | null {
  if (!obj(v) || !keysOk(v, ['claim_id', 'template_key', 'template_version', 'status', 'display', 'text'], ['label', 'confidence_band'])) return null;
  const { claim_id: id, template_key: key, template_version: ver, status, display, text, label, confidence_band: band } = v;
  if (typeof id !== 'string' || !UUID.test(id) || typeof key !== 'string' || !TEMPLATE_KEY.test(key)
      || typeof ver !== 'number' || !Number.isInteger(ver) || ver < 1 || (status !== 'hypothesis' && status !== 'established')
      || display !== 'solid' || !str(text, REPORT_LIMITS.text)) {
    return null;
  }
  // 假设必须标“初步判断”、成立不标
  if ((status === 'hypothesis') !== (label === 'preliminary') || (label !== undefined && label !== 'preliminary')) return null;
  // 家长版每句带置信度说明，学生版不带（SIE 第八章）
  if (audience === 'guardian' ? typeof band !== 'string' || !BANDS.includes(band) : band !== undefined) return null;
  return { claim_id: id, template_key: key, template_version: ver, status, display: 'solid', text,
           ...(status === 'hypothesis' ? { label: 'preliminary' as const } : {}),
           ...(audience === 'guardian' ? { confidence_band: band as ConfidenceBand } : {}) };
}

/**
 * 首份报告的 result：键恰好是契约的七个；格式、受众（必须等于本次请求的受众）、策略版本、提示语与标题长度、1–2 节（节键不重复）、
 * 每节 1–200 句、每句按 ReportSentence；claim_ids 必须恰好是各句判断 ID 按出现顺序排列（不重复、至多 400）。任何一处不合返回 null。
 */
export function pickReport(v: unknown, audience: Audience): FirstReport | null {
  if (!obj(v) || !keysOk(v, ['format', 'audience', 'policy_version', 'notice', 'title', 'sections', 'claim_ids'])) return null;
  const { format, policy_version: policy, notice, title, sections, claim_ids: ids } = v;
  if (format !== 'sie-first-report/0.1' || v['audience'] !== audience || typeof policy !== 'string' || !POLICY_VERSION.test(policy)
      || !str(notice, REPORT_LIMITS.notice) || !str(title, REPORT_LIMITS.title)
      || !Array.isArray(sections) || sections.length < 1 || sections.length > REPORT_LIMITS.sections
      || !Array.isArray(ids) || ids.length < 1 || ids.length > REPORT_LIMITS.claimIds) {
    return null;
  }
  const out: ReportSection[] = [];
  for (const s of sections as unknown[]) {
    if (!obj(s) || !keysOk(s, ['key', 'connective', 'sentences'])) return null;
    const { key, connective, sentences } = s;
    if (typeof key !== 'string' || !SECTION_KEYS.includes(key) || out.some((x) => x.key === key) || !str(connective, REPORT_LIMITS.connective)
        || !Array.isArray(sentences) || sentences.length < 1 || sentences.length > REPORT_LIMITS.sentencesPerSection) {
      return null;
    }
    const picked: ReportSentence[] = [];
    for (const x of sentences as unknown[]) {
      const p = pickSentence(x, audience);
      if (!p) return null;
      picked.push(p);
    }
    out.push({ key: key as ReportSection['key'], connective, sentences: picked });
  }
  const order = out.flatMap((s) => s.sentences.map((x) => x.claim_id));
  if (new Set(order).size !== order.length || ids.length !== order.length || !ids.every((id, i) => id === order[i])) return null;
  return { format: 'sie-first-report/0.1', audience, policy_version: policy, notice, title, sections: out, claim_ids: order };
}

/**
 * 模板键的类别：按现有映射取最后一段（洞察服务策略配置 templates 的类别 → 模板键，合成键形如 syn.math.node_mastery；
 * insight.claim_template 没有类别列）。不是报告节键之一返回 null。
 */
export const templateSection = (templateKey: string): ReportSection['key'] | null => {
  const last = templateKey.slice(templateKey.lastIndexOf('.') + 1);
  return SECTION_KEYS.includes(last) ? last as ReportSection['key'] : null;
};

/** 节键与句子模板类别对不上的句数（node_mastery 节里只能是 *.node_mastery 模板的句子，迁移同理）；非 0 整份不出 */
export const sectionMismatches = (r: FirstReport): number =>
  r.sections.reduce((n, s) => n + s.sentences.filter((x) => templateSection(x.template_key) !== s.key).length, 0);

/** 报告里全部要过禁用推断词表的文字：提示语、标题、衔接短语、每句 */
export const reportTexts = (r: FirstReport): string[] =>
  [r.notice, r.title, ...r.sections.flatMap((s) => [s.connective, ...s.sentences.map((x) => x.text)])];

// ---------------------------------------------------------------- 单条解释：按契约 InsightExplainResponse.result 核对

export interface ExplainEvidence { occurred_at: string; source_type: 'objective' | 'self' | 'guardian' | 'inferred'; evidence_level?: 'A' | 'B'; node_id?: string }
export interface ExplainView { statement: RenderedStatement; evidence_chain: ExplainEvidence[]; confidence_band: ConfidenceBand; expires_at?: string }

/**
 * result 的键只能是 statement、evidence_chain、confidence_band、expires_at；陈述按 RenderedStatement 核对且属于所问的判断；
 * 证据链至多 200 条、逐条按契约核对（不合则整份不可用），只留时间、来源、等级、节点（evidence_id 不转发）。
 */
export function pickExplain(v: unknown, claimId: string): ExplainView | null {
  if (!obj(v) || !keysOk(v, ['statement', 'evidence_chain', 'confidence_band'], ['expires_at'])) return null;
  const st = obj(v['statement']) && keysOk(v['statement'], ['claim_id', 'template_key', 'template_version', 'status', 'display', 'text', 'confidence_band'])
    ? pickStatement(v['statement']) : null;
  const band = v['confidence_band'];
  const chain = v['evidence_chain'];
  if (!st || st.claim_id !== claimId || typeof band !== 'string' || !BANDS.includes(band)
      || !Array.isArray(chain) || chain.length > REPORT_LIMITS.chain || (v['expires_at'] !== undefined && !isIso(v['expires_at']))) {
    return null;
  }
  const evidence: ExplainEvidence[] = [];
  for (const e of chain as unknown[]) {
    if (!obj(e) || !keysOk(e, ['evidence_id', 'occurred_at', 'source_type'], ['evidence_level', 'node_id'])) return null;
    const { evidence_id: eid, occurred_at: at, source_type: src, evidence_level: level, node_id: node } = e;
    if (typeof eid !== 'string' || !UUID.test(eid) || !isIso(at) || typeof src !== 'string' || !SOURCES.includes(src)
        || (level !== undefined && level !== 'A' && level !== 'B') || (node !== undefined && (typeof node !== 'string' || !NODE.test(node)))) {
      return null;
    }
    evidence.push({ occurred_at: new Date(at).toISOString(), source_type: src as ExplainEvidence['source_type'],
                    ...(level !== undefined ? { evidence_level: level } : {}), ...(node !== undefined ? { node_id: node as string } : {}) });
  }
  return { statement: st, evidence_chain: evidence, confidence_band: band as ConfidenceBand,
           ...(typeof v['expires_at'] === 'string' ? { expires_at: new Date(v['expires_at']).toISOString() } : {}) };
}

/** 交给前端的解释：按受众与状态裁剪后的形状（openapi/trust.v0.yaml getClaimExplain 的 if/then） */
export interface ExplainOutStatement {
  claim_id: string; template_key: string; template_version: number; status: RenderedStatement['status']; display: RenderedStatement['display'];
  text?: string; confidence_band?: ConfidenceBand;
}
export interface ExplainOut {
  audience: Audience; statement: ExplainOutStatement; evidence_chain: ExplainEvidence[]; confidence_band?: ConfidenceBand; expires_at?: string;
}

/**
 * 返回前按受众裁剪（SIE 第八章：置信度说明只给家长）：学生受众去掉顶层与 statement 内的 confidence_band；
 * 待验证（虚线）不作为陈述，去掉 statement.text（前端只画虚线占位，结论文字不出 BFF）。
 */
export function explainForAudience(v: ExplainView, audience: Audience): ExplainOut {
  const s = v.statement;
  const solid = s.status !== 'to_verify' && s.display === 'solid';
  return {
    audience,
    statement: { claim_id: s.claim_id, template_key: s.template_key, template_version: s.template_version, status: s.status, display: s.display,
                 ...(solid ? { text: s.text } : {}), ...(audience === 'guardian' ? { confidence_band: s.confidence_band } : {}) },
    evidence_chain: v.evidence_chain,
    ...(audience === 'guardian' ? { confidence_band: v.confidence_band } : {}),
    ...(v.expires_at !== undefined ? { expires_at: v.expires_at } : {}),
  };
}
