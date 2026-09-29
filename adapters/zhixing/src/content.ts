// 服务端只读内容包（spec 设计要点 2）。内容包是合成题（待人确认 #4），题面与采分点只进本进程与模型路由，
// 任何接口都不返回。采分点字段对齐 KGE kge.scoring_point（迁移 0011）：point_id、score、subject_dimension、
// node_ids（kge.node_id，MORAL 命名空间，1–8 个）、keyword_family（三档）、required_logic、material_linkage、
// partial_credit_rule（结构化：mode + allowed_scores + description）、common_errors。
// skill_node 是合成的能力视图节点（五个判分维度各一个，待人确认 #3），正式坐标待教研定。
import { readFileSync } from "node:fs";

export const PACK_FORMAT = "zhixing-content-pack/1";

export const ADMISSIONS = ["admitted", "pending_review", "source_unverified", "rejected"] as const;
export type Admission = (typeof ADMISSIONS)[number];

/** 四类失分归因（legacy daofa-m3-schema.ts ATTRIBUTION；编码取 docs/mapping/error-taxonomy.v0.9.yaml） */
export const ATTRIBUTION_CODES = ["shenti_cuo", "dingwei_cuo", "zhuanhua_cuo", "guifan_cuo"] as const;
export type AttributionCode = (typeof ATTRIBUTION_CODES)[number];

export interface KeywordFamily {
  /** 核心术语：命中即可视为学科表述到位 */
  core: string[];
  /** 可接受的近义表述：方向对、术语不够精确 */
  accepted: string[];
  /** 口语表述：意思接近，属于规范错的信号 */
  colloquial: string[];
}

export const PARTIAL_CREDIT_MODES = ["all_or_nothing", "proportional", "stepwise"] as const;
export type PartialCreditMode = (typeof PARTIAL_CREDIT_MODES)[number];

/**
 * 结构化的给分规则：模型给出的分数（clamp、取 0.5 倍数之后）必须落在 allowed_scores 里，否则该点转复核。
 * allowed_scores 升序、含 0 与满分、都是 0.5 的倍数且不超过分值；all_or_nothing 只允许 [0, 满分]。
 */
export interface PartialCreditRule {
  mode: PartialCreditMode;
  allowed_scores: number[];
  description: string;
}

export interface ScoringPointDef {
  point_id: string;
  score: number;
  subject_dimension: string;
  skill_node: string;
  /** 合成的知识节点 ID（kge.scoring_point.node_ids；格式同 kge.node_id，学科命名空间 MORAL），1–8 个 */
  node_ids: string[];
  keyword_family: KeywordFamily;
  required_logic: string | null;
  material_linkage: string | null;
  partial_credit_rule: PartialCreditRule;
  /** 合成的错误形态（去标识，不是真实作答，规则 13） */
  common_errors: { type: AttributionCode; pattern: string; note: string }[];
}

export interface PackItem {
  id: string;
  /** 契约 v1 只接道法（zhixing #13/A5）；历史题即使出现也拒绝判分 */
  discipline: "daofa" | "history";
  synthetic: boolean;
  admission: Admission;
  rubric_version: string;
  total_score: number;
  title: string;
  grade_scope: string;
  material_text: string;
  prompt_question: string;
  points: ScoringPointDef[];
}

export interface PackNode {
  id: string;
  dimension: string;
  /** 这个维度对应的失分归因；时政敏感度不由 M3 归因供给，为 null */
  attribution: AttributionCode | null;
}

export interface ContentPack {
  format: typeof PACK_FORMAT;
  pack_name: string;
  pack_version: string;
  synthetic: boolean;
  notice: string;
  curriculum: {
    region: string;
    exam_year: number;
    grade: number;
    textbook_version: string;
    subject: "zhixing";
    content_version: string;
  };
  nodes: PackNode[];
  items: PackItem[];
}

export interface LoadedPack {
  pack: ContentPack;
  itemById: Map<string, PackItem>;
  nodeIds: Set<string>;
  /** 归因 → 对应维度的节点 */
  nodeByAttribution: Map<AttributionCode, string>;
  /** SourceVersion 里的 items 段，例如 `items:zhixing-synthetic@0.1.0` */
  itemsSourceVersion: string;
}

const LOCAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;
const NAME_PART = /^[a-z0-9][a-z0-9\-]{0,47}$/;
/** 契约 VersionedName（GradeRequest.rubric_version；kge.scoring_point.rubric_version 是 platform.versioned_name） */
export const VERSIONED_NAME = /^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;
const POINT_ID = /^[a-z0-9_\-]{1,48}$/;
/** kge.node_id（db/migrations/0011_kge__knowledge_map.up.sql；docs/design/kge/check_node_id.py 比对的同一正则） */
export const KGE_NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
/** kge.subject_prefix('zhixing') */
export const ZHIXING_NODE_PREFIX = "MORAL";
export const MAX_NODE_IDS = 8;
const CODE_64 = /^[a-z0-9_\-]{1,64}$/;

const isHalfStep = (x: number) => Number.isFinite(x) && Math.round(x * 2) === x * 2;
const nonEmptyStrings = (a: unknown) => Array.isArray(a) && a.every((s) => typeof s === "string" && s.length > 0 && s.length <= 64);

/** 给分规则的问题清单（空数组 = 合格） */
export function partialCreditProblems(rule: PartialCreditRule | undefined, score: number): string[] {
  if (!rule || typeof rule !== "object") return ["缺失"];
  const out: string[] = [];
  if (!(PARTIAL_CREDIT_MODES as readonly string[]).includes(rule.mode)) out.push("mode 未知");
  if (typeof rule.description !== "string" || rule.description.length === 0 || rule.description.length > 1000) out.push("description 为空或过长");
  const a = rule.allowed_scores;
  if (!Array.isArray(a) || a.length < 2 || !a.every((x) => typeof x === "number" && isHalfStep(x))) {
    out.push("allowed_scores 必须是 0.5 倍数的数组，至少含 0 与满分");
    return out;
  }
  for (let i = 1; i < a.length; i++) if (!(a[i]! > a[i - 1]!)) out.push("allowed_scores 必须严格升序");
  if (a[0] !== 0) out.push("allowed_scores 必须含 0");
  if (a[a.length - 1] !== score) out.push("allowed_scores 必须含满分");
  if (a.some((x) => x < 0 || x > score)) out.push("allowed_scores 不得超出 [0, 分值]");
  if (rule.mode === "all_or_nothing" && !(a.length === 2 && a[0] === 0 && a[1] === score)) out.push("all_or_nothing 只允许 0 与满分");
  return out;
}

