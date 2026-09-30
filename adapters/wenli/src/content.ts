// 服务端只读内容包（spec 设计要点 1）。全部是合成内容（待人确认 #5、#8），题面、选项、标准答案、作文题、量表锚点
// 只进本进程与模型路由（作文判分 Prompt），任何接口都不返回。
//
// - 能力格（cells）：替代 legacy 7 域 × 4 层的 28 格，8–12 个合成格，每格属于一个域（带合成 ROI 权重）与一个层（L1–L4）。
//   格 ID 同时是平台能力视图节点（skill_node 的 local_id）与 KGE 节点 ID 的写法（kge.node_id，CHN 命名空间，迁移 0011）。
// - 客观题（items）：选择（按选项）与填空（规范化匹配，可用“｜”列多个可接受答案），每题挂一个能力格。
// - 作文题（essays）与五维量表（rubric）：每维 L1–L4 四档的分值与锚点；卷面书写维需要图片（requires_image）；
//   另有确定性的“字数规范”维（convention，rule_ref 登记名）。
import { readFileSync } from "node:fs";

export const PACK_FORMAT = "wenli-content-pack/1";

export const ADMISSIONS = ["admitted", "pending_review", "source_unverified", "rejected"] as const;
export type Admission = (typeof ADMISSIONS)[number];

export const LEVELS = ["L1", "L2", "L3", "L4"] as const;
export type Level = (typeof LEVELS)[number];

export interface PackDomain {
  code: string;
  label: string;
  /** 合成的 ROI 近似权重（legacy DOMAIN_ROI 的做法；正式权重待教研，盘点 A1） */
  roi: number;
}

export interface PackCell {
  id: string;
  domain: string;
  level: Level;
}

interface ItemBase {
  id: string;
  synthetic: boolean;
  admission: Admission;
  cell: string;
  estimated_minutes: number;
  stem: string;
}
export interface ChoiceItem extends ItemBase {
  type: "choice";
  options: Record<string, string>;
  /** 正确选项的字母（单选一个，多选多个；判分要求集合完全相等） */
  answer: string[];
}
export interface FillItem extends ItemBase {
  type: "fill";
  /** 标准答案；可用“｜”或“|”列多个可接受答案，括号里的说明在比对时剥离（legacy matchFill） */
  answer: string;
}
export type ObjectiveItem = ChoiceItem | FillItem;

export interface EssayItem {
  id: string;
  synthetic: boolean;
  admission: Admission;
  rubric_version: string;
  cell: string;
  genre: string;
  /** 作文题要求的最低字数（字数规范维按它判） */
  min_chars: number;
  title: string;
  prompt_text: string;
}

export interface RubricLevel {
  level: Level;
  score: number;
  anchor: string;
}
export interface RubricDimension {
  dimension_id: string;
  label: string;
  max_score: number;
  /** 需要卷面图片才能判（卷面书写）：路由接口只收文本，这一维不可判（待人确认 #4） */
  requires_image: boolean;
  levels: RubricLevel[];
}
export interface ConventionRule {
  dimension_id: string;
  label: string;
  max_score: number;
  rule_ref: string;
  /** 每少这么多字扣 1 分（legacy DEDUCTION_RULES：每少 50 字扣 1 分，最多 2 分） */
  chars_per_deduction: number;
  max_deduction: number;
}
export interface EssayRubric {
  rubric_version: string;
  total_score: number;
  dimensions: RubricDimension[];
  convention: ConventionRule;
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
    subject: "wenli";
    content_version: string;
  };
  domains: PackDomain[];
  cells: PackCell[];
  items: ObjectiveItem[];
  essays: EssayItem[];
  rubric: EssayRubric;
}

