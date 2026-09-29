// 读取 KGE-01 地图导入产物（db/seeds/kge/aya-<主题>-<版本>/ 下的 import.sql 与 import-report.json），只读、不连库。
// 只解析报表要用的 kge.map_version 与 kge.knowledge_node 两段 INSERT；其余语句忽略。
// import.sql 由 tools/kge-inventory/src/import 生成：固定 standard_conforming_strings = on，字符串转义只有单引号加倍。
import { readFileSync } from "node:fs";
import { join } from "node:path";

export const COGNITIVE_LEVELS = ["know", "understand", "apply", "synthesize"] as const;
export type CognitiveLevel = (typeof COGNITIVE_LEVELS)[number];

export interface MapNode {
  id: string;
  level: "domain" | "topic" | "node" | "detail";
  kind: string | null;
  name: string;
  cognitiveLevels: string[];
  status: "active" | "deprecated";
}

export interface KgeMap {
  mapVersionId: string;
  version: string;
  topic: string | null;
  nodes: MapNode[];
}

export class MapError extends Error {}

type SqlValue = string | number | null;

// 从 pos 起解析一个 SQL 值：'字符串'（可带 ::类型）、NULL、数字
function parseValue(sql: string, pos: number): [SqlValue, number] {
  let i = pos;
  while (/\s/.test(sql[i] ?? "")) i++;
  let v: SqlValue;
  if (sql[i] === "'") {
    let s = "";
    i++;
    for (;;) {
      const c = sql[i];
      if (c === undefined) throw new MapError("import.sql：字符串没有结束");
      if (c === "'") {
        if (sql[i + 1] === "'") { s += "'"; i += 2; continue; }
        i++;
        break;
      }
      s += c;
      i++;
    }
    v = s;
  } else if (sql.startsWith("NULL", i)) {
    v = null;
    i += 4;
  } else {
    const m = /^-?\d+(\.\d+)?/.exec(sql.slice(i));
    if (!m) throw new MapError(`import.sql：第 ${String(i)} 个字符处无法解析的值`);
    v = Number(m[0]);
    i += m[0].length;
  }
  // 类型转换后缀，如 ::text[]、::uuid
  const cast = /^::[a-z_]+(\[\])?/.exec(sql.slice(i));
  if (cast) i += cast[0].length;
  return [v, i];
}

// 解析 INSERT INTO <table> (列…) VALUES (…), (…); 返回按列名取值的行
export function parseInsert(sql: string, table: string): Record<string, SqlValue>[] {
  const head = new RegExp(`INSERT INTO ${table.replace(".", "\\.")} \\(([^)]*)\\) VALUES`, "g");
  const rows: Record<string, SqlValue>[] = [];
  for (let m = head.exec(sql); m; m = head.exec(sql)) {
    const cols = m[1]!.split(",").map((c) => c.trim());
    let i = m.index + m[0].length;
    for (;;) {
      while (/\s/.test(sql[i] ?? "")) i++;
      if (sql[i] !== "(") throw new MapError(`import.sql：${table} 的 VALUES 格式不对`);
      i++;
      const vals: SqlValue[] = [];
      for (;;) {
        const [v, next] = parseValue(sql, i);
        vals.push(v);
        i = next;
        while (/\s/.test(sql[i] ?? "")) i++;
        if (sql[i] === ",") { i++; continue; }
        if (sql[i] === ")") { i++; break; }
        throw new MapError(`import.sql：${table} 的一行没有正确结束`);
      }
      if (vals.length !== cols.length) throw new MapError(`import.sql：${table} 的一行有 ${String(vals.length)} 个值，列有 ${String(cols.length)} 个`);
      rows.push(Object.fromEntries(cols.map((c, k) => [c, vals[k]!])));
      while (/\s/.test(sql[i] ?? "")) i++;
      if (sql[i] === ",") { i++; continue; }
      if (sql[i] === ";") break;
      throw new MapError(`import.sql：${table} 的 INSERT 没有以分号结束`);
    }
  }
  return rows;
}

