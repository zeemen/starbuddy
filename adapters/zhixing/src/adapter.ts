// 知行（道法）适配器的方法：capabilities、grade（F3 采分点逐点判分）、diagnose（process）、generate_variation、plan、report。
// 规格：specs/adapter-zhixing-sprint2/spec.md（待人确认 #1–#6 按默认执行）；plan、report 见 specs/adapter-zhixing-wenli-methods-sprint4/spec.md
// （待人确认 #1–#4 按默认：确定性、不调模型；teach 不做）。模板：adapters/aya/src/adapter.ts。
// 契约：packages/contracts/openapi/plugin-api.v0.9.yaml 与 v1.0（2026-10-09 签字，能力声明默认报 1.0.0）；按请求的 context.contract_version 校验与应答。
//
// 边界：
// - 适配器不读写任何平台表（CLAUDE.md 规则 1）：证据与推断放在响应里，由学习服务落库。
// - 判分只来自 Rubric（规则 3）：模型逐点判定 → 确定性后处理（core/m3-postprocess.ts）。结果里没有自由文本。
// - 模型调用只经学习服务内部路由接口（route-client.ts），适配器不持有供应商信息（ADR 04 第 1 条）。
// - M3 一律低置信并复核（决定 A2）；不输出掌握度（规则 2）；diagnose 只输出观测，不写关于学生的陈述（规则 11）。
// - plan、report 不产分、不调模型：plan 只输出任务 ID、编码、数字；report 只输出逐维 / 逐类计数与证据 ID，没有结论句与评价（规则 11）。
// - 响应、日志、错误详情里不出现题面、Rubric、模型原文；学生原文只出现在作答证据的 payload 里。
//   例外只有契约 1.1.0 候选版的 present_item（PLT-12）：按白名单返回材料与设问（src/present.ts），仍不返回 Rubric 与采分点。
import { CANDIDATE_CONTRACT_VERSIONS, type ContractValidator, type ContractVersion, type SchemaName } from "./contract.ts";
import {
  ATTRIBUTION_CODES,
  itemErrorCategories,
  itemNodes,
  type Admission,
  type AttributionCode,
  type LoadedPack,
  type PackItem,
} from "./content.ts";
import { parseModelOutput, postProcess, type PointOutcome } from "./core/m3-postprocess.ts";
import { buildM3GradeMessages, hasInjectionSignal, PROMPT_SOURCE_VERSION, PROMPT_TEMPLATE, PROMPT_VERSION } from "./core/m3-prompt.ts";
import { prerequisiteEdges, planUnits, rankUnits, MAX_ITEMS_PER_TASK, MAX_TASKS_WITHOUT_BUDGET, MINUTES_PER_ITEM, type MasteryInput, type PathStep } from "./core/plan.ts";
import { ATTEMPT_KINDS, inWindow, instant, trendOf, type HistoryRecord } from "./core/report.ts";
import { canonical, contentUuid, deriveUuid, requestFingerprint, routeIdempotencyKey } from "./ids.ts";
import { PRESENT_ALGORITHM, presentItem } from "./present.ts";
import { loadPlanWeights, type PlanWeights } from "./plan-weights.ts";
import { callRoute, RouteError, type FetchLike, type RouteConfig, type RouteFailure } from "./route-client.ts";
import type { ZhixingTaxonomy } from "./taxonomy.ts";

export const ADAPTER_VERSION = "0.1.0";
/** 模型判分的主观维度置信度（契约与 0005 的 CHECK：≤ 0.5；未经金标准校准） */
export const RUBRIC_MODEL_CONFIDENCE = 0.4;
export const ATTRIBUTION_ALGORITHM_BASE = "zhixing-m3-attr@0.1.0";
export const attributionAlgorithm = (taxonomyVersion: string) => `${ATTRIBUTION_ALGORITHM_BASE}-tax${taxonomyVersion}`;
export const DIAGNOSE_ALGORITHM = "zhixing-process-diag@0.1.0";
/** 既往判分里有该节点的命中片段：观测为 partial（证据不带得分，分不清满分与部分得分） */
export const PROCESS_HIT_CONFIDENCE = 0.3;
/** 没有可用的既往判分证据：观测为 inconclusive */
export const PROCESS_INCONCLUSIVE_CONFIDENCE = 0.1;
export const SELECT_ALGORITHM = "zhixing-item-select@0.1.0";
export const PLAN_ALGORITHM = "zhixing-plan@0.1.0";
export const REPORT_ALGORITHM = "zhixing-report@0.1.0";
export const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;
/** 作答长度上限（码点）。legacy M3GradeRequestSchema 为 4000 */
export const MAX_ANSWER_CHARS = 4000;

// ---------- 契约里用到的类型（只列本适配器读写的字段） ----------
interface RequestContext {
  request_id: string;
  subject: string;
  student_ref: string;
  data_class: "L2" | "L3";
  idempotency_key: string;
  usage_id?: string;
  contract_version: string;
  content_version?: Record<string, string | number>;
}
interface AttemptInput {
  attempt_id?: string;
  item_ref: string;
  occurred_at: string;
  response: Record<string, unknown> & { text?: string };
  elapsed_sec?: number;
}
interface GradeRequest {
  context: RequestContext;
  attempt: AttemptInput;
  rubric_version: string;
  appeal?: boolean;
  scoring_points?: { point_id: string; score: number }[];
}
interface EvidenceRecord {
  evidence_id: string;
  attempt_id?: string;
  kind: string;
  occurred_at: string;
  item_ref?: string;
  skill_node?: string;
  admission: Admission;
  payload: Record<string, unknown>;
}
interface DiagnoseRequest {
  context: RequestContext;
  mode: "intake" | "paper" | "gap_test" | "process";
  attempts?: AttemptInput[];
  paper_image_refs?: string[];
  prior_evidence?: EvidenceRecord[];
}
interface GenerateVariationRequest {
  context: RequestContext;
  source_item_ref?: string;
  skill_node?: string;
  subject_error_category?: string;
  count: number;
  exclude_item_refs?: string[];
  item_family?: { item_ref: string }[];
}
interface PlanRequest {
  context: RequestContext;
  mastery: { skill_node: string; state: string; p_mastery?: number }[];
  constraints?: { time_budget_minutes?: number; exam_date?: string; focus_skill_nodes?: string[] };
  node_path?: PathStep[];
}
interface ErrorAttributionInput {
  skill_node?: string;
  subject_category: string;
  cross_subject_category: string;
  confidence: number;
  evidence_ids: string[];
}
interface ReportRequest {
  context: RequestContext;
  window: { from: string; to: string };
  audience: "student" | "parent" | "teacher";
  evidence?: EvidenceRecord[];
  error_attributions?: ErrorAttributionInput[];
  mastery_history?: (HistoryRecord & { p_mastery?: number })[];
}
type EvidenceDraft = EvidenceRecord;

