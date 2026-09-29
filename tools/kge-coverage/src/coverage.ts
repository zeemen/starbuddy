// 覆盖矩阵计算（specs/kge-02-atoms-tagging/spec.md 设计要点 4）。纯函数、确定性：排序一律按码点，不用 localeCompare。
// 口径（与 sql/coverage.sql 一致）：
//   - 矩阵的行：知识节点层与细目层（level = node / detail）的有效节点；领域、主题是结构层，不进矩阵；
//   - 格子：以该节点为主考点的题数，按题目认知层次分列，没标层次的记“未标”；
//   - 关联题数：节点出现在 node_ids 里、但不是主考点的题数；
//   - 题目节点数：{主考点} ∪ node_ids 去重后的个数；超过 3 个标出（KGE :746 小题 1–3 个节点）；
//   - 覆盖门槛（KGE :747 覆盖矩阵 100%）：有主考点题的节点 / 矩阵行数，低于 100% 标红，只标注，不作为通过条件；
//   - 范围（scope）：all = 已导入主题里的全部打标结果（A 级与 B 级候选的主考点都算）；item_tag = 只算写进 kge.item_tag 的题
//     （status = item_tag），与 sql/coverage.sql 在库里算的口径一致。未导入主题的题（out_of_scope）两种范围都不算。
import { COGNITIVE_LEVELS, type KgeMap, type MapNode } from "./map.ts";
import { LAYERS, type TaggingReport } from "./tagging.ts";

export const MAX_NODES_PER_ITEM = 3;
export const COVERAGE_THRESHOLD = 1; // 100%

export const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

export type LevelColumn = (typeof COGNITIVE_LEVELS)[number] | "unlabeled";
export const LEVEL_COLUMNS: readonly LevelColumn[] = [...COGNITIVE_LEVELS, "unlabeled"];

export interface MatrixRow {
  node: MapNode;
  byLevel: Record<LevelColumn, number>;
  mainTotal: number;
  relatedTotal: number;
}

export interface Share { key: string; count: number }

export type Scope = "all" | "item_tag";

export interface CoverageResult {
  scope: Scope;
  outOfScope: number; // 未导入主题、不打标的题
  excludedByScope: number; // scope = item_tag 时没写进 item_tag 的题
  mapVersionId: string;
  version: string;
  topic: string | null;
  itemCount: number;
  rows: MatrixRow[];
  uncovered: MapNode[]; // 没有主考点题的节点
  coveredCount: number;
  meetsThreshold: boolean;
  nodeCountDistribution: Share[]; // key = 节点数（字符串），按数值升序
  overLimitItems: { item_id: string; nodes: number; overCap: boolean }[];
  candidateShares: Share[]; // 各层候选条数
  candidateRowShares: Share[] | null; // 写进 kge.tag_candidate 的各层行数（报告里有才有）
  itemLayerShares: Share[]; // 题目最终标签的来源层
  gradeShares: Share[]; // A / B / 未分级
  unplaceable: string[]; // 无处可标（main_node_id 为空）
  mainNotInMap: { item_id: string; node_id: string }[];
  mainNotNodeLevel: { item_id: string; node_id: string }[]; // 主考点落在领域或主题层
  mainDeprecated: { item_id: string; node_id: string }[];
  relatedNotInMap: { item_id: string; node_id: string }[];
}

export class CoverageError extends Error {}

export function itemNodeSet(it: { main_node_id: string | null; node_ids: string[] }): string[] {
  const s = new Set(it.node_ids);
  if (it.main_node_id !== null) s.add(it.main_node_id);
  return [...s].sort(cmp);
}

function orderedShares(counts: Map<string, number>, order: readonly string[]): Share[] {
  const keys = [...counts.keys()].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    if (ia >= 0 || ib >= 0) return (ia < 0 ? order.length : ia) - (ib < 0 ? order.length : ib) || cmp(a, b);
    return cmp(a, b);
  });
  return keys.map((key) => ({ key, count: counts.get(key)! }));
}

const inc = (m: Map<string, number>, k: string, by = 1) => m.set(k, (m.get(k) ?? 0) + by);

