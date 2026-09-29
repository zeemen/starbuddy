// 打标三层的公共类型与常量（spec 设计要点 2；待人确认 #6 的默认阈值）。
// 打标产出的是候选，不是结论：A 级也只写成待审草稿（KGE V1.1 :343）。

export type Layer = "rule" | "vector" | "model";
export type Role = "main" | "secondary";
export type Grade = "A" | "B";

/** 规则直接命中（legacy 显式引用了已导入节点）的置信度 */
export const RULE_DIRECT = 0.9;
/** 规则推断（章节 → 主题、自然语言名称匹配）的置信度 */
export const RULE_INFERRED = 0.6;
/** A 级门槛：置信度 ≥ 0.85 且规则层与另一层一致（待人确认 #6） */
export const A_MIN_CONFIDENCE = 0.85;
/** 小题节点数上限（含主考点；KGE V1.1 :745，待人确认 #6） */
export const MAX_NODES = 3;
/** 向量层给模型层的候选数 */
export const DEFAULT_TOP_K = 5;

export const RULE_TAGGER_VERSION = "aya-tagger-rules@0.1.0";
/** legacy 阿鸦题的来源类型（迁移 0024 给 kge.item_tag.source_kind 新增的取值；kge.tag_candidate.item_source_kind 同一集合） */
export const LEGACY_SOURCE_KIND = "legacy";

/** 模型路由的必需字段：向量层走 T7、模型层走 T8；打标只送题目文本与节点名，数据等级 L0（不含任何学生数据） */
export type RouteTask = "T7" | "T8";
export const VECTOR_TASK = "T7";
export const MODEL_TASK = "T8";
export const TAGGING_DATA_CLASS = "L0";
export type DataClass = typeof TAGGING_DATA_CLASS;

/** 真实路由的桩在调用时一律抛这个错：不回落到假实现 */
export const ROUTE_NOT_CONNECTED = "真实路由未接入（spec 待人确认 #4、#5）";

/** A 级结论依据的一层（写进 kge.item_tag.tag_basis）：规则层写打标器版本，向量层写嵌入器版本，模型层写模型版本 */
export interface TagBasis { layer: Layer; version: string }
export const REASON_CODE = /^[a-z0-9_]{1,48}$/;
export const VERSIONED_NAME = /^[a-z0-9][a-z0-9-]{0,47}@[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
export const MODEL_VERSION = /^model:[a-z0-9-]+\/[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$/;

export interface Candidate {
  itemId: string;
  nodeId: string;
  role: Role;
  layer: Layer;
  /** 0–1，3 位小数 */
  confidence: number;
  taggerVersion: string;
  /** 只有模型层非空 */
  modelVersion: string | null;
  /** 理由码（不存自由文本）：rule_explicit_ref / rule_chapter_topic / vector_similarity / model_choice 等 */
  reasonCode: string;
}

/** 打标输入：一道题（已按章节归到地图版本里的主题）。text 只在内存里用，不落盘 */
export interface TagItem {
  id: string;
  text: string;
  mainNodes: string[];
  auxNodes: string[];
  preNodes: string[];
}

export class TaggingError extends Error {}

export const round3 = (x: number): number => Math.round(x * 1000) / 1000;
export const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
export const LAYER_ORDER: Record<Layer, number> = { rule: 0, model: 1, vector: 2 };

/** 候选的统一排序：题目、角色（主考点在前）、置信度降序、层、节点 */
export function sortCandidates(cs: Candidate[]): Candidate[] {
  return [...cs].sort((a, b) => cmp(a.itemId, b.itemId) || (a.role === b.role ? 0 : a.role === "main" ? -1 : 1)
    || b.confidence - a.confidence || LAYER_ORDER[a.layer] - LAYER_ORDER[b.layer] || cmp(a.nodeId, b.nodeId)
    || cmp(a.reasonCode, b.reasonCode));
}

export function checkCandidate(c: Candidate): Candidate {
  if (!(c.confidence >= 0 && c.confidence <= 1) || round3(c.confidence) !== c.confidence) throw new TaggingError(`${c.itemId}：置信度 ${c.confidence} 必须在 0–1 且最多 3 位小数`);
  if (!REASON_CODE.test(c.reasonCode)) throw new TaggingError(`${c.itemId}：理由码 ${c.reasonCode} 不合法`);
  if (!VERSIONED_NAME.test(c.taggerVersion)) throw new TaggingError(`${c.itemId}：打标器版本 ${c.taggerVersion} 不合法`);
  if ((c.layer === "model") !== (c.modelVersion !== null)) throw new TaggingError(`${c.itemId}：只有模型层带模型版本`);
  if (c.modelVersion !== null && !MODEL_VERSION.test(c.modelVersion)) throw new TaggingError(`${c.itemId}：模型版本 ${c.modelVersion} 不合法`);
  return c;
}
