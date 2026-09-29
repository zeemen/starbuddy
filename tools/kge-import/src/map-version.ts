// 读 KGE-01 已生成的地图版本种子（db/seeds/kge/aya-<专题>-<版本>/import.sql），得到地图版本 ID、节点与旧编码迁移表。
// 只读仓库里的生成文件，不连库；格式由 tools/kge-inventory/src/import/build.ts 的 renderSql 决定（每行一个元组）。
// 一次可以读多个种子目录（KGE-01 待人确认 #8：多专题），但它们必须属于同一个地图版本。
import { readFileSync } from "node:fs";
import { join } from "node:path";

export interface MapNode {
  id: string;
  level: "domain" | "topic" | "node";
  parentId: string | null;
  kind: string | null;
  name: string;
  includes: string[];
  examples: string[];
  cognitiveLevels: string[];
}
export interface MapInput {
  mapVersionId: string;
  version: string;
  subject: "aya";
  nodes: MapNode[]; // 按 ID 排序
  /** 旧编码（K-FUN-0901）→ 新节点 ID */
  idMigration: Map<string, string>;
}

export class MapInputError extends Error {}

type Tok = string | number | null | string[];

/** 解析一个 SQL 元组 `(... , ...)`：单引号字符串（'' 转义）、NULL、数字、ARRAY[...]、'{}'；忽略 ::类型转换 */
export function parseTuple(line: string): Tok[] {
  const s = line.trim().replace(/[,;]$/, "");
  if (!s.startsWith("(") || !s.endsWith(")")) throw new MapInputError(`不是元组：${s.slice(0, 40)}`);
  const body = s.slice(1, -1);
  const out: Tok[] = [];
  let i = 0;
  const skipWs = () => { while (i < body.length && body[i] === " ") i++; };
  const readStr = (): string => {
    let v = "";
    i++; // 开头的单引号
    for (;;) {
      if (i >= body.length) throw new MapInputError("字符串没有闭合");
      const ch = body[i]!;
      if (ch === "'") {
        if (body[i + 1] === "'") { v += "'"; i += 2; continue; }
        i++;
        return v;
      }
      v += ch;
      i++;
    }
  };
  const skipCast = () => { while (body.startsWith("::", i)) { i += 2; while (i < body.length && /[A-Za-z0-9_.[\]]/.test(body[i]!)) i++; } };
  while (i < body.length) {
    skipWs();
    if (body[i] === "'") {
      const v = readStr();
      skipCast();
      out.push(v === "{}" ? [] : v);
    } else if (body.startsWith("NULL", i)) {
      i += 4;
      out.push(null);
    } else if (body.startsWith("ARRAY[", i)) {
      i += 6;
      const arr: string[] = [];
      for (;;) {
        skipWs();
        if (body[i] === "]") { i++; break; }
        if (body[i] !== "'") throw new MapInputError("ARRAY 里只认字符串");
        arr.push(readStr());
        skipWs();
        if (body[i] === ",") i++;
      }
      skipCast();
      out.push(arr);
    } else {
      const m = /^-?\d+(\.\d+)?/.exec(body.slice(i));
      if (!m) throw new MapInputError(`看不懂的值：${body.slice(i, i + 20)}`);
      out.push(Number(m[0]));
      i += m[0].length;
    }
    skipWs();
    if (i < body.length) {
      if (body[i] !== ",") throw new MapInputError(`元组里缺逗号：${body.slice(i, i + 20)}`);
      i++;
    }
  }
  return out;
}

const s = (t: Tok, what: string): string => {
  if (typeof t !== "string") throw new MapInputError(`${what} 应为字符串`);
  return t;
};
const sn = (t: Tok): string | null => (t === null ? null : s(t, "可空字段"));
const arr = (t: Tok): string[] => (Array.isArray(t) ? t : []);

/** 解析一份 import.sql 的文本 */
export function parseSeedSql(sql: string): MapInput {
  let mapVersionId: string | null = null;
  let version: string | null = null;
  const nodes: MapNode[] = [];
  const idMigration = new Map<string, string>();
  let table: string | null = null;
  for (const line of sql.split("\n")) {
    const ins = /^INSERT INTO kge\.([a-z_]+) \(/.exec(line);
    if (ins) {
      table = ins[1]!;
      const inline = /VALUES (\(.*\));$/.exec(line);
      if (table === "map_version" && inline) {
        const t = parseTuple(inline[1]!);
        mapVersionId = s(t[0]!, "map_version.id");
        if (t[1] !== "aya") throw new MapInputError("只支持阿鸦（aya）的地图版本");
        version = s(t[3]!, "map_version.version");
        table = null;
      }
      continue;
    }
    if (!line.startsWith("  (") || table === null) continue; // 边、能力视图挂接等其他表的行不需要
    const t = parseTuple(line);
    if (mapVersionId !== null && t[0] !== mapVersionId) throw new MapInputError("种子里出现了另一个地图版本的行");
    if (table === "knowledge_node") {
      const level = s(t[2]!, "level");
      if (level !== "domain" && level !== "topic" && level !== "node") throw new MapInputError(`节点层级 ${level} 不认识`);
      nodes.push({
        id: s(t[1]!, "id"), level, parentId: sn(t[3]!), kind: sn(t[4]!), name: s(t[5]!, "name"),
        includes: arr(t[6]!), examples: arr(t[8]!), cognitiveLevels: arr(t[9]!),
      });
    } else if (table === "id_migration") {
      idMigration.set(s(t[2]!, "old_id"), s(t[3]!, "new_id"));
    }
  }
  if (mapVersionId === null || version === null) throw new MapInputError("种子里没有 kge.map_version 行");
  if (nodes.length === 0) throw new MapInputError("种子里没有节点");
  const ids = new Set(nodes.map((n) => n.id));
  for (const [old, nid] of idMigration) if (!ids.has(nid)) throw new MapInputError(`迁移表 ${old} → ${nid} 指向不存在的节点`);
  nodes.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return { mapVersionId, version, subject: "aya", nodes, idMigration };
}

/** 合并多个种子目录（同一地图版本的多个专题） */
export function loadMapInput(dirs: string[]): MapInput {
  if (dirs.length === 0) throw new MapInputError("至少给一个地图版本种子目录（--map）");
  const parts = dirs.map((d) => parseSeedSql(readFileSync(join(d, "import.sql"), "utf8")));
  const first = parts[0]!;
  const byId = new Map<string, MapNode>();
  const idMigration = new Map<string, string>();
  for (const p of parts) {
    if (p.mapVersionId !== first.mapVersionId) throw new MapInputError("多个种子目录必须属于同一个地图版本");
    for (const n of p.nodes) byId.set(n.id, n);
    for (const [k, v] of p.idMigration) idMigration.set(k, v);
  }
  const nodes = [...byId.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return { ...first, nodes, idMigration };
}
