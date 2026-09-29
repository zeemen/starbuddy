// 三层候选的合并与分级（spec 设计要点 2；待人确认 #6 的默认口径）：
//   - 去重：同一（节点, 角色, 层）只留置信度最高的一条；同一层里一个节点既是主考点又是次要节点时，只留主考点；
//   - 主考点：有“主考点”候选的节点里取置信度最高者，平手按候选出现顺序（规则层按 legacy 的引用顺序在前）、再按节点 ID；
//   - 节点清单：主考点 + 规则层、模型层给出的其他节点（向量层只作召回，不直接进节点清单），按置信度排序，小题上限 3 个；
//   - A 级：主考点上有规则层“主考点”候选、置信度 ≥ 0.85，且另一层（向量或模型）也把它作主考点；节点数没有超上限。
//     只跑规则层时一律 B 级；--rules-only-a（默认关）放宽为“只跑规则层时，规则直接命中也算 A”，供教研试用；
//   - 其余为 B 级，进低置信候选队列；没有任何候选的是“无处可标”，进工作单。
// A 级也只写成待审草稿（tag_review_status = pending），要经审核才能发布（KGE V1.1 :343）。
import { A_MIN_CONFIDENCE, cmp, LAYER_ORDER, MAX_NODES, sortCandidates, type Candidate, type Grade, type Layer } from "./types.ts";

export interface MergeOptions {
  /** 本次运行开启的层（规则层总是开启） */
  layers: ReadonlySet<Layer>;
  /** 只跑规则层时，规则直接命中也算 A 级（默认 false，严格按 spec 待人确认 #6） */
  rulesOnlyA: boolean;
}

/** B 级的原因码（写进报告，供教研筛选） */
export type BReason = "no_rule_main" | "rule_below_threshold" | "no_layer_agreement" | "over_cap" | "no_main_candidate";

export interface TagDecision {
  itemId: string;
  status: "tagged" | "untaggable";
  grade: Grade | null;
  mainNodeId: string | null;
  /** 含主考点，至多 3 个 */
  nodeIds: string[];
  confidence: number | null;
  /** 最终结果的来源层：A 级为 rule；B 级为主考点上置信度最高的层 */
  source: Layer | null;
  /** 合并前的不同节点数超过上限 3（截断后的清单在 nodeIds） */
  overCap: boolean;
  bReasons: BReason[];
  /** 去重后的全部候选（已排序） */
  candidates: Candidate[];
}

export function dedupe(cs: Candidate[]): Candidate[] {
  const best = new Map<string, Candidate>();
  for (const c of cs) {
    const k = `${c.nodeId}\u0000${c.role}\u0000${c.layer}`;
    const prev = best.get(k);
    if (!prev || c.confidence > prev.confidence || (c.confidence === prev.confidence && cmp(c.reasonCode, prev.reasonCode) < 0)) best.set(k, c);
  }
  const mainKeys = new Set([...best.values()].filter((c) => c.role === "main").map((c) => `${c.nodeId}\u0000${c.layer}`));
  return sortCandidates([...best.values()].filter((c) => c.role === "main" || !mainKeys.has(`${c.nodeId}\u0000${c.layer}`)));
}

export function mergeAndGrade(itemId: string, input: Candidate[], opts: MergeOptions): TagDecision {
  for (const c of input) if (c.itemId !== itemId) throw new Error(`候选 ${c.nodeId} 属于另一道题 ${c.itemId}`);
  const candidates = dedupe(input);
  if (candidates.length === 0) {
    return { itemId, status: "untaggable", grade: null, mainNodeId: null, nodeIds: [], confidence: null, source: null, overCap: false,
             bReasons: [], candidates };
  }
  const firstIndex = new Map<string, number>();
  input.forEach((c, i) => { if (!firstIndex.has(c.nodeId)) firstIndex.set(c.nodeId, i); });
  const maxConf = (node: string, filter: (c: Candidate) => boolean = () => true) =>
    Math.max(-1, ...candidates.filter((c) => c.nodeId === node && filter(c)).map((c) => c.confidence));
  const rank = (a: string, b: string, filter?: (c: Candidate) => boolean) =>
    maxConf(b, filter) - maxConf(a, filter) || firstIndex.get(a)! - firstIndex.get(b)! || cmp(a, b);

  const mainPool = [...new Set(candidates.filter((c) => c.role === "main").map((c) => c.nodeId))];
  const bReasons: BReason[] = [];
  let main: string;
  if (mainPool.length > 0) {
    main = mainPool.sort((a, b) => rank(a, b, (c) => c.role === "main"))[0]!;
  } else {
    bReasons.push("no_main_candidate");
    main = [...new Set(candidates.map((c) => c.nodeId))].sort((a, b) => rank(a, b))[0]!;
  }
  const others = [...new Set(candidates.filter((c) => c.nodeId !== main && c.layer !== "vector").map((c) => c.nodeId))]
    .sort((a, b) => rank(a, b, (c) => c.layer !== "vector"));
  const overCap = 1 + others.length > MAX_NODES;
  const nodeIds = [main, ...others].slice(0, MAX_NODES);

  const ruleMain = candidates.find((c) => c.nodeId === main && c.role === "main" && c.layer === "rule");
  const agreeing = candidates.filter((c) => c.nodeId === main && c.role === "main" && c.layer !== "rule");
  const onlyRules = !opts.layers.has("vector") && !opts.layers.has("model");
  if (!ruleMain) bReasons.push("no_rule_main");
  else if (ruleMain.confidence < A_MIN_CONFIDENCE) bReasons.push("rule_below_threshold");
  if (agreeing.length === 0 && !(opts.rulesOnlyA && onlyRules)) bReasons.push("no_layer_agreement");
  if (overCap) bReasons.push("over_cap");
  const grade: Grade = bReasons.length === 0 ? "A" : "B";

  const onMain = candidates.filter((c) => c.nodeId === main)
    .sort((a, b) => b.confidence - a.confidence || LAYER_ORDER[a.layer] - LAYER_ORDER[b.layer]);
  const source: Layer = grade === "A" ? "rule" : onMain[0]!.layer;
  const confidence = grade === "A" ? ruleMain!.confidence : onMain[0]!.confidence;
  return { itemId, status: "tagged", grade, mainNodeId: main, nodeIds, confidence, source, overCap, bReasons: [...new Set(bReasons)].sort(), candidates };
}
