// 道法观点地图：模板文件的读取与校验（冲刺 4-K，spec 设计要点 1、2）。
// 模板：tools/kge-import/templates/daofa-domain.yaml。层级：领域（domain）→ 大概念（topic）→ 观点（node，kind = viewpoint）。
// 校验要点：
//   - ID 符合 docs/design/kge/id-and-segments.md（MORAL. 前缀，领域 2 段、大概念 3 段、观点 4 段），上一级就是去掉最后一段；
//   - 观点必须挂在大概念下（只能写在某个大概念的 viewpoints 里，ID 前缀一致）；
//   - 不得出现教材原句一类的字段（decisions #5），任何层级都扫；投影只存版次、册、章、页码范围；
//   - 观点之间以“关联”（related）为主：前置边只有教研显式标注（explicit_prerequisite: true + basis）才收，且不能成环、不能是传递边；
//     其他边类型一律拒绝（关联边只用 related）；
//   - 合成样例必须 synthetic: true 且 notice 里写明“合成、非教材内容”。
import { findCycles, findTransitiveEdges, type Edge } from "../../../kge-inventory/src/graph.ts";
import {
  cmp, COGNITIVE, DaofaError, EDITION, keys, nodeId, obj, parentOf, PREFIX, scanForbidden, SCOPES, SUBJECT, text, textList,
} from "./common.ts";
import { parseYamlDoc } from "./yaml-doc.ts";

export const DOMAIN_FORMAT = "kge-daofa-domain/1";
/** 合成样例的 notice 必须含这句（与 samples/daofa 下的文件、seed 注释一致） */
export const SYNTHETIC_MARK = "合成、非教材内容";

export interface Projection {
  edition: string;
  editionYear: number;
  printing: string | null;
  volume: string;
  chapter: string;
  pageFrom: number;
  pageTo: number;
}
export interface Viewpoint {
  id: string;
  name: string; // 自写概括（不得摘抄教材原句）
  cognitiveLevels: string[];
  includes: string[];
  excludes: string[];
  examples: string[];
  scope: string;
  projections: Projection[];
}
export interface BigConcept {
  id: string;
  name: string;
  legacyCode: string | null; // 知行大概念旧短编码（m1、xr1），进 kge.id_migration
  viewpoints: Viewpoint[];
}
export interface Relation {
  from: string;
  to: string;
  type: "related" | "prerequisite";
  basis: string | null; // 前置边的教研依据（显式标注时必填）
}
export interface DaofaDomain {
  file: string;
  synthetic: boolean;
  notice: string;
  domain: { id: string; name: string };
  bigConcepts: BigConcept[];
  relations: Relation[];
}

const LEGACY_CODE = /^[a-z]{1,4}\d{1,3}$/;

function projection(where: string, v: unknown): Projection {
  const p = obj(where, v);
  keys(where, p, ["edition", "edition_year", "printing", "volume", "chapter", "page_from", "page_to"],
    ["edition", "edition_year", "volume", "chapter", "page_from", "page_to"]);
  if (typeof p.edition !== "string" || !EDITION.test(p.edition)) throw new DaofaError(`${where}.edition 不是版次编码（如 bubian-2024）`);
  const year = p.edition_year;
  if (typeof year !== "number" || !Number.isInteger(year) || year < 2000 || year > 2100) throw new DaofaError(`${where}.edition_year 不合法`);
  const from = p.page_from, to = p.page_to;
  if (typeof from !== "number" || !Number.isInteger(from) || from < 1 || from > 32767) throw new DaofaError(`${where}.page_from 必须是正整数`);
  if (typeof to !== "number" || !Number.isInteger(to) || to < from || to > 32767) throw new DaofaError(`${where}.page_to 必须是不小于 page_from 的整数`);
  return {
    edition: p.edition, editionYear: year, printing: p.printing === undefined || p.printing === null ? null : text(`${where}.printing`, p.printing, 32),
    volume: text(`${where}.volume`, p.volume, 32), chapter: text(`${where}.chapter`, p.chapter, 64), pageFrom: from, pageTo: to,
  };
}

