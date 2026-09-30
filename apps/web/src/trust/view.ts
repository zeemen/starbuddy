// 信任界面（冲刺 5-H）的纯函数：BFF 响应的前端再核对、表单解析、冷静期倒计时。不引用 next/*，单测直接调用。
// 判断先于展示（规则 11）：前端对 BFF 给的内容再断言一次——
//   - 关于学生的句子只保留带合法 claim_id、非空文字的；“为什么”的陈述必须属于所问的判断，待验证的判断只能是虚线（不作为陈述）；
//   - 对账卡的偏差方向、平台调整只认契约的编码，出现别的编码整张卡不显示（与 BFF 同一口径）；
//   - 对账卡只有在同页的预测（带 claim_id）对得上时才显示（预测所引的判断就是卡片结构部分的依据）。
// 证据链只挑固定几个字段（时间、来源类型、证据等级、节点编号），不带任何文字字段：逐句对话、作答原文进不来。

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const isIso = (v: unknown): v is string => typeof v === 'string' && ISO.test(v) && Number.isFinite(Date.parse(v));

export type Audience = 'student' | 'guardian';
export type Band = 'few_evidence' | 'moderate' | 'strong';
const BANDS: readonly string[] = ['few_evidence', 'moderate', 'strong'];

// ---------------------------------------------------------------- “为什么”

export interface WhyStatement { claim_id: string; status: 'hypothesis' | 'established' | 'to_verify'; display: 'solid' | 'dashed'; text: string; confidence_band: Band }
export interface WhyEvidence { occurred_at: string; source_type: 'objective' | 'self' | 'guardian' | 'inferred'; evidence_level: 'A' | 'B' | null; node_id: string | null }
export interface WhyView { claim_id: string; statement: WhyStatement; evidence: WhyEvidence[]; source_version: string | null }

const SOURCES: readonly string[] = ['objective', 'self', 'guardian', 'inferred'];

/**
 * “为什么”的响应（BFF 读取接口待补，形状按契约 InsightExplainResponse 取子集，lessons）：
 * 陈述必须属于所问的判断、状态可展示、待验证只能虚线、成立与假设只能实线；证据链逐条只挑四个字段，不合格的条目丢弃。
 */
export function pickWhy(raw: unknown, claimId: string): WhyView | null {
  if (!obj(raw) || !obj(raw['statement'])) return null;
  const s = raw['statement'];
  const status = s['status'];
  const display = s['display'];
  const text = s['text'];
  const band = s['confidence_band'];
  if (s['claim_id'] !== claimId || !UUID.test(claimId)) return null;
  if (status !== 'hypothesis' && status !== 'established' && status !== 'to_verify') return null;
  if ((status === 'to_verify') !== (display === 'dashed') || (display !== 'solid' && display !== 'dashed')) return null;
  if (typeof text !== 'string' || text.trim() === '' || text.length > 300 || typeof band !== 'string' || !BANDS.includes(band)) return null;
  const chain = Array.isArray(raw['evidence_chain']) ? raw['evidence_chain'].slice(0, 200) : [];
  const evidence: WhyEvidence[] = [];
  for (const e of chain) {
    if (!obj(e) || !isIso(e['occurred_at']) || typeof e['source_type'] !== 'string' || !SOURCES.includes(e['source_type'])) continue;
    const level = e['evidence_level'] === 'A' || e['evidence_level'] === 'B' ? e['evidence_level'] : null;
    const node = typeof e['node_id'] === 'string' && NODE_ID.test(e['node_id']) ? e['node_id'] : null;
    evidence.push({ occurred_at: e['occurred_at'], source_type: e['source_type'] as WhyEvidence['source_type'], evidence_level: level, node_id: node });
  }
  const version = typeof raw['source_version'] === 'string' && /^[A-Za-z0-9:@._-]{1,64}$/.test(raw['source_version']) ? raw['source_version'] : null;
  return {
    claim_id: claimId,
    statement: { claim_id: claimId, status, display, text, confidence_band: band as Band },
    evidence, source_version: version,
  };
}