export interface LoadedPack {
  pack: ContentPack;
  itemById: Map<string, ObjectiveItem>;
  essayById: Map<string, EssayItem>;
  cellById: Map<string, PackCell>;
  domainByCode: Map<string, PackDomain>;
  /** 模型定档的维度（不需要图片的） */
  modelDimensions: RubricDimension[];
  /** 需要图片、当前不可判的维度 */
  imageDimensions: RubricDimension[];
  /** SourceVersion 里的 items 段，例如 `items:wenli-synthetic@0.1.0` */
  itemsSourceVersion: string;
}

const LOCAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;
const NAME_PART = /^[a-z0-9][a-z0-9\-]{0,47}$/;
/** 契约 VersionedName（GradeRequest.rubric_version、DimensionScore.rule_ref） */
export const VERSIONED_NAME = /^[a-z0-9][a-z0-9\-]{0,47}@[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;
/** 契约 DimensionScore.dimension_id */
const DIMENSION_ID = /^[a-z0-9_\-]{1,48}$/;
/** kge.node_id（db/migrations/0011_kge__knowledge_map.up.sql；docs/design/kge/check_node_id.py 比对的同一正则） */
export const KGE_NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
/** kge.subject_prefix('wenli') */
export const WENLI_NODE_PREFIX = "CHN";
/** 契约 EvidencePayloadChoice 的选项写法 */
export const CHOICE_CODE = /^[A-Za-z0-9]{1,8}$/;
const DOMAIN_CODE = /^[a-z0-9_]{1,16}$/;

const text = (v: unknown, max: number) => typeof v === "string" && v.length > 0 && v.length <= max;

/** 检查内容包的完整性；不通过就拒绝启动，而不是运行中才发现。 */
export function indexPack(pack: ContentPack): LoadedPack {
  const problems: string[] = [];
  if (pack.format !== PACK_FORMAT) problems.push(`format 应为 ${PACK_FORMAT}`);
  if (!NAME_PART.test(pack.pack_name)) problems.push("pack_name 不符合 SourceVersion 的名称格式");
  if (!VERSION_PART.test(pack.pack_version)) problems.push("pack_version 不符合 SourceVersion 的版本格式");
  if (pack.synthetic !== true || !/合成/.test(pack.notice) || !/非真题/.test(pack.notice)) problems.push("内容包必须标注“合成、非真题”");
  const c = pack.curriculum;
  if (c.subject !== "wenli") problems.push("curriculum.subject 必须是 wenli");
  if (!Number.isInteger(c.exam_year) || !Number.isInteger(c.grade)) problems.push("curriculum 的 exam_year、grade 必须是整数");
  if (!/^[a-z][a-z0-9\-]{1,31}$/.test(c.region) || !/^[a-z0-9][a-z0-9\-]{1,31}$/.test(c.textbook_version) || !/^\d+\.\d+\.\d+$/.test(c.content_version)) {
    problems.push("curriculum 的六段标识不符合契约 ContentVersion 格式");
  }

  const domainByCode = new Map<string, PackDomain>();
  for (const d of pack.domains ?? []) {
    if (!DOMAIN_CODE.test(d.code)) problems.push(`域 ${d.code} 的编码不合格`);
    if (domainByCode.has(d.code)) problems.push(`域 ${d.code} 重复`);
    if (!(typeof d.roi === "number" && d.roi > 0 && d.roi <= 1)) problems.push(`域 ${d.code} 的 ROI 权重必须在 (0, 1]`);
    domainByCode.set(d.code, d);
  }

  const cellById = new Map<string, PackCell>();
  for (const cell of pack.cells ?? []) {
    const where = `能力格 ${cell.id}`;
    if (!LOCAL_ID.test(cell.id) || !KGE_NODE_ID.test(cell.id) || cell.id.split(".")[0] !== WENLI_NODE_PREFIX) {
      problems.push(`${where} 的 ID 不符合 kge.node_id 格式或不在 ${WENLI_NODE_PREFIX} 命名空间`);
    }
    if (cellById.has(cell.id)) problems.push(`${where} 重复`);
    if (!domainByCode.has(cell.domain)) problems.push(`${where} 的域 ${cell.domain} 未定义`);
    if (!(LEVELS as readonly string[]).includes(cell.level)) problems.push(`${where} 的层不是 L1–L4`);
    if ([...cellById.values()].some((x) => x.domain === cell.domain && x.level === cell.level)) problems.push(`${where} 与另一格同域同层`);
    cellById.set(cell.id, cell);
  }
  if (cellById.size < 8 || cellById.size > 12) problems.push("合成能力格应为 8–12 个（spec 设计要点 1）");

  const itemById = new Map<string, ObjectiveItem>();
  const allIds = new Set<string>();
  const checkCommon = (it: { id: string; synthetic: boolean; admission: Admission; cell: string }, where: string) => {
    if (!LOCAL_ID.test(it.id)) problems.push(`${where} 的 ID 不符合平台格式`);
    if (allIds.has(it.id)) problems.push(`${where} 重复`);
    allIds.add(it.id);
    if (!(ADMISSIONS as readonly string[]).includes(it.admission)) problems.push(`${where} 的准入状态未知`);
    if (it.synthetic !== true) problems.push(`${where} 应标为合成题`);
    if (!cellById.has(it.cell)) problems.push(`${where} 的能力格 ${it.cell} 未登记`);
  };
  for (const it of pack.items ?? []) {
    const where = `客观题 ${it.id}`;
    checkCommon(it, where);
    if (!/^synthetic-q\d{2}$/.test(it.id)) problems.push(`${where} 的 ID 应为 synthetic-qNN`);
    if (!text(it.stem, 500) || !it.stem.includes("合成")) problems.push(`${where} 的题面为空、过长或没有标注合成`);
    if (!(Number.isInteger(it.estimated_minutes) && it.estimated_minutes >= 1 && it.estimated_minutes <= 30)) problems.push(`${where} 的 estimated_minutes 不合格`);
    if (it.type === "choice") {
      const keys = Object.keys(it.options ?? {});
      if (keys.length < 2 || !keys.every((k) => /^[A-H]$/.test(k)) || !keys.every((k) => text(it.options[k], 200))) problems.push(`${where} 的选项不合格`);
      if (!Array.isArray(it.answer) || it.answer.length === 0 || new Set(it.answer).size !== it.answer.length || !it.answer.every((a) => keys.includes(a))) {
        problems.push(`${where} 的标准答案必须是已有选项的字母`);
      }
    } else if (it.type === "fill") {
      if (!text(it.answer, 200)) problems.push(`${where} 的标准答案为空或过长`);
    } else {
      problems.push(`${where} 的题型未知`);
    }
    itemById.set(it.id, it);
  }
  if (itemById.size < 8 || itemById.size > 12) problems.push("合成客观题应为 8–12 道（spec 设计要点 1）");

  const r = pack.rubric;
  if (!r || !VERSIONED_NAME.test(r.rubric_version)) problems.push("量表的 rubric_version 不符合契约 VersionedName");
  const dimIds = new Set<string>();
  let sum = 0;
  for (const d of r?.dimensions ?? []) {
    const where = `量表维度 ${d.dimension_id}`;
    if (!DIMENSION_ID.test(d.dimension_id)) problems.push(`${where} 的 dimension_id 不是编码`);
    if (dimIds.has(d.dimension_id)) problems.push(`${where} 重复`);
    dimIds.add(d.dimension_id);
    if (!(d.max_score > 0)) problems.push(`${where} 的满分必须为正`);
    sum += d.max_score;
    const levels = (d.levels ?? []).map((l) => l.level);
    if (levels.join(",") !== LEVELS.join(",")) problems.push(`${where} 必须按 L1–L4 顺序给出四档`);
    let prev = -1;
    for (const l of d.levels ?? []) {
      if (!(typeof l.score === "number" && l.score > prev && l.score <= d.max_score)) problems.push(`${where} 的 ${l.level} 分值必须严格递增且不超过满分`);
      prev = l.score;
      if (!text(l.anchor, 300)) problems.push(`${where} 的 ${l.level} 锚点为空或过长`);
    }
    if (d.levels?.at(-1)?.score !== d.max_score) problems.push(`${where} 的 L4 分值必须等于满分`);
  }
  const conv = r?.convention;
  if (!conv || !DIMENSION_ID.test(conv.dimension_id) || dimIds.has(conv.dimension_id)) problems.push("字数规范维的 dimension_id 不合格或与量表维度重复");
  else {
    if (!VERSIONED_NAME.test(conv.rule_ref)) problems.push("字数规范维的 rule_ref 不符合契约 VersionedName");
    if (!(Number.isInteger(conv.max_score) && conv.max_score > 0)) problems.push("字数规范维的满分必须是正整数");
    if (!(Number.isInteger(conv.chars_per_deduction) && conv.chars_per_deduction > 0)) problems.push("字数规范维的 chars_per_deduction 必须是正整数");
    if (!(Number.isInteger(conv.max_deduction) && conv.max_deduction >= 0 && conv.max_deduction <= conv.max_score)) problems.push("字数规范维的 max_deduction 不合格");
    sum += conv.max_score;
  }
  const expectedDims = ["审题立意", "内容材料", "结构层次", "语言表达", "卷面书写"];
  if ((r?.dimensions ?? []).map((d) => d.label).join(",") !== expectedDims.join(",")) problems.push(`量表必须是五维：${expectedDims.join("、")}`);
  if (r && Math.abs(sum - r.total_score) > 1e-9) problems.push(`量表各维满分之和 ${sum} 不等于总分 ${r.total_score}`);

  const essayById = new Map<string, EssayItem>();
  for (const e of pack.essays ?? []) {
    const where = `作文题 ${e.id}`;
    checkCommon(e, where);
    if (!/^synthetic-essay-0\d$/.test(e.id)) problems.push(`${where} 的 ID 应为 synthetic-essay-0N`);
    if (e.rubric_version !== r?.rubric_version) problems.push(`${where} 的 rubric_version 与量表不一致`);
    if (!(Number.isInteger(e.min_chars) && e.min_chars >= 100 && e.min_chars <= 2000)) problems.push(`${where} 的最低字数不合格`);
    if (!DOMAIN_CODE.test(e.genre)) problems.push(`${where} 的 genre 不是编码`);
    for (const k of ["title", "prompt_text"] as const) if (!text(e[k], 1000) || !e[k].includes("合成")) problems.push(`${where} 的 ${k} 为空、过长或没有标注合成`);
    essayById.set(e.id, e);
  }
  if (essayById.size !== 2) problems.push("合成作文题应为 2 道（spec 设计要点 1）");

  if (problems.length) throw new Error(`内容包不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return {
    pack,
    itemById,
    essayById,
    cellById,
    domainByCode,
    modelDimensions: r.dimensions.filter((d) => !d.requires_image),
    imageDimensions: r.dimensions.filter((d) => d.requires_image),
    itemsSourceVersion: `items:${pack.pack_name}@${pack.pack_version}`,
  };
}

/**
 * 题型编码（generate_variation 的“同题型”按它严格相等，spec 4-D 待人确认 #4）：客观题取内容包的 type（choice / fill），
 * 作文取 `essay_<genre>`（记叙文与情境写作不互为变式）。只在适配器内部使用，不出现在任何响应里。
 */
export const questionTypeOf = (x: ObjectiveItem | EssayItem): string => ("type" in x ? x.type : `essay_${x.genre}`);

export const DEFAULT_PACK_PATH = new URL("../content/pack.json", import.meta.url);

export function loadPack(path: string | URL = DEFAULT_PACK_PATH): LoadedPack {
  return indexPack(JSON.parse(readFileSync(path, "utf8")) as ContentPack);
}
