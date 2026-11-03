// 阿鸦适配器：capabilities、grade（客观题）、diagnose（intake / gap_test），以及冲刺 4-D 补齐的
// plan、teach（提示阶梯与知识页面直出）、generate_variation（选题）、report（字段与证据引用）。
// 规格：specs/adapter-aya-sprint1/spec.md“映射规则”；specs/adapter-aya-methods-sprint4/spec.md（四个新方法只按 v1.0 实现，
// 0.9.0 请求返回 501；它们都是确定性的：不调模型、不写话术与关于学生的陈述、不产生证据、不读系统时钟）。
// 契约：packages/contracts/openapi/plugin-api.v0.9.yaml 与 v1.0（2026-10-09 签字）plugin-api.v1.0.yaml：迁移期按请求的 context.contract_version 选版本校验与应答，
// 能力声明报默认版本 1.0.0（specs/plugin-api-v1.0-step2/spec.md 第 3 条、specs/contract-v1.0-signoff）。v1.1 候选版 plugin-api.v1.1.yaml（PLT-12）：v1.0 的六个方法照旧，
// 另加只读的 present_item（题面；只按 1.1.0 应答，见 src/methods/present.ts）。
//
// 适配器不读写任何平台表（CLAUDE.md 规则 1）：证据与推断放在响应里，由学习服务落库。
// 不输出掌握度（规则 2）；判分只来自验证器（规则 3）；响应和日志里不出现答案、解析、评分标准（decisions #4）。
import { CANDIDATE_CONTRACT_VERSIONS, type ContractValidator, type ContractVersion, type SchemaName } from "./contract.ts";
import type { Admission, LoadedPack, PackItem } from "./content.ts";
import { canonical, deriveUuid, requestFingerprint } from "./ids.ts";
import { ProblemError, type ProblemCode } from "./problem.ts";
import { planTasks, PLAN_ALGORITHM, type PlanInput } from "./methods/plan.ts";
import { buildReport, REPORT_ALGORITHM, type ReportInput } from "./methods/report.ts";
import { checkTeachConfig, DEFAULT_TEACH_CONFIG, HINT_ALGORITHM, PAGE_LESSON_ALGORITHM, teachHint, teachLesson, type TeachConfig } from "./methods/teach.ts";
import { selectVariants, VARIANT_ALGORITHM, type VariationInput } from "./methods/variation.ts";
import { PRESENT_ALGORITHM, presentItem } from "./methods/present.ts";
import type { AyaTaxonomy } from "./taxonomy.ts";
import {
  BOUNDARY_MAX_CONFIDENCE,
  VERIFIER_ID,
  VERIFIER_REF,
  VERIFIER_VERSION,
  verifyChoice,
  verifyFill,
  type Verification,
} from "./verifier.ts";

export const ADAPTER_VERSION = "0.2.0";
/** plan、teach(hint) 引用了未准入的题时的置信度（spec 待人确认 #5 默认）；其余新方法为 1.0 */
export const UNADMITTED_CONTENT_CONFIDENCE = 0.5;
/** 冲刺 4-D 的四个方法：只按 v1.0 实现 */
export const V1_ONLY_METHODS = ["plan", "teach", "generate_variation", "report"] as const;
export const TRAP_ATTRIBUTION_ALGORITHM_BASE = "aya-trap-attr@0.1.0";
/** 错因的算法版本同时带映射表版本（规则 6）：映射表签字后改动，已落库的错因仍能分清按哪一版映射 */
export const trapAttributionAlgorithm = (taxonomyVersion: string) => `${TRAP_ATTRIBUTION_ALGORITHM_BASE}-tax${taxonomyVersion}`;
export const TRAP_ATTRIBUTION_CONFIDENCE = 0.8;
export const DIAGNOSE_ALGORITHM = "aya-diag@0.1.0";
export const GUESSED_MAX_CONFIDENCE = 0.3;
export const HEAVY_HINT_LEVEL = 4;
export const HEAVY_HINT_MAX_CONFIDENCE = 0.5;
export const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;

