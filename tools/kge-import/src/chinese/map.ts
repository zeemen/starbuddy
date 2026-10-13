// 语文切片地图：模板文件的读取与校验（KGE-07 第一步，specs/kge07-chinese-slice/spec.md 范围 1）。
// 模板：tools/kge-import/templates/chinese-domain.yaml。层级：领域（domain）→ 主题（topic）→ 知识节点层（node）。
// 知识节点层只收三类（KGE 分册语文段落 :590-606）：
//   - 能力点 ability_kp = 能力 × 文本类型 × 题型（28 点保留为能力视图，不在本工具里；能力点是诊断单元，带认知层次）；
//   - 素材 material（篇目、实词虚词、字音字形、名著要素）：不计掌握度、不带认知层次、不进前置依赖图；
//   - 方法 method（阅读方法、写作方法）：不计掌握度、不带认知层次，能力点用 uses_method 指向它。
// 边（relations）只用迁移 0011 已有的边类型，不新增类型：
//   - progression（层次递进）→ kge.knowledge_edge type = prerequisite、strength = weak：只连同一能力阶梯的相邻两级（低一级 → 高一级），
//     且两端文本类型、题型相同；
//     诊断与 node.path 只走强前置边（learning/diagnosis/source.py、knowledge/service/reader.py），弱边只表达递进，不参与传导；
//   - prerequisite（教研显式标注的强前置，如“文言实词推断”对“文言翻译”）→ prerequisite、strong：必须 explicit_prerequisite: true + basis；
//   - related（关联）、confusable（易混，素卡“易混项”）→ 同名类型、strong，无方向，一对节点只写一种关联；
//   - uses_method → 同名类型：起点是能力点、终点是方法。
// 校验：素材与方法不进前置图（progression / prerequisite 两端都必须是能力点）；前置图（强弱合在一起，与库里 validate_map_version 同口径）
// 无环、无传递冗余边；知识节点层没有孤点（没有边也没有教材投影）；ID 符合 docs/design/kge/id-and-segments.md 且以 CHN. 开头；
// 不得出现原文、教材注释一类的字段；本步只收合成样例（synthetic: false 在法务与教研确认前一律拒绝），合成样例的名称、ID 与每个
// 自由文本字段（包含、不包含、典型题例、依据、册、课）都要带合成标记（只用于一次性测试库）。
import { findCycles, findTransitiveEdges, type Edge } from "../../../kge-inventory/src/graph.ts";
import { cmp, COGNITIVE, DaofaError, EDITION, FORBIDDEN_KEY, NODE_ID, obj as daofaObj, SCOPES, text as daofaText, textList as daofaTextList } from "../daofa/common.ts";
import { parseYamlDoc } from "../daofa/yaml-doc.ts";

export const CHINESE_FORMAT = "kge-chinese-domain/1";
export const SUBJECT = "wenli" as const;
/** kge.subject_prefix('wenli') */
export const PREFIX = "CHN";
/** 合成样例的 notice 必须含这句；合成样例的每个名称必须含“合成” */
export const SYNTHETIC_MARK = "合成、非教材内容";
/** 合成样例的领域 ID 第二段必须以它开头（CHN.SYN_…），免得合成节点与真实地图的 ID 撞上 */
export const SYNTHETIC_SEGMENT = "SYN";
export const NODE_KINDS = ["ability_kp", "material", "method"] as const;
export type NodeKind = (typeof NODE_KINDS)[number];
export const RELATION_TYPES = ["related", "confusable", "uses_method", "progression", "prerequisite"] as const;
export type RelationType = (typeof RELATION_TYPES)[number];
/**
 * 语文另拒的字段：教材注释、课下注、释义原文（真实实词虚词清单与教材注释不入库，spec 不做项）。
 * 与道法的原句类字段（FORBIDDEN_KEY：quote、original、excerpt、full_text、原文、全文……）一起扫，任何层级出现都拒绝。
 */
export const CHINESE_FORBIDDEN_KEY = /(annotation|footnote|textbook_note|gloss_text|注释|课下注|释文)/i;

const CODE = /^[A-Z][A-Z0-9_]{0,31}$/;
const RUNG = /^[A-Z0-9][A-Z0-9_]{0,15}$/;

