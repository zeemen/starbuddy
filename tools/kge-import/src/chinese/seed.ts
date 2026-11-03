// 语文切片地图 → 草稿地图版本的种子 SQL、导入报告与快照（KGE-07 第一步）。纯函数、确定性：同一输入逐字节相同的输出。
// 写入：kge.map_version（草稿，subject = wenli）、kge.knowledge_node（领域 / 主题 / 能力点、素材、方法）、
//       kge.knowledge_edge（层次递进 = prerequisite + weak；显式强前置 = prerequisite + strong；关联、易混、uses_method）、
//       kge.textbook_projection（只存版次、册、课、页码范围；quote_text 一律为空、mapping_state = unverified）。
// 以 starbuddy_kge 写入（最小权限）；发布走已有的签字与 kge.publish_map_version，本工具不发布。
// 合成样例的种子带挡板（与道法同一段）：只有会话里显式 SET starbuddy.allow_synthetic = on（一次性测试库）才能执行。
import { stableUuid, VERSION } from "../daofa/common.ts";
import { syntheticGuard } from "../daofa/seed.ts";
import { stableJson } from "../daofa/snapshot.ts";
import { q, qn } from "../items/sql.ts";
import { ChineseError, SUBJECT, type ChineseMap } from "./map.ts";

export { stableJson };

export const SNAPSHOT_FORMAT = "kge-chinese-snapshot/1";
export const REPORT_FORMAT = "kge-chinese-import-report/1";
export const DEFAULT_STANDARD_YEAR = 2022;

export const mapVersionIdOf = (version: string): string => stableUuid(`kge:${SUBJECT}:${version}`);
export const projectionIdOf = (version: string, p: { nodeId: string; edition: string; volume: string; chapter: string; pageFrom: number; pageTo: number }): string =>
  stableUuid(`kge:${SUBJECT}:${version}:projection:${p.nodeId}:${p.edition}:${p.volume}:${p.chapter}:${p.pageFrom}-${p.pageTo}`);

const textArr = (xs: string[]) => (xs.length === 0 ? "'{}'::text[]" : `ARRAY[${xs.map(q).join(", ")}]::text[]`);
const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

export interface SnapshotNode {
  id: string;
  level: "domain" | "topic" | "node" | "detail";
  parent_id: string | null;
  kind: string | null;
  name: string;
  scope: string;
  cognitive_levels: string[];
  status: "active" | "deprecated";
}
export interface SnapshotEdge { from_id: string; to_id: string; type: string; strength: "strong" | "weak" }
export interface SnapshotProjection {
  id: string;
  node_id: string;
  edition: string;
  edition_year: number;
  volume: string;
  chapter: string;
  page_from: number;
  page_to: number;
  mapping_state: "unverified" | "verified" | "disputed";
}
/** 覆盖报表（tools/kge-coverage --subject wenli）的输入；库上由 tools/kge-coverage/sql/chinese-snapshot.sql 只读导出同一格式 */
export interface ChineseSnapshot {
  format: typeof SNAPSHOT_FORMAT;
  subject: typeof SUBJECT;
  map_version_id: string;
  version: string;
  nodes: SnapshotNode[];
  edges: SnapshotEdge[];
  projections: SnapshotProjection[];
}

/** 固定排序（与 chinese-snapshot.sql 的 ORDER BY 一致） */
export function sortSnapshot(s: ChineseSnapshot): ChineseSnapshot {
  return {
    ...s,
    nodes: [...s.nodes].sort((a, b) => cmp(a.id, b.id)),
    edges: [...s.edges].sort((a, b) => cmp(a.from_id, b.from_id) || cmp(a.to_id, b.to_id) || cmp(a.type, b.type)),
    projections: [...s.projections].sort((a, b) => cmp(a.id, b.id)),
  };
}

export interface SeedOptions { version: string; standardYear?: number }
export interface SeedResult { mapVersionId: string; sql: string; snapshot: ChineseSnapshot; report: Record<string, unknown> }

