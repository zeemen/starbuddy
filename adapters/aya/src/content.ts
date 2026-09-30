// 服务端只读内容包（specs/adapter-aya-sprint1 范围第 3 条；待人确认 #2 方案 a）。
// 内容包由 scripts/build-content-pack.ts 从 legacy 生成；答案只在本进程内使用，任何接口都不返回。
//
// 格式 2（specs/adapter-aya-methods-sprint4 设计要点第 0 节）：题目加 usage、estMinutes、hintPath，节点加 board，
// 另加板块中考占比（Q1 推导值，待教研校准）与“阿鸦节点 → KGE 知识节点”对应表（KGE-01 导入阿鸦图谱前为空）。
// 仍然没有题干、解析与前置关系（前置关系由请求里的 node_path 提供，知识结构以 KGE 为准）。
// 与 4-G 发布闸门的格式变更（逐题来源、准入决定文件）的合并顺序见 lessons.md 待人确认。
import { readFileSync } from "node:fs";
import type { Question, RootCause } from "./core/types.ts";
import { ROOT_CAUSES } from "./core/types.ts";

export const PACK_FORMAT = "aya-content-pack/2";

export type Admission = "admitted" | "pending_review" | "source_unverified" | "rejected";

/** 题目用途（legacy QuestionUsage）：D 诊断、P 错误探针、L 学习训练、T 迁移、R 延迟复测 */
export const ITEM_USAGES = ["D", "P", "L", "T", "R"] as const;
export type ItemUsage = (typeof ITEM_USAGES)[number];

/** 提示阶梯最多 7 级（0–6，阿鸦规格书 §3.1）；索引即等级 */
export const MAX_HINT_PATH = 7;

export interface PackItem extends Question {
  admission: Admission;
  usage: ItemUsage;
  /** 预估用时（分钟，legacy estMinutes） */
  estMinutes: number;
  /** 提示阶梯原文（legacy hintPath，索引即等级）。只在 teach(hint) 里按等级取一条，不整体返回 */
  hintPath: string[];
}

export interface PackNode {
  id: string;
  kind: string;
  cluster: string;
  /** 十大知识板块之一（legacy boardOfNode） */
  board: string;
}

/** 板块中考分值占比：legacy content/examweights.ts BOARD_EXAM_SHARE，产品推导值（Q1），待教研校准（spec 待人确认 #6） */
export interface BoardExamShare {
  status: "q1_derived_uncalibrated";
  shares: Record<string, number>;
}

/**
 * 阿鸦节点 → KGE 知识节点（spec 待人确认 #2 默认）：内容来自平台导出的 kge.skill_link，带地图版本。
 * KGE-01 导入阿鸦图谱之前为空（map_ref = null、links = []），此时 plan 不使用 node_path。
 */
export interface SkillLinks {
  /** 地图版本，写进 source_version 的 graph 段，例如 `aya-map@1.0.0` */
  map_ref: string | null;
  links: { skill_node: string; node_id: string }[];
}

export interface ContentPack {
  format: typeof PACK_FORMAT;
  pack_name: string;
  pack_version: string;
  legacy_commit: string;
  curriculum: {
    region: string;
    exam_year: number;
    grade: number;
    textbook_version: string;
    subject: "aya";
    content_version: string;
  };
  source_verified: boolean;
  nodes: PackNode[];
  items: PackItem[];
  traps: { code: string; rootCause: RootCause }[];
  board_exam_share: BoardExamShare;
  skill_links: SkillLinks;
}

export interface LoadedPack {
  pack: ContentPack;
  itemById: Map<string, PackItem>;
  nodeById: Map<string, PackNode>;
  /** 主测节点 → 题目（按题号排序） */
  itemsByNode: Map<string, PackItem[]>;
  trapByCode: Map<string, RootCause>;
  /** 阿鸦节点 → KGE 知识节点（对应表为空时为空） */
  knowledgeNodesOf: Map<string, string[]>;
  /** SourceVersion 里的 items 段，例如 `items:aya-pack@0.1.0-0ed2fe4` */
  itemsSourceVersion: string;
}

const REVIEW_ORDER = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5"] as const;

/** 准入规则（aya adapter-design §3.1）：审核等级 ≥ Q2 且来源已核才算 admitted。 */
export function admissionOf(reviewStatus: Question["reviewStatus"], sourceVerified: boolean): Admission {
  if (!sourceVerified) return "source_unverified";
  return REVIEW_ORDER.indexOf(reviewStatus) >= 2 ? "admitted" : "pending_review";
}

const LOCAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
/** 与 knowledge-service.v0.9.yaml NodeId 同一正则 */
export const KGE_NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
/** 与契约 SourceVersion 的一段同一写法（名称@版本），类别固定为 graph */
const MAP_REF = /^[a-z0-9][a-z0-9\-]{0,47}@\d+\.\d+\.\d+$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;