export function computeCoverage(map: KgeMap, tagging: TaggingReport, scope: Scope = "all"): CoverageResult {
  if ((tagging.map_version !== map.version && tagging.map_version !== map.mapVersionId)
    || (tagging.map_version_id !== null && tagging.map_version_id !== map.mapVersionId)) {
    throw new CoverageError(`打标报告的地图版本（${tagging.map_version}${tagging.map_version_id ? ` / ${tagging.map_version_id}` : ""}）` +
      `与地图（${map.version} / ${map.mapVersionId}）不一致`);
  }
  const inScope = tagging.items.filter((it) => scope === "all" || it.status === null || it.status === "item_tag");
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  const matrixNodes = map.nodes.filter((n) => (n.level === "node" || n.level === "detail") && n.status === "active").sort((a, b) => cmp(a.id, b.id));
  const rows = new Map<string, MatrixRow>(matrixNodes.map((node) => [node.id, {
    node, byLevel: { know: 0, understand: 0, apply: 0, synthesize: 0, unlabeled: 0 }, mainTotal: 0, relatedTotal: 0,
  }]));

  const r: Omit<CoverageResult, "rows" | "uncovered" | "coveredCount" | "meetsThreshold" | "nodeCountDistribution" | "candidateShares"
    | "candidateRowShares" | "itemLayerShares" | "gradeShares"> = {
    scope, outOfScope: tagging.out_of_scope, excludedByScope: tagging.items.length - inScope.length,
    mapVersionId: map.mapVersionId, version: map.version, topic: map.topic, itemCount: inScope.length,
    overLimitItems: [], unplaceable: [], mainNotInMap: [], mainNotNodeLevel: [], mainDeprecated: [], relatedNotInMap: [],
  };
  const dist = new Map<number, number>();
  const layers = new Map<string, number>();
  const grades = new Map<string, number>();
  const items = [...inScope].sort((a, b) => cmp(a.item_id, b.item_id));
  for (const it of items) {
    inc(layers, it.layer ?? "none");
    inc(grades, it.grade ?? "none");
    const set = itemNodeSet(it);
    dist.set(set.length, (dist.get(set.length) ?? 0) + 1);
    if (set.length > MAX_NODES_PER_ITEM || it.over_cap) r.overLimitItems.push({ item_id: it.item_id, nodes: set.length, overCap: it.over_cap });
    const main = it.main_node_id;
    if (main === null) {
      r.unplaceable.push(it.item_id);
    } else {
      const n = byId.get(main);
      if (!n) r.mainNotInMap.push({ item_id: it.item_id, node_id: main });
      else if (n.level === "domain" || n.level === "topic") r.mainNotNodeLevel.push({ item_id: it.item_id, node_id: main });
      else if (n.status !== "active") r.mainDeprecated.push({ item_id: it.item_id, node_id: main });
      const row = rows.get(main);
      if (row) {
        // 报告没给认知层次时，与导入器写 kge.item_tag 的规则一致：主考点节点只声明一个层次就用它
        const declared = row.node.cognitiveLevels;
        const level = it.cognitive_level_given ? it.cognitive_level
          : declared.length === 1 && (COGNITIVE_LEVELS as readonly string[]).includes(declared[0]!) ? declared[0] as LevelColumn : null;
        row.byLevel[level ?? "unlabeled"]++;
        row.mainTotal++;
      }
    }
    for (const x of set) {
      if (x === main) continue;
      if (!byId.has(x)) r.relatedNotInMap.push({ item_id: it.item_id, node_id: x });
      const row = rows.get(x);
      if (row) row.relatedTotal++;
    }
  }
  const rowList = [...rows.values()];
  const uncovered = rowList.filter((x) => x.mainTotal === 0).map((x) => x.node);
  const coveredCount = rowList.length - uncovered.length;
  return {
    ...r,
    rows: rowList,
    uncovered,
    coveredCount,
    // 没有矩阵行时覆盖率无从谈起，按未达标处理
    meetsThreshold: rowList.length > 0 && coveredCount >= COVERAGE_THRESHOLD * rowList.length,
    nodeCountDistribution: [...dist.entries()].sort((a, b) => a[0] - b[0]).map(([k, count]) => ({ key: String(k), count })),
    candidateShares: orderedShares(new Map(Object.entries(tagging.candidates_by_layer)), LAYERS),
    candidateRowShares: tagging.candidate_rows_by_layer ? orderedShares(new Map(Object.entries(tagging.candidate_rows_by_layer)), LAYERS) : null,
    itemLayerShares: orderedShares(layers, [...LAYERS, "none"]),
    gradeShares: orderedShares(grades, ["A", "B", "none"]),
  };
}

// 百分比：整数运算后保留一位小数，结果与平台无关
export function pct(n: number, d: number): string {
  if (d === 0) return "—";
  return `${(Math.round((n * 1000) / d) / 10).toFixed(1)}%`;
}
