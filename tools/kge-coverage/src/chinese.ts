// 语文覆盖报表（KGE-07 第一步，specs/kge07-chinese-slice/spec.md 范围 2）。纯函数、确定性：排序一律按码点。
// 输入：语文快照（format = kge-chinese-snapshot/1），由导入器离线生成（tools/kge-import/src/chinese/seed.ts）或
//       在演练库上用 sql/chinese-snapshot.sql 只读导出，两边格式与排序相同（tools/kge-import/tests/chinese/chinese-db.sh 核对）。
// 口径：
//   - 矩阵行：有效的知识节点层节点（level = node，status = active；能力点、素材、方法），按 ID 码点排序；
//   - 认知层次：只对能力点打 1 / 0（素材与方法不计掌握度，不带层次）；
//   - 投影：已核对 / 未核对 / 有争议分别计数（素材的投影就是素材卡的“出处（册-课-页）”）；
//   - 边：层次递进 = prerequisite + weak；显式强前置 = prerequisite + strong；关联、易混不分方向，按端点计；uses_method 能力点计出、方法计入；
//   - 必修覆盖：只算 scope = required 的节点；考情拓展、拓展素材等单列，不计入（KGE 分册语文段“南京在地文化语料”）；
//   - 缺口清单（默认整张语文地图 CHN，可用 --focus-domain 收窄到一个领域）：主题下没有知识节点；能力点缺认知层次、不在任何层次递进上；
//     必修素材没有教材出处、出处都未核对；方法没有被任何能力点使用。
// 能力 × 文本类型 × 题型三列不在库里（迁移 0011 没有这几列），只在导入器的离线报告里；本报表按主题与节点出矩阵。
// 报表只含汇总与 ID，不含节点名称，不含任何学生数据。
import { cmp, pct } from "./coverage.ts";
import { COGNITIVE_LEVELS } from "./map.ts";

export const CHINESE_SNAPSHOT_FORMAT = "kge-chinese-snapshot/1";
export const DEFAULT_CHINESE_FOCUS = "CHN";
export const CHINESE_FOCUS = /^CHN(\.[A-Z0-9][A-Z0-9_]{0,31})?$/;

export interface ChineseNode {
  id: string;
  level: string;
  parent_id: string | null;
  kind: string | null;
  name: string;
  scope: string;
  cognitive_levels: string[];
  status: string;
}
export interface ChineseSnapshot {
  format: string;
  subject: string;
  map_version_id: string;
  version: string;
  nodes: ChineseNode[];
  edges: { from_id: string; to_id: string; type: string; strength: string }[];
  projections: { id: string; node_id: string; edition: string; mapping_state: string }[];
}

export class ChineseCoverageError extends Error {}

const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const MAPPING_STATES = ["unverified", "verified", "disputed"];
const EDGE_TYPES = ["prerequisite", "part_of", "related", "confusable", "uses_method", "has_pitfall", "aligned_to"];
const SCOPES = ["required", "elective", "edition_ext", "exam_ext", "enrichment"];
const KINDS = ["kp", "ability_kp", "viewpoint", "material", "method", "pitfall", "exam_point"];

