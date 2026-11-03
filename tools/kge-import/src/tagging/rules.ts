// 打标规则层（spec 设计要点 2，本任务完整实现）：确定性，只看 legacy 的结构字段与地图版本，不看题干。
// 三类规则：
//   1. 题目 ID 前缀与章节 → 主题映射：把题目归到章节（Q-FUN-U01-… → fun；压轴专项按主测节点所在章节），决定在不在已导入主题里；
//   2. 显式引用：legacy 的 mainNodes / auxNodes 里的旧编码经 kge.id_migration 落到新节点——直接命中，置信度 0.9；
//      旧编码在已导入章节里、但节点没导入（能力子节点 C、缺错因的易错点 P）时，退到它所在知识簇的主题节点——推断，0.6；
//   3. 自然语言引用（KGE-00 统计 97 处，全部在 preNodes）：按名称规范化后与节点名比对；与地图节点同名 → 推断候选 0.6（只对
//      mainNodes / auxNodes 生效）；preNodes 是前置（错误追溯方向），不是题目考查的节点，只解析、进工作单，不产生候选。
//   退到主题的候选，在同一题已有该主题下的节点被命中时丢掉（父子节点不同时进节点清单）。
// 名称比对不猜近义词：部分包含只作为给教研的“legacy 节点建议”，不产生候选（KGE-00 待人确认 #6：不自动猜映射）。
import type { LegacyNodeInfo, LegacyQuestion } from "../legacy-items.ts";
import type { MapInput } from "../map-version.ts";
import { cmp, RULE_DIRECT, RULE_INFERRED, RULE_TAGGER_VERSION, type Candidate, type Role } from "./types.ts";

export interface LegacyNodeIndexEntry extends LegacyNodeInfo { chapter: string }

export interface RuleContext {
  map: MapInput;
  /** 全部 legacy 节点（按旧编码；同一编码在多个章节重复定义时取第一次出现） */
  legacyNodes: Map<string, LegacyNodeIndexEntry>;
  /** 地图版本已导入的章节（由迁移表里的旧编码所在章节推出） */
  importedChapters: Set<string>;
  /** 知识簇段（FUN_CON）→ 地图里的主题节点 ID */
  clusterTopic: Map<string, string>;
  /** 地图叶子节点：规范化名称 → 节点 ID 列表 */
  mapNames: Map<string, string[]>;
  /** legacy 节点：规范化名称 → 旧编码列表 */
  legacyNames: Map<string, string[]>;
  knownChapters: Set<string>;
  /** 地图节点 → 父节点 */
  parentOf: Map<string, string | null>;
}

const LEGACY_ID_SHAPE = /^[A-Z]{1,2}-[A-Z0-9]{2,6}-[0-9A-Z]{2,6}$/;

/** 名称规范化：全角转半角（NFKC）、去空白、小写 */
export function normalizeName(s: string): string {
  return s.normalize("NFKC").replace(/\s+/g, "").toLowerCase();
}
/** 去掉结尾的括号补充说明：“数据的集中趋势（平均数、中位数、众数）” → “数据的集中趋势” */
export function baseName(s: string): string {
  return normalizeName(s).replace(/\([^()]*\)$/, "");
}

export function buildRuleContext(map: MapInput, topics: { id: string; nodes: LegacyNodeInfo[] }[]): RuleContext {
  const legacyNodes = new Map<string, LegacyNodeIndexEntry>();
  for (const t of topics) for (const n of t.nodes) if (!legacyNodes.has(n.id)) legacyNodes.set(n.id, { ...n, chapter: t.id });
  const importedChapters = new Set<string>();
  for (const old of map.idMigration.keys()) {
    const n = legacyNodes.get(old);
    if (n) importedChapters.add(n.chapter);
  }
  const clusterTopic = new Map<string, string>();
  for (const n of map.nodes) if (n.level === "topic") clusterTopic.set(n.id.split(".").pop()!, n.id);
  const push = (m: Map<string, string[]>, k: string, v: string) => {
    if (k === "") return;
    const xs = m.get(k) ?? [];
    if (!xs.includes(v)) xs.push(v);
    m.set(k, xs);
  };
  const mapNames = new Map<string, string[]>();
  for (const n of map.nodes) if (n.level === "node") push(mapNames, normalizeName(n.name), n.id);
  const legacyNames = new Map<string, string[]>();
  for (const n of [...legacyNodes.values()].sort((a, b) => cmp(a.id, b.id))) push(legacyNames, normalizeName(n.name), n.id);
  return { map, legacyNodes, importedChapters, clusterTopic, mapNames, legacyNames, knownChapters: new Set(topics.map((t) => t.id)),
           parentOf: new Map(map.nodes.map((n) => [n.id, n.parentId])) };
}

