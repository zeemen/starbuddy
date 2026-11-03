// 道法覆盖报表（冲刺 4-K，specs/kge-daofa-slice-sprint4/spec.md 设计要点 5、6）。纯函数、确定性：排序一律按码点。
// 输入：道法快照（format = kge-daofa-snapshot/1），由导入器离线组装（tools/kge-import/src/daofa/snapshot.ts）或
//       在演练库上用 sql/daofa-snapshot.sql 只读导出，两边格式相同。
// 口径：
//   - 矩阵行：有效的观点节点（level = node，kind = viewpoint，status = active），按 ID 码点排序；
//   - 观点 × 认知层次：该观点声明的认知层次（课标四级）打 1，未声明打 0；列合计 = 声明该层次的观点数；
//   - 投影：已核对（verified）/ 未核对（unverified）/ 有争议（disputed，页码核验不通过）分别计数；
//   - 采分点数：node_ids 含该观点的采分点个数；题目数：这些采分点所属的不同题目数；
//   - 时政数：node_ids 含该观点、且未下架（review_status <> retired）的时政条数（不看有效期，到期提醒另见 affairs-expiring 报表）；
//   - 缺口清单（默认法律领域 MORAL.LAW）：没有观点的大概念；观点缺认知层次、没有已核对投影、没有采分点、没有题目；
//   - 《学生读本》：封测范围明确排除（spec 待人确认 #1），报表单列“读本内容：已排除”。
// 报表只含汇总与 ID（节点 ID、大概念名称与观点名称是公共层的自写概括），不含任何学生数据。
import { cmp, pct } from "./coverage.ts";
import { COGNITIVE_LEVELS } from "./map.ts";

export const DAOFA_SNAPSHOT_FORMAT = "kge-daofa-snapshot/1";
export const DEFAULT_FOCUS_DOMAIN = "MORAL.LAW";
export const STUDENT_READER_LINE = "读本内容：已排除";

export interface DaofaNode { id: string; level: string; parent_id: string | null; kind: string | null; name: string; cognitive_levels: string[]; status: string }
export interface DaofaSnapshot {
  format: string;
  subject: string;
  map_version_id: string;
  version: string;
  nodes: DaofaNode[];
  edges: { from_id: string; to_id: string; type: string }[];
  projections: { id: string; node_id: string; edition: string; mapping_state: string }[];
  scoring_points: { item_id: string; rubric_version: string; point_id: string; node_ids: string[] }[];
  current_affairs: { id: string; node_ids: string[]; review_status: string }[];
}

export class DaofaCoverageError extends Error {}

const NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
const MAPPING_STATES = ["unverified", "verified", "disputed"];

/** 快照按不可信输入核对（字段类型、ID 格式、引用的节点存在） */
export function checkSnapshot(raw: unknown): DaofaSnapshot {
  const s = raw as DaofaSnapshot;
  if (s === null || typeof s !== "object" || s.format !== DAOFA_SNAPSHOT_FORMAT) throw new DaofaCoverageError(`快照的 format 应为 ${DAOFA_SNAPSHOT_FORMAT}`);
  if (s.subject !== "zhixing") throw new DaofaCoverageError("快照的学科应为 zhixing");
  if (typeof s.map_version_id !== "string" || typeof s.version !== "string") throw new DaofaCoverageError("快照缺地图版本");
  for (const k of ["nodes", "edges", "projections", "scoring_points", "current_affairs"] as const) {
    if (!Array.isArray(s[k])) throw new DaofaCoverageError(`快照缺 ${k}`);
  }
  const ids = new Set<string>();
  for (const n of s.nodes) {
    if (typeof n?.id !== "string" || !NODE_ID.test(n.id) || !n.id.startsWith("MORAL.")) throw new DaofaCoverageError(`节点 ID 不合法：${String(n?.id)}`);
    if (ids.has(n.id)) throw new DaofaCoverageError(`节点 ${n.id} 重复`);
    ids.add(n.id);
    if (!Array.isArray(n.cognitive_levels) || n.cognitive_levels.some((c) => !(COGNITIVE_LEVELS as readonly string[]).includes(c))) {
      throw new DaofaCoverageError(`节点 ${n.id} 的认知层次不合法`);
    }
    if (typeof n.name !== "string" || typeof n.level !== "string") throw new DaofaCoverageError(`节点 ${n.id} 缺名称或层级`);
  }
  for (const p of s.projections) {
    if (!ids.has(p.node_id)) throw new DaofaCoverageError(`投影 ${p.id} 指向不存在的节点`);
    if (!MAPPING_STATES.includes(p.mapping_state)) throw new DaofaCoverageError(`投影 ${p.id} 的核对状态不合法`);
  }
  for (const x of [...s.scoring_points, ...s.current_affairs]) {
    if (!Array.isArray(x.node_ids) || x.node_ids.some((n) => typeof n !== "string")) throw new DaofaCoverageError("采分点或时政的 node_ids 不合法");
  }
  return s;
}

