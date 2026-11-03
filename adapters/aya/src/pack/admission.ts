// 内容包格式 3：逐题来源与准入决定（specs/release-gate-sprint4 设计要点第 7 节、待人确认 #9、#15）。
//
// - 准入的唯一真相源是平台题目表的准入列（平台库 item 表的 admission 列）。内容包里的准入是副本：
//   由平台导出“准入决定文件”（格式 aya-admission-decisions/1），人执行构建脚本读入并重建内容包。
// - 决定文件是当前状态的完整快照：文件里没有的题一律回到默认（来源待核、source_unverified）。
//   所以重建时先把全部题目重置为默认，再逐条套用，旧决定不会残留。
// - 来源与授权材料不进仓库（#15）：决定文件与内容包只记编号与枚举。所有字符串都要符合编号格式，
//   多出的字段一律拒绝，防止把授权原文、网址、人名写进来。
// - 准入升降的方向（source_unverified → pending_review → admitted，任意 → rejected）由平台的
//   准入函数（change_admission，迁移待定）保证；这里只核对快照本身自洽（例如 admitted 必须有来源编号、授权已核、审核等级 ≥ Q2）。
import { createHash } from "node:crypto";
import type { Admission, ContentPack, PackItem } from "../content.ts";

export const ADMISSION_DECISIONS_FORMAT = "aya-admission-decisions/1";