export class ChineseError extends Error {}

/** 复用道法导入器的文本校验（长度、控制字符、个人信息模式），报错统一成 ChineseError */
function wrap<T>(f: () => T): T {
  try {
    return f();
  } catch (e) {
    if (e instanceof DaofaError) throw new ChineseError(e.message);
    throw e;
  }
}
const text = (where: string, v: unknown, max: number) => wrap(() => daofaText(where, v, max));
const textList = (where: string, v: unknown) => wrap(() => daofaTextList(where, v));
const obj = (where: string, v: unknown) => wrap(() => daofaObj(where, v));

function forbiddenKey(k: string): boolean {
  return FORBIDDEN_KEY.test(k) || CHINESE_FORBIDDEN_KEY.test(k);
}
const FORBIDDEN_WHY = "篇目原文、教材原句与注释、真实实词虚词释义在法务与教研确认前不入库；素材卡的原文段只放占位";

/** 递归扫描：任何层级出现原文或注释类键名都拒绝 */
export function scanForbidden(where: string, v: unknown): void {
  if (Array.isArray(v)) v.forEach((x, i) => scanForbidden(`${where}[${i}]`, x));
  else if (v !== null && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      if (forbiddenKey(k)) throw new ChineseError(`${where}.${k}：不得出现原文或注释字段（${FORBIDDEN_WHY}）`);
      scanForbidden(`${where}.${k}`, x);
    }
  }
}

function keys(where: string, o: Record<string, unknown>, allowed: readonly string[], required: readonly string[] = []): void {
  for (const k of Object.keys(o)) if (!allowed.includes(k)) throw new ChineseError(`${where}：不认识的键“${k}”`);
  for (const k of required) if (o[k] === undefined || o[k] === null) throw new ChineseError(`${where}：缺“${k}”`);
}

const DEPTH_NAME: Record<number, string> = { 2: "领域", 3: "主题", 4: "知识节点层" };

export function nodeId(where: string, v: unknown, depth: number): string {
  if (typeof v !== "string" || !NODE_ID.test(v)) throw new ChineseError(`${where}：节点 ID 不符合规范（docs/design/kge/id-and-segments.md）：${String(v)}`);
  if (v.split(".")[0] !== PREFIX) throw new ChineseError(`${where}：语文节点 ID 必须以 ${PREFIX}. 开头：${v}`);
  if (v.split(".").length !== depth) throw new ChineseError(`${where}：${DEPTH_NAME[depth]}的 ID 应为 ${depth} 段：${v}`);
  return v;
}

export const parentOf = (id: string): string => id.split(".").slice(0, -1).join(".");

export interface Projection {
  edition: string;
  editionYear: number;
  printing: string | null;
  volume: string; // 册
  chapter: string; // 课
  pageFrom: number;
  pageTo: number;
}
export interface AbilityTag { ability: string; rung: string; textType: string; questionType: string }
export interface ChineseNode {
  id: string;
  kind: NodeKind;
  name: string;
  scope: string;
  cognitiveLevels: string[];
  includes: string[];
  excludes: string[];
  examples: string[];
  projections: Projection[];
  ability: AbilityTag | null; // 只有能力点有
}
export interface Topic { id: string; name: string; nodes: ChineseNode[] }
export interface Ladder { code: string; name: string; rungs: string[] }
export interface Vocab { code: string; name: string }
export interface Relation { from: string; to: string; type: RelationType; basis: string | null }
export interface ChineseDomain {
  file: string;
  synthetic: boolean;
  notice: string;
  domain: { id: string; name: string };
  ladders: Ladder[];
  textTypes: Vocab[];
  questionTypes: Vocab[];
  topics: Topic[];
  relations: Relation[];
}

function code(where: string, v: unknown, re = CODE): string {
  if (typeof v !== "string" || !re.test(v)) throw new ChineseError(`${where} 应为大写字母、数字、下划线组成的编码：${String(v)}`);
  return v;
}

function syntheticName(where: string, name: string, synthetic: boolean): string {
  if (synthetic && !name.includes("合成")) throw new ChineseError(`${where}：合成样例的名称必须带“合成”字样（防止真实篇目或词条混进合成样例）`);
  return name;
}