// Postgres 一维数组字面量 {a,"b c",...}：只支持导入器会产出的形式（双引号元素内的 \" 与 \\ 转义）
export function parsePgArray(lit: string): string[] {
  if (!lit.startsWith("{") || !lit.endsWith("}")) throw new MapError(`import.sql：不是数组字面量：${lit}`);
  const body = lit.slice(1, -1);
  const out: string[] = [];
  let i = 0;
  while (i < body.length) {
    if (body[i] === '"') {
      let s = "";
      i++;
      while (i < body.length && body[i] !== '"') {
        if (body[i] === "\\") i++;
        s += body[i] ?? "";
        i++;
      }
      i++; // 结束引号
      out.push(s);
    } else {
      const end = body.indexOf(",", i);
      out.push(body.slice(i, end < 0 ? body.length : end).trim());
      i = end < 0 ? body.length : end;
    }
    if (body[i] === ",") i++;
  }
  return out;
}

const str = (v: SqlValue | undefined, what: string): string => {
  if (typeof v !== "string") throw new MapError(`import.sql：${what} 应为字符串`);
  return v;
};

export function parseMap(sql: string, report: { map_version_id?: unknown; version?: unknown; topic?: unknown }): KgeMap {
  const mv = parseInsert(sql, "kge.map_version");
  if (mv.length !== 1) throw new MapError(`import.sql：应恰好有 1 个地图版本，实际 ${String(mv.length)} 个`);
  const mapVersionId = str(mv[0]!.id, "map_version.id");
  const version = str(mv[0]!.version, "map_version.version");
  if (report.map_version_id !== mapVersionId) throw new MapError("import-report.json 的 map_version_id 与 import.sql 不一致");
  if (report.version !== undefined && report.version !== version) throw new MapError("import-report.json 的 version 与 import.sql 不一致");
  const nodes = parseInsert(sql, "kge.knowledge_node").map((r): MapNode => {
    if (r.map_version_id !== mapVersionId) throw new MapError(`节点 ${String(r.id)} 不属于地图版本 ${mapVersionId}`);
    const level = str(r.level, "level");
    if (!["domain", "topic", "node", "detail"].includes(level)) throw new MapError(`节点 ${String(r.id)} 的 level 不合法：${level}`);
    const status = r.status === undefined || r.status === null ? "active" : str(r.status, "status");
    return {
      id: str(r.id, "knowledge_node.id"),
      level: level as MapNode["level"],
      kind: r.kind === null || r.kind === undefined ? null : str(r.kind, "kind"),
      name: str(r.name, "name"),
      cognitiveLevels: r.cognitive_levels === undefined ? [] : parsePgArray(str(r.cognitive_levels, "cognitive_levels")),
      status: status === "deprecated" ? "deprecated" : "active",
    };
  });
  const seen = new Set<string>();
  for (const n of nodes) {
    if (seen.has(n.id)) throw new MapError(`节点 ${n.id} 重复`);
    seen.add(n.id);
  }
  return { mapVersionId, version, topic: typeof report.topic === "string" ? report.topic : null, nodes };
}

// 同一地图版本的多个专题种子目录（KGE-01 待人确认 #8）合成一张图：地图版本必须相同；同一节点（如共用的领域）只留一份，定义必须一致
export function mergeMaps(maps: KgeMap[]): KgeMap {
  if (maps.length === 0) throw new MapError("至少要一个地图产物目录");
  const [first] = maps as [KgeMap, ...KgeMap[]];
  const nodes = new Map<string, MapNode>();
  for (const m of maps) {
    if (m.mapVersionId !== first.mapVersionId || m.version !== first.version) {
      throw new MapError(`地图产物不属于同一地图版本：${first.version}（${first.mapVersionId}）与 ${m.version}（${m.mapVersionId}）`);
    }
    for (const n of m.nodes) {
      const had = nodes.get(n.id);
      if (had && JSON.stringify(had) !== JSON.stringify(n)) throw new MapError(`节点 ${n.id} 在两个种子里定义不同`);
      nodes.set(n.id, n);
    }
  }
  const topics = [...new Set(maps.map((m) => m.topic).filter((t): t is string => t !== null))].sort();
  return { mapVersionId: first.mapVersionId, version: first.version, topic: topics.length ? topics.join("、") : null, nodes: [...nodes.values()] };
}

export function loadMapDir(dir: string): KgeMap {
  const sql = readFileSync(join(dir, "import.sql"), "utf8");
  const report = JSON.parse(readFileSync(join(dir, "import-report.json"), "utf8")) as Record<string, unknown>;
  return parseMap(sql, report);
}