/** 来源类型：unknown = 未登记（默认） */
export const SOURCE_TYPES = ["unknown", "self_authored", "adapted", "textbook", "exam_paper", "reference_book"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

/** 授权状态：unverified 待核（默认）；cleared 已核（有授权或无需授权）；denied 不得使用 */
export const LICENSE_STATUSES = ["unverified", "cleared", "denied"] as const;
export type LicenseStatus = (typeof LICENSE_STATUSES)[number];

export const ADMISSIONS = ["source_unverified", "pending_review", "admitted", "rejected"] as const;

/** 学科审核等级（legacy Q0–Q5）；阿鸦升为 admitted 须 ≥ Q2（spec 设计要点第 7 节） */
export const REVIEW_LEVELS = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5"] as const;
export type ReviewLevel = (typeof REVIEW_LEVELS)[number];
export const MIN_ADMITTED_REVIEW_LEVEL: ReviewLevel = "Q2";

export interface ItemSource {
  /** 来源记录编号（由法务或教研保管的材料的编号，不是材料本身）；未登记为 null */
  source_ref: string | null;
  source_type: SourceType;
  license_status: LicenseStatus;
}

/** 平台准入决定的依据：学科审核等级与审核记录编号 */
export interface AdmissionReview {
  review_level: ReviewLevel | null;
  review_ids: string[];
}

export interface AdmissionDecision {
  item_id: string;
  admission: Admission;
  source_ref: string | null;
  source_type: SourceType;
  license_status: LicenseStatus;
  review_level: ReviewLevel | null;
  review_ids: string[];
}

export interface AdmissionDecisionFile {
  format: typeof ADMISSION_DECISIONS_FORMAT;
  /** 导出批次编号（平台导出时生成） */
  export_ref: string;
  /** 导出时刻（RFC 3339）。只作记录；构建不读时钟 */
  exported_at: string;
  /** 六段标识，必须与内容包一致 */
  curriculum: ContentPack["curriculum"];
  decisions: AdmissionDecision[];
}

/** 内容包里对所用决定文件的记录（没有决定文件时为 null） */
export interface AdmissionDecisionsRecord {
  format: typeof ADMISSION_DECISIONS_FORMAT;
  export_ref: string;
  exported_at: string;
  /** 规范化后决定集合的 SHA-256（与决定的排列顺序、字段顺序无关） */
  digest: string;
  decisions: number;
}

export const DEFAULT_SOURCE: Readonly<ItemSource> = Object.freeze({ source_ref: null, source_type: "unknown", license_status: "unverified" });

/** 编号：字母数字开头，只含字母数字与 . _ : -，最长 64；不允许空白、斜杠、中文（不能是网址、人名或材料原文） */
export const REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

const rank = (l: ReviewLevel | null) => (l === null ? -1 : REVIEW_LEVELS.indexOf(l));

/**
 * 一道题的来源、准入与依据是否自洽（决定文件校验与内容包加载共用）。返回问题列表，空 = 合格。
 * - admitted：来源编号、来源类型已知、授权 cleared、审核等级 ≥ Q2、至少一条审核记录；
 * - pending_review：来源编号、来源类型已知、授权 cleared、至少一条审核记录（来源已核、学科审核未到 Q2）；
 * - rejected：至少一条审核记录；
 * - source_unverified：授权只能是 unverified（已核或已拒的授权不能停在“待核”）。
 */
export function admissionProblems(id: string, admission: Admission, source: ItemSource, review: AdmissionReview | undefined): string[] {
  const out: string[] = [];
  if (!(ADMISSIONS as readonly string[]).includes(admission)) return [`题目 ${id} 的准入状态未知`];
  if (!(SOURCE_TYPES as readonly string[]).includes(source.source_type)) out.push(`题目 ${id} 的来源类型未知`);
  if (!(LICENSE_STATUSES as readonly string[]).includes(source.license_status)) out.push(`题目 ${id} 的授权状态未知`);
  if (source.source_ref !== null && !REF_PATTERN.test(source.source_ref)) out.push(`题目 ${id} 的来源编号不符合编号格式`);
  if (review) {
    if (review.review_level !== null && !(REVIEW_LEVELS as readonly string[]).includes(review.review_level)) out.push(`题目 ${id} 的审核等级未知`);
    if (!Array.isArray(review.review_ids) || !review.review_ids.every((r) => typeof r === "string" && UUID.test(r))) out.push(`题目 ${id} 的审核记录编号不是 UUID`);
    else if (new Set(review.review_ids).size !== review.review_ids.length) out.push(`题目 ${id} 的审核记录编号重复`);
  }
  const reviewed = review !== undefined && review.review_ids.length > 0;
  const verifiedSource = source.source_ref !== null && source.source_type !== "unknown" && source.license_status === "cleared";
  switch (admission) {
    case "admitted":
      if (!verifiedSource) out.push(`题目 ${id} 准入为 admitted，但来源编号、来源类型或授权（须 cleared）不全`);
      if (rank(review?.review_level ?? null) < rank(MIN_ADMITTED_REVIEW_LEVEL)) out.push(`题目 ${id} 准入为 admitted，但审核等级低于 ${MIN_ADMITTED_REVIEW_LEVEL}`);
      if (!reviewed) out.push(`题目 ${id} 准入为 admitted，但没有审核记录编号`);
      break;
    case "pending_review":
      if (!verifiedSource) out.push(`题目 ${id} 准入为 pending_review，但来源编号、来源类型或授权（须 cleared）不全`);
      if (!reviewed) out.push(`题目 ${id} 准入为 pending_review，但没有审核记录编号`);
      break;
    case "rejected":
      if (!reviewed) out.push(`题目 ${id} 准入为 rejected，但没有审核记录编号`);
      break;
    case "source_unverified":
      if (source.license_status !== "unverified") out.push(`题目 ${id} 仍是 source_unverified，授权状态却不是 unverified`);
      break;
  }
  return out;
}

const DECISION_KEYS = ["item_id", "admission", "source_ref", "source_type", "license_status", "review_level", "review_ids"].sort();
const FILE_KEYS = ["format", "export_ref", "exported_at", "curriculum", "decisions"].sort();
const CURRICULUM_KEYS = ["region", "exam_year", "grade", "textbook_version", "subject", "content_version"] as const;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const sameKeys = (o: Record<string, unknown>, keys: string[]) => JSON.stringify(Object.keys(o).sort()) === JSON.stringify(keys);

/**
 * 解析并校验平台导出的准入决定文件。任何问题都抛错（列出前 20 条），不做部分套用。
 * `itemIds` 是内容包里的题号：决定里出现内容包没有的题一律拒绝（不同内容版本的导出不能混用）。
 */
export function parseAdmissionDecisions(raw: unknown, curriculum: ContentPack["curriculum"], itemIds: ReadonlySet<string>): AdmissionDecisionFile {
  const problems: string[] = [];
  if (!isObj(raw)) throw new Error("准入决定文件不合格：顶层必须是对象");
  if (raw.format !== ADMISSION_DECISIONS_FORMAT) problems.push(`format 应为 ${ADMISSION_DECISIONS_FORMAT}`);
  if (!sameKeys(raw, FILE_KEYS)) problems.push(`顶层字段只能是 ${FILE_KEYS.join("、")}`);
  if (typeof raw.export_ref !== "string" || !REF_PATTERN.test(raw.export_ref)) problems.push("export_ref 不符合编号格式");
  if (typeof raw.exported_at !== "string" || !RFC3339.test(raw.exported_at) || Number.isNaN(Date.parse(raw.exported_at))) problems.push("exported_at 不是 RFC 3339 时刻");
  const c = raw.curriculum;
  if (!isObj(c) || !sameKeys(c, [...CURRICULUM_KEYS].sort())) problems.push("curriculum 必须是六段标识");
  else for (const k of CURRICULUM_KEYS) if (c[k] !== curriculum[k]) problems.push(`curriculum.${k} 与内容包不一致`);
  const seen = new Set<string>();
  const decisions: AdmissionDecision[] = [];
  if (!Array.isArray(raw.decisions)) problems.push("decisions 必须是数组");
  else {
    raw.decisions.forEach((d: unknown, i: number) => {
      if (!isObj(d) || !sameKeys(d, DECISION_KEYS)) {
        problems.push(`decisions[${i}] 的字段只能是 ${DECISION_KEYS.join("、")}`);
        return;
      }
      const id = d.item_id;
      if (typeof id !== "string" || !itemIds.has(id)) {
        problems.push(`decisions[${i}] 的题目不在内容包里`);
        return;
      }
      if (seen.has(id)) problems.push(`题目 ${id} 在决定文件里重复`);
      seen.add(id);
      if (d.source_ref !== null && typeof d.source_ref !== "string") problems.push(`题目 ${id} 的来源编号必须是字符串或 null`);
      if (d.review_level !== null && typeof d.review_level !== "string") problems.push(`题目 ${id} 的审核等级必须是字符串或 null`);
      if (!Array.isArray(d.review_ids)) {
        problems.push(`题目 ${id} 的 review_ids 必须是数组`);
        return;
      }
      const decision = d as unknown as AdmissionDecision;
      problems.push(
        ...admissionProblems(
          id,
          decision.admission,
          { source_ref: decision.source_ref, source_type: decision.source_type, license_status: decision.license_status },
          { review_level: decision.review_level, review_ids: decision.review_ids },
        ),
      );
      decisions.push(decision);
    });
  }
  if (problems.length) throw new Error(`准入决定文件不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return raw as unknown as AdmissionDecisionFile;
}

/** 规范化后的决定集合：按题号排序，每条按固定字段顺序；审核记录编号排序 */
function canonicalDecisions(file: AdmissionDecisionFile): string {
  const rows = [...file.decisions]
    .sort((a, b) => (a.item_id < b.item_id ? -1 : a.item_id > b.item_id ? 1 : 0))
    .map((d) => [d.item_id, d.admission, d.source_ref, d.source_type, d.license_status, d.review_level, [...d.review_ids].sort()]);
  return JSON.stringify({ format: file.format, export_ref: file.export_ref, curriculum: CURRICULUM_KEYS.map((k) => file.curriculum[k]), rows });
}

export function decisionsDigest(file: AdmissionDecisionFile): string {
  return `sha256:${createHash("sha256").update(canonicalDecisions(file), "utf8").digest("hex")}`;
}

/** 内容包版本：格式 3 为 0.3.0-<legacy 提交前 7 位>；套用了准入决定时再加 .d<决定摘要前 8 位> */
export const PACK_VERSION_BASE = "0.3.0";
export function packVersionFor(legacyCommit: string, digest: string | null): string {
  const base = `${PACK_VERSION_BASE}-${legacyCommit.slice(0, 7)}`;
  return digest === null ? base : `${base}.d${digest.slice("sha256:".length, "sha256:".length + 8)}`;
}

/**
 * 按准入决定重建：先把全部题目重置为默认（来源待核、source_unverified、没有审核依据），再逐条套用决定。
 * 不改输入；返回新的内容包（hint_scan 原样保留，由 C9 检查另行写入）。
 */
export function applyAdmissionDecisions(pack: ContentPack, file: AdmissionDecisionFile | null): ContentPack {
  const byId = new Map((file?.decisions ?? []).map((d) => [d.item_id, d]));
  const items = pack.items.map((it): PackItem => {
    const { admission_review: _drop, ...rest } = it;
    void _drop;
    const d = byId.get(it.id);
    if (!d) return { ...rest, source: { ...DEFAULT_SOURCE }, admission: "source_unverified" };
    return {
      ...rest,
      source: { source_ref: d.source_ref, source_type: d.source_type, license_status: d.license_status },
      admission: d.admission,
      admission_review: { review_level: d.review_level, review_ids: [...d.review_ids].sort() },
    };
  });
  const digest = file ? decisionsDigest(file) : null;
  return {
    ...pack,
    pack_version: packVersionFor(pack.legacy_commit, digest),
    items,
    admission_decisions: file
      ? { format: ADMISSION_DECISIONS_FORMAT, export_ref: file.export_ref, exported_at: file.exported_at, digest: digest!, decisions: file.decisions.length }
      : null,
  };
}

/** 递归按键名排序后序列化（缩进 1、结尾换行）：同样的内容无论以什么顺序构造，字节都相同 */
export function serializePack(pack: ContentPack): string {
  const sortKeys = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(sortKeys);
    if (isObj(v)) return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])]));
    return v;
  };
  return JSON.stringify(sortKeys(pack), null, 1) + "\n";
}