type ProblemCode =
  | "INVALID_REQUEST"
  | "MODE_NOT_SUPPORTED" // v1.0 起；0.9.0 请求仍报 INVALID_REQUEST
  | "CONTRACT_VERSION_UNSUPPORTED"
  | "SUBJECT_MISMATCH"
  | "METHOD_NOT_IMPLEMENTED"
  | "CONTENT_VERSION_NOT_FOUND"
  | "ITEM_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
  | "UPSTREAM_TIMEOUT"
  | "UPSTREAM_UNAVAILABLE"
  | "RATE_LIMITED" // 幂等缓存全是进行中的请求：稍后用同一个 key 重试
  | "DISCIPLINE_NOT_SUPPORTED"
  | "JOB_NOT_FOUND"
  | "INTERNAL";

export interface HandlerResult {
  status: number;
  body: unknown;
}

/** 日志记录只含这些字段：不写作答、题面、Rubric、模型输出（契约 Problem.detail；zhixing inventory §1.6） */
export interface LogRecord {
  method: string;
  status: number;
  code?: ProblemCode;
  request_id?: string;
  duration_ms: number;
  /** 只在 500 时出现：异常的类名（不含消息，消息可能带内容） */
  error_class?: string;
  /** 只在模型路由失败时出现：失败类别编码 */
  route_failure?: RouteFailure | "not_configured";
}

export interface AdapterDeps {
  content: LoadedPack;
  taxonomy: ZhixingTaxonomy;
  contract: ContractValidator;
  /** 学习服务内部模型路由接口；null 表示没有配置（启动时缺配置）：grade 返回暂不可用，其他方法照常 */
  route: RouteConfig | null;
  /** 测试注入用；默认全局 fetch */
  fetchImpl?: FetchLike;
  log?: (record: LogRecord) => void;
  /** 幂等缓存条数上限 */
  idempotencyCacheSize?: number;
  /** 候选版（CANDIDATE_CONTRACT_VERSIONS）签字前只在测试与演练里接受 */
  allowCandidate?: boolean;
  /** plan 的领域权重（合成值，待教研校准）；缺省读 content/plan-weights.json 并与内容包核对 */
  planWeights?: PlanWeights;
}

class ProblemError extends Error {
  status: number;
  code: ProblemCode;
  detail: string | undefined;
  retryable: boolean;
  routeFailure: LogRecord["route_failure"];
  constructor(status: number, code: ProblemCode, title: string, detail?: string, retryable = false) {
    super(title);
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.retryable = retryable;
  }
}

