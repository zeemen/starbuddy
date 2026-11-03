// 道法观点地图 → 草稿地图版本的种子 SQL（冲刺 4-K，spec 设计要点 1、2）。纯函数、确定性：同一输入逐字节相同的输出。
// 写入：kge.map_version（草稿）、kge.knowledge_node（领域 / 大概念 / 观点）、kge.knowledge_edge（关联边；显式前置边）、
//       kge.textbook_projection（只存版次、册、章、页码范围，quote_text 一律为空、mapping_state = unverified）、
//       kge.id_migration（大概念旧短编码 → 大概念节点，rule = manual）。
// 以 starbuddy_kge 写入（最小权限）；发布走已有的签字与 kge.publish_map_version（0019），本工具不发布。
// 合成样例的种子带挡板：只有会话里显式 SET starbuddy.allow_synthetic = on（一次性测试库）才能执行。
import { q, qn } from "../items/sql.ts";
import { stableUuid, SUBJECT, VERSION, DaofaError } from "./common.ts";
import type { DaofaMap } from "./domain.ts";
import { SNAPSHOT_FORMAT, sortSnapshot, type DaofaSnapshot } from "./snapshot.ts";

export const DEFAULT_STANDARD_YEAR = 2022;

export const mapVersionIdOf = (version: string): string => stableUuid(`kge:${SUBJECT}:${version}`);
export const projectionIdOf = (version: string, p: { nodeId: string; edition: string; volume: string; pageFrom: number; pageTo: number }): string =>
  stableUuid(`kge:${SUBJECT}:${version}:projection:${p.nodeId}:${p.edition}:${p.volume}:${p.pageFrom}-${p.pageTo}`);

const textArr = (xs: string[]) => (xs.length === 0 ? "'{}'::text[]" : `ARRAY[${xs.map(q).join(", ")}]::text[]`);

/** 合成样例的挡板：没有显式允许就报错（整个事务回滚） */
export function syntheticGuard(): string {
  return "DO $synthetic$ BEGIN IF current_setting('starbuddy.allow_synthetic', true) IS DISTINCT FROM 'on' THEN "
    + "RAISE EXCEPTION '合成样例（合成、非教材内容），只能在一次性测试库执行：先 SET starbuddy.allow_synthetic = on'; END IF; END $synthetic$;";
}

export interface SeedOptions { version: string; standardYear?: number }

export interface SeedResult {
  mapVersionId: string;
  sql: string;
  snapshot: DaofaSnapshot;
  report: Record<string, unknown>;
}

export function renderMapSeed(map: DaofaMap, opts: SeedOptions): SeedResult {
  if (!VERSION.test(opts.version)) throw new DaofaError(`地图版本号 ${opts.version} 必须是 主.次.修订`);
  const year = opts.standardYear ?? DEFAULT_STANDARD_YEAR;
  if (!Number.isInteger(year) || year < 2011 || year > 2100) throw new DaofaError(`课标年份 ${year} 不合法`);
  const mv = mapVersionIdOf(opts.version);
  const lines = [
    `-- 冲刺 4-K 道法观点地图（生成文件，勿手改）：${map.files.join("、")} → 地图版本 ${SUBJECT} ${opts.version}（草稿）`,
    "-- 由 tools/kge-import/src/daofa 生成；文件自带事务，在演练库用 psql -v ON_ERROR_STOP=1 执行。不含任何学生数据，不含教材原句（quote_text 一律为空）。",
    ...(map.synthetic ? ["-- 合成样例（合成、非教材内容）：观点、页码均为工程起草的占位，只用于在一次性测试库证明流程。"] : []),
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
      map.edges.map((e) => `  (${q(mv)}, ${q(e.from)}, ${q(mv)}, ${q(e.to)}, ${q(e.type)}, 'strong', `
        + `${q(e.type === "prerequisite" ? `explicit:${e.basis!}`.slice(0, 256) : "daofa:related")})`).join(",\n") + ";");
  }
  const projRows = map.projections.map((p) => ({ ...p, id: projectionIdOf(opts.version, p) }));
  if (projRows.length > 0) {
    lines.push("INSERT INTO kge.textbook_projection (id, map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to, quote_text, mapping_state) VALUES",
      projRows.map((p) => `  (${q(p.id)}, ${q(mv)}, ${q(p.nodeId)}, ${q(p.edition)}, ${p.editionYear}, ${qn(p.printing)}, ${q(p.volume)}, ${q(p.chapter)}, `
        + `${p.pageFrom}, ${p.pageTo}, NULL, 'unverified')`).join(",\n") + ";");
  }
  if (map.legacyCodes.length > 0) {
    lines.push("INSERT INTO kge.id_migration (map_version_id, subject, old_id, new_id, rule) VALUES",
      map.legacyCodes.map((l) => `  (${q(mv)}, '${SUBJECT}', ${q(l.oldId)}, ${q(l.newId)}, 'manual')`).join(",\n") + ";");
  }
  lines.push("COMMIT;", "");

  const snapshot = sortSnapshot({
    format: SNAPSHOT_FORMAT, subject: SUBJECT, map_version_id: mv, version: opts.version,
    nodes: map.nodes.map((n) => ({ id: n.id, level: n.level, parent_id: n.parentId, kind: n.kind, name: n.name, cognitive_levels: n.cognitiveLevels,
      status: "active" as const })),
    edges: map.edges.map((e) => ({ from_id: e.from, to_id: e.to, type: e.type })),
    projections: projRows.map((p) => ({ id: p.id, node_id: p.nodeId, edition: p.edition, edition_year: p.editionYear, volume: p.volume,
      chapter: p.chapter, page_from: p.pageFrom, page_to: p.pageTo, mapping_state: "unverified" as const })),
    scoring_points: [], current_affairs: [],
  });
  const count = (level: string) => map.nodes.filter((n) => n.level === level).length;
  const report = {
    format: "kge-daofa-import-report/1", subject: SUBJECT, version: opts.version, map_version_id: mv, synthetic: map.synthetic, files: map.files,
    domains: count("domain"), big_concepts: count("topic"), viewpoints: count("node"),
    related_edges: map.edges.filter((e) => e.type === "related").length,
    explicit_prerequisites: map.edges.filter((e) => e.type === "prerequisite").length,
    projections: projRows.length, legacy_codes: map.legacyCodes.length,
    // 与库里 validate_map_version 的 missing_boundary 口径一致（观点缺包含、不包含、典型题例或认知层次）
    expected_missing_boundary: map.nodes.filter((n) => n.kind === "viewpoint"
      && (n.includes.length === 0 || n.excludes.length === 0 || n.examples.length === 0 || n.cognitiveLevels.length === 0)).map((n) => n.id),
    // 与 orphan 口径一致：观点没有有效边，也没有教材投影（题目标签另算）
    expected_orphans: map.viewpointIds.filter((id) => !map.edges.some((e) => e.from === id || e.to === id)
      && !map.projections.some((p) => p.nodeId === id)),
    // 《学生读本》：封测范围明确排除（spec 待人确认 #1）
    student_reader: "excluded",
  };
  return { mapVersionId: mv, sql: lines.join("\n"), snapshot, report };
}