/** 合成样例的每个自由文本字段（包含、不包含、典型题例、依据、册、课、印次）都必须带“合成”标记：防止真实原文、释义、例句混进来 */
function syntheticText(where: string, value: string, synthetic: boolean): string {
  if (synthetic && !value.includes("合成")) {
    throw new ChineseError(`${where}：合成样例的自由文本必须带“合成”字样（防止真实篇目原文、释义、例句或教材页面信息混进合成样例）`);
  }
  return value;
}
const syntheticList = (where: string, xs: string[], synthetic: boolean): string[] => xs.map((x, i) => syntheticText(`${where}[${i}]`, x, synthetic));

function projection(where: string, v: unknown, synthetic: boolean): Projection {
  const p = obj(where, v);
  keys(where, p, ["edition", "edition_year", "printing", "volume", "chapter", "page_from", "page_to"],
    ["edition", "edition_year", "volume", "chapter", "page_from", "page_to"]);
  if (typeof p.edition !== "string" || !EDITION.test(p.edition)) throw new ChineseError(`${where}.edition 不是版次编码（如 tongbian-2024）`);
  const year = p.edition_year;
  if (typeof year !== "number" || !Number.isInteger(year) || year < 2000 || year > 2100) throw new ChineseError(`${where}.edition_year 不合法`);
  const from = p.page_from, to = p.page_to;
  if (typeof from !== "number" || !Number.isInteger(from) || from < 1 || from > 32767) throw new ChineseError(`${where}.page_from 必须是正整数`);
  if (typeof to !== "number" || !Number.isInteger(to) || to < from || to > 32767) throw new ChineseError(`${where}.page_to 必须是不小于 page_from 的整数`);
  return {
    edition: p.edition, editionYear: year,
    printing: p.printing === undefined || p.printing === null ? null : syntheticText(`${where}.printing`, text(`${where}.printing`, p.printing, 32), synthetic),
    volume: syntheticText(`${where}.volume`, text(`${where}.volume`, p.volume, 32), synthetic),
    chapter: syntheticText(`${where}.chapter`, text(`${where}.chapter`, p.chapter, 64), synthetic), pageFrom: from, pageTo: to,
  };
}

const ABILITY_KEYS = ["ability", "rung", "text_type", "question_type"] as const;

function node(where: string, v: unknown, topicId: string, synthetic: boolean): ChineseNode {
  const o = obj(where, v);
  keys(where, o, ["id", "kind", "name", "scope", "cognitive_levels", "includes", "excludes", "examples", "projections", ...ABILITY_KEYS],
    ["id", "kind", "name"]);
  const id = nodeId(`${where}.id`, o.id, 4);
  if (parentOf(id) !== topicId) throw new ChineseError(`${where}：节点 ${id} 必须挂在所在主题 ${topicId} 下（ID 前缀一致）`);
  if (!(NODE_KINDS as readonly unknown[]).includes(o.kind)) {
    throw new ChineseError(`${where}.kind 只能取 ${NODE_KINDS.join(" / ")}（语文知识节点层：能力点、素材、方法）`);
  }
  const kind = o.kind as NodeKind;
  const levels = o.cognitive_levels === undefined || o.cognitive_levels === null ? [] : o.cognitive_levels;
  if (!Array.isArray(levels) || levels.some((c) => !(COGNITIVE as readonly unknown[]).includes(c))) {
    throw new ChineseError(`${where}.cognitive_levels 只能取 ${COGNITIVE.join(" / ")}`);
  }
  if (new Set(levels).size !== levels.length) throw new ChineseError(`${where}.cognitive_levels 重复`);
  if (kind !== "ability_kp" && levels.length > 0) {
    throw new ChineseError(`${where}：${kind === "material" ? "素材" : "方法"}不计掌握度，不带认知层次（迁移 0011 node_cognitive_levels_kind）`);
  }
  const scope = o.scope ?? "required";
  if (!(SCOPES as readonly unknown[]).includes(scope)) throw new ChineseError(`${where}.scope 只能取 ${SCOPES.join(" / ")}`);
  let ability: AbilityTag | null = null;
  if (kind === "ability_kp") {
    for (const k of ABILITY_KEYS) {
      if (o[k] === undefined || o[k] === null) throw new ChineseError(`${where}：能力点必须写 ${k}（能力点 = 能力 × 文本类型 × 题型，并标明在能力阶梯的哪一级）`);
    }
    ability = { ability: code(`${where}.ability`, o.ability), rung: code(`${where}.rung`, o.rung, RUNG),
      textType: code(`${where}.text_type`, o.text_type), questionType: code(`${where}.question_type`, o.question_type) };
  } else {
    for (const k of ABILITY_KEYS) if (o[k] !== undefined) throw new ChineseError(`${where}：${k} 只用于能力点（ability_kp）`);
  }
  const projections = o.projections === undefined || o.projections === null ? [] : o.projections;
  if (!Array.isArray(projections) || projections.length > 32) throw new ChineseError(`${where}.projections 必须是不超过 32 条的列表`);
  return {
    id, kind, name: syntheticName(where, text(`${where}.name`, o.name, 128), synthetic), scope: scope as string,
    // 按课标四级的固定顺序存（与数学、道法导入器一致）
    cognitiveLevels: COGNITIVE.filter((c) => (levels as string[]).includes(c)),
    includes: syntheticList(`${where}.includes`, textList(`${where}.includes`, o.includes), synthetic),
    excludes: syntheticList(`${where}.excludes`, textList(`${where}.excludes`, o.excludes), synthetic),
    examples: syntheticList(`${where}.examples`, textList(`${where}.examples`, o.examples), synthetic),
    projections: projections.map((p, i) => projection(`${where}.projections[${i}]`, p, synthetic)), ability,
  };
}