/** 路由失败 → 契约 Problem。5xx 一律按结果未知处理（契约 v1.0 N18）；retryable 只标可以原键重试的情况 */
function routeProblem(failure: RouteFailure | "not_configured"): ProblemError {
  const table: Record<RouteFailure | "not_configured", [number, ProblemCode, string, boolean]> = {
    not_configured: [503, "UPSTREAM_UNAVAILABLE", "model route is not configured on this adapter", false],
    timeout: [504, "UPSTREAM_TIMEOUT", "model route timed out", true],
    unavailable: [503, "UPSTREAM_UNAVAILABLE", "model route is unavailable", true],
    rejected: [502, "UPSTREAM_UNAVAILABLE", "model route rejected the request", false],
    unauthorized: [502, "UPSTREAM_UNAVAILABLE", "model route refused the adapter credential", false],
    bad_response: [502, "UPSTREAM_UNAVAILABLE", "model route returned an unexpected response", true],
  };
  const [status, code, title, retryable] = table[failure];
  const e = new ProblemError(status, code, title, undefined, retryable);
  e.routeFailure = failure;
  return e;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const round3 = (x: number) => Math.round(x * 1000) / 1000;
const codePoints = (s: string) => Array.from(s).length;
const uniq = <T>(xs: T[]) => [...new Set(xs)];

/** 一次 grade 的判定结果（幂等缓存里存它，不存学生原文与模型原文） */
interface GradeOutcome {
  points: PointOutcome[];
  total: number | null;
  fullScore: boolean;
  modelVersion: string;
  modelMismatch: boolean;
  /** 作答里有指令特征（Prompt 注入的信号）：复核原因加 low_confidence */
  injectionSuspected: boolean;
}

export function createAdapter(deps: AdapterDeps) {
  const { content, taxonomy, contract } = deps;
  const log = deps.log ?? (() => {});
  const cacheSize = deps.idempotencyCacheSize ?? 10_000;
  const fetchImpl = deps.fetchImpl ?? fetch;
  const planWeights = deps.planWeights ?? loadPlanWeights(content);
  /** 同 key：只存请求体指纹与判定结果（不含原文）；进行中的请求共享同一个 Promise，避免重复调用模型 */
  const idempotency = new Map<string, { fingerprint: string; at: number; value?: unknown; pending?: Promise<unknown> }>();

  const problem = (e: ProblemError, requestId?: string): HandlerResult => {
    const body: Record<string, unknown> = {
      type: `/problems/${e.code.toLowerCase()}`,
      title: e.message,
      status: e.status,
      code: e.code,
      retryable: e.retryable,
    };
    if (e.detail) body.detail = e.detail.slice(0, 500);
    if (requestId) body.request_id = requestId;
    return { status: e.status, body };
  };

  const requestVersion = (body: unknown): ContractVersion => {
    const ctx = (body as { context?: unknown } | null | undefined)?.context;
    const v = ctx && typeof ctx === "object" ? (ctx as { contract_version?: unknown }).contract_version : undefined;
    if (typeof v !== "string") return contract.version;
    if (!contract.supports(v)) {
      throw new ProblemError(400, "CONTRACT_VERSION_UNSUPPORTED", "contract_version is not supported by this adapter", `supported: ${contract.versions.join(", ")}`);
    }
    if (CANDIDATE_CONTRACT_VERSIONS.includes(v) && !deps.allowCandidate) {
      throw new ProblemError(400, "CONTRACT_VERSION_UNSUPPORTED", "candidate contract version is not enabled on this adapter");
    }
    return v;
  };

  const unsupported = (version: ContractVersion, title: string) =>
    new ProblemError(422, version === "0.9.0" ? "INVALID_REQUEST" : "MODE_NOT_SUPPORTED", title);

  const validateOrThrow = (schema: SchemaName, value: unknown, version: ContractVersion) => {
    const r = contract.validate(schema, value, version);
    if (!r.ok) throw new ProblemError(400, "INVALID_REQUEST", "request does not match the contract", r.errors.join("; "));
  };

  const checkContext = (ctx: RequestContext, requireContentVersion: boolean) => {
    if (ctx.subject !== "zhixing") throw new ProblemError(400, "SUBJECT_MISMATCH", "this adapter only serves subject zhixing");
    const cv = ctx.content_version;
    if (!cv) {
      if (requireContentVersion) throw new ProblemError(400, "INVALID_REQUEST", "content_version is required");
      return;
    }
    const served = content.pack.curriculum;
    const same = (Object.keys(served) as (keyof typeof served)[]).every((k) => cv[k] === served[k]);
    if (!same) throw new ProblemError(404, "CONTENT_VERSION_NOT_FOUND", "content_version is not served by this adapter");
  };

  const findItem = (itemRef: string): PackItem => {
    const [subject, localId] = itemRef.split(":", 2) as [string, string];
    if (subject !== "zhixing") throw new ProblemError(400, "SUBJECT_MISMATCH", "item_ref is not a zhixing item");
    const item = content.itemById.get(localId);
    if (!item) throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
    // 契约 v1 的 zhixing 只接道法判分，不含历史（zhixing #13/A5）
    if (item.discipline !== "daofa") throw new ProblemError(422, "DISCIPLINE_NOT_SUPPORTED", "only daofa items are graded by contract v1");
    return item;
  };

  /** 只接文字作答：非空，长度不超过上限 */
  const answerText = (attempt: AttemptInput, version: ContractVersion): string => {
    const r = attempt.response;
    const keys = Object.keys(r);
    if (keys.length !== 1 || typeof r.text !== "string") {
      throw unsupported(version, "zhixing accepts response.text only");
    }
    if (r.text.trim().length === 0) throw new ProblemError(422, "INVALID_REQUEST", "response.text is empty");
    if (codePoints(r.text) > MAX_ANSWER_CHARS) throw new ProblemError(422, "INVALID_REQUEST", `response.text exceeds ${MAX_ANSWER_CHARS} characters`);
    return r.text;
  };

  /** 作答证据的 ID：有 attempt_id 时按作答本身派生（grade 与 diagnose 同一道作答同一个 ID），否则按 key 与指纹 */
  const responseEvidenceId = (attempt: AttemptInput, text: string, fallback: () => string) =>
    attempt.attempt_id ? contentUuid("response", attempt.attempt_id, attempt.item_ref, attempt.occurred_at, text) : fallback();

  const responseEvidence = (item: PackItem, attempt: AttemptInput, text: string, id: string, version: ContractVersion): EvidenceDraft => ({
    evidence_id: id,
    ...(version !== "0.9.0" && attempt.attempt_id ? { attempt_id: attempt.attempt_id } : {}),
    kind: "response_text",
    occurred_at: attempt.occurred_at,
    item_ref: `zhixing:${item.id}`,
    skill_node: `zhixing:${itemNodes(item)[0]}`,
    admission: item.admission,
    payload: { text },
  });

  const envelopeFlags = (admissions: Admission[], extra: string[]) => {
    const reasons: string[] = [];
    const allAdmitted = admissions.every((a) => a === "admitted");
    for (const r of extra) if (!reasons.includes(r)) reasons.push(r);
    if (!allAdmitted && !reasons.includes("content_not_admitted")) reasons.push("content_not_admitted");
    return {
      human_review_required: reasons.length > 0,
      ...(reasons.length > 0 ? { review_reasons: reasons } : {}),
      semantics: allAdmitted ? "formal" : "practice",
    };
  };

  /**
   * 幂等包装：同 key 同请求体 → 同一结果；同 key 不同请求体 → IDEMPOTENCY_CONFLICT。
   * 缓存里只存请求体指纹与判定结果（点号、分数、字符位置、模型版本），不存学生原文与模型原文。
   * 计算失败（例如路由不可用）不缓存：平台用同一个 key 重试时重新计算。
   */
  const idempotent = async <T>(method: string, key: string, fingerprint: string, run: () => Promise<T>): Promise<T> => {
    const cacheKey = `${method}\0${key}`;
    const now = Date.now();
    let hit = idempotency.get(cacheKey);
    if (hit && now - hit.at > IDEMPOTENCY_TTL_MS && !hit.pending) {
      idempotency.delete(cacheKey);
      hit = undefined;
    }
    if (hit && hit.fingerprint !== fingerprint) {
      throw new ProblemError(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different request body");
    }
    if (hit?.pending) return (await hit.pending) as T;
    if (hit && "value" in hit) return hit.value as T;
    if (idempotency.size >= cacheSize) {
      // 缓存满：淘汰最早的已完成条目；进行中的条目不淘汰（淘汰了同 key 重试就会再调一次模型）
      let victim: string | undefined;
      for (const [k, v] of idempotency) {
        if (!v.pending) {
          victim = k;
          break;
        }
      }
      if (victim === undefined) {
        throw new ProblemError(429, "RATE_LIMITED", "adapter is busy; retry later with the same idempotency_key", undefined, true);
      }
      idempotency.delete(victim);
    }
    const entry: { fingerprint: string; at: number; value?: unknown; pending?: Promise<unknown> } = { fingerprint, at: now };
    const pending = run();
    entry.pending = pending;
    idempotency.set(cacheKey, entry);
    try {
      const value = await pending;
      entry.value = value;
      delete entry.pending;
      return value;
    } catch (e) {
      if (idempotency.get(cacheKey) === entry) idempotency.delete(cacheKey);
      throw e;
    }
  };

  const selfCheck = (schema: SchemaName, body: unknown, version: ContractVersion): HandlerResult => {
    const r = contract.validate(schema, body, version);
    if (!r.ok) throw new ProblemError(500, "INTERNAL", "adapter produced a response that violates the contract", r.errors.join("; "));
    return { status: 200, body };
  };

  // ---------- capabilities ----------
  const capabilities = (): HandlerResult =>
    selfCheck("Capabilities", {
      subject: "zhixing",
      adapter_version: ADAPTER_VERSION,
      contract_version: contract.version,
      methods: {
        diagnose: { implemented: true, async: false, notes_code: "process_mode_only" },
        plan: { implemented: true, async: false, notes_code: "domain_weights_uncalibrated" },
        teach: { implemented: false },
        grade: { implemented: true, async: false, notes_code: "history_grading_excluded" },
        generate_variation: { implemented: true },
        report: { implemented: true, async: false, notes_code: "counts_only" },
        // v1.1（PLT-12）：能力声明逐一声明七个方法；1.1.0 以下的能力声明没有这一项（v1.0 Capabilities 不允许多余方法）
        ...(contract.version === "1.1.0" ? { present_item: { implemented: true, async: false } } : {}),
      },
    }, contract.version);

  // ---------- grade ----------
  const judge = async (req: GradeRequest, item: PackItem, text: string, fp: string): Promise<GradeOutcome> => {
    if (!deps.route) throw routeProblem("not_configured");
    let reply;
    try {
      reply = await callRoute(deps.route, {
        task: "T2",
        data_class: "L2",
        subject: "zhixing",
        prompt_template: PROMPT_TEMPLATE,
        prompt_version: PROMPT_VERSION,
        messages: buildM3GradeMessages(item, text),
        usage_id: req.context.usage_id!,
        idempotency_key: routeIdempotencyKey(req.context.idempotency_key, fp),
        deadline_ms: deps.route.deadlineMs,
      }, fetchImpl);
    } catch (e) {
      if (e instanceof RouteError) throw routeProblem(e.failure);
      throw e;
    }
    const processed = postProcess(item, parseModelOutput(reply.output), text);
    return { ...processed, modelVersion: reply.model_version, modelMismatch: reply.model_mismatch, injectionSuspected: hasInjectionSignal(text) };
  };

  const gradeResponse = (req: GradeRequest, version: ContractVersion, item: PackItem, text: string, fp: string, o: GradeOutcome) => {
    const key = req.context.idempotency_key;
    const itemRef = `zhixing:${item.id}`;
    const responseId = responseEvidenceId(req.attempt, text, () => deriveUuid("grade", key, fp, "a0:response"));
    const response = responseEvidence(item, req.attempt, text, responseId, version);
    const defs = new Map(item.points.map((p) => [p.point_id, p]));
    const spans: EvidenceDraft[] = [];
    const idsOf = new Map<string, string[]>();
    for (const p of o.points) {
      const ids = [responseId];
      if (p.decided && p.span) {
        const [start, end] = p.span;
        const spanId = contentUuid("span", responseId, req.rubric_version, p.point_id, String(start), String(end));
        spans.push({
          evidence_id: spanId,
          ...(version !== "0.9.0" && req.attempt.attempt_id ? { attempt_id: req.attempt.attempt_id } : {}),
          kind: "scoring_point_span",
          occurred_at: req.attempt.occurred_at,
          item_ref: itemRef,
          skill_node: `zhixing:${defs.get(p.point_id)!.skill_node}`,
          admission: item.admission,
          payload: { source_evidence_id: responseId, span_start: start, span_end: end },
        });
        ids.push(spanId);
      }
      idsOf.set(p.point_id, ids);
    }
    const dimensions = o.points.map((p) => ({
      dimension_id: p.point_id,
      score: p.decided ? p.score : null,
      max_score: p.max,
      judged_by: "rubric_model",
      subjective: true,
      confidence: p.decided ? RUBRIC_MODEL_CONFIDENCE : 0,
      evidence_ids: idsOf.get(p.point_id)!,
    }));
    const scoringPoints = o.points.map((p) => ({
      point_id: p.point_id,
      achieved: p.decided ? p.achieved : null,
      ...(p.decided ? { points_awarded: p.score } : {}),
      evidence_ids: idsOf.get(p.point_id)!,
    }));
    // 错因：模型逐点给出的四类归因 → 映射表（草稿）→ 跨科大类。同一类合并证据；只来自已判定的点
    const byCode = new Map<AttributionCode, string[]>();
    for (const p of o.points) {
      if (!p.decided || !p.attribution) continue;
      const ids = idsOf.get(p.point_id)!;
      byCode.set(p.attribution, [...(byCode.get(p.attribution) ?? []), ids[ids.length - 1]!]);
    }
    const errorAttributions = ATTRIBUTION_CODES.filter((c) => byCode.has(c)).map((c) => ({
      skill_node: `zhixing:${content.nodeByAttribution.get(c)!}`,
      subject_category: c,
      cross_subject_category: taxonomy.crossSubjectOf.get(c)!,
      confidence: RUBRIC_MODEL_CONFIDENCE,
      evidence_ids: uniq(byCode.get(c)!),
      algorithm_version: attributionAlgorithm(taxonomy.version),
    }));
    const undecided = o.points.filter((p) => !p.decided).map((p) => p.point_id);
    const verdict = undecided.length > 0 ? "needs_review" : o.total === item.total_score ? "correct" : o.total === 0 ? "incorrect" : "partial";
    const extra = ["subjective_dimension"];
    // 待复核的点：契约要求 verdict = needs_review 时带 verifier_boundary_case，维度分为 null 时带 dimension_unavailable
    if (undecided.length > 0) extra.push("verifier_boundary_case", "dimension_unavailable");
    if (o.fullScore) extra.push("full_score_second_review");
    if (o.modelMismatch || o.injectionSuspected) extra.push("low_confidence");
    if (req.appeal) extra.push("student_appeal");
    const evidence = [response, ...spans];
    return selfCheck("GradeResponse", {
      contract_version: version,
      result: {
        verdict,
        rubric_version: req.rubric_version,
        // 未经金标准校准（A4：合成标注集待教研提供），如实标 false
        score: {
          total: o.total,
          max_total: item.total_score,
          calibrated: false,
          ...(undecided.length > 0 ? { excluded_dimensions: undecided } : {}),
        },
        dimensions,
        scoring_points: scoringPoints,
        error_attributions: errorAttributions,
      },
      evidence,
      evidence_ids: evidence.map((e) => e.evidence_id),
      confidence: round3(Math.min(...dimensions.map((d) => d.confidence))),
      source_version: `rubric:${item.rubric_version};${PROMPT_SOURCE_VERSION};${content.itemsSourceVersion}`,
      model_version: o.modelVersion,
      ...envelopeFlags(evidence.map((e) => e.admission), extra),
    }, version);
  };

  const grade = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("GradeRequest", body, version);
    const req = body as GradeRequest;
    checkContext(req.context, true);
    // 路由表里判分任务（T2）只接到 L2：L3 请求在这里明确拒绝，不送路由（否则会变成 502）
    if (req.context.data_class !== "L2") {
      throw new ProblemError(422, "INVALID_REQUEST", "data_class is not supported for grade", "data_class L3 not supported for grade");
    }
    const item = findItem(req.attempt.item_ref);
    if (req.rubric_version !== item.rubric_version) {
      throw new ProblemError(400, "INVALID_REQUEST", "rubric_version does not match the content pack for this item");
    }
    const text = answerText(req.attempt, version);
    // v1.0：编排层带来的采分点必须与内容包里同一 rubric_version 的采分点一致（点号与分值）
    if (req.scoring_points) {
      const given = new Map(req.scoring_points.map((p) => [p.point_id, p.score]));
      const same = given.size === item.points.length && item.points.every((p) => given.get(p.point_id) === p.score);
      if (!same) throw new ProblemError(400, "INVALID_REQUEST", "scoring_points differ from the rubric_version in the content pack");
    }
    // appeal 缺省等于 false：归一后再算指纹
    const fp = requestFingerprint({ ...req, appeal: req.appeal === true } as unknown as Parameters<typeof requestFingerprint>[0]);
    const outcome = await idempotent("grade", req.context.idempotency_key, fp, () => judge(req, item, text, fp));
    return gradeResponse(req, version, item, text, fp, outcome);
  };

  // ---------- diagnose（mode = process） ----------
  const sameInstant = (a: string, b: string) => {
    const x = Date.parse(a);
    const y = Date.parse(b);
    return Number.isFinite(x) && x === y;
  };

  const diagnose = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("DiagnoseRequest", body, version);
    const req = body as DiagnoseRequest;
    checkContext(req.context, false);
    if (req.mode !== "process") throw unsupported(version, "zhixing diagnose supports mode process only");
    if (!req.attempts || req.attempts.length === 0) throw new ProblemError(422, "INVALID_REQUEST", "process diagnose needs at least one attempt");
    const attempts = req.attempts;
    const ids = attempts.map((a) => a.attempt_id).filter((x): x is string => typeof x === "string");
    if (new Set(ids).size !== ids.length) throw new ProblemError(422, "INVALID_REQUEST", "attempt_id is duplicated");
    const prior = req.prior_evidence ?? [];
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    const key = req.context.idempotency_key;

    const evidence: EvidenceDraft[] = [];
    const referencedPrior: string[] = [];
    const observations: Record<string, unknown>[] = [];
    attempts.forEach((a, i) => {
      const item = findItem(a.item_ref);
      const text = answerText(a, version);
      const newId = responseEvidenceId(a, text, () => deriveUuid("diagnose", key, fp, `a${i}:response`));
      evidence.push(responseEvidence(item, a, text, newId, version));
      // 既往判分：同一道作答的作答证据（平台从 evidence_event 取来）与指向它的采分点片段证据
      const priorResponses = prior.filter(
        (e) =>
          e.kind === "response_text" &&
          e.item_ref === a.item_ref &&
          (a.attempt_id && e.attempt_id ? e.attempt_id === a.attempt_id : sameInstant(e.occurred_at, a.occurred_at)),
      );
      if (priorResponses.some((e) => e.payload.text !== text)) {
        throw new ProblemError(422, "INVALID_REQUEST", "prior_evidence does not match the attempt");
      }
      const responseIds = new Set(priorResponses.map((e) => e.evidence_id));
      const nodes = itemNodes(item);
      const nodeRefs = new Set(nodes.map((n) => `zhixing:${n}`));
      const length = codePoints(text);
      const spans = prior.filter((e) => e.kind === "scoring_point_span" && responseIds.has(e.payload.source_evidence_id as string));
      for (const s of spans) {
        const start = s.payload.span_start as number;
        const end = s.payload.span_end as number;
        if (s.item_ref !== a.item_ref || !s.skill_node || !nodeRefs.has(s.skill_node) || !(start < end && end <= length)) {
          throw new ProblemError(422, "INVALID_REQUEST", "prior_evidence does not match the attempt");
        }
      }
      for (const n of nodes) {
        const hits = uniq(spans.filter((s) => s.skill_node === `zhixing:${n}`).map((s) => s.evidence_id));
        const hit = responseIds.size > 0 && hits.length > 0;
        referencedPrior.push(...hits);
        observations.push({
          skill_node: `zhixing:${n}`,
          outcome: hit ? "partial" : "inconclusive",
          confidence: hit ? PROCESS_HIT_CONFIDENCE : PROCESS_INCONCLUSIVE_CONFIDENCE,
          evidence_ids: [newId, ...hits],
          algorithm_version: DIAGNOSE_ALGORITHM,
        });
      }
    });
    const admissions = attempts.map((a) => findItem(a.item_ref).admission);
    // 计算是确定性的：请求校验都通过后才占用幂等键（只做同 key 不同请求体的冲突检测）
    await idempotent("diagnose", key, fp, async () => true);
    return selfCheck("DiagnoseResponse", {
      contract_version: version,
      result: { observations, error_attributions: [] },
      evidence,
      evidence_ids: uniq([...evidence.map((e) => e.evidence_id), ...referencedPrior]),
      confidence: round3(Math.min(...observations.map((o) => o.confidence as number))),
      source_version: `algo:${DIAGNOSE_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${DIAGNOSE_ALGORITHM}`,
      // 观测来自主观判分的证据，一律低置信并复核
      ...envelopeFlags(admissions, ["low_confidence"]),
    }, version);
  };

  // ---------- generate_variation（选题，不生成） ----------
  const generateVariation = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("GenerateVariationRequest", body, version);
    const req = body as GenerateVariationRequest;
    checkContext(req.context, false);
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);

    const source = req.source_item_ref ? findItem(req.source_item_ref) : undefined;
    let emptyReason: string | undefined;
    const targetNodes = new Set(source ? itemNodes(source) : []);
    if (req.skill_node) {
      const [subject, local] = req.skill_node.split(":", 2) as [string, string];
      if (subject !== "zhixing") throw new ProblemError(400, "SUBJECT_MISMATCH", "skill_node is not a zhixing node");
      if (content.nodeIds.has(local)) targetNodes.add(local);
      else emptyReason = "skill_node_unknown";
    }
    const category = (ATTRIBUTION_CODES as readonly string[]).includes(req.subject_error_category ?? "")
      ? (req.subject_error_category as AttributionCode)
      : undefined;
    const excluded = new Set([...(req.exclude_item_refs ?? []), ...(req.source_item_ref ? [req.source_item_ref] : [])]);
    const family = req.item_family ? new Set(req.item_family.map((f) => f.item_ref)) : undefined;
    let candidates: Record<string, unknown>[] = [];
    if (!emptyReason) {
      const matched = content.pack.items
        .filter((it) => it.discipline === "daofa" && it.id !== source?.id)
        .map((it) => ({
          it,
          ref: `zhixing:${it.id}`,
          same_skill_node: itemNodes(it).some((n) => targetNodes.has(n)),
          same_error_category: category !== undefined && itemErrorCategories(it).has(category),
        }))
        .filter((m) => m.same_skill_node || m.same_error_category)
        // 只从已准入的题里选（契约 v1；adapter-design 3.1）；编排层给了同族候选时只在其中选
        .filter((m) => m.it.admission === "admitted" && (!family || family.has(m.ref)));
      const eligible = matched
        .filter((m) => !excluded.has(m.ref))
        .sort((a, b) => Number(b.same_skill_node && b.same_error_category) - Number(a.same_skill_node && a.same_error_category) || (a.it.id < b.it.id ? -1 : 1))
        .slice(0, req.count);
      candidates = eligible.map((m) => ({
        item_ref: m.ref,
        admission: "admitted",
        content_version: { ...content.pack.curriculum },
        match: { same_skill_node: m.same_skill_node, same_error_category: m.same_error_category },
      }));
      if (candidates.length === 0) emptyReason = matched.length > 0 ? "all_excluded" : "no_admitted_candidate";
    }
    await idempotent("generate_variation", req.context.idempotency_key, fp, async () => true);
    return selfCheck("GenerateVariationResponse", {
      contract_version: version,
      result: { candidates, ...(candidates.length === 0 ? { empty_reason: emptyReason } : {}) },
      evidence_ids: [],
      confidence: 1,
      source_version: `algo:${SELECT_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${SELECT_ALGORITHM}`,
      human_review_required: false,
      semantics: "formal",
    }, version);
  };

  // ---------- plan（领域权重 × 失分 × 可提升） ----------
  /** 引用必须是知行的（v1.0 schema 没有给 zhixing 的 plan、report 加前缀约束，适配器自查；评审 N7） */
  const zhixingLocal = (ref: string, what: string): string => {
    const [subject, local] = ref.split(":", 2) as [string, string];
    if (subject !== "zhixing") throw new ProblemError(400, "SUBJECT_MISMATCH", `${what} is not a zhixing reference`);
    return local;
  };
  const nodeOrder = content.pack.nodes.map((n) => n.id);
  const units = planUnits(content.pack.items, nodeOrder, planWeights.domainOfPoint, (d) => planWeights.domainByCode.get(d)!.weight);

  const plan = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("PlanRequest", body, version);
    const req = body as PlanRequest;
    checkContext(req.context, false);
    // 平台掌握快照：不认识的节点跳过；同一节点出现多次取最后一条（与文砺 plan 一致）
    const mastery = new Map<string, MasteryInput>();
    for (const m of req.mastery) {
      const local = zhixingLocal(m.skill_node, "skill_node");
      if (content.nodeIds.has(local)) mastery.set(local, { state: m.state, ...(m.p_mastery === undefined ? {} : { p_mastery: m.p_mastery }) });
    }
    const focus = new Set((req.constraints?.focus_skill_nodes ?? []).map((r) => zhixingLocal(r, "focus_skill_nodes")));
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    const key = req.context.idempotency_key;

    const ranked = rankUnits(focus.size > 0 ? units.filter((u) => focus.has(u.skillNode)) : units, mastery, nodeOrder);
    const budget = req.constraints?.time_budget_minutes;
    const selected: (typeof ranked)[number][] = [];
    const picks: { refs: string[]; minutes: number }[] = [];
    let used = 0;
    for (const u of ranked) {
      if (budget === undefined && selected.length >= MAX_TASKS_WITHOUT_BUDGET) break;
      // 学生只拿到已准入的题（与 generate_variation 同一口径）；没有已准入题时不给 item_refs，由编排层另行选题
      const refs = u.items.filter((it) => it.admission === "admitted").slice(0, MAX_ITEMS_PER_TASK).map((it) => `zhixing:${it.id}`);
      const minutes = MINUTES_PER_ITEM * Math.max(1, refs.length);
      // 时间预算内按优先级截断：放不下的任务及其后的都不要（与文砺 plan 一致）
      if (budget !== undefined && used + minutes > budget) break;
      used += minutes;
      selected.push(u);
      picks.push({ refs, minutes });
    }
    const taskIds = selected.map((u) => deriveUuid("plan", key, fp, `task:${u.skillNode}`));
    const prereq = prerequisiteEdges(selected, req.node_path ?? []);
    const tasks = selected.map((u, i) => ({
      task_id: taskIds[i]!,
      kind: u.kind,
      skill_nodes: [`zhixing:${u.skillNode}`],
      ...(picks[i]!.refs.length > 0 ? { item_refs: picks[i]!.refs } : {}),
      priority: round3(u.raw),
      estimated_minutes: picks[i]!.minutes,
      ...(prereq.has(i) ? { prerequisite_task_ids: prereq.get(i)!.map((j) => taskIds[j]!) } : {}),
      rationale_code: u.rationale,
    }));
    await idempotent("plan", key, fp, async () => true);
    return selfCheck("PlanResponse", {
      contract_version: version,
      result: { tasks },
      evidence_ids: [],
      confidence: 1,
      source_version: `algo:${PLAN_ALGORITHM};${planWeights.sourceVersion};${content.itemsSourceVersion}`,
      model_version: `algo:${PLAN_ALGORITHM}`,
      human_review_required: false,
      semantics: "formal",
    }, version);
  };

  // ---------- report（逐维作答计数、四类失分归因计数、证据 ID；不写结论句） ----------
  const report = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("ReportRequest", body, version);
    const req = body as ReportRequest;
    checkContext(req.context, false);
    const evidence = req.evidence ?? [];
    const attributions = req.error_attributions ?? [];
    const history = req.mastery_history ?? [];
    for (const e of evidence) {
      if (e.item_ref) zhixingLocal(e.item_ref, "evidence item_ref");
      if (e.skill_node) zhixingLocal(e.skill_node, "evidence skill_node");
    }
    for (const a of attributions) if (a.skill_node) zhixingLocal(a.skill_node, "error_attributions skill_node");
    for (const h of history) zhixingLocal(h.skill_node, "mastery_history skill_node");
    const from = instant(req.window.from);
    const to = instant(req.window.to);
    if (!(Number.isFinite(from) && Number.isFinite(to) && to > from)) throw new ProblemError(422, "INVALID_REQUEST", "window.to must be later than window.from");
    // 失分归因只收知行四类，且跨科大类必须与映射表一致（MORAL 口径，不与语文 CHN 同名编码混算）
    for (const a of attributions) {
      const code = a.subject_category as AttributionCode;
      if (!(ATTRIBUTION_CODES as readonly string[]).includes(code) || taxonomy.crossSubjectOf.get(code) !== a.cross_subject_category) {
        throw new ProblemError(422, "INVALID_REQUEST", "error_attributions do not match the zhixing error taxonomy");
      }
    }
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);

    // 逐维作答计数：作答证据挂到题目涉及的全部维度节点（内容包里的道法题），不在内容包里的按证据自带的 skill_node
    const attemptsByNode = new Map<string, Set<string>>();
    const counted: EvidenceRecord[] = [];
    for (const e of evidence) {
      if (!inWindow(e.occurred_at, from, to)) continue;
      if (ATTEMPT_KINDS.has(e.kind)) {
        const item = e.item_ref ? content.itemById.get(zhixingLocal(e.item_ref, "evidence item_ref")) : undefined;
        const nodes = item && item.discipline === "daofa" ? itemNodes(item) : e.skill_node ? [zhixingLocal(e.skill_node, "evidence skill_node")] : [];
        if (nodes.length === 0) continue;
        const attemptKey = e.attempt_id ?? e.evidence_id;
        for (const n of nodes) attemptsByNode.set(n, (attemptsByNode.get(n) ?? new Set()).add(attemptKey));
        counted.push(e);
      } else if (e.kind === "scoring_point_span") {
        counted.push(e);
      }
    }
    const nodes = new Set<string>(attemptsByNode.keys());
    for (const h of history) if (instant(h.at) < to) nodes.add(zhixingLocal(h.skill_node, "mastery_history skill_node"));
    const order = (n: string) => (nodeOrder.includes(n) ? nodeOrder.indexOf(n) : nodeOrder.length);
    const skillSummary = [...nodes]
      .sort((a, b) => order(a) - order(b) || (a < b ? -1 : a > b ? 1 : 0))
      .map((n) => ({
        skill_node: `zhixing:${n}`,
        trend: trendOf(history.filter((h) => h.skill_node === `zhixing:${n}`), from, to),
        attempts: attemptsByNode.get(n)?.size ?? 0,
      }));
    // 四类失分归因逐类计数（四类都列出，没有的记 0）
    const errorDistribution = ATTRIBUTION_CODES.map((c) => ({
      cross_subject_category: taxonomy.crossSubjectOf.get(c)!,
      subject_category: c,
      count: attributions.filter((a) => a.subject_category === c).length,
    }));
    const evidenceIds = uniq([...counted.map((e) => e.evidence_id), ...attributions.flatMap((a) => a.evidence_ids)]);
    await idempotent("report", req.context.idempotency_key, fp, async () => true);
    return selfCheck("ReportResponse", {
      contract_version: version,
      result: { skill_summary: skillSummary, error_distribution: errorDistribution },
      evidence_ids: evidenceIds,
      confidence: attributions.length > 0 ? round3(Math.min(...attributions.map((a) => a.confidence))) : 1,
      source_version: `algo:${REPORT_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${REPORT_ALGORITHM}`,
      // 不判分：不置复核；计入的证据有未准入的 → 只作练习效力
      human_review_required: false,
      semantics: counted.every((e) => e.admission === "admitted") ? "formal" : "practice",
    }, version);
  };

  // ---------- present_item（契约 1.1.0 候选版，PLT-12；只读、不调模型） ----------
  /**
   * 题面：只按 1.1.0 应答（更低版本 501）。不带 usage_id（schema 已拒）；顶层 content_version 必须是本包，
   * context.content_version 同时出现时必须与它相同。历史题与 grade 同一口径（契约 v1 只接道法）。同 key 不同请求体 → 409。
   */
  const present = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    if (version !== "1.1.0") {
      throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", "present_item is implemented for contract 1.1.0 only");
    }
    validateOrThrow("PresentItemRequest", body, version);
    const req = body as { context: RequestContext; item_ref: string; content_version: Record<string, string | number> };
    if (req.context.content_version && canonical(req.context.content_version) !== canonical(req.content_version)) {
      throw new ProblemError(400, "INVALID_REQUEST", "context.content_version and content_version differ");
    }
    checkContext({ ...req.context, content_version: req.content_version } as RequestContext, true);
    // 契约 PresentItemContext：不带 student_ref、usage_id，幂等键可选。只读、结果由（题目, 内容版本）确定，
    // 不进幂等缓存（审查阻断 2：进缓存只会挤掉 grade 等写方法的条目）
    const item = findItem(req.item_ref);
    return selfCheck("PresentItemResponse", {
      contract_version: version,
      result: presentItem(content, item, MAX_ANSWER_CHARS),
      evidence_ids: [],
      confidence: 1,
      source_version: `algo:${PRESENT_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${PRESENT_ALGORITHM}`,
      human_review_required: false,
      semantics: item.admission === "admitted" ? "formal" : "practice",
    }, version);
  };

  const NOT_IMPLEMENTED = new Set(["teach"]);
  const KNOWN_ROUTES = new Set(["capabilities", "grade", "diagnose", "generate_variation", "plan", "report", "present_item", ...NOT_IMPLEMENTED]);

  /** 路由（路径不含 /plugin/v0 前缀）。所有错误都转成契约 Problem。 */
  const handle = async (method: string, path: string, body: unknown): Promise<HandlerResult> => {
    const started = performance.now();
    const requestId = (body as { context?: { request_id?: unknown } } | undefined)?.context?.request_id;
    const rid = typeof requestId === "string" && UUID_RE.test(requestId) ? requestId : undefined;
    let result: HandlerResult;
    let code: ProblemCode | undefined;
    let errorClass: string | undefined;
    let routeFailure: LogRecord["route_failure"];
    try {
      const name = path.replace(/^\/+/, "");
      if (method === "GET" && name === "capabilities") result = capabilities();
      else if (method === "POST" && name === "grade") result = await grade(body);
      else if (method === "POST" && name === "diagnose") result = await diagnose(body);
      else if (method === "POST" && name === "generate_variation") result = await generateVariation(body);
      else if (method === "POST" && name === "plan") result = await plan(body);
      else if (method === "POST" && name === "report") result = await report(body);
      else if (method === "POST" && name === "present_item") result = await present(body);
      else if (method === "POST" && NOT_IMPLEMENTED.has(name)) {
        throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", `${name} is not implemented by the zhixing adapter yet`);
      } else if (method === "GET" && name.startsWith("jobs/")) {
        throw new ProblemError(404, "JOB_NOT_FOUND", "the zhixing adapter runs synchronously and has no jobs");
      } else throw new ProblemError(404, "INVALID_REQUEST", "unknown route");
    } catch (e) {
      if (!(e instanceof ProblemError)) errorClass = e instanceof Error ? e.constructor.name.slice(0, 48) : typeof e;
      const err = e instanceof ProblemError ? e : new ProblemError(500, "INTERNAL", "internal error");
      code = err.code;
      routeFailure = err.routeFailure;
      result = problem(err, rid);
    }
    const logged = path.replace(/^\/+/, "");
    log({
      method: KNOWN_ROUTES.has(logged) ? logged : logged.startsWith("jobs/") ? "jobs" : "unknown",
      status: result.status,
      ...(code ? { code } : {}),
      ...(rid ? { request_id: rid } : {}),
      duration_ms: Math.round(performance.now() - started),
      ...(errorClass ? { error_class: errorClass } : {}),
      ...(routeFailure ? { route_failure: routeFailure } : {}),
    });
    return result;
  };

  return { capabilities, handle };
}

export type Adapter = ReturnType<typeof createAdapter>;
