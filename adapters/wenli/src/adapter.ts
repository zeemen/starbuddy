// 文砺（语文）适配器的方法：capabilities、grade（作文五维）、diagnose（原生自适应测评：客观题确定性判分）、plan（确定性学科处方）、
// generate_variation（只从已准入题库选题）、report（逐维计数与证据 ID）。
// 规格：specs/adapter-wenli-sprint2/spec.md（待人确认 #1–#8 按默认执行）；generate_variation、report 见
// specs/adapter-zhixing-wenli-methods-sprint4/spec.md（待人确认 #1–#4 按默认：确定性、不调模型；teach 不做）。模板：adapters/zhixing/src/adapter.ts。
// 契约：packages/contracts/openapi/plugin-api.v0.9.yaml 与 v1.0 候选版；按请求的 context.contract_version 校验与应答。
//
// 边界：
// - 适配器不读写任何平台表（CLAUDE.md 规则 1）：证据与推断放在响应里，由学习服务落库。
// - 判分只来自量表与确定性规则（规则 3）：模型逐维定档 → 确定性后处理（core/essay-postprocess.ts）；客观题是确定性匹配。结果里没有自由文本。
// - 模型调用只经学习服务内部路由接口（route-client.ts），适配器不持有供应商信息（ADR 04 第 1 条）；diagnose、plan 不调模型。
// - 作文一律低置信并复核（待人确认 #1）；不输出掌握度（规则 2）；diagnose 只输出观测、plan 只输出编码，不写关于学生的陈述（规则 11）；
//   没有位次、没有预计提分。generate_variation 不生成新题；report 不写结论句与评价（规则 11）。
// - 响应、日志、错误详情里不出现题面、选项、标准答案、量表锚点、模型原文；学生原文只出现在作答证据的 payload 里。
import { CANDIDATE_CONTRACT_VERSIONS, type ContractValidator, type ContractVersion, type SchemaName } from "./contract.ts";
import { CHOICE_CODE, questionTypeOf, type Admission, type EssayItem, type LoadedPack, type ObjectiveItem } from "./content.ts";
import { recommendTargets, type CellState } from "./core/adaptive.ts";
import { evidenceLevel, LEVEL_WEIGHT, type PriorRecord } from "./core/channel.ts";
import { buildEssayGradeMessages, hasInjectionSignal, PROMPT_SOURCE_VERSION, PROMPT_TEMPLATE, PROMPT_VERSION } from "./core/essay-prompt.ts";
import { conventionScore, parseModelOutput, postProcessEssay, rejectAllModelDimensions, type EssayPostProcessed } from "./core/essay-postprocess.ts";
import { gradeObjective } from "./core/objective.ts";
import { ATTEMPT_KINDS, inWindow, instant, trendOf, type HistoryRecord } from "./core/report.ts";
import { contentUuid, deriveUuid, requestFingerprint, routeIdempotencyKey } from "./ids.ts";
import { callRoute, RouteError, type FetchLike, type RouteConfig, type RouteFailure } from "./route-client.ts";
import { loadTaxonomy, type WenliTaxonomy } from "./taxonomy.ts";

export const ADAPTER_VERSION = "0.1.0";
/** 模型定档的主观维度置信度（契约与 0005 的 CHECK：≤ 0.5；未经金标准校准） */
export const RUBRIC_MODEL_CONFIDENCE = 0.4;
/** 字数规范维（确定性规则）的置信度（spec 设计要点 2） */
export const CONVENTION_CONFIDENCE = 0.9;
export const DIAGNOSE_ALGORITHM = "wenli-objective-diag@0.1.0";
export const PLAN_ALGORITHM = "wenli-plan@0.1.0";
export const SELECT_ALGORITHM = "wenli-item-select@0.1.0";
export const REPORT_ALGORITHM = "wenli-report@0.1.0";
export const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;
/** 作文长度上限（码点） */
export const MAX_ESSAY_CHARS = 5000;
/** 填空作答长度上限（码点） */
export const MAX_FILL_CHARS = 200;
/** 没有时间预算时，plan 推荐的目标数（legacy recommendTargets 默认 3） */
export const DEFAULT_PLAN_TARGETS = 3;
/** 每类任务的最少分钟数（题目自带的 estimated_minutes 之和更大时取和） */
export const MIN_TASK_MINUTES: Readonly<Record<"diagnose" | "practice", number>> = { diagnose: 5, practice: 10 };
/**
 * 平台掌握状态（MasterySnapshot.state）→ 用于“离最优练习窗多远”排序的数值（p_mastery 缺省时）。
 * 已掌握 = transfer、retain（星亮门槛，ADR 11）。映射未经教研确认（README 待人确认）。
 */