/** 快照按不可信输入核对（字段类型、ID 格式与前缀、取值范围、引用的节点存在） */
export function checkChineseSnapshot(raw: unknown): ChineseSnapshot {
  const s = raw as ChineseSnapshot;
  if (s === null || typeof s !== "object" || s.format !== CHINESE_SNAPSHOT_FORMAT) throw new ChineseCoverageError(`快照的 format 应为 ${CHINESE_SNAPSHOT_FORMAT}`);
  if (s.subject !== "wenli") throw new ChineseCoverageError("快照的学科应为 wenli");
  if (typeof s.map_version_id !== "string" || typeof s.version !== "string") throw new ChineseCoverageError("快照缺地图版本");
  for (const k of ["nodes", "edges", "projections"] as const) if (!Array.isArray(s[k])) throw new ChineseCoverageError(`快照缺 ${k}`);
  const ids = new Set<string>();
  for (const n of s.nodes) {
    if (typeof n?.id !== "string" || !NODE_ID.test(n.id) || !n.id.startsWith("CHN.")) throw new ChineseCoverageError(`节点 ID 不合法：${String(n?.id)}`);
    if (ids.has(n.id)) throw new ChineseCoverageError(`节点 ${n.id} 重复`);
    ids.add(n.id);
    if (!Array.isArray(n.cognitive_levels) || n.cognitive_levels.some((c) => !(COGNITIVE_LEVELS as readonly string[]).includes(c))) {
      throw new ChineseCoverageError(`节点 ${n.id} 的认知层次不合法`);
    }
    if (typeof n.name !== "string" || typeof n.level !== "string") throw new ChineseCoverageError(`节点 ${n.id} 缺名称或层级`);
    if (!SCOPES.includes(n.scope)) throw new ChineseCoverageError(`节点 ${n.id} 的范围属性不合法`);
    if (n.kind !== null && !KINDS.includes(n.kind)) throw new ChineseCoverageError(`节点 ${n.id} 的类型不合法`);
  }
  for (const e of s.edges) {
    if (!ids.has(e.from_id) || !ids.has(e.to_id)) throw new ChineseCoverageError(`边 ${String(e.from_id)} → ${String(e.to_id)} 指向不存在的节点`);
    if (!EDGE_TYPES.includes(e.type) || !["strong", "weak"].includes(e.strength)) throw new ChineseCoverageError(`边 ${e.from_id} → ${e.to_id} 的类型或强弱不合法`);
    // 弱边只用于层次递进（prerequisite + weak）；其他类型的弱边在本报表里没有口径
    if (e.strength === "weak" && e.type !== "prerequisite") throw new ChineseCoverageError(`边 ${e.from_id} → ${e.to_id}：弱边只能是 prerequisite（层次递进）`);
  }
  for (const p of s.projections) {
    if (!ids.has(p.node_id)) throw new ChineseCoverageError(`投影 ${p.id} 指向不存在的节点`);
    if (!MAPPING_STATES.includes(p.mapping_state)) throw new ChineseCoverageError(`投影 ${p.id} 的核对状态不合法`);
  }
  return s;
}

type Level = (typeof COGNITIVE_LEVELS)[number];
export interface NodeRow {
  id: string;
  topic: string;
  kind: string;
  scope: string;
  levels: Record<Level, 0 | 1>;
  verified: number;
  unverified: number;
  disputed: number;
  progressionIn: number;
  progressionOut: number;
  prerequisiteIn: number;
  prerequisiteOut: number;
  related: number;
  confusable: number;
  usesMethod: number;
}
export interface TopicRow { id: string; abilityKps: number; materials: number; methods: number; levels: Record<Level, number> }
export interface Gap { id: string; issue: string }
export interface ChineseCoverage {
  mapVersionId: string;
  version: string;
  rows: NodeRow[];
  topics: TopicRow[];
  totals: {
    abilityKps: number; materials: number; methods: number; topics: number; projections: number; verified: number; unverified: number; disputed: number;
    progression: number; strongPrerequisite: number; related: number; confusable: number; usesMethod: number;
    required: number; requiredVerified: number;
  };
  nonRequired: { id: string; scope: string }[];
  focusDomain: string;
  gaps: Gap[];
}