// ---------------------------------------------------------------- 异议

export const DISPUTE_KINDS = ['i_know', 'one_off_slip', 'data_error'] as const;
export type DisputeKind = (typeof DISPUTE_KINDS)[number];

/** 各受众能用的异议类型：“那次只是失误”只给学生本人（规则 6；BFF 对家长会话也 403） */
export const disputeKindsFor = (audience: Audience): DisputeKind[] =>
  audience === 'student' ? [...DISPUTE_KINDS] : DISPUTE_KINDS.filter((k) => k !== 'one_off_slip');

export const NEXT_STEPS = ['verification_scheduled', 'observe_followups', 'open_fact_for_correction'] as const;

/** 异议的幂等键（每次渲染表单生成一个；同一次提交的重试用同一个键） */
export const newIdempotencyKey = (uuid: string): string => `web.${uuid}`;
export const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$/;

export interface DisputeRow { dispute_id: string; claim_id: string; kind: DisputeKind; status: string; created_at: string; resolved_at: string | null }
const DISPUTE_STATUSES: readonly string[] = ['open', 'verifying', 'resolved_supported', 'resolved_unsupported', 'fact_corrected', 'rejected', 'withdrawn'];

/** 异议列表：只挑编码列；结论码（result_code）不显示（没有文案，也不能把编码直接给人看） */
export function pickDisputes(raw: unknown): DisputeRow[] | null {
  if (!obj(raw) || !Array.isArray(raw['disputes'])) return null;
  const out: DisputeRow[] = [];
  for (const d of raw['disputes']) {
    if (!obj(d) || typeof d['dispute_id'] !== 'string' || !UUID.test(d['dispute_id']) || typeof d['claim_id'] !== 'string' || !UUID.test(d['claim_id'])) continue;
    if (!(DISPUTE_KINDS as readonly unknown[]).includes(d['kind']) || typeof d['status'] !== 'string' || !DISPUTE_STATUSES.includes(d['status'])) continue;
    if (!isIso(d['created_at']) || (d['resolved_at'] !== null && !isIso(d['resolved_at']))) continue;
    out.push({ dispute_id: d['dispute_id'], claim_id: d['claim_id'], kind: d['kind'] as DisputeKind, status: d['status'],
               created_at: d['created_at'], resolved_at: d['resolved_at'] as string | null });
  }
  return out;
}

// ---------------------------------------------------------------- 考试、预测与对账卡

export const EXAM_SUBJECTS = ['aya', 'wenli', 'zhixing'] as const;  // 英语（echo）不显示
export const EXAM_TYPES = ['monthly', 'midterm', 'final', 'mock1', 'mock2', 'other'] as const;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

export interface ExamPlanInput { subject: string; exam_type: string; exam_date: string; full_score: number; scope_node_ids: string[] }

/** 考试安排表单：格式在这里先挡一次（日期是否在一年内、节点是否存在等由库函数判断） */
export function parseExamPlan(f: { get(k: string): unknown }): ExamPlanInput | null {
  const s = (k: string): string => (typeof f.get(k) === 'string' ? (f.get(k) as string).trim() : '');
  const subject = s('subject');
  const type = s('exam_type');
  const date = s('exam_date');
  const full = Number(s('full_score'));
  const nodes = s('scope_node_ids').split(/[\s,，、]+/).filter((x) => x !== '');
  if (!(EXAM_SUBJECTS as readonly string[]).includes(subject) || !(EXAM_TYPES as readonly string[]).includes(type) || !DAY.test(date)) return null;
  if (!Number.isInteger(full) || full < 1 || full > 300) return null;
  if (nodes.length < 1 || nodes.length > 64 || new Set(nodes).size !== nodes.length || !nodes.every((n) => NODE_ID.test(n))) return null;
  return { subject, exam_type: type, exam_date: date, full_score: full, scope_node_ids: nodes };
}