// ---------- 契约里用到的类型（只列本适配器读写的字段） ----------
interface RequestContext {
  request_id: string;
  subject: string;
  student_ref: string;
  data_class: string;
  idempotency_key: string;
  usage_id?: string;
  contract_version: string;
  content_version?: Record<string, string | number>;
}
interface AttemptInput {
  attempt_id?: string;
  item_ref: string;
  occurred_at: string;
  response: { text?: string; choice?: string[]; image_ref?: string; transcript_ref?: string };
  elapsed_sec?: number;
  hint_level_used?: number;
  self_report?: "confident" | "unsure" | "guessed";
}
interface GradeRequest {
  context: RequestContext;
  attempt: AttemptInput;
  rubric_version: string;
  appeal?: boolean;
}
interface DiagnoseRequest {
  context: RequestContext;
  mode: "intake" | "paper" | "gap_test" | "process";
  attempts?: AttemptInput[];
  paper_image_refs?: string[];
}
interface EvidenceDraft {
  evidence_id: string;
  /** v1.0：证据属于哪道作答（请求里 AttemptInput.attempt_id；作答类证据必填，评审 N3） */
  attempt_id?: string;
  kind: "response_text" | "response_choice" | "verifier_trace";
  occurred_at: string;
  item_ref: string;
  skill_node: string;
  admission: Admission;
  payload: Record<string, unknown>;
}
interface ErrorAttribution {
  skill_node: string;
  subject_category: string;
  cross_subject_category: string;
  confidence: number;
  evidence_ids: string[];
  algorithm_version: string;
}

export interface HandlerResult {
  status: number;
  body: unknown;
}

/** 日志记录只含这些字段：不写作答、答案、题目内容（decisions #4；契约 Problem.detail） */
export interface LogRecord {
  method: string;
  status: number;
  code?: ProblemCode;
  request_id?: string;
  duration_ms: number;
  /** 只在 500 时出现：异常的类名（不含消息，消息可能带内容） */
  error_class?: string;
}