export const STATE_PROXY: Readonly<Record<string, number>> = { understand: 0.3, execute: 0.5, stable: 0.75, transfer: 0.9, retain: 0.95 };
export const MASTERED_STATES: ReadonlySet<string> = new Set(["transfer", "retain"]);

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
  response: Record<string, unknown> & { text?: string; choice?: string[] };
  elapsed_sec?: number;
}
interface GradeRequest {
  context: RequestContext;
  attempt: AttemptInput;
  rubric_version: string;
  appeal?: boolean;
  scoring_points?: { point_id: string; score: number }[];
}
interface EvidenceRecord extends PriorRecord {
  evidence_id: string;
  skill_node?: string;
  admission: Admission;
}
interface DiagnoseRequest {
  context: RequestContext;
  mode: "intake" | "paper" | "gap_test" | "process";
  attempts?: AttemptInput[];
  paper_image_refs?: string[];
  prior_evidence?: EvidenceRecord[];
}
interface PlanRequest {
  context: RequestContext;
  mastery: { skill_node: string; state: string; p_mastery?: number }[];
  constraints?: { time_budget_minutes?: number; exam_date?: string; focus_skill_nodes?: string[] };
}
interface GenerateVariationRequest {
  context: RequestContext;
  source_item_ref?: string;
  skill_node?: string;
  subject_error_category?: string;
  count: number;
  exclude_item_refs?: string[];
  item_family?: { item_ref: string; prior_difficulty?: number }[];
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
  evidence?: (EvidenceRecord & { kind: string; occurred_at: string; item_ref?: string; attempt_id?: string })[];
  error_attributions?: ErrorAttributionInput[];
  mastery_history?: (HistoryRecord & { p_mastery?: number })[];
}
type EvidenceDraft = Omit<EvidenceRecord, "payload"> & { payload: Record<string, unknown> };

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
  | "JOB_NOT_FOUND"
  | "INTERNAL";

export interface HandlerResult {
  status: number;
  body: unknown;
}

/** 日志记录只含这些字段：不写作答、题面、量表、模型输出（契约 Problem.detail） */
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
  /** 错因映射表（report 核对错因记录用）；缺省读 docs/mapping/error-taxonomy.v0.9.yaml */
  taxonomy?: WenliTaxonomy;
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

/** 路由失败 → 契约 Problem。5xx 一律按结果未知处理（契约 v1.0 N18）；retryable 只标可以原键重试的情况（同知行） */
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
  processed: EssayPostProcessed;
  modelVersion: string;
  modelMismatch: boolean;
  /** 作文里有指令特征（Prompt 注入的信号）：复核原因加 low_confidence */
  injectionSuspected: boolean;
}