export function computeChineseCoverage(s: ChineseSnapshot, focusDomain = DEFAULT_CHINESE_FOCUS): ChineseCoverage {
  const active = s.nodes.filter((n) => n.status === "active");
  const activeIds = new Set(active.map((n) => n.id));
  const nodes = active.filter((n) => n.level === "node").sort((a, b) => cmp(a.id, b.id));
  // 只看两端都有效的边（与库里 validate_map_version 的口径一致：停用节点上的边不算）
  const edges = s.edges.filter((e) => activeIds.has(e.from_id) && activeIds.has(e.to_id));
  const progression = edges.filter((e) => e.type === "prerequisite" && e.strength === "weak");
  const strong = edges.filter((e) => e.type === "prerequisite" && e.strength === "strong");
  const touch = (type: string, id: string) => edges.filter((e) => e.type === type && (e.from_id === id || e.to_id === id)).length;
  const rows = nodes.map((n): NodeRow => {
    const pr = s.projections.filter((p) => p.node_id === n.id);
    const isAbility = n.kind === "ability_kp";
    return {
      id: n.id, topic: n.parent_id ?? "", kind: n.kind ?? "", scope: n.scope,
      levels: Object.fromEntries(COGNITIVE_LEVELS.map((c) => [c, isAbility && n.cognitive_levels.includes(c) ? 1 : 0])) as NodeRow["levels"],
      verified: pr.filter((p) => p.mapping_state === "verified").length,
      unverified: pr.filter((p) => p.mapping_state === "unverified").length,
      disputed: pr.filter((p) => p.mapping_state === "disputed").length,
      progressionIn: progression.filter((e) => e.to_id === n.id).length, progressionOut: progression.filter((e) => e.from_id === n.id).length,
      prerequisiteIn: strong.filter((e) => e.to_id === n.id).length, prerequisiteOut: strong.filter((e) => e.from_id === n.id).length,
      related: touch("related", n.id), confusable: touch("confusable", n.id), usesMethod: touch("uses_method", n.id),
    };
  });
  const topicNodes = active.filter((n) => n.level === "topic").sort((a, b) => cmp(a.id, b.id));
  const topics = topicNodes.map((t): TopicRow => {
    const under = rows.filter((r) => r.topic === t.id);
    const ab = under.filter((r) => r.kind === "ability_kp");
    return { id: t.id, abilityKps: ab.length, materials: under.filter((r) => r.kind === "material").length,
      methods: under.filter((r) => r.kind === "method").length,
      levels: Object.fromEntries(COGNITIVE_LEVELS.map((c) => [c, ab.filter((r) => r.levels[c] === 1).length])) as TopicRow["levels"] };
  });
  const inFocus = (id: string) => id === focusDomain || id.startsWith(`${focusDomain}.`);
  const gaps: Gap[] = [];
  for (const t of topics.filter((x) => inFocus(x.id))) {
    if (t.abilityKps + t.materials + t.methods === 0) gaps.push({ id: t.id, issue: "主题下没有知识节点" });
  }
  for (const r of rows.filter((x) => inFocus(x.id) && x.scope === "required")) {
    if (r.kind === "ability_kp") {
      if (COGNITIVE_LEVELS.every((c) => r.levels[c] === 0)) gaps.push({ id: r.id, issue: "能力点缺认知层次" });
      if (r.progressionIn + r.progressionOut === 0) gaps.push({ id: r.id, issue: "能力点不在任何层次递进上" });
    } else if (r.kind === "material") {
      if (r.verified + r.unverified + r.disputed === 0) gaps.push({ id: r.id, issue: "素材没有教材出处（册-课-页）" });
      else if (r.verified === 0) gaps.push({ id: r.id, issue: "素材的教材出处都未核对" });
    } else if (r.kind === "method" && edges.every((e) => !(e.type === "uses_method" && e.to_id === r.id))) {
      gaps.push({ id: r.id, issue: "方法没有被任何能力点使用" });
    }
  }
  if (!active.some((n) => inFocus(n.id))) gaps.push({ id: focusDomain, issue: "地图里没有这个领域" });
  const required = rows.filter((r) => r.scope === "required");
  const nodeIds = new Set(rows.map((r) => r.id));
  return {
    mapVersionId: s.map_version_id, version: s.version, rows, topics,
    totals: {
      abilityKps: rows.filter((r) => r.kind === "ability_kp").length, materials: rows.filter((r) => r.kind === "material").length,
      methods: rows.filter((r) => r.kind === "method").length, topics: topics.length,
      projections: s.projections.filter((p) => nodeIds.has(p.node_id)).length,
      verified: rows.reduce((a, r) => a + r.verified, 0), unverified: rows.reduce((a, r) => a + r.unverified, 0),
      disputed: rows.reduce((a, r) => a + r.disputed, 0),
      progression: progression.length, strongPrerequisite: strong.length, related: edges.filter((e) => e.type === "related").length,
      confusable: edges.filter((e) => e.type === "confusable").length, usesMethod: edges.filter((e) => e.type === "uses_method").length,
      required: required.length, requiredVerified: required.filter((r) => r.verified > 0).length,
    },
    nonRequired: rows.filter((r) => r.scope !== "required").map((r) => ({ id: r.id, scope: r.scope })),
    focusDomain, gaps,
  };
}