export function parseScore(v: unknown): number | null {
  if (typeof v !== 'string' || !/^\d{1,3}(\.\d{1,2})?$/.test(v.trim())) return null;
  const n = Number(v.trim());
  return n >= 0 && n <= 300 ? n : null;
}

/** 满分默认值（spec：七八年级 100 分制、九年级 120 分制；学生会话拿不到年级时按 100） */
export const defaultFullScore = (grade: number | null): number => (grade === 9 ? 120 : 100);

export interface PredictionView { prediction_id: string; claim_id: string; low: number; high: number; interval_level: number; frozen_at: string; frozen: boolean }

export function pickPrediction(raw: unknown): PredictionView | null {
  if (!obj(raw) || !obj(raw['prediction'])) return null;
  const p = raw['prediction'];
  const low = p['low'];
  const high = p['high'];
  if (typeof p['prediction_id'] !== 'string' || !UUID.test(p['prediction_id']) || typeof p['claim_id'] !== 'string' || !UUID.test(p['claim_id'])) return null;
  if (typeof low !== 'number' || typeof high !== 'number' || !(low >= 0 && low <= high && high <= 150)) return null;
  if (p['interval_level'] !== 0.8 || !isIso(p['frozen_at']) || typeof p['frozen'] !== 'boolean') return null;
  return { prediction_id: p['prediction_id'], claim_id: p['claim_id'], low, high, interval_level: 0.8, frozen_at: p['frozen_at'], frozen: p['frozen'] };
}

export const DEVIATION_DIRECTIONS = ['item_type_transfer', 'time_shortage', 'calculation_slip', 'diagnosis_optimistic'] as const;
export const ADJUSTMENTS = ['claims_to_verify', 'retest_scheduled', 'interval_widened', 'model_parameter_adjusted', 'none'] as const;

export interface CardStatement { claim_id: string; text: string; confidence_band: Band }
export interface ReconcileView {
  prediction_id: string; actual_score: number; within_interval: boolean; deviation_nodes: string[];
  deviation_directions: (typeof DEVIATION_DIRECTIONS)[number][]; adjustments: (typeof ADJUSTMENTS)[number][]; statements: CardStatement[];
}

/**
 * 对账卡的前端再断言：方向、调整出现契约外的编码 → 整张卡不显示；没有合法 claim_id 或文字的句子丢弃（dropped 计数）；
 * 卡片所依据的预测必须是同页那条带 claim_id 的预测，否则整张卡不显示。
 */
export function pickReconciliation(raw: unknown, prediction: PredictionView | null): { card: ReconcileView; dropped: number } | null {
  if (!prediction || !obj(raw) || !obj(raw['reconciliation'])) return null;
  const c = raw['reconciliation'];
  if (c['prediction_id'] !== prediction.prediction_id) return null;
  const dirs = c['deviation_directions'];
  const adj = c['adjustments'];
  if (!Array.isArray(dirs) || !dirs.every((d) => (DEVIATION_DIRECTIONS as readonly unknown[]).includes(d)) || new Set(dirs).size !== dirs.length) return null;
  if (!Array.isArray(adj) || !adj.every((a) => (ADJUSTMENTS as readonly unknown[]).includes(a)) || new Set(adj).size !== adj.length) return null;
  if (typeof c['actual_score'] !== 'number' || !(c['actual_score'] >= 0 && c['actual_score'] <= 150) || typeof c['within_interval'] !== 'boolean') return null;
  const nodes = Array.isArray(c['deviation_nodes']) ? c['deviation_nodes'].filter((n): n is string => typeof n === 'string' && NODE_ID.test(n)).slice(0, 100) : [];
  const raws = Array.isArray(c['statements']) ? c['statements'].slice(0, 20) : [];
  const statements: CardStatement[] = [];
  for (const s of raws) {
    if (obj(s) && typeof s['claim_id'] === 'string' && UUID.test(s['claim_id']) && typeof s['text'] === 'string' && s['text'].trim() !== ''
        && s['text'].length <= 300 && typeof s['confidence_band'] === 'string' && BANDS.includes(s['confidence_band'])) {
      statements.push({ claim_id: s['claim_id'], text: s['text'], confidence_band: s['confidence_band'] as Band });
    }
  }
  return {
    card: { prediction_id: prediction.prediction_id, actual_score: c['actual_score'], within_interval: c['within_interval'], deviation_nodes: nodes,
            deviation_directions: dirs as ReconcileView['deviation_directions'], adjustments: adj as ReconcileView['adjustments'], statements },
    dropped: raws.length - statements.length,
  };
}