function viewpoint(where: string, v: unknown, topicId: string): Viewpoint {
  const o = obj(where, v);
  keys(where, o, ["id", "name", "cognitive_levels", "includes", "excludes", "examples", "scope", "projections"], ["id", "name"]);
  const id = nodeId(`${where}.id`, o.id, 4);
  if (parentOf(id) !== topicId) throw new DaofaError(`${where}：观点 ${id} 必须挂在所在大概念 ${topicId} 下（ID 前缀一致）`);
  const levels = o.cognitive_levels === undefined ? [] : o.cognitive_levels;
  if (!Array.isArray(levels) || levels.some((c) => !(COGNITIVE as readonly unknown[]).includes(c))) {
    throw new DaofaError(`${where}.cognitive_levels 只能取 ${COGNITIVE.join(" / ")}`);
  }
  if (new Set(levels).size !== levels.length) throw new DaofaError(`${where}.cognitive_levels 重复`);
  const scope = o.scope ?? "required";
  if (!(SCOPES as readonly unknown[]).includes(scope)) throw new DaofaError(`${where}.scope 只能取 ${SCOPES.join(" / ")}`);
  const projections = o.projections === undefined || o.projections === null ? [] : o.projections;
  if (!Array.isArray(projections) || projections.length > 32) throw new DaofaError(`${where}.projections 必须是不超过 32 条的列表`);
  return {
    id, name: text(`${where}.name`, o.name, 128),
    // 按课标四级的固定顺序存（与数学导入器一致，便于比对）
    cognitiveLevels: COGNITIVE.filter((c) => (levels as string[]).includes(c)),
    includes: textList(`${where}.includes`, o.includes), excludes: textList(`${where}.excludes`, o.excludes),
    examples: textList(`${where}.examples`, o.examples), scope: scope as string,
    projections: projections.map((p, i) => projection(`${where}.projections[${i}]`, p)),
  };
}

/** 解析一个领域文件（纯函数；file 只用于报错与报告） */
export function parseDomain(yamlText: string, file = "domain.yaml"): DaofaDomain {
  let raw;
  try {
    raw = parseYamlDoc(yamlText);
  } catch (e) {
    throw new DaofaError(`${file}：${(e as Error).message}`);
  }
  scanForbidden(file, raw);
  keys(file, raw, ["format", "synthetic", "notice", "subject", "domain", "big_concepts", "relations"],
    ["format", "synthetic", "notice", "subject", "domain", "big_concepts"]);
  if (raw.format !== DOMAIN_FORMAT) throw new DaofaError(`${file}：format 应为 ${DOMAIN_FORMAT}`);
  if (raw.subject !== SUBJECT) throw new DaofaError(`${file}：subject 必须是 ${SUBJECT}（道法与历史分库，decisions #13）`);
  if (typeof raw.synthetic !== "boolean") throw new DaofaError(`${file}：synthetic 必须是 true 或 false`);
  const notice = text(`${file}.notice`, raw.notice, 400);
  if (raw.synthetic && !notice.includes(SYNTHETIC_MARK)) throw new DaofaError(`${file}：合成样例的 notice 必须写明“${SYNTHETIC_MARK}”`);
  const d = obj(`${file}.domain`, raw.domain);
  keys(`${file}.domain`, d, ["id", "name"], ["id", "name"]);
  const domainId = nodeId(`${file}.domain.id`, d.id, 2);
  const domain = { id: domainId, name: text(`${file}.domain.name`, d.name, 128) };

  if (!Array.isArray(raw.big_concepts) || raw.big_concepts.length === 0 || raw.big_concepts.length > 64) {
    throw new DaofaError(`${file}：big_concepts 必须是 1–64 个大概念的列表（观点必须挂在大概念下）`);
  }
  const bigConcepts = raw.big_concepts.map((b, i): BigConcept => {
    const where = `${file}.big_concepts[${i}]`;
    const o = obj(where, b);
    keys(where, o, ["id", "name", "legacy_code", "viewpoints"], ["id", "name"]);
    const id = nodeId(`${where}.id`, o.id, 3);
    if (parentOf(id) !== domainId) throw new DaofaError(`${where}：大概念 ${id} 必须挂在领域 ${domainId} 下`);
    const code = o.legacy_code;
    if (code !== undefined && code !== null && (typeof code !== "string" || !LEGACY_CODE.test(code))) {
      throw new DaofaError(`${where}.legacy_code 应为知行大概念的短编码（如 m1、xr1）`);
    }
    const vps = o.viewpoints === undefined || o.viewpoints === null ? [] : o.viewpoints;
    if (!Array.isArray(vps) || vps.length > 128) throw new DaofaError(`${where}.viewpoints 必须是列表`);
    return {
      id, name: text(`${where}.name`, o.name, 128), legacyCode: typeof code === "string" ? code : null,
      viewpoints: vps.map((v, k) => viewpoint(`${where}.viewpoints[${k}]`, v, id)),
    };
  });

  const rels = raw.relations === undefined || raw.relations === null ? [] : raw.relations;
  if (!Array.isArray(rels) || rels.length > 512) throw new DaofaError(`${file}：relations 必须是列表`);
  const relations = rels.map((r, i): Relation => {
    const where = `${file}.relations[${i}]`;
    const o = obj(where, r);
    keys(where, o, ["from", "to", "type", "explicit_prerequisite", "basis"], ["from", "to"]);
    const type = o.type ?? "related";
    if (type !== "related" && type !== "prerequisite") {
      throw new DaofaError(`${where}：观点之间的边只用 related（关联）；确需前置时写 type: prerequisite 并显式标注，不收 ${String(type)}`);
    }
    if (type === "prerequisite" && o.explicit_prerequisite !== true) {
      throw new DaofaError(`${where}：观点之间不硬凑前置边；确需前置由教研显式标注（explicit_prerequisite: true，并写 basis）`);
    }
    if (type === "related" && o.explicit_prerequisite !== undefined) throw new DaofaError(`${where}：explicit_prerequisite 只用于前置边`);
    if (type === "prerequisite" && (o.basis === undefined || o.basis === null)) throw new DaofaError(`${where}：显式前置边必须写 basis（教研依据）`);
    if (type === "related" && o.basis !== undefined && o.basis !== null) throw new DaofaError(`${where}：basis 只用于前置边`);
    return {
      from: nodeId(`${where}.from`, o.from, 4), to: nodeId(`${where}.to`, o.to, 4), type,
      basis: type === "prerequisite" ? text(`${where}.basis`, o.basis, 256) : null,
    };
  });
  // 顶层直接写观点（缺大概念）已被 keys 拒绝；这里再拦 ID 重复
  const seen = new Set<string>([domainId]);
  for (const b of bigConcepts) {
    for (const id of [b.id, ...b.viewpoints.map((v) => v.id)]) {
      if (seen.has(id)) throw new DaofaError(`${file}：节点 ${id} 重复`);
      seen.add(id);
    }
  }
  return { file, synthetic: raw.synthetic, notice, domain, bigConcepts, relations };
}