export function renderMapSeed(map: ChineseMap, opts: SeedOptions): SeedResult {
  if (!VERSION.test(opts.version)) throw new ChineseError(`地图版本号 ${opts.version} 必须是 主.次.修订`);
  const year = opts.standardYear ?? DEFAULT_STANDARD_YEAR;
  if (!Number.isInteger(year) || year < 2011 || year > 2100) throw new ChineseError(`课标年份 ${year} 不合法`);
  const mv = mapVersionIdOf(opts.version);
  const lines = [
    `-- KGE-07 语文切片地图（生成文件，勿手改）：${map.files.join("、")} → 地图版本 ${SUBJECT} ${opts.version}（草稿）`,
    "-- 由 tools/kge-import/src/chinese 生成；文件自带事务，在演练库用 psql -v ON_ERROR_STOP=1 执行。不含任何学生数据，不含篇目原文与教材注释（quote_text 一律为空）。",
    "-- 层次递进边写成 prerequisite + strength = weak（evidence_ref = chinese:progression:…）；显式强前置写成 prerequisite + strong（evidence_ref = explicit:…）。",
    ...(map.synthetic ? ["-- 合成样例（合成、非教材内容）：能力点、素材、方法与页码均为工程起草的占位，只用于在一次性测试库证明流程。"] : []),
    "BEGIN;",
    "SET LOCAL standard_conforming_strings = on;  -- 转义只靠单引号加倍",
    ...(map.synthetic ? [syntheticGuard()] : []),
    "SET LOCAL ROLE starbuddy_kge;  -- 以知识服务角色写入（最小权限）",
    `INSERT INTO kge.map_version (id, subject, standard_year, version) VALUES (${q(mv)}, '${SUBJECT}', ${year}, ${q(opts.version)});`,
    "INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name, includes, excludes, examples, scope, cognitive_levels) VALUES",
    map.nodes.map((n) => `  (${q(mv)}, ${q(n.id)}, ${q(n.level)}, ${qn(n.parentId)}, ${qn(n.kind)}, ${q(n.name)}, ${textArr(n.includes)}, `
      + `${textArr(n.excludes)}, ${textArr(n.examples)}, ${q(n.scope)}, ${textArr(n.cognitiveLevels)})`).join(",\n") + ";",
  ];
  if (map.edges.length > 0) {
    lines.push("INSERT INTO kge.knowledge_edge (map_version_id, from_id, to_map_version_id, to_id, type, strength, evidence_ref) VALUES",
      map.edges.map((e) => `  (${q(mv)}, ${q(e.from)}, ${q(mv)}, ${q(e.to)}, ${q(e.type)}, ${q(e.strength)}, ${q(e.evidenceRef)})`).join(",\n") + ";");
  }
  const projRows = map.projections.map((p) => ({ ...p, id: projectionIdOf(opts.version, p) }));
  if (projRows.length > 0) {
    lines.push("INSERT INTO kge.textbook_projection (id, map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to, quote_text, mapping_state) VALUES",
      projRows.map((p) => `  (${q(p.id)}, ${q(mv)}, ${q(p.nodeId)}, ${q(p.edition)}, ${p.editionYear}, ${qn(p.printing)}, ${q(p.volume)}, ${q(p.chapter)}, `
        + `${p.pageFrom}, ${p.pageTo}, NULL, 'unverified')`).join(",\n") + ";");
  }
  lines.push("COMMIT;", "");

  const snapshot = sortSnapshot({
    format: SNAPSHOT_FORMAT, subject: SUBJECT, map_version_id: mv, version: opts.version,
    nodes: map.nodes.map((n) => ({ id: n.id, level: n.level, parent_id: n.parentId, kind: n.kind, name: n.name, scope: n.scope,
      cognitive_levels: n.cognitiveLevels, status: "active" as const })),
    edges: map.edges.map((e) => ({ from_id: e.from, to_id: e.to, type: e.type, strength: e.strength })),
    projections: projRows.map((p) => ({ id: p.id, node_id: p.nodeId, edition: p.edition, edition_year: p.editionYear, volume: p.volume,
      chapter: p.chapter, page_from: p.pageFrom, page_to: p.pageTo, mapping_state: "unverified" as const })),
  });
  const kindCount = (k: string) => map.nodes.filter((n) => n.kind === k).length;
  const relCount = (r: string) => map.edges.filter((e) => e.relation === r).length;
  // 能力 × 文本类型 × 题型 的能力阶梯格（只在离线报告里：库里没有这三列，覆盖报表按主题与节点出矩阵）
  const grid = map.nodes.filter((n) => n.ability).map((n) => ({ ability: n.ability!.ability, text_type: n.ability!.textType,
    question_type: n.ability!.questionType, rung: n.ability!.rung, node_id: n.id }))
    .sort((a, b) => cmp(a.ability, b.ability) || cmp(a.text_type, b.text_type) || cmp(a.question_type, b.question_type)
      || map.ladders.find((l) => l.code === a.ability)!.rungs.indexOf(a.rung) - map.ladders.find((l) => l.code === b.ability)!.rungs.indexOf(b.rung));
  const report = {
    format: REPORT_FORMAT, subject: SUBJECT, version: opts.version, map_version_id: mv, synthetic: map.synthetic, files: map.files,
    domains: map.nodes.filter((n) => n.level === "domain").length, topics: map.nodes.filter((n) => n.level === "topic").length,
    ability_kps: kindCount("ability_kp"), materials: kindCount("material"), methods: kindCount("method"),
    progression_edges: relCount("progression"), explicit_prerequisites: relCount("prerequisite"), related_edges: relCount("related"),
    confusable_edges: relCount("confusable"), uses_method_edges: relCount("uses_method"), projections: projRows.length,
    // 与库里 validate_map_version 的 missing_boundary 口径一致（能力点缺包含、不包含、典型题例或认知层次）
    expected_missing_boundary: map.nodes.filter((n) => n.kind === "ability_kp"
      && (n.includes.length === 0 || n.excludes.length === 0 || n.examples.length === 0 || n.cognitiveLevels.length === 0)).map((n) => n.id),
    // 范围属性：考情拓展、拓展素材不进必修覆盖统计（KGE 分册语文段“南京在地文化语料”）
    non_required_scope: map.nodes.filter((n) => n.level === "node" && n.scope !== "required").map((n) => n.id),
    ladders: map.ladders.map((l) => ({ code: l.code, rungs: l.rungs })),
    ability_grid: grid,
  };
  return { mapVersionId: mv, sql: lines.join("\n"), snapshot, report };
}