// ---------------------------------------------------------------- 家长数据权利

export interface ErasureRow {
  request_id: string; state: 'awaiting_confirm' | 'cooling_off' | 'cancelled' | 'executed'; requested_at: string;
  cooling_off_until: string | null; cancel_reason: string | null; receipt: { tables: [string, number][]; scopes: string[] } | null;
}
const SCOPES: readonly string[] = ['claim_ledger', 'facts', 'evidence', 'inference', 'reports', 'uploads'];
/** 撤回原因只认契约枚举（trust.v0.yaml cancel_reason），别的一律当作没有 */
export const CANCEL_REASONS = ['guardian', 'link_revoked', 'confirm_expired'] as const;
const TABLE = /^[a-z_][a-z0-9_]{0,62}(\.[a-z_][a-z0-9_]{0,62})?$/;

/** 删除申请列表：pending 按是否已确认分“待确认 / 冷静期中”；回执只有“表名 → 行数”与范围编码（执行后才有） */
export function pickErasures(raw: unknown): ErasureRow[] | null {
  if (!obj(raw) || !Array.isArray(raw['requests'])) return null;
  const out: ErasureRow[] = [];
  for (const r of raw['requests']) {
    if (!obj(r) || typeof r['request_id'] !== 'string' || !UUID.test(r['request_id']) || !isIso(r['requested_at'])) continue;
    const st = r['status'];
    const state = st === 'pending' ? (r['confirmed_at'] ? 'cooling_off' : 'awaiting_confirm') : st === 'cancelled' ? 'cancelled' : st === 'executed' ? 'executed' : null;
    if (!state) continue;
    let receipt: ErasureRow['receipt'] = null;
    if (state === 'executed' && obj(r['receipt']) && obj(r['receipt']['tables'])) {
      const tables = Object.entries(r['receipt']['tables'])
        .filter((e): e is [string, number] => TABLE.test(e[0]) && typeof e[1] === 'number' && Number.isInteger(e[1]) && e[1] >= 0)
        .sort((a, b) => a[0].localeCompare(b[0]));
      const scopes = Array.isArray(r['receipt']['scopes']) ? r['receipt']['scopes'].filter((x): x is string => typeof x === 'string' && SCOPES.includes(x)) : [];
      receipt = { tables, scopes };
    }
    out.push({ request_id: r['request_id'], state, requested_at: r['requested_at'],
               cooling_off_until: state === 'cooling_off' && isIso(r['cooling_off_until']) ? r['cooling_off_until'] : null,
               cancel_reason: (CANCEL_REASONS as readonly unknown[]).includes(r['cancel_reason']) ? r['cancel_reason'] as string : null, receipt });
  }
  return out;
}

/** 冷静期剩余（天、小时，向下取整；已过为 0）：页面渲染时的剩余时间 */
export function coolingLeft(until: string, now: number): { days: number; hours: number } {
  const ms = Math.max(0, Date.parse(until) - now);
  const h = Math.floor(ms / 3_600_000);
  return { days: Math.floor(h / 24), hours: h % 24 };
}

/** 页面上显示的日期（上海时区，YYYY-MM-DD） */
export const shanghaiDay = (iso: string): string => new Date(Date.parse(iso) + 8 * 3600_000).toISOString().slice(0, 10);

export const DOWNLOAD_REF = /^dl_[0-9a-f]{64}$/;
export const CONFIRM_TOKEN = /^[0-9a-f]{64}$/;