export interface MapNodeRow {
  id: string;
  level: "domain" | "topic" | "node";
  parentId: string | null;
  kind: "viewpoint" | null;
  name: string;
  includes: string[];
  excludes: string[];
  examples: string[];
  scope: string;
  cognitiveLevels: string[];
}
export interface ProjectionRow extends Projection { nodeId: string }
export interface DaofaMap {
  synthetic: boolean;
  files: string[];
  nodes: MapNodeRow[]; // 按 ID 码点排序
  edges: Relation[]; // 按（from, to, type）排序
  projections: ProjectionRow[];
  legacyCodes: { oldId: string; newId: string }[];
  viewpointIds: string[];
}

const relKey = (r: { from: string; to: string }) => `${r.from}\u0000${r.to}`;

/** 把若干领域文件合成一张地图（同一地图版本），做跨文件的校验：领域不重复、边的两端都是本图的观点、关联边不重复、前置边不成环也不传递 */
export function buildMap(domains: DaofaDomain[]): DaofaMap {
  if (domains.length === 0) throw new DaofaError("至少要一个领域文件");
  // 合成样例与真实内容不能进同一个地图版本（合成样例只在一次性库上执行）
  if (new Set(domains.map((d) => d.synthetic)).size > 1) throw new DaofaError("合成样例与真实内容不能导入同一个地图版本");
  const nodes: MapNodeRow[] = [];
  const projections: ProjectionRow[] = [];
  const legacyCodes: { oldId: string; newId: string }[] = [];
  const domainIds = new Set<string>();
  for (const d of domains) {
    if (domainIds.has(d.domain.id)) throw new DaofaError(`领域 ${d.domain.id} 在两个文件里出现`);
    domainIds.add(d.domain.id);
    nodes.push({ id: d.domain.id, level: "domain", parentId: null, kind: null, name: d.domain.name, includes: [], excludes: [], examples: [],
      scope: "required", cognitiveLevels: [] });
    for (const b of d.bigConcepts) {
      nodes.push({ id: b.id, level: "topic", parentId: d.domain.id, kind: null, name: b.name, includes: [], excludes: [], examples: [],
        scope: "required", cognitiveLevels: [] });
      if (b.legacyCode) legacyCodes.push({ oldId: b.legacyCode, newId: b.id });
      for (const v of b.viewpoints) {
        nodes.push({ id: v.id, level: "node", parentId: b.id, kind: "viewpoint", name: v.name, includes: v.includes, excludes: v.excludes,
          examples: v.examples, scope: v.scope, cognitiveLevels: v.cognitiveLevels });
        for (const p of v.projections) projections.push({ ...p, nodeId: v.id });
      }
    }
  }
  const ids = new Set(nodes.map((n) => n.id));
  if (ids.size !== nodes.length) throw new DaofaError("节点 ID 在文件之间重复");
  const oldIds = new Set<string>();
  for (const l of legacyCodes) {
    if (oldIds.has(l.oldId)) throw new DaofaError(`大概念旧编码 ${l.oldId} 重复`);
    oldIds.add(l.oldId);
  }
  const viewpointIds = nodes.filter((n) => n.kind === "viewpoint").map((n) => n.id).sort(cmp);
  const vpSet = new Set(viewpointIds);
  const edges: Relation[] = [];
  const related = new Set<string>();
  const prereq = new Set<string>();
  for (const r of domains.flatMap((d) => d.relations)) {
    if (!vpSet.has(r.from) || !vpSet.has(r.to)) throw new DaofaError(`边 ${r.from} → ${r.to}：两端都必须是本地图里的观点`);
    if (r.from === r.to) throw new DaofaError(`边 ${r.from} → ${r.to}：不能自己连自己`);
    const k = relKey(r), rev = relKey({ from: r.to, to: r.from });
    if (r.type === "related") {
      // 关联边无方向：a–b 与 b–a 只收一条
      if (related.has(k) || related.has(rev)) throw new DaofaError(`关联边 ${r.from} — ${r.to} 重复（关联无方向，只写一条）`);
      related.add(k);
    } else {
      if (prereq.has(k)) throw new DaofaError(`前置边 ${r.from} → ${r.to} 重复`);
      prereq.add(k);
    }
    edges.push(r);
  }
  const pre: Edge[] = edges.filter((e) => e.type === "prerequisite").map((e) => [e.from, e.to] as const);
  const cycles = findCycles(viewpointIds, pre);
  if (cycles.length > 0) throw new DaofaError(`显式前置边成环：${cycles.map((c) => c.join(" ↔ ")).join("；")}`);
  const trans = findTransitiveEdges(viewpointIds, pre);
  if (trans.length > 0) throw new DaofaError(`显式前置边里有可由其他前置路径推出的传递边：${trans.map(([a, b]) => `${a} → ${b}`).join("；")}`);
  for (const n of nodes) if (n.id.split(".")[0] !== PREFIX) throw new DaofaError(`节点 ${n.id} 不在 ${PREFIX} 命名空间`);
  nodes.sort((a, b) => cmp(a.id, b.id));
  edges.sort((a, b) => cmp(a.from, b.from) || cmp(a.to, b.to) || cmp(a.type, b.type));
  projections.sort((a, b) => cmp(a.nodeId, b.nodeId) || cmp(a.edition, b.edition) || cmp(a.volume, b.volume) || a.pageFrom - b.pageFrom || a.pageTo - b.pageTo);
  for (let i = 1; i < projections.length; i++) {
    const a = projections[i - 1]!, b = projections[i]!;
    if (a.nodeId === b.nodeId && a.edition === b.edition && a.volume === b.volume && a.pageFrom === b.pageFrom && a.pageTo === b.pageTo) {
      throw new DaofaError(`观点 ${a.nodeId} 在 ${a.edition} ${a.volume} 第 ${a.pageFrom}–${a.pageTo} 页的投影重复`);
    }
  }
  legacyCodes.sort((a, b) => cmp(a.oldId, b.oldId));
  return { synthetic: domains.some((d) => d.synthetic), files: domains.map((d) => d.file), nodes, edges, projections, legacyCodes, viewpointIds };
}