export interface ViewpointRow {
  id: string;
  name: string;
  bigConcept: string;
  levels: Record<(typeof COGNITIVE_LEVELS)[number], 0 | 1>;
  verified: number;
  unverified: number;
  disputed: number;
  scoringPoints: number;
  items: number;
  affairs: number;
}
export interface Gap { id: string; issue: string }
export interface DaofaCoverage {
  mapVersionId: string;
  version: string;
  rows: ViewpointRow[];
  levelTotals: Record<(typeof COGNITIVE_LEVELS)[number], number>;
  totals: { viewpoints: number; bigConcepts: number; projections: number; verified: number; unverified: number; disputed: number;
    scoringPoints: number; items: number; affairs: number; relatedEdges: number; prerequisiteEdges: number };
  focusDomain: string;
  gaps: Gap[];
  studentReader: typeof STUDENT_READER_LINE;
}

export function computeDaofaCoverage(s: DaofaSnapshot, focusDomain = DEFAULT_FOCUS_DOMAIN): DaofaCoverage {
  const active = s.nodes.filter((n) => n.status === "active");
  const vps = active.filter((n) => n.level === "node" && n.kind === "viewpoint").sort((a, b) => cmp(a.id, b.id));
  const liveAffairs = s.current_affairs.filter((c) => c.review_status !== "retired");
  const rows = vps.map((n): ViewpointRow => {
    const pr = s.projections.filter((p) => p.node_id === n.id);
    const sp = s.scoring_points.filter((p) => p.node_ids.includes(n.id));
    return {
      id: n.id, name: n.name, bigConcept: n.parent_id ?? "",
      levels: Object.fromEntries(COGNITIVE_LEVELS.map((c) => [c, n.cognitive_levels.includes(c) ? 1 : 0])) as ViewpointRow["levels"],
      verified: pr.filter((p) => p.mapping_state === "verified").length,
      unverified: pr.filter((p) => p.mapping_state === "unverified").length,
      disputed: pr.filter((p) => p.mapping_state === "disputed").length,
      scoringPoints: sp.length, items: new Set(sp.map((p) => p.item_id)).size,
      affairs: liveAffairs.filter((c) => c.node_ids.includes(n.id)).length,
    };
  });
  const levelTotals = Object.fromEntries(COGNITIVE_LEVELS.map((c) => [c, rows.filter((r) => r.levels[c] === 1).length])) as DaofaCoverage["levelTotals"];
  const vpIds = new Set(vps.map((n) => n.id));
  const onVp = <T extends { node_ids: string[] }>(xs: T[]) => xs.filter((x) => x.node_ids.some((id) => vpIds.has(id)));
  const inFocus = (id: string) => id === focusDomain || id.startsWith(`${focusDomain}.`);
  const gaps: Gap[] = [];
  for (const t of active.filter((n) => n.level === "topic" && inFocus(n.id)).sort((a, b) => cmp(a.id, b.id))) {
    if (!vps.some((v) => v.parent_id === t.id)) gaps.push({ id: t.id, issue: "大概念下没有观点（待观点下钻）" });
  }
  for (const r of rows.filter((x) => inFocus(x.id))) {
    if (COGNITIVE_LEVELS.every((c) => r.levels[c] === 0)) gaps.push({ id: r.id, issue: "缺认知层次" });
    if (r.verified === 0) gaps.push({ id: r.id, issue: r.unverified + r.disputed === 0 ? "没有教材投影" : "没有已核对的教材投影" });
    if (r.scoringPoints === 0) gaps.push({ id: r.id, issue: "没有采分点" });
    if (r.items === 0) gaps.push({ id: r.id, issue: "没有题目" });
  }
  if (!active.some((n) => inFocus(n.id))) gaps.push({ id: focusDomain, issue: "地图里没有这个领域" });
  const edgesOnMap = s.edges;
  return {
    mapVersionId: s.map_version_id, version: s.version, rows, levelTotals,
    totals: {
      viewpoints: rows.length, bigConcepts: active.filter((n) => n.level === "topic").length,
      projections: s.projections.filter((p) => vpIds.has(p.node_id)).length,
      verified: rows.reduce((a, r) => a + r.verified, 0), unverified: rows.reduce((a, r) => a + r.unverified, 0),
      disputed: rows.reduce((a, r) => a + r.disputed, 0),
      scoringPoints: onVp(s.scoring_points).length, items: new Set(onVp(s.scoring_points).map((p) => p.item_id)).size,
      affairs: onVp(liveAffairs).length,
      relatedEdges: edgesOnMap.filter((e) => e.type === "related").length,
      prerequisiteEdges: edgesOnMap.filter((e) => e.type === "prerequisite").length,
    },
    focusDomain, gaps, studentReader: STUDENT_READER_LINE,
  };
}