export interface AdapterDeps {
  content: LoadedPack;
  taxonomy: AyaTaxonomy;
  contract: ContractValidator;
  log?: (record: LogRecord) => void;
  /** 幂等缓存条数上限（只存指纹，用于同 key 不同请求体的冲突检测；ID 本身是确定性派生的） */
  idempotencyCacheSize?: number;
  /** teach 配置：max_hint_level_before_submit（默认 3，只能 0–3；v1.0 没有“作答已提交”的信号，4 级及以上一律不给） */
  teach?: Partial<TeachConfig>;
  /** 候选版（CANDIDATE_CONTRACT_VERSIONS）签字前只在测试与演练里接受：未开时声明候选版的请求返回 CONTRACT_VERSION_UNSUPPORTED */
  allowCandidate?: boolean;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const round3 = (x: number) => Math.round(x * 1000) / 1000;

export function createAdapter(deps: AdapterDeps) {
  const { content, taxonomy, contract } = deps;
  const teachConfig = checkTeachConfig({ ...DEFAULT_TEACH_CONFIG, ...deps.teach });
  const log = deps.log ?? (() => {});
  const cacheSize = deps.idempotencyCacheSize ?? 10_000;
  const idempotency = new Map<string, { fingerprint: string; at: number }>();

  const problem = (e: ProblemError, requestId?: string): HandlerResult => {
    const body: Record<string, unknown> = {
      type: `/problems/${e.code.toLowerCase()}`,
      title: e.message,
      status: e.status,
      code: e.code,
      retryable: false,
    };
    if (e.detail) body.detail = e.detail.slice(0, 500);
    if (requestId) body.request_id = requestId;
    return { status: e.status, body };
  };

  /**
   * 请求按哪个契约版本校验与应答：取 context.contract_version。不是字符串（缺失、类型不对）时按默认版本校验，
   * 由 schema 报 INVALID_REQUEST；是字符串但不在支持范围 → CONTRACT_VERSION_UNSUPPORTED。
   */
  const requestVersion = (body: unknown): ContractVersion => {
    const ctx = (body as { context?: unknown } | null | undefined)?.context;
    const v = ctx && typeof ctx === "object" ? (ctx as { contract_version?: unknown }).contract_version : undefined;
    if (typeof v !== "string") return contract.version;
    if (!contract.supports(v)) {
      throw new ProblemError(400, "CONTRACT_VERSION_UNSUPPORTED", "contract_version is not supported by this adapter", `supported: ${contract.versions.join(", ")}`);
    }
    // 适配器自己的第二道闸（安全审查建议 4）：学习服务之外，候选版未开放时也不应答
    if (CANDIDATE_CONTRACT_VERSIONS.includes(v) && !deps.allowCandidate) {
      throw new ProblemError(400, "CONTRACT_VERSION_UNSUPPORTED", "candidate contract version is not enabled on this adapter");
    }
    return v;
  };

  /** 不支持的模式或作答形态：v1.0 用 MODE_NOT_SUPPORTED，0.9.0 仍用 INVALID_REQUEST（契约审查建议 3） */
  const unsupported = (version: ContractVersion, title: string) =>
    new ProblemError(422, version === "0.9.0" ? "INVALID_REQUEST" : "MODE_NOT_SUPPORTED", title);

  const validateOrThrow = (schema: SchemaName, value: unknown, version: ContractVersion) => {
    const r = contract.validate(schema, value, version);
    if (!r.ok) throw new ProblemError(400, "INVALID_REQUEST", "request does not match the contract", r.errors.join("; "));
  };

  const checkContext = (ctx: RequestContext, requireContentVersion: boolean) => {
    if (ctx.subject !== "aya") throw new ProblemError(400, "SUBJECT_MISMATCH", "this adapter only serves subject aya");
    const cv = ctx.content_version;
    if (!cv) {
      if (requireContentVersion) throw new ProblemError(400, "INVALID_REQUEST", "content_version is required");
      return;
    }
    const served = content.pack.curriculum;
    const same = (Object.keys(served) as (keyof typeof served)[]).every((k) => cv[k] === served[k]);
    if (!same) {
      throw new ProblemError(404, "CONTENT_VERSION_NOT_FOUND", "content_version is not served by this adapter");
    }
  };

  const findItem = (itemRef: string): PackItem => {
    const [subject, localId] = itemRef.split(":", 2) as [string, string];
    if (subject !== "aya") throw new ProblemError(400, "SUBJECT_MISMATCH", "item_ref is not an aya item");
    const item = content.itemById.get(localId);
    if (!item) throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
    return item;
  };

  /** 判一道作答：只支持客观题（单选、多选、填空）。过程题（拍照）在后续冲刺。 */
  const verifyAttempt = (item: PackItem, attempt: AttemptInput, version: ContractVersion): Verification => {
    const r = attempt.response as AttemptInput["response"] & Record<string, unknown>;
    if (r.image_ref || r.transcript_ref) {
      throw unsupported(version, "process grading from photos or transcripts is not implemented");
    }
    // v1.0 新增的作答形态：阿鸦暂不支持（latex 能否接现有验证器待教研决定，lessons 待人确认）
    if (r["latex"] !== undefined || r["mhchem"] !== undefined || r["semantic"] !== undefined) {
      throw unsupported(version, "aya accepts response.text and response.choice only");
    }
    if (item.format === "fill") {
      if (r.text === undefined) throw new ProblemError(422, "INVALID_REQUEST", "fill item expects response.text");
      return verifyFill(item, r.text);
    }
    if (!r.choice) throw new ProblemError(422, "INVALID_REQUEST", "choice item expects response.choice");
    const keys = new Set((item.options ?? []).map((o) => o.key));
    if (!r.choice.every((k) => keys.has(k))) throw new ProblemError(422, "INVALID_REQUEST", "response.choice contains an unknown option key");
    if (new Set(r.choice).size !== r.choice.length) throw new ProblemError(422, "INVALID_REQUEST", "response.choice contains duplicate keys");
    if (item.format === "single" && r.choice.length !== 1) {
      throw new ProblemError(422, "INVALID_REQUEST", "single choice item expects exactly one option");
    }
    return verifyChoice(item, r.choice);
  };

  /** 一道作答的证据：作答本身 + 一条验证器轨迹 */
  const buildEvidence = (item: PackItem, attempt: AttemptInput, v: Verification, id: (label: string) => string, version: ContractVersion) => {
    const common = {
      // v1.0：作答与验证器轨迹都指回所属作答（schema 已要求 v1.0 请求带 attempt_id）；v0.9 不输出，行为不变
      ...(version !== "0.9.0" && attempt.attempt_id ? { attempt_id: attempt.attempt_id } : {}),
      occurred_at: attempt.occurred_at,
      item_ref: `aya:${item.id}`,
      skill_node: `aya:${item.mainNodes[0]}`,
      admission: item.admission,
    };
    const response: EvidenceDraft =
      item.format === "fill"
        ? { evidence_id: id("response"), kind: "response_text", ...common, payload: { text: attempt.response.text } }
        : { evidence_id: id("response"), kind: "response_choice", ...common, payload: { choice: attempt.response.choice } };
    const trace: EvidenceDraft = {
      evidence_id: id("trace"),
      kind: "verifier_trace",
      ...common,
      payload: { sampled_points: v.sampled, agreeing_points: v.agreeing, reason_codes: v.reasonCodes },
    };
    return { response, trace };
  };

  /** 错因：答错且命中预标注陷阱 → 陷阱码 → 十二根因 → 映射表编码 → 跨科大类。没命中陷阱不产出。 */
  const attribute = (item: PackItem, v: Verification, responseEvidenceId: string): ErrorAttribution[] => {
    if (v.verdict !== "incorrect") return [];
    const seen = new Set<string>();
    const out: ErrorAttribution[] = [];
    for (const trap of v.trapsHit) {
      const rootCause = content.trapByCode.get(trap);
      const entry = rootCause && taxonomy.byRootCause.get(rootCause);
      // 敏感类（心理与习惯）不自动归因：完整方案第四章红线，是否保留待教研与产品决定
      if (!entry || entry.sensitive || seen.has(entry.code)) continue;
      seen.add(entry.code);
      out.push({
        skill_node: `aya:${item.mainNodes[0]}`,
        subject_category: entry.code,
        cross_subject_category: entry.crossSubject,
        confidence: TRAP_ATTRIBUTION_CONFIDENCE,
        evidence_ids: [responseEvidenceId],
        algorithm_version: trapAttributionAlgorithm(taxonomy.version),
      });
    }
    return out;
  };

  /** 信封的复核原因与效力：任何一条证据未准入 → practice + content_not_admitted（decisions #11） */
  const envelopeFlags = (evidence: EvidenceDraft[], extra: string[]) => {
    const reasons = new Set<string>();
    const allAdmitted = evidence.every((e) => e.admission === "admitted");
    if (!allAdmitted) reasons.add("content_not_admitted");
    for (const r of extra) reasons.add(r);
    return {
      human_review_required: reasons.size > 0,
      ...(reasons.size > 0 ? { review_reasons: [...reasons] } : {}),
      semantics: allAdmitted ? "formal" : "practice",
    };
  };

  /**
   * 幂等包装：同 key 同请求体 → 同一响应；同 key 不同请求体 → IDEMPOTENCY_CONFLICT。
   * 缓存里只存请求体指纹（不存响应，因此不在内存里长期保留学生原文）。命中时重新计算：
   * 证据 ID 由 key 与指纹确定性派生、判分是确定性的，所以重算结果与第一次完全相同。
   */
  const idempotent = (method: string, key: string, fingerprint: string, run: () => HandlerResult): HandlerResult => {
    const cacheKey = `${method}\0${key}`;
    const now = Date.now();
    let hit = idempotency.get(cacheKey);
    if (hit && now - hit.at > IDEMPOTENCY_TTL_MS) {
      idempotency.delete(cacheKey);
      hit = undefined;
    }
    if (hit && hit.fingerprint !== fingerprint) {
      throw new ProblemError(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different request body");
    }
    const result = run();
    if (result.status === 200 && !hit) {
      if (idempotency.size >= cacheSize) idempotency.delete(idempotency.keys().next().value!);
      idempotency.set(cacheKey, { fingerprint, at: now });
    }
    return result;
  };

  /** 出门前按契约自检：不合契约的响应不发出去（fail closed） */
  const selfCheck = (schema: SchemaName, body: unknown, version: ContractVersion): HandlerResult => {
    const r = contract.validate(schema, body, version);
    if (!r.ok) throw new ProblemError(500, "INTERNAL", "adapter produced a response that violates the contract", r.errors.join("; "));
    return { status: 200, body };
  };

  const sourceVersion = (...parts: string[]) => [...parts, content.itemsSourceVersion].join(";");

  // ---------- 方法 ----------
  // 能力声明按默认版本报：0.9.0 下四个新方法未实现（请求返回 501），1.0.0 下六个方法都已实现（阿鸦 6/6）
  const v1Methods = contract.version !== "0.9.0";
  const capabilities = (): HandlerResult =>
    selfCheck("Capabilities", {
      subject: "aya",
      adapter_version: ADAPTER_VERSION,
      contract_version: contract.version,
      methods: {
        diagnose: { implemented: true, async: false },
        plan: v1Methods ? { implemented: true, async: false } : { implemented: false },
        teach: v1Methods ? { implemented: true, async: false, notes_code: "deterministic_no_model" } : { implemented: false },
        grade: { implemented: true, async: false },
        generate_variation: v1Methods ? { implemented: true, async: false } : { implemented: false },
        report: v1Methods ? { implemented: true, async: false } : { implemented: false },
        // v1.1（PLT-12）：能力声明逐一声明七个方法；1.1.0 以下的能力声明没有这一项（v1.0 Capabilities 不允许多余方法）
        ...(contract.version === "1.1.0" ? { present_item: { implemented: true, async: false } } : {}),
      },
      verifiers: [{ verifier_id: VERIFIER_ID, version: VERIFIER_VERSION }],
    }, contract.version);

  const grade = (body: unknown): HandlerResult => {
    const version = requestVersion(body);
    validateOrThrow("GradeRequest", body, version);
    const req = body as GradeRequest;
    checkContext(req.context, true);
    if (req.rubric_version !== VERIFIER_REF) {
      throw new ProblemError(400, "INVALID_REQUEST", `rubric_version must be ${VERIFIER_REF}`);
    }
    // appeal 缺省等于 false：归一后再算指纹，两种写法得到同一批 evidence_id
    const fp = requestFingerprint({ ...req, appeal: req.appeal === true } as unknown as Parameters<typeof requestFingerprint>[0]);
    return idempotent("grade", req.context.idempotency_key, fp, () => {
      const item = findItem(req.attempt.item_ref);
      const v = verifyAttempt(item, req.attempt, version);
      const id = (label: string) => deriveUuid("grade", req.context.idempotency_key, fp, `a0:${label}`);
      const { response, trace } = buildEvidence(item, req.attempt, v, id, version);
      const evidence = [response, trace];
      const score = v.verdict === "correct" ? 1 : v.verdict === "incorrect" ? 0 : null;
      const extra: string[] = [];
      if (v.verdict === "needs_review" || v.reasonCodes.length > 0) extra.push("verifier_boundary_case");
      if (score === null) extra.push("dimension_unavailable");
      if (req.appeal) extra.push("student_appeal");
      const confidence = round3(v.verdict === "needs_review" ? Math.min(v.confidence, BOUNDARY_MAX_CONFIDENCE) : v.confidence);
      return selfCheck("GradeResponse", {
        contract_version: version,
        result: {
          verdict: v.verdict,
          rubric_version: req.rubric_version,
          // 未经金标准校准（evals/grade 尚未建立），如实标 false
          score: { total: score, max_total: 1, calibrated: false, ...(score === null ? { excluded_dimensions: ["answer"] } : {}) },
          dimensions: [
            {
              dimension_id: "answer",
              score,
              max_score: 1,
              judged_by: "verifier",
              subjective: false,
              rule_ref: VERIFIER_REF,
              confidence,
              evidence_ids: [response.evidence_id, trace.evidence_id],
            },
          ],
          error_attributions: attribute(item, v, response.evidence_id),
          verifier: { verifier_id: VERIFIER_ID, version: VERIFIER_VERSION, reason_codes: v.reasonCodes },
        },
        evidence,
        evidence_ids: evidence.map((e) => e.evidence_id),
        confidence,
        source_version: sourceVersion(`verifier:${VERIFIER_REF}`),
        model_version: `algo:${VERIFIER_REF}`,
        ...envelopeFlags(evidence, extra),
      }, version);
    });
  };

  const diagnose = (body: unknown): HandlerResult => {
    const version = requestVersion(body);
    validateOrThrow("DiagnoseRequest", body, version);
    const req = body as DiagnoseRequest;
    checkContext(req.context, false);
    if (req.mode !== "intake" && req.mode !== "gap_test") {
      throw unsupported(version, "aya diagnose supports mode intake and gap_test only");
    }
    if (!req.attempts || req.attempts.length === 0) {
      throw new ProblemError(422, "INVALID_REQUEST", "aya diagnose needs at least one attempt");
    }
    const attempts = req.attempts;
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    return idempotent("diagnose", req.context.idempotency_key, fp, () => {
      const evidence: EvidenceDraft[] = [];
      const observations: Record<string, unknown>[] = [];
      const attributions: ErrorAttribution[] = [];
      let anyBoundary = false;
      attempts.forEach((attempt, i) => {
        const item = findItem(attempt.item_ref);
        const v = verifyAttempt(item, attempt, version);
        const id = (label: string) => deriveUuid("diagnose", req.context.idempotency_key, fp, `a${i}:${label}`);
        const { response, trace } = buildEvidence(item, attempt, v, id, version);
        evidence.push(response, trace);
        if (v.verdict === "needs_review") anyBoundary = true;
        const outcome = v.verdict === "correct" ? "demonstrated" : v.verdict === "incorrect" ? "not_demonstrated" : "inconclusive";
        let confidence = v.verdict === "needs_review" ? Math.min(v.confidence, BOUNDARY_MAX_CONFIDENCE) : v.confidence;
        // 答对但自评“猜的”：降权（aya adapter-design §1）；用了 4 级及以上提示：降权
        if (v.verdict === "correct" && attempt.self_report === "guessed") confidence = Math.min(confidence, GUESSED_MAX_CONFIDENCE);
        if ((attempt.hint_level_used ?? 0) >= HEAVY_HINT_LEVEL) confidence = Math.min(confidence, HEAVY_HINT_MAX_CONFIDENCE);
        for (const node of new Set(item.mainNodes)) {
          observations.push({
            skill_node: `aya:${node}`,
            outcome,
            confidence: round3(confidence),
            evidence_ids: [response.evidence_id, trace.evidence_id],
            algorithm_version: DIAGNOSE_ALGORITHM,
          });
        }
        attributions.push(...attribute(item, v, response.evidence_id));
      });
      const confidence = Math.min(...observations.map((o) => o.confidence as number));
      return selfCheck("DiagnoseResponse", {
        contract_version: version,
        result: { observations, error_attributions: attributions },
        evidence,
        evidence_ids: evidence.map((e) => e.evidence_id),
        confidence,
        source_version: sourceVersion(`verifier:${VERIFIER_REF}`, `algo:${DIAGNOSE_ALGORITHM}`),
        model_version: `algo:${DIAGNOSE_ALGORITHM}`,
        ...envelopeFlags(evidence, anyBoundary ? ["verifier_boundary_case"] : []),
      }, version);
    });
  };

  // ---------- 冲刺 4-D：四个确定性方法（只按 v1.0） ----------
  /** 共同入口：选版本（0.9.0 → 501）、按契约校验、查学科与内容版本、按请求体指纹做幂等 */
  const v1Method = (method: (typeof V1_ONLY_METHODS)[number], schema: SchemaName, body: unknown, run: (req: Record<string, unknown> & { context: RequestContext }, fp: string, version: ContractVersion) => HandlerResult): HandlerResult => {
    const version = requestVersion(body);
    if (version === "0.9.0") {
      throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", `${method} is implemented for contract 1.0.0 only`);
    }
    validateOrThrow(schema, body, version);
    const req = body as Record<string, unknown> & { context: RequestContext };
    checkContext(req.context, false);
    const fp = requestFingerprint(req as unknown as Parameters<typeof requestFingerprint>[0]);
    return idempotent(method, req.context.idempotency_key, fp, () => run(req, fp, version));
  };
  /** 结果的效力与置信度：引用了未准入的题 → practice（不置 human_review_required：四个方法不判分，spec 设计要点第 0 节） */
  const contentEnvelope = (items: PackItem[], lowerConfidence: boolean) => {
    const admitted = items.every((it) => it.admission === "admitted");
    return {
      confidence: admitted || !lowerConfidence ? 1 : UNADMITTED_CONTENT_CONFIDENCE,
      semantics: admitted ? "formal" : "practice",
      human_review_required: false,
    };
  };

  const plan = (body: unknown): HandlerResult =>
    v1Method("plan", "PlanRequest", body, (req, fp, version) => {
      const out = planTasks(content, req as unknown as PlanInput);
      const ids = out.tasks.map((t) => deriveUuid("plan", req.context.idempotency_key, fp, `task:${t.kind}:${t.skill}`));
      const tasks = out.tasks.map((t, i) => ({
        task_id: ids[i]!,
        kind: t.kind,
        skill_nodes: [`aya:${t.skill}`],
        item_refs: t.items.map((it) => `aya:${it.id}`),
        priority: t.priority,
        estimated_minutes: t.estimatedMinutes,
        ...(t.prerequisites.length > 0 ? { prerequisite_task_ids: t.prerequisites.map((j) => ids[j]!) } : {}),
        rationale_code: t.rationale,
      }));
      const graph = out.usedGraph && content.pack.skill_links.map_ref ? [`graph:${content.pack.skill_links.map_ref}`] : [];
      return selfCheck("PlanResponse", {
        contract_version: version,
        result: { tasks },
        evidence_ids: [],
        source_version: sourceVersion(`algo:${PLAN_ALGORITHM}`, ...graph),
        model_version: `algo:${PLAN_ALGORITHM}`,
        ...contentEnvelope(out.tasks.flatMap((t) => t.items), true),
      }, version);
    });

  const teach = (body: unknown): HandlerResult =>
    v1Method("teach", "TeachRequest", body, (req, _fp, version) => {
      const r = req as unknown as { mode: "hint" | "lesson"; skill_node: string; item_ref?: string; allowed_hint_level: number; knowledge_page?: { body_md: string } };
      const hint = r.mode === "hint";
      const out = hint ? teachHint(content, r, teachConfig) : teachLesson(content, r);
      const algorithm = hint ? HINT_ALGORITHM : PAGE_LESSON_ALGORITHM;
      return selfCheck("TeachResponse", {
        contract_version: version,
        result: {
          segments: out.segments,
          hint_level_used: out.hintLevelUsed,
          // v0 不写任何人设话术：身份披露与转介由平台人设层负责（待人确认 #1 附带项）
          ai_disclosure_shown: false,
          referral_triggered: false,
        },
        evidence_ids: [],
        source_version: sourceVersion(`algo:${algorithm}`),
        model_version: `algo:${algorithm}`,
        ...contentEnvelope(out.item ? [out.item] : [], true),
      }, version);
    });

  const generateVariation = (body: unknown): HandlerResult =>
    v1Method("generate_variation", "GenerateVariationRequest", body, (req, _fp, version) => {
      const out = selectVariants(content, taxonomy, req as unknown as VariationInput);
      const candidates = out.picks.map((p) => ({
        item_ref: `aya:${p.item.id}`,
        admission: p.item.admission,
        content_version: { ...content.pack.curriculum },
        match: { same_skill_node: p.sameSkillNode, same_error_category: p.sameErrorCategory },
        ...(p.difficulty !== undefined ? { difficulty: p.difficulty } : {}),
      }));
      return selfCheck("GenerateVariationResponse", {
        contract_version: version,
        result: { candidates, ...(out.emptyReason ? { empty_reason: out.emptyReason } : {}) },
        evidence_ids: [],
        source_version: sourceVersion(`algo:${VARIANT_ALGORITHM}`),
        model_version: `algo:${VARIANT_ALGORITHM}`,
        // 候选一律已准入（空结果同契约示例）：formal、1.0
        ...contentEnvelope(out.picks.map((p) => p.item), false),
      }, version);
    });

  const report = (body: unknown): HandlerResult =>
    v1Method("report", "ReportRequest", body, (req, _fp, version) => {
      const out = buildReport(taxonomy, req as unknown as ReportInput);
      return selfCheck("ReportResponse", {
        contract_version: version,
        result: {
          skill_summary: out.skillSummary,
          error_distribution: out.errorDistribution,
          highlight_codes: out.highlightCodes,
        },
        evidence_ids: out.evidenceIds,
        confidence: out.confidence,
        source_version: sourceVersion(`algo:${REPORT_ALGORITHM}`),
        model_version: `algo:${REPORT_ALGORITHM}`,
        human_review_required: false,
        semantics: out.anyUnadmitted ? "practice" : "formal",
      }, version);
    });

  // ---------- PLT-12：present_item（只按 1.1.0 候选版） ----------
  /**
   * 题面：只读、非消耗类、与学生无关（契约 PresentItemContext：不带 student_ref、usage_id，schema 已拒）。请求顶层的
   * content_version 必须是本适配器服务的那一版；context.content_version 同时出现时必须与它相同（schema 表达不了，这里核对）。
   * 不进幂等缓存（审查阻断 2）：结果由（题目, 内容版本）确定、不产生证据，重放天然一致；进缓存只会挤掉 grade 等写方法的条目。
   */
  const present = (body: unknown): HandlerResult => {
    const version = requestVersion(body);
    if (version !== "1.1.0") {
      throw new ProblemError(501, "METHOD_NOT_IMPLEMENTED", "present_item is implemented for contract 1.1.0 only");
    }
    validateOrThrow("PresentItemRequest", body, version);
    const req = body as { context: { subject: string; content_version?: Record<string, string | number> }; item_ref: string;
      content_version: Record<string, string | number> };
    if (req.context.content_version && canonical(req.context.content_version) !== canonical(req.content_version)) {
      throw new ProblemError(400, "INVALID_REQUEST", "context.content_version and content_version differ");
    }
    checkContext({ ...req.context, content_version: req.content_version } as unknown as RequestContext, true);
    const item = findItem(req.item_ref);
    return selfCheck("PresentItemResponse", {
      contract_version: version,
      result: presentItem(content, item),
      evidence_ids: [],
      confidence: 1,
      source_version: sourceVersion(`algo:${PRESENT_ALGORITHM}`),
      model_version: `algo:${PRESENT_ALGORITHM}`,
      human_review_required: false,
      // 题面不判分、不进复核；未准入的题只能当练习展示（同其他三个确定性方法）
      semantics: item.admission === "admitted" ? "formal" : "practice",
    }, version);
  };

  const V1_ROUTES: Record<(typeof V1_ONLY_METHODS)[number], (body: unknown) => HandlerResult> = {
    plan,
    teach,
    generate_variation: generateVariation,
    report,
  };
  const KNOWN_ROUTES = new Set<string>(["capabilities", "grade", "diagnose", "present_item", ...V1_ONLY_METHODS]);

  /** 路由（路径不含 /plugin/v0 前缀）。所有错误都转成契约 Problem。 */
  const handle = (method: string, path: string, body: unknown): HandlerResult => {
    const started = performance.now();
    const requestId = (body as { context?: { request_id?: unknown } } | undefined)?.context?.request_id;
    // 只接受 UUID 形状的 request_id，防止把任意字符串带进日志
    const rid = typeof requestId === "string" && UUID_RE.test(requestId) ? requestId : undefined;
    let result: HandlerResult;
    let code: ProblemCode | undefined;
    let errorClass: string | undefined;
    try {
      const name = path.replace(/^\/+/, "");
      if (method === "GET" && name === "capabilities") result = capabilities();
      else if (method === "POST" && name === "grade") result = grade(body);
      else if (method === "POST" && name === "diagnose") result = diagnose(body);
      else if (method === "POST" && Object.hasOwn(V1_ROUTES, name)) result = V1_ROUTES[name as keyof typeof V1_ROUTES](body);
      else if (method === "POST" && name === "present_item") result = present(body);
      else if (method === "GET" && name.startsWith("jobs/")) {
        throw new ProblemError(404, "JOB_NOT_FOUND", "the aya adapter runs synchronously and has no jobs");
      } else throw new ProblemError(404, "INVALID_REQUEST", "unknown route");
    } catch (e) {
      if (!(e instanceof ProblemError)) errorClass = e instanceof Error ? e.constructor.name.slice(0, 48) : typeof e;
      const err = e instanceof ProblemError ? e : new ProblemError(500, "INTERNAL", "internal error");
      code = err.code;
      result = problem(err, rid);
    }
    const logged = path.replace(/^\/+/, "");
    log({ method: KNOWN_ROUTES.has(logged) ? logged : logged.startsWith("jobs/") ? "jobs" : "unknown", status: result.status, ...(code ? { code } : {}), ...(rid ? { request_id: rid } : {}), duration_ms: Math.round(performance.now() - started), ...(errorClass ? { error_class: errorClass } : {}) });
    return result;
  };

  return { capabilities, handle };
}

export type Adapter = ReturnType<typeof createAdapter>;