export function createAdapter(deps: AdapterDeps) {
  const { content, contract } = deps;
  const log = deps.log ?? (() => {});
  const cacheSize = deps.idempotencyCacheSize ?? 10_000;
  const fetchImpl = deps.fetchImpl ?? fetch;
  const rubric = content.pack.rubric;
  const taxonomy = deps.taxonomy ?? loadTaxonomy();
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
    if (ctx.subject !== "wenli") throw new ProblemError(400, "SUBJECT_MISMATCH", "this adapter only serves subject wenli");
    const cv = ctx.content_version;
    if (!cv) {
      if (requireContentVersion) throw new ProblemError(400, "INVALID_REQUEST", "content_version is required");
      return;
    }
    const served = content.pack.curriculum;
    const same = (Object.keys(served) as (keyof typeof served)[]).every((k) => cv[k] === served[k]);
    if (!same) throw new ProblemError(404, "CONTENT_VERSION_NOT_FOUND", "content_version is not served by this adapter");
  };

  /** 引用的学科前缀与本地 ID */
  const localRef = (ref: string, what: string): string => {
    const [subject, local] = ref.split(":", 2) as [string, string];
    if (subject !== "wenli") throw new ProblemError(400, "SUBJECT_MISMATCH", `${what} is not a wenli reference`);
    return local;
  };

  const findEssay = (itemRef: string, version: ContractVersion): EssayItem => {
    const local = localRef(itemRef, "item_ref");
    const essay = content.essayById.get(local);
    if (essay) return essay;
    if (content.itemById.has(local)) throw unsupported(version, "wenli grade accepts essay items only; objective items go through diagnose");
    throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
  };

  const findObjective = (itemRef: string, version: ContractVersion): ObjectiveItem => {
    const local = localRef(itemRef, "item_ref");
    const item = content.itemById.get(local);
    if (item) return item;
    if (content.essayById.has(local)) throw unsupported(version, "wenli diagnose accepts objective items only; essays go through grade");
    throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
  };

  const onlyKey = (r: Record<string, unknown>) => {
    const keys = Object.keys(r);
    return keys.length === 1 ? keys[0] : undefined;
  };

  /** 作文只接文字作答：非空，长度不超过上限 */
  const essayText = (attempt: AttemptInput, version: ContractVersion): string => {
    const r = attempt.response;
    if (onlyKey(r) !== "text" || typeof r.text !== "string") throw unsupported(version, "wenli essay grading accepts response.text only");
    if (r.text.trim().length === 0) throw new ProblemError(422, "INVALID_REQUEST", "response.text is empty");
    if (codePoints(r.text) > MAX_ESSAY_CHARS) throw new ProblemError(422, "INVALID_REQUEST", `response.text exceeds ${MAX_ESSAY_CHARS} characters`);
    return r.text;
  };

  /** 客观题的作答形态必须与题型一致：选择题 response.choice，填空题 response.text（拍照原图、转写引用不接，ADR 15） */
  const objectiveResponse = (item: ObjectiveItem, attempt: AttemptInput, version: ContractVersion): { choice: string[] } | { text: string } => {
    const r = attempt.response;
    const key = onlyKey(r);
    if (item.type === "choice") {
      if (key !== "choice" || !Array.isArray(r.choice)) throw unsupported(version, "choice items accept response.choice only");
      if (!r.choice.every((c) => typeof c === "string" && CHOICE_CODE.test(c)) || r.choice.length > 16) {
        throw new ProblemError(422, "INVALID_REQUEST", "response.choice values must be option codes");
      }
      return { choice: r.choice };
    }
    if (key !== "text" || typeof r.text !== "string") throw unsupported(version, "fill items accept response.text only");
    if (codePoints(r.text) > MAX_FILL_CHARS) throw new ProblemError(422, "INVALID_REQUEST", `response.text exceeds ${MAX_FILL_CHARS} characters`);
    return { text: r.text };
  };

  /** 作答证据的 ID：有 attempt_id 时按作答本身派生（同一道作答在各方法里同一个 ID），否则按 key 与指纹 */
  const responseEvidenceId = (attempt: AttemptInput, fallback: () => string) =>
    attempt.attempt_id ? contentUuid("response", attempt.attempt_id, attempt.item_ref, attempt.occurred_at, JSON.stringify(attempt.response)) : fallback();

  const responseEvidence = (
    itemRef: string,
    cell: string,
    admission: Admission,
    attempt: AttemptInput,
    payload: { text: string } | { choice: string[] },
    id: string,
    version: ContractVersion,
  ): EvidenceDraft => ({
    evidence_id: id,
    ...(version !== "0.9.0" && attempt.attempt_id ? { attempt_id: attempt.attempt_id } : {}),
    kind: "choice" in payload ? "response_choice" : "response_text",
    occurred_at: attempt.occurred_at,
    item_ref: itemRef,
    skill_node: `wenli:${cell}`,
    admission,
    payload,
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
   * 幂等包装（同知行）：同 key 同请求体 → 同一结果；同 key 不同请求体 → IDEMPOTENCY_CONFLICT。
   * 缓存里只存请求体指纹与判定结果（维度、档位、分值、字符位置、模型版本），不存学生原文与模型原文。
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
      subject: "wenli",
      adapter_version: ADAPTER_VERSION,
      contract_version: contract.version,
      methods: {
        diagnose: { implemented: true, async: false, notes_code: "objective_gap_test_intake_only" },
        plan: { implemented: true },
        teach: { implemented: false },
        grade: { implemented: true, async: false, notes_code: "handwriting_dimension_null" },
        generate_variation: { implemented: true, async: false, notes_code: "admitted_items_only" },
        report: { implemented: true, async: false, notes_code: "counts_only" },
      },
    }, contract.version);

  // ---------- grade（作文五维） ----------
  const judge = async (req: GradeRequest, essay: EssayItem, text: string, fp: string): Promise<GradeOutcome> => {
    if (!deps.route) throw routeProblem("not_configured");
    let reply;
    try {
      reply = await callRoute(deps.route, {
        task: "T4",
        data_class: "L2",
        subject: "wenli",
        prompt_template: PROMPT_TEMPLATE,
        prompt_version: PROMPT_VERSION,
        messages: buildEssayGradeMessages(essay, rubric, content.modelDimensions, text),
        usage_id: req.context.usage_id!,
        idempotency_key: routeIdempotencyKey(req.context.idempotency_key, fp),
        deadline_ms: deps.route.deadlineMs,
      }, fetchImpl);
    } catch (e) {
      if (e instanceof RouteError) throw routeProblem(e.failure);
      throw e;
    }
    const injectionSuspected = hasInjectionSignal(text);
    const judged = postProcessEssay(content.modelDimensions, parseModelOutput(reply.output), text);
    const processed = injectionSuspected ? rejectAllModelDimensions(judged, "injection_suspected") : judged;
    return { processed, modelVersion: reply.model_version, modelMismatch: reply.model_mismatch, injectionSuspected };
  };

  const gradeResponse = (req: GradeRequest, version: ContractVersion, essay: EssayItem, text: string, fp: string, o: GradeOutcome) => {
    const key = req.context.idempotency_key;
    const itemRef = `wenli:${essay.id}`;
    const cellRef = `wenli:${essay.cell}`;
    const responseId = responseEvidenceId(req.attempt, () => deriveUuid("grade", key, fp, "a0:response"));
    const response = responseEvidence(itemRef, essay.cell, essay.admission, req.attempt, { text }, responseId, version);
    const spans: EvidenceDraft[] = [];
    const modelDims = o.processed.dimensions.map((d) => {
      const ids = [responseId];
      if (d.decided) {
        const [start, end] = d.span;
        const spanId = contentUuid("span", responseId, req.rubric_version, d.dimension_id, String(start), String(end));
        spans.push({
          evidence_id: spanId,
          ...(version !== "0.9.0" && req.attempt.attempt_id ? { attempt_id: req.attempt.attempt_id } : {}),
          kind: "scoring_point_span",
          occurred_at: req.attempt.occurred_at,
          item_ref: itemRef,
          skill_node: cellRef,
          admission: essay.admission,
          payload: { source_evidence_id: responseId, span_start: start, span_end: end },
        });
        ids.push(spanId);
      }
      return {
        dimension_id: d.dimension_id,
        score: d.decided ? d.score : null,
        max_score: d.max,
        judged_by: "rubric_model",
        subjective: true,
        confidence: d.decided ? RUBRIC_MODEL_CONFIDENCE : 0,
        ...(d.decided ? { uncertainty: d.uncertainty } : {}),
        evidence_ids: ids,
      };
    });
    // 卷面书写：需要卷面图片，路由接口只收文本 → 不可判（待人确认 #4）
    const imageDims = content.imageDimensions.map((d) => ({
      dimension_id: d.dimension_id,
      score: null,
      max_score: d.max_score,
      judged_by: "rubric_model",
      subjective: true,
      confidence: 0,
      evidence_ids: [responseId],
    }));
    // 字数规范：确定性规则，不调模型（待人确认 #3）
    const conv = conventionScore(rubric.convention, essay.min_chars, text);
    const conventionDim = {
      dimension_id: rubric.convention.dimension_id,
      score: conv.score,
      max_score: rubric.convention.max_score,
      judged_by: "rubric_rule",
      subjective: false,
      rule_ref: rubric.convention.rule_ref,
      confidence: CONVENTION_CONFIDENCE,
      evidence_ids: [responseId],
    };
    const dimensions = [...modelDims, ...imageDims, conventionDim];
    const undecided = o.processed.dimensions.filter((d) => !d.decided).map((d) => d.dimension_id);
    const excluded = [...undecided, ...imageDims.map((d) => d.dimension_id)];
    const total = o.processed.modelTotal === null ? null : o.processed.modelTotal + conv.score;
    const extra = ["subjective_dimension", "dimension_unavailable"];
    // 契约：verdict = needs_review 时带 verifier_boundary_case
    if (undecided.length > 0) extra.push("verifier_boundary_case");
    // 一类卷（各维最高档）强制复核
    if (o.processed.topBand) extra.push("full_score_second_review");
    // 模型维不可判（模型输出不合格、越界、命中注入等）也加 low_confidence，与卷面维“按设计不可判”区分开（契约审查建议 3）
    if (o.modelMismatch || o.injectionSuspected || undecided.length > 0) extra.push("low_confidence");
    if (req.appeal) extra.push("student_appeal");
    const evidence = [response, ...spans];
    // 整体置信度：取可判维度（卷面维除外）的最低值；有模型维不可判时为 0
    const scored = [...modelDims, conventionDim];
    return selfCheck("GradeResponse", {
      contract_version: version,
      result: {
        // 作文没有“对 / 错”：全部可判时为 partial（契约示例同），有维度不可判时 needs_review
        verdict: undecided.length > 0 ? "needs_review" : "partial",
        rubric_version: req.rubric_version,
        // 未经金标准校准（A6：合成标注集待教研提供），如实标 false
        score: { total, max_total: rubric.total_score, calibrated: false, excluded_dimensions: excluded },
        dimensions,
      },
      evidence,
      evidence_ids: evidence.map((e) => e.evidence_id),
      confidence: round3(Math.min(...scored.map((d) => d.confidence))),
      ...(o.processed.modelUncertainty
        ? { uncertainty: { low: o.processed.modelUncertainty.low + conv.score, high: o.processed.modelUncertainty.high + conv.score } }
        : {}),
      source_version: `rubric:${rubric.rubric_version};rubric:${rubric.convention.rule_ref};${PROMPT_SOURCE_VERSION};${content.itemsSourceVersion}`,
      model_version: o.modelVersion,
      ...envelopeFlags(evidence.map((e) => e.admission), extra),
    }, version);
  };

  const grade = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("GradeRequest", body, version);
    const req = body as GradeRequest;
    checkContext(req.context, true);
    // 路由表里作文判分任务（T4）只接到 L2：L3 请求在这里明确拒绝，不送路由（否则会变成 502）
    if (req.context.data_class !== "L2") {
      throw new ProblemError(422, "INVALID_REQUEST", "data_class is not supported for grade", "data_class L3 not supported for grade");
    }
    const essay = findEssay(req.attempt.item_ref, version);
    if (req.rubric_version !== essay.rubric_version) {
      throw new ProblemError(400, "INVALID_REQUEST", "rubric_version does not match the content pack for this item");
    }
    const text = essayText(req.attempt, version);
    // v1.0：编排层带来的采分点（point_id = dimension_id）必须与内容包里同一量表的维度与满分一致
    if (req.scoring_points) {
      const all = [...rubric.dimensions.map((d) => [d.dimension_id, d.max_score] as const), [rubric.convention.dimension_id, rubric.convention.max_score] as const];
      const given = new Map(req.scoring_points.map((p) => [p.point_id, p.score]));
      const same = given.size === all.length && all.every(([id, max]) => given.get(id) === max);
      if (!same) throw new ProblemError(400, "INVALID_REQUEST", "scoring_points differ from the rubric_version in the content pack");
    }
    // appeal 缺省等于 false：归一后再算指纹
    const fp = requestFingerprint({ ...req, appeal: req.appeal === true } as unknown as Parameters<typeof requestFingerprint>[0]);
    const outcome = await idempotent("grade", req.context.idempotency_key, fp, () => judge(req, essay, text, fp));
    return gradeResponse(req, version, essay, text, fp, outcome);
  };

  // ---------- diagnose（mode = gap_test / intake：客观题确定性判分） ----------
  const diagnose = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("DiagnoseRequest", body, version);
    const req = body as DiagnoseRequest;
    checkContext(req.context, false);
    if (req.mode !== "gap_test" && req.mode !== "intake") throw unsupported(version, "wenli diagnose supports modes gap_test and intake only");
    if (req.paper_image_refs && req.paper_image_refs.length > 0) throw unsupported(version, "wenli diagnose does not read paper images");
    if (!req.attempts || req.attempts.length === 0) throw new ProblemError(422, "INVALID_REQUEST", "diagnose needs at least one attempt");
    const attempts = req.attempts;
    const ids = attempts.map((a) => a.attempt_id).filter((x): x is string => typeof x === "string");
    if (new Set(ids).size !== ids.length) throw new ProblemError(422, "INVALID_REQUEST", "attempt_id is duplicated");
    const prior = req.prior_evidence ?? [];
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    const key = req.context.idempotency_key;

    const evidence: EvidenceDraft[] = [];
    const observations: Record<string, unknown>[] = [];
    const needsConfirmation: string[] = [];
    attempts.forEach((a, i) => {
      const item = findObjective(a.item_ref, version);
      const response = objectiveResponse(item, a, version);
      const itemRef = `wenli:${item.id}`;
      const id = responseEvidenceId(a, () => deriveUuid("diagnose", key, fp, `a${i}:response`));
      evidence.push(responseEvidence(itemRef, item.cell, item.admission, a, response, id, version));
      const weight = LEVEL_WEIGHT[evidenceLevel(a, prior)];
      // C 级（未确认的拍照作答）：不判分，请学生先确认（契约 DiagnoseResult.ocr_confirmation_required；ADR 15）
      if (weight === null) {
        needsConfirmation.push(itemRef);
        return;
      }
      observations.push({
        skill_node: `wenli:${item.cell}`,
        outcome: gradeObjective(item, response) ? "demonstrated" : "not_demonstrated",
        local_level: content.cellById.get(item.cell)!.level,
        confidence: weight,
        evidence_ids: [id],
        algorithm_version: DIAGNOSE_ALGORITHM,
      });
    });
    const admissions = attempts.map((a) => findObjective(a.item_ref, version).admission);
    // 计算是确定性的：请求校验都通过后才占用幂等键（只做同 key 不同请求体的冲突检测）
    await idempotent("diagnose", key, fp, async () => true);
    const confirm = uniq(needsConfirmation);
    return selfCheck("DiagnoseResponse", {
      contract_version: version,
      result: { observations, error_attributions: [], ...(confirm.length > 0 ? { ocr_confirmation_required: confirm } : {}) },
      evidence,
      evidence_ids: evidence.map((e) => e.evidence_id),
      // 没有任何可判的作答（全是待确认）时整体置信度为 0
      confidence: observations.length > 0 ? round3(Math.min(...observations.map((o) => o.confidence as number))) : 0,
      source_version: `algo:${DIAGNOSE_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${DIAGNOSE_ALGORITHM}`,
      ...envelopeFlags(admissions, []),
    }, version);
  };

  // ---------- plan（确定性学科处方） ----------
  const plan = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("PlanRequest", body, version);
    const req = body as PlanRequest;
    checkContext(req.context, false);
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    const key = req.context.idempotency_key;

    // 平台掌握状态 → 能力格状态（不认识的节点忽略；同一节点出现多次取最后一条）
    const states = new Map<string, CellState & { state: string }>();
    for (const m of req.mastery) {
      const local = localRef(m.skill_node, "skill_node");
      if (!content.cellById.has(local) || m.state === "unseen") continue;
      states.set(local, { cell: local, state: m.state, mastery: m.p_mastery ?? STATE_PROXY[m.state] ?? 0, mastered: MASTERED_STATES.has(m.state) });
    }
    const focus: string[] = [];
    for (const ref of req.constraints?.focus_skill_nodes ?? []) {
      const local = localRef(ref, "focus_skill_nodes");
      if (content.cellById.has(local) && !states.get(local)?.mastered && !focus.includes(local)) focus.push(local);
    }
    const budget = req.constraints?.time_budget_minutes;
    const recommended = recommendTargets(content, [...states.values()], budget === undefined ? DEFAULT_PLAN_TARGETS : content.pack.domains.length);
    const ordered = [
      ...focus.map((cell) => ({ cell, rationale: "focus_requested" })),
      ...recommended.filter((t) => !focus.includes(t.cell)).map((t) => ({ cell: t.cell, rationale: t.blank ? "cell_not_yet_assessed" : "zone_of_proximal_development" })),
    ];
    // 学生只能拿到已准入的题（盘点决定：审核门禁）：item_refs 只取内容包里已准入的题
    const admittedRefs = (cell: string) => [
      ...content.pack.items.filter((it) => it.cell === cell && it.admission === "admitted").map((it) => ({ ref: `wenli:${it.id}`, minutes: it.estimated_minutes })),
      ...content.pack.essays.filter((e) => e.cell === cell && e.admission === "admitted").map((e) => ({ ref: `wenli:${e.id}`, minutes: 0 })),
    ];
    const tasks: Record<string, unknown>[] = [];
    let used = 0;
    for (const [i, t] of ordered.entries()) {
      const kind = states.has(t.cell) ? "practice" : "diagnose";
      const items = admittedRefs(t.cell);
      const minutes = Math.max(MIN_TASK_MINUTES[kind], items.reduce((s, x) => s + x.minutes, 0));
      // 时间预算内按优先级截断：放不下的任务及其后的任务都不要
      if (budget !== undefined && used + minutes > budget) break;
      used += minutes;
      tasks.push({
        task_id: deriveUuid("plan", key, fp, `task:${t.cell}`),
        kind,
        skill_nodes: [`wenli:${t.cell}`],
        ...(items.length > 0 ? { item_refs: items.map((x) => x.ref) } : {}),
        priority: round3(1 - i / ordered.length),
        estimated_minutes: minutes,
        rationale_code: t.rationale,
      });
    }
    await idempotent("plan", key, fp, async () => true);
    return selfCheck("PlanResponse", {
      contract_version: version,
      result: { tasks },
      evidence_ids: [],
      confidence: 1,
      source_version: `algo:${PLAN_ALGORITHM};${content.itemsSourceVersion}`,
      model_version: `algo:${PLAN_ALGORITHM}`,
      human_review_required: false,
      semantics: "formal",
    }, version);
  };

  // ---------- generate_variation（选题，不生成） ----------
  /** 内容包里的任意一道题（客观题或作文） */
  const findAny = (itemRef: string): ObjectiveItem | EssayItem => {
    const local = localRef(itemRef, "item_ref");
    const it = content.itemById.get(local) ?? content.essayById.get(local);
    if (!it) throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
    return it;
  };
  const allItems: (ObjectiveItem | EssayItem)[] = [...content.pack.items, ...content.pack.essays].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  const generateVariation = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("GenerateVariationRequest", body, version);
    const req = body as GenerateVariationRequest;
    checkContext(req.context, false);
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    const source = req.source_item_ref ? findAny(req.source_item_ref) : undefined;
    // 同能力点：目标能力格 = 源题的格，或请求给的 skill_node；两者都给了却不同 → 请求自相矛盾
    let target = source?.cell;
    let emptyReason: string | undefined;
    if (req.skill_node) {
      const local = localRef(req.skill_node, "skill_node");
      if (!content.cellById.has(local)) emptyReason = "skill_node_unknown";
      else if (source && local !== source.cell) throw new ProblemError(422, "INVALID_REQUEST", "skill_node differs from the skill node of source_item_ref");
      else target = local;
    }
    for (const r of req.exclude_item_refs ?? []) localRef(r, "exclude_item_refs");
    // 同题型：题型编码严格相等（待人确认 #4）；只给 skill_node 时没有参照题型，只按能力格选
    const type = source ? questionTypeOf(source) : undefined;
    const excluded = new Set([...(req.exclude_item_refs ?? []), ...(req.source_item_ref ? [req.source_item_ref] : [])]);
    const family = req.item_family ? new Map(req.item_family.map((f) => [f.item_ref, f])) : undefined;
    let candidates: Record<string, unknown>[] = [];
    if (!emptyReason) {
      // 只从已准入的题里选（契约 v1；盘点决定：审核门禁）；编排层给了同族候选时只在其中选
      const matched = allItems.filter(
        (it) =>
          it.id !== source?.id &&
          it.cell === target &&
          (type === undefined || questionTypeOf(it) === type) &&
          it.admission === "admitted" &&
          (!family || family.has(`wenli:${it.id}`)),
      );
      candidates = matched
        .filter((it) => !excluded.has(`wenli:${it.id}`))
        .slice(0, req.count)
        .map((it) => {
          const d = family?.get(`wenli:${it.id}`)?.prior_difficulty;
          return {
            item_ref: `wenli:${it.id}`,
            admission: "admitted",
            content_version: { ...content.pack.curriculum },
            // 内容包没有错因标注：同错因无从判断，一律 false（候选都满足同能力点）
            match: { same_skill_node: true, same_error_category: false },
            ...(d === undefined ? {} : { difficulty: d }),
          };
        });
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

  // ---------- report（逐能力格作答计数、错因逐类计数、证据 ID；不写结论句） ----------
  const cellOrder = content.pack.cells.map((c) => c.id);
  const report = async (body: unknown): Promise<HandlerResult> => {
    const version = requestVersion(body);
    validateOrThrow("ReportRequest", body, version);
    const req = body as ReportRequest;
    checkContext(req.context, false);
    const evidence = req.evidence ?? [];
    const attributions = req.error_attributions ?? [];
    const history = req.mastery_history ?? [];
    // report 的请求 schema 不按学科约束前缀（评审 N7），适配器自查
    for (const e of evidence) {
      if (e.item_ref) localRef(e.item_ref, "evidence item_ref");
      if (e.skill_node) localRef(e.skill_node, "evidence skill_node");
    }
    for (const a of attributions) if (a.skill_node) localRef(a.skill_node, "error_attributions skill_node");
    for (const h of history) localRef(h.skill_node, "mastery_history skill_node");
    const from = instant(req.window.from);
    const to = instant(req.window.to);
    if (!(Number.isFinite(from) && Number.isFinite(to) && to > from)) throw new ProblemError(422, "INVALID_REQUEST", "window.to must be later than window.from");
    // 错因只收语文编码（CHN 口径；道法的 *_cuo 等其他学科编码不混算），跨科大类必须与映射表一致
    for (const a of attributions) {
      if (taxonomy.crossSubjectOf.get(a.subject_category) !== a.cross_subject_category) {
        throw new ProblemError(422, "INVALID_REQUEST", "error_attributions do not match the wenli error taxonomy");
      }
    }
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);

    // 逐能力格作答计数：题目在内容包里按题目的格，否则按证据自带的 skill_node
    const attemptsByCell = new Map<string, Set<string>>();
    const counted: typeof evidence = [];
    for (const e of evidence) {
      if (!inWindow(e.occurred_at, from, to)) continue;
      if (ATTEMPT_KINDS.has(e.kind)) {
        const local = e.item_ref ? localRef(e.item_ref, "evidence item_ref") : undefined;
        const it = local ? (content.itemById.get(local) ?? content.essayById.get(local)) : undefined;
        const cell = it ? it.cell : e.skill_node ? localRef(e.skill_node, "evidence skill_node") : undefined;
        if (!cell) continue;
        const attemptKey = e.attempt_id ?? e.evidence_id;
        attemptsByCell.set(cell, (attemptsByCell.get(cell) ?? new Set()).add(attemptKey));
        counted.push(e);
      } else if (e.kind === "scoring_point_span") {
        counted.push(e);
      }
    }
    const cells = new Set<string>(attemptsByCell.keys());
    for (const h of history) if (instant(h.at) < to) cells.add(localRef(h.skill_node, "mastery_history skill_node"));
    const order = (c: string) => (cellOrder.includes(c) ? cellOrder.indexOf(c) : cellOrder.length);
    const skillSummary = [...cells]
      .sort((a, b) => order(a) - order(b) || (a < b ? -1 : a > b ? 1 : 0))
      .map((c) => ({
        skill_node: `wenli:${c}`,
        trend: trendOf(history.filter((h) => h.skill_node === `wenli:${c}`), from, to),
        attempts: attemptsByCell.get(c)?.size ?? 0,
      }));
    // 错因逐类计数：只列出现过的编码，按映射表顺序
    const errorDistribution = taxonomy.order
      .map((code) => ({ cross_subject_category: taxonomy.crossSubjectOf.get(code)!, subject_category: code, count: attributions.filter((a) => a.subject_category === code).length }))
      .filter((d) => d.count > 0);
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

  const NOT_IMPLEMENTED = new Set(["teach"]);
  const KNOWN_ROUTES = new Set(["capabilities", "grade", "diagnose", "plan", "generate_variation", "report", ...NOT_IMPLEMENTED]);

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
      else if (method === "POST" && name === "plan") result = await plan(body);
      else if (method === "POST" && name === "generate_variation") result = await generateVariation(body);
      else if (method === "POST" && name === "report") result = await report(body);
      else if (method === "POST" && NOT_IMPLEMENTED.has(name)) {
        throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", `${name} is not implemented by the wenli adapter yet`);
      } else if (method === "GET" && name.startsWith("jobs/")) {
        throw new ProblemError(404, "JOB_NOT_FOUND", "the wenli adapter runs synchronously and has no jobs");
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