const cell = (s: string) => s.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/[\r\n]/g, " ");
const csvField = (v: string | number) => {
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const DAOFA_CSV_COLUMNS = ["node_id", "big_concept", ...COGNITIVE_LEVELS, "projections_verified", "projections_unverified",
  "projections_disputed", "scoring_points", "items", "current_affairs"] as const;

export function toDaofaCsv(r: DaofaCoverage): string {
  return [DAOFA_CSV_COLUMNS.join(","), ...r.rows.map((x) => [x.id, x.bigConcept, ...COGNITIVE_LEVELS.map((c) => x.levels[c]), x.verified, x.unverified,
    x.disputed, x.scoringPoints, x.items, x.affairs].map(csvField).join(","))].join("\n") + "\n";
}

export function toDaofaMarkdown(r: DaofaCoverage): string {
  const t = r.totals;
  const lines = [
    `# 道法覆盖报表：地图版本 ${r.version}`,
    "",
    `地图版本 ID：${r.mapVersionId}。生成文件，同一输入逐字节相同；只含汇总与 ID。`,
    "",
    "## 1. 范围",
    "",
    `- ${r.studentReader}（《学生读本》封测范围明确排除，spec 待人确认 #1；入口范围说明文案由人提供）`,
    `- 观点 ${t.viewpoints} 个，大概念 ${t.bigConcepts} 个；关联边 ${t.relatedEdges} 条，显式前置边 ${t.prerequisiteEdges} 条`,
    `- 教材投影 ${t.projections} 条：已核对 ${t.verified}（${pct(t.verified, t.projections)}），未核对 ${t.unverified}，有争议 ${t.disputed}`,
    `- 采分点 ${t.scoringPoints} 个，涉及题目 ${t.items} 道；时政（未下架）${t.affairs} 条`,
    "",
    "## 2. 观点 × 认知层次",
    "",
    "1 = 该观点声明了这一层次。投影列为 已核对 / 未核对 / 有争议。",
    "",
    `| 观点 | 大概念 | ${COGNITIVE_LEVELS.join(" | ")} | 投影 | 采分点 | 题目 | 时政 |`,
    `|---|---|${COGNITIVE_LEVELS.map(() => "---:").join("|")}|---|---:|---:|---:|`,
    ...r.rows.map((x) => `| ${cell(x.id)} | ${cell(x.bigConcept)} | ${COGNITIVE_LEVELS.map((c) => String(x.levels[c])).join(" | ")} | `
      + `${x.verified} / ${x.unverified} / ${x.disputed} | ${x.scoringPoints} | ${x.items} | ${x.affairs} |`),
    `| 合计 | | ${COGNITIVE_LEVELS.map((c) => String(r.levelTotals[c])).join(" | ")} | ${t.verified} / ${t.unverified} / ${t.disputed} | `
      + `${t.scoringPoints} | ${t.items} | ${t.affairs} |`,
    "",
    `## 3. ${r.focusDomain} 领域缺口清单`,
    "",
    ...(r.gaps.length === 0 ? ["无缺口。"] : ["| 节点 | 缺口 |", "|---|---|", ...r.gaps.map((g) => `| ${cell(g.id)} | ${g.issue} |`)]),
    "",
  ];
  return lines.join("\n");
}