/** 检查内容包的完整性；不通过就拒绝启动，而不是运行中才发现。 */
export function indexPack(pack: ContentPack): LoadedPack {
  const problems: string[] = [];
  if (pack.format !== PACK_FORMAT) problems.push(`format 应为 ${PACK_FORMAT}`);
  if (!NAME_PART.test(pack.pack_name)) problems.push("pack_name 不符合 SourceVersion 的名称格式");
  if (!VERSION_PART.test(pack.pack_version)) problems.push("pack_version 不符合 SourceVersion 的版本格式");
  const c = pack.curriculum;
  if (c.subject !== "zhixing") problems.push("curriculum.subject 必须是 zhixing");
  if (!Number.isInteger(c.exam_year) || !Number.isInteger(c.grade)) problems.push("curriculum 的 exam_year、grade 必须是整数");
  if (!/^[a-z][a-z0-9\-]{1,31}$/.test(c.region) || !/^[a-z0-9][a-z0-9\-]{1,31}$/.test(c.textbook_version) || !/^\d+\.\d+\.\d+$/.test(c.content_version)) {
    problems.push("curriculum 的六段标识不符合契约 ContentVersion 格式");
  }
  const nodeIds = new Set<string>();
  const nodeByAttribution = new Map<AttributionCode, string>();
  for (const n of pack.nodes) {
    if (!LOCAL_ID.test(n.id)) problems.push(`节点 ID ${n.id} 不符合平台格式`);
    if (nodeIds.has(n.id)) problems.push(`节点 ${n.id} 重复`);
    nodeIds.add(n.id);
    if (n.attribution !== null) {
      if (!(ATTRIBUTION_CODES as readonly string[]).includes(n.attribution)) problems.push(`节点 ${n.id} 的归因未知`);
      else if (nodeByAttribution.has(n.attribution)) problems.push(`归因 ${n.attribution} 对应了多个节点`);
      else nodeByAttribution.set(n.attribution, n.id);
    }
  }
  for (const a of ATTRIBUTION_CODES) if (!nodeByAttribution.has(a)) problems.push(`归因 ${a} 没有对应的维度节点`);

  const itemById = new Map<string, PackItem>();
  for (const it of pack.items) {
    const where = `题目 ${it.id}`;
    if (!LOCAL_ID.test(it.id)) problems.push(`${where} 的 ID 不符合平台格式`);
    if (itemById.has(it.id)) problems.push(`${where} 重复`);
    if (it.discipline !== "daofa" && it.discipline !== "history") problems.push(`${where} 的 discipline 未知`);
    if (!(ADMISSIONS as readonly string[]).includes(it.admission)) problems.push(`${where} 的准入状态未知`);
    if (pack.synthetic && it.synthetic !== true) problems.push(`${where} 应标为合成题`);
    if (!VERSIONED_NAME.test(it.rubric_version)) problems.push(`${where} 的 rubric_version 不符合契约 VersionedName`);
    if (!(it.total_score > 0) || !isHalfStep(it.total_score)) problems.push(`${where} 的满分必须是正的 0.5 倍数`);
    for (const k of ["title", "grade_scope", "material_text", "prompt_question"] as const) {
      if (typeof it[k] !== "string" || it[k].length === 0 || it[k].length > 2000) problems.push(`${where} 的 ${k} 为空或过长`);
    }
    if (!Array.isArray(it.points) || it.points.length === 0 || it.points.length > 30) problems.push(`${where} 的采分点数量不合格`);
    const seen = new Set<string>();
    let sum = 0;
    for (const p of it.points ?? []) {
      const pw = `${where} 采分点 ${p.point_id}`;
      if (!POINT_ID.test(p.point_id)) problems.push(`${pw} 的 point_id 必须是小写编码（契约 v1.0 svc-#7）`);
      if (seen.has(p.point_id)) problems.push(`${pw} 重复`);
      seen.add(p.point_id);
      if (!(p.score > 0) || !isHalfStep(p.score)) problems.push(`${pw} 的分值必须是正的 0.5 倍数`);
      sum += p.score;
      if (!CODE_64.test(p.subject_dimension)) problems.push(`${pw} 的 subject_dimension 不是编码`);
      if (!nodeIds.has(p.skill_node)) problems.push(`${pw} 的 skill_node ${p.skill_node} 未登记`);
      const kf = p.keyword_family;
      if (!kf || !nonEmptyStrings(kf.core) || kf.core.length === 0 || !nonEmptyStrings(kf.accepted) || !nonEmptyStrings(kf.colloquial)) {
        problems.push(`${pw} 的 keyword_family 三档不完整`);
      }
      for (const k of ["required_logic", "material_linkage"] as const) {
        const v = p[k];
        if (v !== null && (typeof v !== "string" || v.length === 0 || v.length > 1000)) problems.push(`${pw} 的 ${k} 不合格`);
      }
      const ids = p.node_ids;
      if (!Array.isArray(ids) || ids.length < 1 || ids.length > MAX_NODE_IDS) problems.push(`${pw} 的 node_ids 必须有 1–${MAX_NODE_IDS} 个`);
      else {
        if (new Set(ids).size !== ids.length) problems.push(`${pw} 的 node_ids 重复`);
        for (const id of ids) {
          if (typeof id !== "string" || !KGE_NODE_ID.test(id) || id.split(".")[0] !== ZHIXING_NODE_PREFIX) {
            problems.push(`${pw} 的 node_ids 不符合 kge.node_id 格式或不在 ${ZHIXING_NODE_PREFIX} 命名空间`);
          }
        }
      }
      problems.push(...partialCreditProblems(p.partial_credit_rule, p.score).map((m) => `${pw} 的 partial_credit_rule ${m}`));
      for (const e of p.common_errors ?? []) {
        if (!(ATTRIBUTION_CODES as readonly string[]).includes(e.type)) problems.push(`${pw} 的常见失分类型未知`);
      }
    }
    if (Math.abs(sum - it.total_score) > 1e-9) problems.push(`${where} 的采分点分值之和 ${sum} 不等于满分 ${it.total_score}`);
    itemById.set(it.id, it);
  }
  if (problems.length) throw new Error(`内容包不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return { pack, itemById, nodeIds, nodeByAttribution, itemsSourceVersion: `items:${pack.pack_name}@${pack.pack_version}` };
}

/** 一道题涉及的能力视图节点（按采分点顺序去重） */
export const itemNodes = (item: PackItem): string[] => [...new Set(item.points.map((p) => p.skill_node))];

/** 一道题的错因集合（取自采分点的合成常见失分类型；变式“同错因”按它选） */
export const itemErrorCategories = (item: PackItem): Set<AttributionCode> => new Set(item.points.flatMap((p) => p.common_errors.map((e) => e.type)));

export const DEFAULT_PACK_PATH = new URL("../content/pack.json", import.meta.url);

export function loadPack(path: string | URL = DEFAULT_PACK_PATH): LoadedPack {
  return indexPack(JSON.parse(readFileSync(path, "utf8")) as ContentPack);
}