// ---------- 1. 章节归属 ----------
export interface Route {
  chapter: string | null;
  /** topic：legacy 专题注册表；id_prefix：题目 ID 前缀；main_nodes：主测节点所在章节（压轴专项） */
  via: "topic" | "id_prefix" | "main_nodes" | null;
  /** 题目 ID 前缀与所在专题不一致时的提示 */
  prefixMismatch: boolean;
}

export function routeItem(q: Pick<LegacyQuestion, "id" | "topic" | "mainNodes">, ctx: Pick<RuleContext, "legacyNodes" | "knownChapters">): Route {
  const m = /^Q-([A-Z]{2,8})-U\d{2}-/.exec(q.id);
  const prefix = m ? m[1]!.toLowerCase() : null;
  if (q.topic !== null) return { chapter: q.topic, via: "topic", prefixMismatch: prefix !== null && prefix !== q.topic };
  if (prefix !== null && ctx.knownChapters.has(prefix)) return { chapter: prefix, via: "id_prefix", prefixMismatch: false };
  // 压轴专项（Q-SPR-…）：按主测节点所在章节投票，平票取第一个主测节点的章节
  const votes = new Map<string, number>();
  const order: string[] = [];
  for (const ref of q.mainNodes) {
    const ch = ctx.legacyNodes.get(ref)?.chapter;
    if (!ch) continue;
    if (!votes.has(ch)) order.push(ch);
    votes.set(ch, (votes.get(ch) ?? 0) + 1);
  }
  if (order.length === 0) return { chapter: null, via: null, prefixMismatch: false };
  const best = order.reduce((a, b) => ((votes.get(b) ?? 0) > (votes.get(a) ?? 0) ? b : a));
  return { chapter: best, via: "main_nodes", prefixMismatch: false };
}

// ---------- 3. 自然语言引用 ----------
export interface TextResolution {
  text: string;
  /** 与地图叶子节点同名（唯一）时的节点 ID */
  mapNode: string | null;
  /** 与地图叶子节点同名但不唯一（不产生候选） */
  mapAmbiguous: string[];
  /** 与 legacy 节点同名的旧编码（给教研的建议） */
  legacyExact: string[];
  /** 名称互相包含的 legacy 旧编码（最多 3 个，给教研的建议；较短一方至少 2 个字） */
  legacyPartial: string[];
}

export function isNaturalLanguageRef(ref: string, ctx: Pick<RuleContext, "legacyNodes">): boolean {
  return !ctx.legacyNodes.has(ref) && !LEGACY_ID_SHAPE.test(ref);
}

export function resolveText(text: string, ctx: Pick<RuleContext, "mapNames" | "legacyNames">): TextResolution {
  const keys = [...new Set([normalizeName(text), baseName(text)])].filter((k) => k !== "");
  const mapHits = [...new Set(keys.flatMap((k) => ctx.mapNames.get(k) ?? []))].sort(cmp);
  const legacyExact = [...new Set(keys.flatMap((k) => ctx.legacyNames.get(k) ?? []))].sort(cmp);
  const partial: string[] = [];
  const k0 = baseName(text);
  if (legacyExact.length === 0 && k0.length >= 2) {
    for (const [name, ids] of ctx.legacyNames) {
      const shorter = name.length < k0.length ? name : k0;
      if (shorter.length >= 2 && (name.includes(k0) || k0.includes(name))) partial.push(...ids);
    }
  }
  return {
    text,
    mapNode: mapHits.length === 1 ? mapHits[0]! : null,
    mapAmbiguous: mapHits.length > 1 ? mapHits : [],
    legacyExact,
    legacyPartial: [...new Set(partial)].sort(cmp).slice(0, 3),
  };
}

