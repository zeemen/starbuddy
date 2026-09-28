// KGE-01 骨架导入：把阿鸦一个专题的节点转成草稿地图版本的 SQL、教研工作单与核对报告（specs/kge-01-skeleton-import/spec.md）。
// 纯函数、确定性：同样的输入逐字节同样的输出。不连库；SQL 由人在演练库执行。
import { createHash } from "node:crypto";
import { findCycles, findTransitiveEdges, type Edge } from "../graph.ts";

export interface LegacyNode {
  id: string;
  name: string;
  kind: string; // 阿鸦 K / S / P / M / C
  cluster: string; // 如 FUN-CON
  clusterName: string;
  core: string;
  prerequisites: string[];
}

export type KgeKind = "kp" | "ability_kp" | "viewpoint" | "material" | "method" | "pitfall" | "exam_point";
export const ERROR_TYPES = [
  "reading_the_question", "locating_information", "thinking_method", "computation_or_grammar",
  "expression_convention", "strategy_and_time", "knowledge",
] as const;
const COGNITIVE = ["know", "understand", "apply", "synthesize"] as const;
const KINDS: readonly KgeKind[] = ["kp", "ability_kp", "viewpoint", "material", "method", "pitfall", "exam_point"];

/** 默认类型映射（spec 待人确认 #2，按默认）；C（能力子节点）不作知识节点 */
export const DEFAULT_KIND_MAP: Record<string, KgeKind | null> = { K: "kp", S: "ability_kp", M: "method", P: "pitfall", C: null };

export interface NodeOverride {
  id_segment?: string; // 语义化命名，如 VERTEX_FORM
  kind?: KgeKind;
  includes?: string[];
  excludes?: string[];
  examples?: string[];
  cognitive_levels?: string[];
  error_type?: string;
  /** 教研补的前置（本专题内节点的旧编码），用于把自然语言前置映射到节点 */
  extra_prerequisites?: string[];
}
export interface Overrides {
  domain?: { segment: string; name: string };
  kind_map?: Record<string, KgeKind | null>;
  nodes?: Record<string, NodeOverride>;
}
export interface BuildOptions {
  topic: string;
  version: string; // 地图版本号 x.y.z
  standardYear?: number;
}

export interface BuiltNode {
  id: string;
  level: "domain" | "topic" | "node";
  parentId: string | null;
  kind: KgeKind | null;
  name: string;
  includes: string[];
  excludes: string[];
  examples: string[];
  cognitiveLevels: string[];
  errorType: string | null;
  oldId: string | null;
}
export interface WorkItem { oldId: string; newId: string | null; name: string; core: string; issue: string }
export interface BuildResult {
  mapVersionId: string;
  nodes: BuiltNode[];
  edges: Edge[]; // 新 ID，前置 → 后续
  droppedTransitive: Edge[];
  worklist: WorkItem[];
  report: Record<string, unknown>;
  sql: string;
  worklistMd: string;
}

const SEGMENT = /^[A-Z0-9][A-Z0-9_]{0,31}$/;
const LEGACY_ID = /^[A-Z]{1,2}-[A-Z]{2,4}-[0-9A-Z]{2,6}$/;
const DEFAULT_DOMAIN: Record<string, { segment: string; name: string }> = { fun: { segment: "FUNC", name: "函数" } };

