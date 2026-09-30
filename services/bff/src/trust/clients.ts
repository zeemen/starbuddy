// SIE-03 PR③：BFF → 洞察服务的异议写入，与预测、对账卡的读取接口（packages/contracts/openapi/insight-service.v0.9.yaml）。
//   - insight.dispute：caller = bff，带 actor（学生本人，或家长 + guardian_student_link），洞察服务再核对一次授权（规则 5）。
//     BFF 只把 dispute_id、next_step、verify_by 交给前端：fact_ref 是原始事实的内部引用（PR② 安全审查 F5），
//     verification_item_refs 在 KGE-04 第二步前是接口桩的占位引用（PR② 待人确认 5），两者都不转发。
//   - 预测与对账卡：契约 v0.9 的 insight.predict / insight.reconcile 只给编排层（PR② 契约差异 4），BFF 没有可用的契约工具，
//     0034 也没给 BFF 这两张表的权限。这里只定义 BFF 需要的读取接口（ExamViewClient）与按契约形状的核对；
//     缺省没有实现（路由回 503），通道由人定（lessons PR③ 待人确认）。
// 本模块不产生任何关于学生的文字：异议类型、偏差方向、调整都只传编码（显示文案属 5-H 模板，必停）。
import type { Audience, RenderedStatement } from '../shell/insight-client.ts';
import { pickStatement } from '../shell/insight-client.ts';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const NODE = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
export const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$/;
const FACT_REF = /^(attempt|exam_record|upload):[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/;

/** 契约统一信封（ServiceResultBase）：contract_version、证据 ID、置信度、版本、是否人工复核；不合就当不可用 */
export function envelopeOk(v: unknown): boolean {
  if (!v || typeof v !== 'object') return false;
  const b = v as Record<string, unknown>;
  return b['contract_version'] === '0.9.0' && Array.isArray(b['evidence_ids']) && b['evidence_ids'].every((x) => typeof x === 'string' && UUID.test(x))
    && typeof b['confidence'] === 'number' && b['confidence'] >= 0 && b['confidence'] <= 1
    && typeof b['source_version'] === 'string' && typeof b['model_version'] === 'string' && typeof b['human_review_required'] === 'boolean';
}

export const DISPUTE_KINDS = ['i_know', 'one_off_slip', 'data_error'] as const;
export type DisputeKind = (typeof DISPUTE_KINDS)[number];
export const NEXT_STEPS = ['verification_scheduled', 'observe_followups', 'open_fact_for_correction'] as const;
export type NextStep = (typeof NEXT_STEPS)[number];
/** 偏差归因固定四方向（SIE 第五章、契约 deviation_directions、0034） */
export const DIRECTIONS = ['item_type_transfer', 'time_shortage', 'calculation_slip', 'diagnosis_optimistic'] as const;
export const ADJUSTMENTS = ['claims_to_verify', 'retest_scheduled', 'interval_widened', 'model_parameter_adjusted', 'none'] as const;

export interface Who {
  studentRef: string;
  audience: Audience;
  /** 当前登录账号（学生本人或家长） */
  actorRef: string;
  /** 家长访问时的绑定关系 */
  linkId?: string;
}

export interface DisputeRequest extends Who { claimId: string; kind: DisputeKind; idempotencyKey: string }
/** 前端能看到的异议结果：只有编码与时间 */
export interface DisputeAccepted { dispute_id: string; next_step: NextStep; verify_by: string | null }
export type DisputeOutcome =
  | { kind: 'ok'; value: DisputeAccepted }
  | { kind: 'not_found' } | { kind: 'conflict' } | { kind: 'too_many' } | { kind: 'invalid' } | { kind: 'unavailable' };

export interface DisputeClient {
  dispute(req: DisputeRequest, timeoutMs: number): Promise<DisputeOutcome>;
}

/** 按契约 InsightDisputeResponse.result 核对；只取三个字段（fact_ref、verification_item_refs 在这里就丢掉） */
export function pickDispute(v: unknown): DisputeAccepted | null {
  if (!v || typeof v !== 'object') return null;
  const r = v as Record<string, unknown>;
  const step = r['next_step'];
  if (typeof r['dispute_id'] !== 'string' || !UUID.test(r['dispute_id']) || typeof step !== 'string'
      || !(NEXT_STEPS as readonly string[]).includes(step)) {
    return null;
  }
  const verifyBy = r['verify_by'];
  if (verifyBy !== undefined && (typeof verifyBy !== 'string' || Number.isNaN(Date.parse(verifyBy)))) return null;
  if (step === 'verification_scheduled' && verifyBy === undefined) return null;
  // 契约：open_fact_for_correction 必带 fact_ref（核对它在、形状对，但不转发给前端）
  if (step === 'open_fact_for_correction' && (typeof r['fact_ref'] !== 'string' || !FACT_REF.test(r['fact_ref']))) return null;
  return { dispute_id: r['dispute_id'], next_step: step as NextStep, verify_by: typeof verifyBy === 'string' ? new Date(verifyBy).toISOString() : null };
}

export function disputeBody(req: DisputeRequest, requestId: string): Record<string, unknown> {
  const actor: Record<string, string> = { role: req.audience, actor_ref: req.actorRef };
  if (req.audience === 'guardian' && req.linkId) actor['link_id'] = req.linkId;
  return {
    context: { request_id: requestId, contract_version: '0.9.0', caller: 'bff', data_class: 'L2', student_ref: req.studentRef,
               audience: req.audience, actor, idempotency_key: req.idempotencyKey },
    claim_id: req.claimId,
    kind: req.kind,
  };
}

export interface HttpTrustOptions { baseUrl: string; token: string; fetchImpl?: typeof fetch }

export function httpDisputeClient(opts: HttpTrustOptions): DisputeClient {
  const doFetch = opts.fetchImpl ?? fetch;
  const url = `${opts.baseUrl.replace(/\/$/, '')}/v0/insight.dispute`;
  return {
    async dispute(req, timeoutMs) {
      try {
        const res = await doFetch(url, {
          method: 'POST',
          headers: { authorization: `Bearer ${opts.token}`, 'content-type': 'application/json' },
          body: JSON.stringify(disputeBody(req, crypto.randomUUID())),
          signal: AbortSignal.timeout(timeoutMs),
          redirect: 'error',
        });
        if (res.status !== 200) {
          await res.body?.cancel();
          // 洞察服务：404 不存在或无权；409 状态不允许（已有结论、复核队列拒收等）；429 限额；400 请求不合契约；501 / 503 未就绪
          if (res.status === 404) return { kind: 'not_found' };
          if (res.status === 409) return { kind: 'conflict' };
          if (res.status === 429) return { kind: 'too_many' };
          if (res.status === 400) return { kind: 'invalid' };
          return { kind: 'unavailable' };
        }
        const body = (await res.json()) as { result?: unknown };
        const value = envelopeOk(body) ? pickDispute(body.result) : null;
        return value ? { kind: 'ok', value } : { kind: 'unavailable' };
      } catch {
        return { kind: 'unavailable' };
      }
    },
  };
}

// ---------------------------------------------------------------- 预测与对账卡（读）

export interface ExamViewRequest extends Who { examRecordId: string }

/** 契约 InsightPredictResponse.result 的 BFF 子集（basis 的计数不给前端） */
export interface PredictionView { prediction_id: string; claim_id: string; low: number; high: number; interval_level: number; frozen_at: string }

export interface ReconciliationView {
  prediction_id: string;
  actual_score: number;
  within_interval: boolean;
  deviation_nodes: string[];
  deviation_directions: string[];
  adjustments: string[];
  statements: RenderedStatement[];
}

export interface ExamViewClient {
  /** 契约 InsightPredictResponse.result 形状；没有预测或不可用返回 null */
  prediction(req: ExamViewRequest, timeoutMs: number): Promise<unknown>;
  /** 契约 InsightReconcileResponse.result 形状；没有对账卡或不可用返回 null */
  reconciliation(req: ExamViewRequest, timeoutMs: number): Promise<unknown>;
}

const num = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
const codes = (v: unknown, allowed: readonly string[]): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === 'string' && allowed.includes(x)) && new Set(v).size === v.length;