function vocab(where: string, v: unknown, synthetic: boolean): Vocab[] {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.length > 64) throw new ChineseError(`${where} 必须是不超过 64 条的列表`);
  return v.map((x, i) => {
    const o = obj(`${where}[${i}]`, x);
    keys(`${where}[${i}]`, o, ["code", "name"], ["code", "name"]);
    return { code: code(`${where}[${i}].code`, o.code), name: syntheticName(`${where}[${i}]`, text(`${where}[${i}].name`, o.name, 64), synthetic) };
  });
}

function ladders(where: string, v: unknown, synthetic: boolean): Ladder[] {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.length > 64) throw new ChineseError(`${where} 必须是不超过 64 条的列表`);
  return v.map((x, i) => {
    const w = `${where}[${i}]`;
    const o = obj(w, x);
    keys(w, o, ["code", "name", "rungs"], ["code", "name", "rungs"]);
    const rungs = o.rungs;
    if (!Array.isArray(rungs) || rungs.length < 2 || rungs.length > 8) throw new ChineseError(`${w}.rungs 必须是 2–8 级（由低到高）`);
    const rs = rungs.map((r, k) => code(`${w}.rungs[${k}]`, r, RUNG));
    if (new Set(rs).size !== rs.length) throw new ChineseError(`${w}.rungs 重复`);
    return { code: code(`${w}.code`, o.code), name: syntheticName(w, text(`${w}.name`, o.name, 64), synthetic), rungs: rs };
  });
}