/** 确定性的 UUIDv4 形状 ID（同一学科、同一版本号得到同一地图版本 ID，便于评审看差异） */
export function stableUuid(text: string): string {
  const h = createHash("sha256").update(text).digest("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-4${h.slice(13, 16)}-8${h.slice(17, 20)}-${h.slice(20, 32)}`;
}

export class ImportError extends Error {}

function checkOverrides(o: Overrides): void {
  if (o.domain && !SEGMENT.test(o.domain.segment)) throw new ImportError(`覆盖文件：领域段 ${o.domain.segment} 不合法`);
  for (const [k, v] of Object.entries(o.kind_map ?? {})) {
    if (v !== null && !KINDS.includes(v)) throw new ImportError(`覆盖文件：类型映射 ${k} → ${v} 不在 KGE 类型里`);
  }
  for (const [id, n] of Object.entries(o.nodes ?? {})) {
    if (n.id_segment !== undefined && !SEGMENT.test(n.id_segment)) throw new ImportError(`覆盖文件：${id} 的命名段 ${n.id_segment} 不合法`);
    if (n.kind !== undefined && !KINDS.includes(n.kind)) throw new ImportError(`覆盖文件：${id} 的类型 ${n.kind} 不合法`);
    if (n.cognitive_levels?.some((c) => !(COGNITIVE as readonly string[]).includes(c))) throw new ImportError(`覆盖文件：${id} 的认知层次不合法`);
    if (n.extra_prerequisites?.some((p) => !LEGACY_ID.test(p))) throw new ImportError(`覆盖文件：${id} 的补充前置必须是旧编码`);
    if (n.error_type !== undefined && !(ERROR_TYPES as readonly string[]).includes(n.error_type)) {
      throw new ImportError(`覆盖文件：${id} 的错因大类 ${n.error_type} 不合法`);
    }
  }
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

export function build(legacy: LegacyNode[], opts: BuildOptions, overrides: Overrides = {}): BuildResult {
  checkOverrides(overrides);
  if (!/^\d+\.\d+\.\d+$/.test(opts.version)) throw new ImportError(`地图版本号 ${opts.version} 必须是 主.次.修订`);
  const domain = overrides.domain ?? DEFAULT_DOMAIN[opts.topic];
  if (!domain) throw new ImportError(`专题 ${opts.topic} 没有默认领域，请在覆盖文件里给 domain`);
  const kindMap = { ...DEFAULT_KIND_MAP, ...(overrides.kind_map ?? {}) };
  const nodeOv = overrides.nodes ?? {};
  const mapVersionId = stableUuid(`kge:aya:${opts.version}`);
  const domainId = `MATH.${domain.segment}`;
  const worklist: WorkItem[] = [];
  const sorted = [...legacy].sort((a, b) => cmp(a.id, b.id));
  const byOld = new Map(sorted.map((n) => [n.id, n]));
  if (byOld.size !== legacy.length) throw new ImportError("专题里有重复的节点编码");

  // 1. 节点：主题按知识簇；知识节点按映射后的类型，能力子节点与缺错因的易错点不导入
  const topics = new Map<string, string>(); // 新主题 ID → 名称
  const newIdOf = new Map<string, string>();
  const nodes: BuiltNode[] = [];
  const usedIds = new Set<string>();
  for (const n of sorted) {
    if (!LEGACY_ID.test(n.id)) throw new ImportError(`旧编码 ${n.id} 形状不认识`);
    const ov = nodeOv[n.id] ?? {};
    const kind = ov.kind ?? kindMap[n.id.split("-")[0]!.slice(0, 1)] ?? null;
    if (kind === null) {
      worklist.push({ oldId: n.id, newId: null, name: n.name, core: n.core, issue: "能力子节点：不作知识节点，作为能力视图候选（ADR 11）" });
      continue;
    }
    if (kind === "pitfall" && !ov.error_type) {
      worklist.push({ oldId: n.id, newId: null, name: n.name, core: n.core, issue: "易错点缺错因大类：未导入，请在覆盖文件里给 error_type（或改类型）" });
      continue;
    }
    const clusterSeg = n.cluster.split("-").slice(1).join("_");
    if (!SEGMENT.test(clusterSeg)) throw new ImportError(`知识簇 ${n.cluster} 无法转成主题段`);
    const topicId = `${domainId}.${clusterSeg}`;
    topics.set(topicId, n.clusterName || clusterSeg);
    const id = `${topicId}.${ov.id_segment ?? n.id.replace(/-/g, "_")}`;
    if (usedIds.has(id)) throw new ImportError(`新 ID ${id} 重复`);
    usedIds.add(id);
    newIdOf.set(n.id, id);
    nodes.push({
      id, level: "node", parentId: topicId, kind, name: n.name,
      includes: ov.includes ?? [], excludes: ov.excludes ?? [], examples: ov.examples ?? [],
      cognitiveLevels: kind === "kp" || kind === "ability_kp" || kind === "viewpoint" ? (ov.cognitive_levels ?? []) : [],
      errorType: kind === "pitfall" ? (ov.error_type ?? null) : null, oldId: n.id,
    });
  }
  const structural: BuiltNode[] = [
    { id: domainId, level: "domain", parentId: null, kind: null, name: domain.name, includes: [], excludes: [], examples: [],
      cognitiveLevels: [], errorType: null, oldId: null },
    ...[...topics].sort((a, b) => cmp(a[0], b[0])).map(([id, name]): BuiltNode => ({
      id, level: "topic", parentId: domainId, kind: null, name, includes: [], excludes: [], examples: [], cognitiveLevels: [],
      errorType: null, oldId: null })),
  ];

  // 2. 前置边：只连本专题内已导入的节点；其余进工作单（不自动猜映射）
  const rawEdges: Edge[] = [];
  for (const n of sorted) {
    const to = newIdOf.get(n.id);
    for (const p of nodeOv[n.id]?.extra_prerequisites ?? []) {
      const from = newIdOf.get(p);
      if (!from) throw new ImportError(`覆盖文件：${n.id} 的补充前置 ${p} 不是本专题已导入的节点`);
      if (to) rawEdges.push([from, to]);
    }
    for (const p of n.prerequisites) {
      const from = newIdOf.get(p);
      if (from && to) {
        rawEdges.push([from, to]);
        continue;
      }
      if (!to) continue; // 本节点没导入：它的前置随它一起在工作单里
      const issue = byOld.has(p) ? `前置 ${p} 未导入（见该节点的工作单行），这条边没连`
        : LEGACY_ID.test(p) ? `前置 ${p} 在本专题之外，这条边没连（跨专题边待教研确认）`
        : `自然语言前置“${p}”，没连边（待教研映射到节点）`;
      worklist.push({ oldId: n.id, newId: to, name: n.name, core: n.core, issue });
    }
  }
  const edgeKey = (e: Edge) => `${e[0]}\u0000${e[1]}`;
  const uniq = [...new Map(rawEdges.map((e) => [edgeKey(e), e])).values()].sort((x, y) => cmp(x[0], y[0]) || cmp(x[1], y[1]));
  const ids = nodes.map((n) => n.id);
  const cycles = findCycles(ids, uniq);
  if (cycles.length > 0) {
    throw new ImportError(`前置边成环，需教研决定断哪条边：${cycles.map((c) => c.join(" ↔ ")).join("；")}`);
  }
  const transitive = findTransitiveEdges(ids, uniq);
  const drop = new Set(transitive.map(edgeKey));
  const edges = uniq.filter((e) => !drop.has(edgeKey(e)));

  // 3. 孤点与缺边界：与库里 validate_map_version 的口径一致
  const touched = new Set(edges.flat());
  for (const n of nodes) {
    if (!touched.has(n.id)) worklist.push({ oldId: n.oldId!, newId: n.id, name: n.name, core: "", issue: "孤点：没有任何前置或后续边（发布前校验会报 orphan）" });
    const missing = n.kind === "pitfall"
      ? (n.includes.length === 0 ? ["识别特征（includes）"] : [])
      : ["kp", "ability_kp", "viewpoint"].includes(n.kind!)
        ? [n.includes.length === 0 && "包含", n.excludes.length === 0 && "不包含", n.examples.length === 0 && "典型题例",
           n.cognitiveLevels.length === 0 && "认知层次"].filter((x): x is string => Boolean(x))
        : [];
    if (missing.length > 0) worklist.push({ oldId: n.oldId!, newId: n.id, name: n.name, core: "", issue: `缺${missing.join("、")}` });
  }
  worklist.sort((a, b) => cmp(a.oldId, b.oldId) || cmp(a.issue, b.issue));

  const all = [...structural, ...nodes];
  const report = {
    topic: opts.topic, version: opts.version, map_version_id: mapVersionId,
    legacy_nodes: legacy.length, imported_nodes: nodes.length, topics: topics.size,
    by_kind: Object.fromEntries([...new Set(nodes.map((n) => n.kind!))].sort().map((k) => [k, nodes.filter((n) => n.kind === k).length])),
    not_imported: sorted.filter((n) => !newIdOf.has(n.id)).map((n) => n.id),
    edges: edges.length, dropped_transitive: transitive.map(([a, b]) => `${a} -> ${b}`),
    worklist_items: worklist.length,
  };
  return {
    mapVersionId, nodes: all, edges, droppedTransitive: transitive, worklist, report,
    sql: renderSql(mapVersionId, opts, all, edges), worklistMd: renderWorklist(opts, report, worklist),
  };
}

const q = (s: string) => `'${s.replace(/'/g, "''")}'`;
const arr = (xs: string[]) => (xs.length === 0 ? "'{}'::text[]" : `ARRAY[${xs.map(q).join(", ")}]::text[]`);

function renderSql(mv: string, opts: BuildOptions, nodes: BuiltNode[], edges: Edge[]): string {
  const lines = [
    `-- KGE-01 骨架导入（生成文件，勿手改）：阿鸦专题 ${opts.topic} → 地图版本 aya ${opts.version}（草稿）`,
    "-- 由 tools/kge-inventory/src/import 生成；在演练库以单事务执行（psql -1 -v ON_ERROR_STOP=1）。不含任何学生数据。",
    "BEGIN;",
    "SET LOCAL ROLE starbuddy_kge;  -- 以知识服务角色写入：只用它有的权限，不依赖超级用户",
    `INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (${q(mv)}, 'aya', ${opts.standardYear ?? 2022}, ${q(opts.version)});`,
    "INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, cognitive_levels, error_type) VALUES",
    nodes.map((n) => `  (${q(mv)}, ${q(n.id)}, ${q(n.level)}, ${n.parentId ? q(n.parentId) : "NULL"}, ${n.kind ? q(n.kind) : "NULL"}, ${q(n.name)}, `
      + `${arr(n.includes)}, ${arr(n.excludes)}, ${arr(n.examples)}, ${arr(n.cognitiveLevels)}, `
      + `${n.errorType ? `${q(n.errorType)}::platform.cross_subject_error` : "NULL"})`).join(",\n") + ";",
  ];
  if (edges.length > 0) {
    lines.push("INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength, evidence_ref) VALUES",
      edges.map(([a, b]) => `  (${q(mv)}, ${q(a)}, ${q(mv)}, ${q(b)}, 'prerequisite', 'strong', 'legacy:aya prerequisites')`).join(",\n") + ";");
  }
  const leaves = nodes.filter((n) => n.oldId);
  lines.push("INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule) VALUES",
    leaves.map((n) => `  (${q(mv)}, 'aya', ${q(n.oldId!)}, ${q(n.id)}, 'rename')`).join(",\n") + ";",
    "INSERT INTO kge.skill_link (map_version_id, node_id, subject, local_id, weight) VALUES",
    leaves.map((n) => `  (${q(mv)}, ${q(n.id)}, 'aya', ${q(n.oldId!)}, 1)`).join(",\n") + ";",
    "COMMIT;", "");
  return lines.join("\n");
}

function renderWorklist(opts: BuildOptions, report: Record<string, unknown>, items: WorkItem[]): string {
  const cell = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
  return [
    `# KGE-01 教研工作单：阿鸦 ${opts.topic} → 地图版本 ${opts.version}`,
    "",
    "生成文件，勿手改。补齐后把内容写进覆盖文件（`--overrides`）重新生成；`core` 列是阿鸦原文，只供参考，不是边界说明。",
    "",
    `导入 ${String(report.imported_nodes)} / ${String(report.legacy_nodes)} 个节点，前置边 ${String(report.edges)} 条，丢弃传递边 ${(report.dropped_transitive as string[]).length} 条，工作项 ${items.length} 条。`,
    "",
    "| 旧编码 | 新 ID | 名称 | 阿鸦 core | 待办 |",
    "|---|---|---|---|---|",
    ...items.map((w) => `| ${w.oldId} | ${w.newId ?? "（未导入）"} | ${cell(w.name)} | ${cell(w.core)} | ${cell(w.issue)} |`),
    "",
  ].join("\n");
}