/** 检查内容包的引用完整性；不通过就拒绝启动，而不是运行中才发现。 */
export function indexPack(pack: ContentPack): LoadedPack {
  const problems: string[] = [];
  if (pack.format !== PACK_FORMAT) problems.push(`format 应为 ${PACK_FORMAT}`);
  if (!VERSION_PART.test(pack.pack_version)) problems.push("pack_version 不符合 SourceVersion 的版本格式");
  const c = pack.curriculum;
  if (!Number.isInteger(c.exam_year) || !Number.isInteger(c.grade)) problems.push("curriculum 的 exam_year、grade 必须是整数");
  if (!/^[a-z][a-z0-9\-]{1,31}$/.test(c.region) || !/^[a-z0-9][a-z0-9\-]{1,31}$/.test(c.textbook_version) || !/^\d+\.\d+\.\d+$/.test(c.content_version)) {
    problems.push("curriculum 的六段标识不符合契约 ContentVersion 格式");
  }
  const nodeById = new Map(pack.nodes.map((n) => [n.id, n]));
  const nodeIds = new Set(nodeById.keys());
  const share = pack.board_exam_share;
  if (!share || share.status !== "q1_derived_uncalibrated") problems.push("board_exam_share 必须标注 q1_derived_uncalibrated");
  for (const [b, v] of Object.entries(share?.shares ?? {})) {
    if (!(typeof v === "number" && v >= 0 && v <= 1)) problems.push(`板块 ${b} 的中考占比不在 0–1`);
  }
  for (const n of pack.nodes) if (!(n.board in (share?.shares ?? {}))) problems.push(`节点 ${n.id} 的板块 ${n.board} 没有中考占比`);
  const links = pack.skill_links;
  const knowledgeNodesOf = new Map<string, string[]>();
  if (!links || !Array.isArray(links.links)) problems.push("缺少 skill_links");
  else {
    if (links.map_ref !== null && !MAP_REF.test(links.map_ref)) problems.push("skill_links.map_ref 不符合 名称@x.y.z");
    if (links.links.length > 0 && links.map_ref === null) problems.push("skill_links 有对应关系却没有地图版本");
    for (const l of links.links) {
      if (!nodeIds.has(l.skill_node)) problems.push(`对应表里的阿鸦节点 ${l.skill_node} 未登记`);
      if (!KGE_NODE_ID.test(l.node_id)) problems.push(`对应表里的知识节点 ${l.node_id} 不符合 KGE NodeId`);
      const list = knowledgeNodesOf.get(l.skill_node) ?? [];
      if (!list.includes(l.node_id)) list.push(l.node_id);
      knowledgeNodesOf.set(l.skill_node, list);
    }
  }
  const trapByCode = new Map<string, RootCause>();
  for (const t of pack.traps) {
    if (!(ROOT_CAUSES as readonly string[]).includes(t.rootCause)) problems.push(`陷阱 ${t.code} 的根因未知`);
    trapByCode.set(t.code, t.rootCause);
  }
  const itemById = new Map<string, PackItem>();
  for (const it of pack.items) {
    if (!LOCAL_ID.test(it.id)) problems.push(`题目 ID ${it.id} 不符合平台格式`);
    if (itemById.has(it.id)) problems.push(`题目 ${it.id} 重复`);
    if (it.mainNodes.length === 0) problems.push(`题目 ${it.id} 没有主测节点`);
    for (const n of it.mainNodes) if (!nodeIds.has(n)) problems.push(`题目 ${it.id} 的主测节点 ${n} 未登记`);
    const traps = [...(it.options ?? []).flatMap((o) => o.traps ?? []), ...(it.wrongAnswerTraps ?? []).flatMap((w) => w.traps)];
    for (const c of traps) if (!trapByCode.has(c)) problems.push(`题目 ${it.id} 引用了未知陷阱 ${c}`);
    if (it.admission !== admissionOf(it.reviewStatus, pack.source_verified)) problems.push(`题目 ${it.id} 的准入状态与规则不符`);
    if (!(ITEM_USAGES as readonly string[]).includes(it.usage)) problems.push(`题目 ${it.id} 的用途未知`);
    if (!(Number.isInteger(it.estMinutes) && it.estMinutes >= 1 && it.estMinutes <= 120)) problems.push(`题目 ${it.id} 的预估用时不是 1–120 的整数`);
    if (!Array.isArray(it.hintPath) || it.hintPath.length === 0 || it.hintPath.length > MAX_HINT_PATH || !it.hintPath.every((h) => typeof h === "string" && h.trim() !== "" && h.length <= 4000)) {
      problems.push(`题目 ${it.id} 的提示阶梯不合格`);
    }
    itemById.set(it.id, it);
  }
  const itemsByNode = new Map<string, PackItem[]>();
  for (const it of [...itemById.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
    for (const n of new Set(it.mainNodes)) {
      const list = itemsByNode.get(n) ?? [];
      list.push(it);
      itemsByNode.set(n, list);
    }
  }
  for (const n of pack.nodes) if (!LOCAL_ID.test(n.id)) problems.push(`节点 ID ${n.id} 不符合平台格式`);
  if (problems.length) throw new Error(`内容包不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return {
    pack,
    itemById,
    nodeById,
    itemsByNode,
    trapByCode,
    knowledgeNodesOf,
    itemsSourceVersion: `items:${pack.pack_name}@${pack.pack_version}`,
  };
}

export const DEFAULT_PACK_PATH = new URL("../content/pack.json", import.meta.url);

export function loadPack(path: string | URL = DEFAULT_PACK_PATH): LoadedPack {
  return indexPack(JSON.parse(readFileSync(path, "utf8")) as ContentPack);
}