/** 解析一个领域文件（纯函数；file 只用于报错与报告） */
export function parseDomain(yamlText: string, file = "domain.yaml"): ChineseDomain {
  let raw;
  try {
    raw = parseYamlDoc(yamlText);
  } catch (e) {
    throw new ChineseError(`${file}：${(e as Error).message}`);
  }
  scanForbidden(file, raw);
  keys(file, raw, ["format", "synthetic", "notice", "subject", "domain", "ladders", "text_types", "question_types", "topics", "relations"],
    ["format", "synthetic", "notice", "subject", "domain", "topics"]);
  if (raw.format !== CHINESE_FORMAT) throw new ChineseError(`${file}：format 应为 ${CHINESE_FORMAT}`);
  if (raw.subject !== SUBJECT) throw new ChineseError(`${file}：subject 必须是 ${SUBJECT}（语文，节点前缀 ${PREFIX}）`);
  if (typeof raw.synthetic !== "boolean") throw new ChineseError(`${file}：synthetic 必须是 true 或 false`);
  const synthetic = raw.synthetic;
  if (!synthetic) {
    // KGE-07 第一步全程合成：真实篇目、能力点与页码要等法务（原文与注释的授权）和语文教研确认后才收，届时由人放开这一条
    throw new ChineseError(`${file}：真实内容（synthetic: false）在法务与语文教研确认前一律不收；本步只收合成样例（synthetic: true）`);
  }
  const notice = text(`${file}.notice`, raw.notice, 400);
  if (synthetic && !notice.includes(SYNTHETIC_MARK)) throw new ChineseError(`${file}：合成样例的 notice 必须写明“${SYNTHETIC_MARK}”`);
  const d = obj(`${file}.domain`, raw.domain);
  keys(`${file}.domain`, d, ["id", "name"], ["id", "name"]);
  const domainId = nodeId(`${file}.domain.id`, d.id, 2);
  if (synthetic && !domainId.split(".")[1]!.startsWith(SYNTHETIC_SEGMENT)) {
    throw new ChineseError(`${file}：合成样例的领域 ID 第二段必须以 ${SYNTHETIC_SEGMENT} 开头（如 ${PREFIX}.${SYNTHETIC_SEGMENT}_READ）：${domainId}`);
  }
  const domain = { id: domainId, name: syntheticName(`${file}.domain`, text(`${file}.domain.name`, d.name, 128), synthetic) };

  if (!Array.isArray(raw.topics) || raw.topics.length === 0 || raw.topics.length > 64) {
    throw new ChineseError(`${file}：topics 必须是 1–64 个主题的列表（知识节点必须挂在主题下）`);
  }
  const topics = raw.topics.map((t, i): Topic => {
    const where = `${file}.topics[${i}]`;
    const o = obj(where, t);
    keys(where, o, ["id", "name", "nodes"], ["id", "name"]);
    const id = nodeId(`${where}.id`, o.id, 3);
    if (parentOf(id) !== domainId) throw new ChineseError(`${where}：主题 ${id} 必须挂在领域 ${domainId} 下`);
    const ns = o.nodes === undefined || o.nodes === null ? [] : o.nodes;
    if (!Array.isArray(ns) || ns.length > 256) throw new ChineseError(`${where}.nodes 必须是不超过 256 个节点的列表`);
    return { id, name: syntheticName(where, text(`${where}.name`, o.name, 128), synthetic), nodes: ns.map((n, k) => node(`${where}.nodes[${k}]`, n, id, synthetic)) };
  });

  const rels = raw.relations === undefined || raw.relations === null ? [] : raw.relations;
  if (!Array.isArray(rels) || rels.length > 1024) throw new ChineseError(`${file}：relations 必须是列表`);
  const relations = rels.map((r, i): Relation => {
    const where = `${file}.relations[${i}]`;
    const o = obj(where, r);
    keys(where, o, ["from", "to", "type", "explicit_prerequisite", "basis"], ["from", "to", "type"]);
    if (!(RELATION_TYPES as readonly unknown[]).includes(o.type)) {
      throw new ChineseError(`${where}：边类型只能取 ${RELATION_TYPES.join(" / ")}（只用迁移 0011 已有的边类型表达，不新增类型）`);
    }
    const type = o.type as RelationType;
    if (type === "prerequisite" && o.explicit_prerequisite !== true) {
      throw new ChineseError(`${where}：能力点之间前置关系弱，不照搬数学 DAG；确需强前置由教研显式标注（explicit_prerequisite: true，并写 basis）`);
    }
    if (type !== "prerequisite" && o.explicit_prerequisite !== undefined) throw new ChineseError(`${where}：explicit_prerequisite 只用于显式前置边`);
    if (type === "prerequisite" && (o.basis === undefined || o.basis === null)) throw new ChineseError(`${where}：显式前置边必须写 basis（教研依据）`);
    if (type !== "prerequisite" && o.basis !== undefined && o.basis !== null) throw new ChineseError(`${where}：basis 只用于显式前置边`);
    return {
      from: nodeId(`${where}.from`, o.from, 4), to: nodeId(`${where}.to`, o.to, 4), type,
      basis: type === "prerequisite" ? syntheticText(`${where}.basis`, text(`${where}.basis`, o.basis, 200), synthetic) : null,
    };
  });
  const seen = new Set<string>([domainId]);
  for (const t of topics) {
    for (const id of [t.id, ...t.nodes.map((n) => n.id)]) {
      if (seen.has(id)) throw new ChineseError(`${file}：节点 ${id} 重复`);
      seen.add(id);
    }
  }
  return {
    file, synthetic, notice, domain, ladders: ladders(`${file}.ladders`, raw.ladders, synthetic),
    textTypes: vocab(`${file}.text_types`, raw.text_types, synthetic), questionTypes: vocab(`${file}.question_types`, raw.question_types, synthetic),
    topics, relations,
  };
}