export function pickPrediction(v: unknown): PredictionView | null {
  if (!v || typeof v !== 'object') return null;
  const r = v as Record<string, unknown>;
  if (typeof r['prediction_id'] !== 'string' || !UUID.test(r['prediction_id']) || typeof r['claim_id'] !== 'string' || !UUID.test(r['claim_id'])
      // interval_level 固定 0.8：与契约 InsightPredictResponse（const 0.8）和 0034 exam_prediction.level 同步，改动须三处一起改
      || !num(r['low'], 0, 150) || !num(r['high'], 0, 150) || r['low'] > r['high'] || r['interval_level'] !== 0.8
      || typeof r['frozen_at'] !== 'string' || Number.isNaN(Date.parse(r['frozen_at']))) {
    return null;
  }
  return { prediction_id: r['prediction_id'], claim_id: r['claim_id'], low: r['low'], high: r['high'], interval_level: 0.8,
           frozen_at: new Date(r['frozen_at']).toISOString() };
}

/**
 * 对账卡：结构字段按契约核对（方向只收四值、调整只收契约编码；不合就整张不显示）。
 * 句子逐条过 pickStatement：没有 claim_id（或形状不对）的句子丢弃，dropped 计数（调用方记日志）。
 */
export function pickReconciliation(v: unknown): { card: ReconciliationView; dropped: number } | null {
  if (!v || typeof v !== 'object') return null;
  const r = v as Record<string, unknown>;
  const nodes = r['deviation_nodes'] ?? [];
  if (typeof r['prediction_id'] !== 'string' || !UUID.test(r['prediction_id']) || !num(r['actual_score'], 0, 150)
      || typeof r['within_interval'] !== 'boolean' || !codes(r['deviation_directions'], DIRECTIONS) || !codes(r['adjustments'], ADJUSTMENTS)
      || !Array.isArray(nodes) || nodes.length > 100 || !nodes.every((n) => typeof n === 'string' && NODE.test(n))
      || !Array.isArray(r['statements']) || r['statements'].length > 20
      || r['within_interval'] !== ((r['deviation_directions'] as string[]).length === 0)) {
    return null;
  }
  const statements: RenderedStatement[] = [];
  let dropped = 0;
  for (const s of r['statements'] as unknown[]) {
    const st = pickStatement(s);
    if (st) statements.push(st); else dropped += 1;
  }
  return {
    card: { prediction_id: r['prediction_id'], actual_score: r['actual_score'], within_interval: r['within_interval'], deviation_nodes: nodes as string[],
            deviation_directions: r['deviation_directions'] as string[], adjustments: r['adjustments'] as string[], statements },
    dropped,
  };
}