// ---------- 2. 显式引用与候选 ----------
export type RefField = "main" | "aux" | "pre";
export type RefOutcome =
  | "mapped" // 旧编码经迁移表落到节点（直接命中）
  | "topic_fallback" // 节点未导入，退到知识簇的主题节点（推断）
  | "not_in_map" // 在已导入章节里，但节点与其主题都不在地图里
  | "other_chapter" // 旧编码在未导入的章节
  | "dangling" // 形似旧编码但 legacy 里没有
  | "text_mapped" // 自然语言，与地图节点同名（推断）
  | "text_unmatched" // 自然语言，对不上地图节点
  | "prerequisite"; // 前置里的旧编码（不产生候选）

export interface RefResult {
  field: RefField;
  ref: string;
  outcome: RefOutcome;
  nodeId: string | null;
  text: TextResolution | null;
}

export interface RuleResult {
  candidates: Candidate[];
  refs: RefResult[];
}

function cand(itemId: string, nodeId: string, role: Role, confidence: number, reasonCode: string): Candidate {
  return { itemId, nodeId, role, layer: "rule", confidence, taggerVersion: RULE_TAGGER_VERSION, modelVersion: null, reasonCode };
}

/** 解析一道题的全部节点引用；inScope 为真时（题目在已导入主题里）产出候选。候选顺序即 legacy 里的引用顺序 */
export function ruleLayer(q: Pick<LegacyQuestion, "id" | "mainNodes" | "auxNodes" | "preNodes">, ctx: RuleContext, inScope: boolean): RuleResult {
  const candidates: Candidate[] = [];
  const refs: RefResult[] = [];
  const fields: [RefField, string[]][] = [["main", q.mainNodes], ["aux", q.auxNodes], ["pre", q.preNodes]];
  for (const [field, list] of fields) {
    for (const ref of list) {
      const role: Role = field === "main" ? "main" : "secondary";
      if (isNaturalLanguageRef(ref, ctx)) {
        const t = resolveText(ref, ctx);
        if (t.mapNode !== null && field !== "pre") {
          refs.push({ field, ref, outcome: "text_mapped", nodeId: t.mapNode, text: t });
          if (inScope) candidates.push(cand(q.id, t.mapNode, role, RULE_INFERRED, "rule_text_name"));
        } else {
          refs.push({ field, ref, outcome: t.mapNode !== null ? "text_mapped" : "text_unmatched", nodeId: t.mapNode, text: t });
        }
        continue;
      }
      const legacy = ctx.legacyNodes.get(ref);
      if (!legacy) {
        refs.push({ field, ref, outcome: "dangling", nodeId: null, text: null });
        continue;
      }
      if (field === "pre") {
        refs.push({ field, ref, outcome: "prerequisite", nodeId: ctx.map.idMigration.get(ref) ?? null, text: null });
        continue;
      }
      const mapped = ctx.map.idMigration.get(ref);
      if (mapped) {
        refs.push({ field, ref, outcome: "mapped", nodeId: mapped, text: null });
        if (inScope) candidates.push(cand(q.id, mapped, role, RULE_DIRECT, field === "main" ? "rule_explicit_ref" : "rule_explicit_aux"));
        continue;
      }
      if (!ctx.importedChapters.has(legacy.chapter)) {
        refs.push({ field, ref, outcome: "other_chapter", nodeId: null, text: null });
        continue;
      }
      const topic = ctx.clusterTopic.get(legacy.cluster.replace(/-/g, "_"));
      if (topic) {
        refs.push({ field, ref, outcome: "topic_fallback", nodeId: topic, text: null });
        if (inScope) candidates.push(cand(q.id, topic, role, RULE_INFERRED, "rule_chapter_topic"));
      } else {
        refs.push({ field, ref, outcome: "not_in_map", nodeId: null, text: null });
      }
    }
  }
  // 退到主题的候选，如果同一题已有这个主题下的节点被直接或按名称命中，就是多余的（父子节点同时进节点清单没有意义）：丢掉候选，引用记录保留
  const covered = new Set(candidates.filter((c) => c.reasonCode !== "rule_chapter_topic").map((c) => ctx.parentOf.get(c.nodeId)));
  return { candidates: candidates.filter((c) => c.reasonCode !== "rule_chapter_topic" || !covered.has(c.nodeId)), refs };
}