// ---------- 合成一张地图 ----------
export interface MapNodeRow {
  id: string;
  level: "domain" | "topic" | "node";
  parentId: string | null;
  kind: NodeKind | null;
  name: string;
  includes: string[];
  excludes: string[];
  examples: string[];
  scope: string;
  cognitiveLevels: string[];
  ability: AbilityTag | null;
}
export interface ProjectionRow extends Projection { nodeId: string }
/** 写进 kge.knowledge_edge 的边（type 是库里的边类型；relation 是模板里的写法，progression 落成弱前置） */
export interface EdgeRow {
  from: string;
  to: string;
  type: "prerequisite" | "related" | "confusable" | "uses_method";
  strength: "strong" | "weak";
  relation: RelationType;
  evidenceRef: string;
}
export interface ChineseMap {
  synthetic: boolean;
  files: string[];
  nodes: MapNodeRow[]; // 按 ID 码点排序
  edges: EdgeRow[]; // 按（from, to, type）排序
  projections: ProjectionRow[];
  ladders: Ladder[];
  textTypes: Vocab[];
  questionTypes: Vocab[];
}

const pairKey = (a: string, b: string) => (a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`);

/** 同编码的定义在各文件之间必须一致（能力阶梯、文本类型、题型可以在多个领域文件里重复写） */
function mergeVocab<T extends { code: string }>(what: string, lists: T[][]): T[] {
  const by = new Map<string, T>();
  for (const x of lists.flat()) {
    const prev = by.get(x.code);
    if (prev && JSON.stringify(prev) !== JSON.stringify(x)) throw new ChineseError(`${what} ${x.code} 在不同文件里的定义不一致`);
    by.set(x.code, x);
  }
  return [...by.values()].sort((a, b) => cmp(a.code, b.code));
}

const KIND_ZH: Record<NodeKind, string> = { ability_kp: "能力点", material: "素材", method: "方法" };

/** 把若干领域文件合成一张地图（同一地图版本），做跨文件的校验 */
export function buildMap(domains: ChineseDomain[]): ChineseMap {
  if (domains.length === 0) throw new ChineseError("至少要一个领域文件");
  if (new Set(domains.map((d) => d.synthetic)).size > 1) throw new ChineseError("合成样例与真实内容不能导入同一个地图版本");
  const ladderList = mergeVocab("能力阶梯", domains.map((d) => d.ladders));
  const textTypes = mergeVocab("文本类型", domains.map((d) => d.textTypes));
  const questionTypes = mergeVocab("题型", domains.map((d) => d.questionTypes));
  const ladderBy = new Map(ladderList.map((l) => [l.code, l]));
  const nodes: MapNodeRow[] = [];
  const projections: ProjectionRow[] = [];
  const domainIds = new Set<string>();
  const combos = new Map<string, string>();
  for (const d of domains) {
    if (domainIds.has(d.domain.id)) throw new ChineseError(`领域 ${d.domain.id} 在两个文件里出现`);
    domainIds.add(d.domain.id);
    nodes.push({ id: d.domain.id, level: "domain", parentId: null, kind: null, name: d.domain.name, includes: [], excludes: [], examples: [],
      scope: "required", cognitiveLevels: [], ability: null });
    for (const t of d.topics) {
      nodes.push({ id: t.id, level: "topic", parentId: d.domain.id, kind: null, name: t.name, includes: [], excludes: [], examples: [],
        scope: "required", cognitiveLevels: [], ability: null });
      for (const n of t.nodes) {
        if (n.ability) {
          const a = n.ability;
          const ladder = ladderBy.get(a.ability);
          if (!ladder) throw new ChineseError(`能力点 ${n.id}：能力 ${a.ability} 没有在 ladders 里定义`);
          if (!ladder.rungs.includes(a.rung)) throw new ChineseError(`能力点 ${n.id}：能力阶梯 ${a.ability} 没有 ${a.rung} 这一级（${ladder.rungs.join(" < ")}）`);
          if (!textTypes.some((x) => x.code === a.textType)) throw new ChineseError(`能力点 ${n.id}：文本类型 ${a.textType} 没有在 text_types 里定义`);
          if (!questionTypes.some((x) => x.code === a.questionType)) throw new ChineseError(`能力点 ${n.id}：题型 ${a.questionType} 没有在 question_types 里定义`);
          const k = `${a.ability}|${a.rung}|${a.textType}|${a.questionType}`;
          const dup = combos.get(k);
          if (dup) throw new ChineseError(`能力点 ${n.id} 与 ${dup} 的“能力 × 级 × 文本类型 × 题型”相同（${k.replace(/\|/g, " × ")}），应合并为一个能力点`);
          combos.set(k, n.id);
        }
        nodes.push({ id: n.id, level: "node", parentId: t.id, kind: n.kind, name: n.name, includes: n.includes, excludes: n.excludes,
          examples: n.examples, scope: n.scope, cognitiveLevels: n.cognitiveLevels, ability: n.ability });
        for (const p of n.projections) projections.push({ ...p, nodeId: n.id });
      }
    }
  }
  const byId = new Map<string, MapNodeRow>();
  for (const n of nodes) {
    if (byId.has(n.id)) throw new ChineseError(`节点 ID ${n.id} 在文件之间重复`);
    if (n.id.split(".")[0] !== PREFIX) throw new ChineseError(`节点 ${n.id} 不在 ${PREFIX} 命名空间`);
    byId.set(n.id, n);
  }

  const edges: EdgeRow[] = [];
  const undirected = new Map<string, RelationType>();
  const directed = new Map<string, RelationType>(); // 前置图（progression 与 prerequisite）上的一对节点，不分方向
  const methodUse = new Set<string>();
  for (const r of domains.flatMap((d) => d.relations)) {
    const a = byId.get(r.from), b = byId.get(r.to);
    if (!a || !b || a.level !== "node" || b.level !== "node") throw new ChineseError(`边 ${r.from} → ${r.to}：两端都必须是本地图里的知识节点层节点`);
    if (r.from === r.to) throw new ChineseError(`边 ${r.from} → ${r.to}：不能自己连自己`);
    const ka = a.kind!, kb = b.kind!;
    if (r.type === "progression" || r.type === "prerequisite") {
      if (ka === "material" || kb === "material") {
        throw new ChineseError(`边 ${r.from} → ${r.to}：素材节点不进前置依赖图（${r.type}），素材只允许 related / confusable 关联类边（KGE 分册语文段；validate_map_version 的 edge_kind_mismatch）`);
      }
      if (ka === "method" || kb === "method") throw new ChineseError(`边 ${r.from} → ${r.to}：方法节点不进前置依赖图，能力点用 uses_method 指向方法`);
      const k = pairKey(r.from, r.to);
      const prev = directed.get(k);
      if (prev) throw new ChineseError(`边 ${r.from} → ${r.to}：这一对能力点已经有一条 ${prev} 边（层次递进与显式前置在库里都是 prerequisite，只能写一条）`);
      directed.set(k, r.type);
      if (r.type === "progression") {
        const x = a.ability!, y = b.ability!;
        const ladder = ladderBy.get(x.ability)!;
        if (x.ability !== y.ability || ladder.rungs.indexOf(y.rung) !== ladder.rungs.indexOf(x.rung) + 1) {
          throw new ChineseError(`层次递进边 ${r.from} → ${r.to}：只连同一能力阶梯的相邻两级（低一级 → 高一级），`
            + `这里是 ${x.ability}.${x.rung} → ${y.ability}.${y.rung}`);
        }
        if (x.textType !== y.textType || x.questionType !== y.questionType) {
          throw new ChineseError(`层次递进边 ${r.from} → ${r.to}：两端的文本类型与题型必须相同（同一“能力 × 文本类型 × 题型”的相邻两级），`
            + `这里是 ${x.textType} × ${x.questionType} → ${y.textType} × ${y.questionType}`);
        }
        edges.push({ from: r.from, to: r.to, type: "prerequisite", strength: "weak", relation: r.type,
          evidenceRef: `chinese:progression:${x.ability}:${x.rung}->${y.rung}` });
      } else {
        edges.push({ from: r.from, to: r.to, type: "prerequisite", strength: "strong", relation: r.type, evidenceRef: `explicit:${r.basis!}`.slice(0, 256) });
      }
    } else if (r.type === "uses_method") {
      if (ka !== "ability_kp" || kb !== "method") throw new ChineseError(`边 ${r.from} → ${r.to}：uses_method 的起点必须是能力点、终点必须是方法`);
      const k = `${r.from}\u0000${r.to}`;
      if (methodUse.has(k)) throw new ChineseError(`边 ${r.from} → ${r.to}：uses_method 重复`);
      methodUse.add(k);
      edges.push({ from: r.from, to: r.to, type: "uses_method", strength: "strong", relation: r.type, evidenceRef: "chinese:uses_method" });
    } else {
      if (r.type === "confusable" && (ka !== kb || ka === "method")) {
        throw new ChineseError(`易混边 ${r.from} — ${r.to}：只连同类的素材或同类的能力点（这里是${KIND_ZH[ka]}与${KIND_ZH[kb]}）`);
      }
      const k = pairKey(r.from, r.to);
      const prev = undirected.get(k);
      if (prev) throw new ChineseError(`关联边 ${r.from} — ${r.to} 重复（关联与易混无方向，一对节点只写一种、一条；已有 ${prev}）`);
      undirected.set(k, r.type);
      edges.push({ from: r.from, to: r.to, type: r.type, strength: "strong", relation: r.type, evidenceRef: `chinese:${r.type}` });
    }
  }
  // 前置图：强前置与层次递进（弱前置）合在一起，与库里 validate_map_version 的环、传递边同口径（它不分强弱）
  const abilityIds = nodes.filter((n) => n.kind === "ability_kp").map((n) => n.id).sort(cmp);
  const pre: Edge[] = edges.filter((e) => e.type === "prerequisite").map((e) => [e.from, e.to] as const);
  const cycles = findCycles(abilityIds, pre);
  if (cycles.length > 0) throw new ChineseError(`前置图成环：${cycles.map((c) => c.join(" ↔ ")).join("；")}`);
  const trans = findTransitiveEdges(abilityIds, pre);
  if (trans.length > 0) throw new ChineseError(`前置图里有可由其他前置路径推出的传递冗余边：${trans.map(([x, y]) => `${x} → ${y}`).join("；")}`);
  // 孤点：知识节点层没有任何边、也没有教材投影（与 validate_map_version 的 orphan 同口径；本工具不写题目标签）
  const touched = new Set([...edges.flatMap((e) => [e.from, e.to]), ...projections.map((p) => p.nodeId)]);
  const orphans = nodes.filter((n) => n.level === "node" && !touched.has(n.id)).map((n) => n.id).sort(cmp);
  if (orphans.length > 0) throw new ChineseError(`孤点（没有任何边，也没有教材投影）：${orphans.join("、")}`);

  nodes.sort((a, b) => cmp(a.id, b.id));
  edges.sort((a, b) => cmp(a.from, b.from) || cmp(a.to, b.to) || cmp(a.type, b.type));
  projections.sort((a, b) => cmp(a.nodeId, b.nodeId) || cmp(a.edition, b.edition) || cmp(a.volume, b.volume) || cmp(a.chapter, b.chapter)
    || a.pageFrom - b.pageFrom || a.pageTo - b.pageTo);
  for (let i = 1; i < projections.length; i++) {
    const a = projections[i - 1]!, b = projections[i]!;
    if (a.nodeId === b.nodeId && a.edition === b.edition && a.volume === b.volume && a.chapter === b.chapter && a.pageFrom === b.pageFrom
      && a.pageTo === b.pageTo) {
      throw new ChineseError(`节点 ${a.nodeId} 在 ${a.edition} ${a.volume} ${a.chapter} 第 ${a.pageFrom}–${a.pageTo} 页的投影重复`);
    }
  }
  return { synthetic: domains.some((d) => d.synthetic), files: domains.map((d) => d.file), nodes, edges, projections, ladders: ladderList,
    textTypes, questionTypes };
}