const cell = (s: string) => s.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/[\r\n]/g, " ");
const csvField = (v: string | number) => {
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const KIND_ZH: Record<string, string> = { ability_kp: "能力点", material: "素材", method: "方法" };
const SCOPE_ZH: Record<string, string> = { required: "必修", elective: "选修", edition_ext: "版本拓展", exam_ext: "考情拓展", enrichment: "拓展" };

export const CHINESE_CSV_COLUMNS = ["node_id", "topic", "kind", "scope", ...COGNITIVE_LEVELS, "projections_verified", "projections_unverified",
  "projections_disputed", "progression_in", "progression_out", "prerequisite_in", "prerequisite_out", "related", "confusable", "uses_method"] as const;

export function toChineseCsv(r: ChineseCoverage): string {
  return [CHINESE_CSV_COLUMNS.join(","), ...r.rows.map((x) => [x.id, x.topic, x.kind, x.scope, ...COGNITIVE_LEVELS.map((c) => x.levels[c]),
    x.verified, x.unverified, x.disputed, x.progressionIn, x.progressionOut, x.prerequisiteIn, x.prerequisiteOut, x.related, x.confusable,
    x.usesMethod].map(csvField).join(","))].join("\n") + "\n";
}

export function toChineseMarkdown(r: ChineseCoverage): string {
  const t = r.totals;
  const lines = [
    `# 语文覆盖报表：地图版本 ${r.version}`,
    "",
    `地图版本 ID：${r.mapVersionId}。生成文件，同一输入逐字节相同；只含汇总与 ID。`,
    "",
    "## 1. 范围",
    "",
    `- 能力点 ${t.abilityKps} 个、素材 ${t.materials} 个、方法 ${t.methods} 个，主题 ${t.topics} 个`,
    `- 层次递进边（prerequisite + weak）${t.progression} 条，显式强前置 ${t.strongPrerequisite} 条；关联 ${t.related} 条，易混 ${t.confusable} 条，用方法 ${t.usesMethod} 条`,
    `- 教材投影 ${t.projections} 条：已核对 ${t.verified}（${pct(t.verified, t.projections)}），未核对 ${t.unverified}，有争议 ${t.disputed}`,
    `- 必修节点 ${t.required} 个，有已核对教材投影的 ${t.requiredVerified} 个（${pct(t.requiredVerified, t.required)}）`,
    `- 不计入必修覆盖：${r.nonRequired.length === 0 ? "无" : r.nonRequired.map((x) => `${x.id}（${SCOPE_ZH[x.scope] ?? x.scope}）`).join("、")}`,
    "- 能力 × 文本类型 × 题型的能力阶梯格见导入器的离线报告（import-report.json 的 ability_grid）；库里没有这三列",
    "",
    "## 2. 主题 × 节点类型 × 认知层次",
    "",
    "认知层次列只算能力点（素材与方法不计掌握度）。",
    "",
    `| 主题 | 能力点 | 素材 | 方法 | ${COGNITIVE_LEVELS.join(" | ")} |`,
    `|---|---:|---:|---:|${COGNITIVE_LEVELS.map(() => "---:").join("|")}|`,
    ...r.topics.map((x) => `| ${cell(x.id)} | ${x.abilityKps} | ${x.materials} | ${x.methods} | ${COGNITIVE_LEVELS.map((c) => String(x.levels[c])).join(" | ")} |`),
    `| 合计 | ${t.abilityKps} | ${t.materials} | ${t.methods} | ${COGNITIVE_LEVELS.map((c) => String(r.topics.reduce((a, x) => a + x.levels[c], 0))).join(" | ")} |`,
    "",
    "## 3. 节点矩阵",
    "",
    "投影列为 已核对 / 未核对 / 有争议；递进、强前置列为 入 / 出。",
    "",
    `| 节点 | 类型 | 范围 | ${COGNITIVE_LEVELS.join(" | ")} | 投影 | 递进 | 强前置 | 关联 | 易混 | 用方法 |`,
    `|---|---|---|${COGNITIVE_LEVELS.map(() => "---:").join("|")}|---|---|---|---:|---:|---:|`,
    ...r.rows.map((x) => `| ${cell(x.id)} | ${KIND_ZH[x.kind] ?? cell(x.kind)} | ${SCOPE_ZH[x.scope] ?? cell(x.scope)} | `
      + `${COGNITIVE_LEVELS.map((c) => String(x.levels[c])).join(" | ")} | ${x.verified} / ${x.unverified} / ${x.disputed} | `
      + `${x.progressionIn} / ${x.progressionOut} | ${x.prerequisiteIn} / ${x.prerequisiteOut} | ${x.related} | ${x.confusable} | ${x.usesMethod} |`),
    "",
    `## 4. ${r.focusDomain} 缺口清单（只看必修节点）`,
    "",
    ...(r.gaps.length === 0 ? ["无缺口。"] : ["| 节点 | 缺口 |", "|---|---|", ...r.gaps.map((g) => `| ${cell(g.id)} | ${g.issue} |`)]),
    "",
  ];
  return lines.join("\n");
}
