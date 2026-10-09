// 服务端只读内容包（specs/adapter-aya-sprint1 范围第 3 条；待人确认 #2 方案 a）。
// 内容包由 scripts/build-content-pack.ts 从 legacy 生成；答案只在本进程内使用，任何接口都不返回。
//
// 格式 2（specs/adapter-aya-methods-sprint4 设计要点第 0 节）：题目加 usage、estMinutes、hintPath，节点加 board，
// 另加板块中考占比（Q1 推导值，待教研校准）与“阿鸦节点 → KGE 知识节点”对应表（KGE-01 导入阿鸦图谱前为空）。
// 仍然没有题干、解析与前置关系（前置关系由请求里的 node_path 提供，知识结构以 KGE 为准）。
// 格式 3（specs/release-gate-sprint4 设计要点第 7 节、待人确认 #9）：去掉整包的 source_verified，改为逐题来源
// （source：来源编号、来源类型、授权状态）与准入依据（admission_review），准入按平台导出的准入决定文件重建
// （src/pack/admission.ts）；另加 admission_decisions（所用决定文件的摘要）与 hint_scan（提示原文的 C9 词表检查记录）。
// PLT-12（契约 1.1.0 present_item）：格式 3 的题目可选带 stem、option_content（受限富文本，src/rich-text.ts）。真实内容包仍然没有题干
// （版权未核，spec“不做”），present_item 对这些题返回 content_state = withheld；只有测试与演练用的合成内容包带题面。
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { Question, RootCause } from "./core/types.ts";
import { ROOT_CAUSES } from "./core/types.ts";
import { admissionProblems, type AdmissionDecisionsRecord, type AdmissionReview, type ItemSource } from "./pack/admission.ts";
import type { HintScanRecord } from "./pack/hint-scan.ts";
import { hintRevealsAnswer } from "./methods/teach.ts";
import { MAX_OPTION_RUNS, MAX_TOTAL_CHARS, richTextProblems, runsChars, runsPlain, runsProblems, type RichInline, type RichParagraph } from "./rich-text.ts";

export const PACK_FORMAT = "aya-content-pack/3";

export type Admission = "admitted" | "pending_review" | "source_unverified" | "rejected";

/** 题目用途（legacy QuestionUsage）：D 诊断、P 错误探针、L 学习训练、T 迁移、R 延迟复测 */
export const ITEM_USAGES = ["D", "P", "L", "T", "R"] as const;
export type ItemUsage = (typeof ITEM_USAGES)[number];

/** 提示阶梯最多 7 级（0–6，阿鸦规格书 §3.1）；索引即等级 */
export const MAX_HINT_PATH = 7;

export interface PackItem extends Question {
  /** 准入副本：平台题目表准入列的快照，由准入决定文件重建；没有决定时为 source_unverified */
  admission: Admission;
  /** 逐题来源（只有编号与枚举，材料本身不进仓库，spec 待人确认 #15） */
  source: ItemSource;
  /** 准入依据（学科审核等级、审核记录编号）；只在准入决定文件里有这道题时出现 */
  admission_review?: AdmissionReview;
  usage: ItemUsage;
  /** 预估用时（分钟，legacy estMinutes） */
  estMinutes: number;
  /** 提示阶梯原文（legacy hintPath，索引即等级）。只在 teach(hint) 里按等级取一条，不整体返回 */
  hintPath: string[];
  /**
   * 题面（受限富文本，契约 1.1.0 present_item，PLT-12）。可选：真实内容包至今不收录题干（版权未核，spec“不做”），
   * 没有这个字段的题 present_item 返回 content_state = withheld。只有测试与演练用的合成内容包会带（AYA_CONTENT_PACK）。
   */
  stem?: RichParagraph[];
  /** 选项内容（选项键 → 片段）。有 stem 的选择题每个选项都要有；没有 stem 的题不得有 */
  option_content?: Record<string, RichInline[]>;
}

export interface PackNode {
  id: string;
  kind: string;
  cluster: string;
  /** 十大知识板块之一（legacy boardOfNode） */
  board: string;
}

/** 板块中考分值占比：legacy content/examweights.ts BOARD_EXAM_SHARE，产品推导值（Q1），待教研校准（spec 待人确认 #6） */
export interface BoardExamShare {
  status: "q1_derived_uncalibrated";
  shares: Record<string, number>;
}

/**
 * 阿鸦节点 → KGE 知识节点（spec 待人确认 #2 默认）：内容来自平台导出的 kge.skill_link，带地图版本。
 * KGE-01 导入阿鸦图谱之前为空（map_ref = null、links = []），此时 plan 不使用 node_path。
 */
export interface SkillLinks {
  /** 地图版本，写进 source_version 的 graph 段，例如 `aya-map@1.0.0` */
  map_ref: string | null;
  links: { skill_node: string; node_id: string }[];
}

export interface ContentPack {
  format: typeof PACK_FORMAT;
  pack_name: string;
  pack_version: string;
  legacy_commit: string;
  curriculum: {
    region: string;
    exam_year: number;
    grade: number;
    textbook_version: string;
    subject: "aya";
    content_version: string;
  };
  /** 所用准入决定文件（格式、导出批次、导出时刻、摘要、条数）；没有决定文件时为 null */
  admission_decisions: AdmissionDecisionsRecord | null;
  nodes: PackNode[];
  items: PackItem[];
  traps: { code: string; rootCause: RootCause }[];
  board_exam_share: BoardExamShare;
  skill_links: SkillLinks;
  /** 提示原文的 C9 词表检查记录（src/pack/hint-scan.ts） */
  hint_scan: HintScanRecord;
}

export interface LoadedPack {
  pack: ContentPack;
  itemById: Map<string, PackItem>;
  nodeById: Map<string, PackNode>;
  /** 主测节点 → 题目（按题号排序） */
  itemsByNode: Map<string, PackItem[]>;
  trapByCode: Map<string, RootCause>;
  /** 阿鸦节点 → KGE 知识节点（对应表为空时为空） */
  knowledgeNodesOf: Map<string, string[]>;
  /** 内容包版本：pack_version；用了替代包或题面叠加文件时再接 `-<sha256 前 8 位>` */
  versionTag: string;
  /** SourceVersion 里的 items 段，例如 `items:aya-pack@0.1.0-0ed2fe4` */
  itemsSourceVersion: string;
}

const LOCAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
/** 与 knowledge-service.v0.9.yaml NodeId 同一正则 */
export const KGE_NODE_ID = /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/;
/** 与契约 SourceVersion 的一段同一写法（名称@版本），类别固定为 graph */
const MAP_REF = /^[a-z0-9][a-z0-9\-]{0,47}@\d+\.\d+\.\d+$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;

/** 题面字段（stem、option_content，PLT-12）的问题：受限富文本、与选项键一一对应、没有题干就不能有选项内容 */
export function presentationProblems(it: PackItem): string[] {
  const where = `题目 ${it.id}`;
  const out: string[] = [];
  if (it.stem !== undefined) out.push(...richTextProblems(it.stem, `${where} 的题干`));
  const oc = it.option_content;
  if (oc === undefined) {
    if (it.stem !== undefined && it.format !== "fill") out.push(`${where} 有题干却没有选项内容`);
    return out;
  }
  if (it.stem === undefined) return [...out, `${where} 没有题干却有选项内容`];
  if (it.format === "fill" || typeof oc !== "object" || oc === null) return [...out, `${where} 的选项内容不合格`];
  const keys = (it.options ?? []).map((o) => o.key).sort();
  if (JSON.stringify(Object.keys(oc).sort()) !== JSON.stringify(keys)) out.push(`${where} 的选项内容与选项键不一致`);
  for (const [k, runs] of Object.entries(oc)) out.push(...runsProblems(runs, `${where} 选项 ${k}`, MAX_OPTION_RUNS));
  return out;
}

/**
 * 题面里像是写出答案或解析的词（选择题的答案是选项键，不能按答案原文查，改查这些标记）。
 * “解析式”是数学名词（求解析式），不算解析标记
 */
export const ANSWER_MARKERS = /答案|故选|正确选项|正确答案|解析(?!式)|【解】|参考答/u;

/**
 * 题面的答案词扫描与总字数（审查建议 12、15；做法沿用 teach 的提示防泄露 hintRevealsAnswer）：
 * 填空题的题干与选项里不得出现标准答案原文（去掉空白与 `$` 后两个字符以上的答案按子串查；单个字符的答案——
 * 例如数字 2——在题干里本来就常见（题号、系数），不查）；任何题的题面不得出现“答案、故选、解析”之类的标记；
 * 题干与选项全部片段合计不超过 8000 字。只在格式已合格（presentationProblems 为空）时调用。
 */
export function presentationLeakProblems(it: PackItem): string[] {
  if (it.stem === undefined) return [];
  const where = `题目 ${it.id}`;
  const parts = [...it.stem.map((p) => p.runs), ...Object.values(it.option_content ?? {})];
  const out: string[] = [];
  if (parts.reduce((n, runs) => n + runsChars(runs), 0) > MAX_TOTAL_CHARS) out.push(`${where} 的题面超过 ${MAX_TOTAL_CHARS} 字`);
  const plain = parts.map(runsPlain);
  const answers = it.answer.filter((a) => a.replace(/[\s$]/g, "").length >= 2);
  if (it.format === "fill" && plain.some((s) => hintRevealsAnswer(s, answers))) out.push(`${where} 的题面里出现了标准答案`);
  if (plain.some((s) => ANSWER_MARKERS.test(s))) out.push(`${where} 的题面里有答案或解析标记`);
  return out;
}

/** 检查内容包的引用完整性；不通过就拒绝启动，而不是运行中才发现。 */
export function indexPack(pack: ContentPack, altDigest?: string): LoadedPack {
  const problems: string[] = [];
  if (pack.format !== PACK_FORMAT) problems.push(`format 应为 ${PACK_FORMAT}`);
  if (!VERSION_PART.test(pack.pack_version)) problems.push("pack_version 不符合 SourceVersion 的版本格式");
  const c = pack.curriculum;
  if (!Number.isInteger(c.exam_year) || !Number.isInteger(c.grade)) problems.push("curriculum 的 exam_year、grade 必须是整数");
  if (!/^[a-z][a-z0-9\-]{1,31}$/.test(c.region) || !/^[a-z0-9][a-z0-9\-]{1,31}$/.test(c.textbook_version) || !/^\d+\.\d+\.\d+$/.test(c.content_version)) {
    problems.push("curriculum 的六段标识不符合契约 ContentVersion 格式");
  }
  const nodeById = new Map(pack.nodes.map((n) => [n.id, n]));
  const nodeIds = new Set(nodeById.keys());
  const share = pack.board_exam_share;
  if (!share || share.status !== "q1_derived_uncalibrated") problems.push("board_exam_share 必须标注 q1_derived_uncalibrated");
  for (const [b, v] of Object.entries(share?.shares ?? {})) {
    if (!(typeof v === "number" && v >= 0 && v <= 1)) problems.push(`板块 ${b} 的中考占比不在 0–1`);
  }
  for (const n of pack.nodes) if (!(n.board in (share?.shares ?? {}))) problems.push(`节点 ${n.id} 的板块 ${n.board} 没有中考占比`);
  const links = pack.skill_links;
  const knowledgeNodesOf = new Map<string, string[]>();
  if (!links || !Array.isArray(links.links)) problems.push("缺少 skill_links");
  else {
    if (links.map_ref !== null && !MAP_REF.test(links.map_ref)) problems.push("skill_links.map_ref 不符合 名称@x.y.z");
    if (links.links.length > 0 && links.map_ref === null) problems.push("skill_links 有对应关系却没有地图版本");
    for (const l of links.links) {
      if (!nodeIds.has(l.skill_node)) problems.push(`对应表里的阿鸦节点 ${l.skill_node} 未登记`);
      if (!KGE_NODE_ID.test(l.node_id)) problems.push(`对应表里的知识节点 ${l.node_id} 不符合 KGE NodeId`);
      const list = knowledgeNodesOf.get(l.skill_node) ?? [];
      if (!list.includes(l.node_id)) list.push(l.node_id);
      knowledgeNodesOf.set(l.skill_node, list);
    }
  }
  const trapByCode = new Map<string, RootCause>();
  for (const t of pack.traps) {
    if (!(ROOT_CAUSES as readonly string[]).includes(t.rootCause)) problems.push(`陷阱 ${t.code} 的根因未知`);
    trapByCode.set(t.code, t.rootCause);
  }
  const itemById = new Map<string, PackItem>();
  let reviewed = 0;
  for (const it of pack.items) {
    if (!LOCAL_ID.test(it.id)) problems.push(`题目 ID ${it.id} 不符合平台格式`);
    if (itemById.has(it.id)) problems.push(`题目 ${it.id} 重复`);
    if (it.mainNodes.length === 0) problems.push(`题目 ${it.id} 没有主测节点`);
    for (const n of it.mainNodes) if (!nodeIds.has(n)) problems.push(`题目 ${it.id} 的主测节点 ${n} 未登记`);
    const traps = [...(it.options ?? []).flatMap((o) => o.traps ?? []), ...(it.wrongAnswerTraps ?? []).flatMap((w) => w.traps)];
    for (const c of traps) if (!trapByCode.has(c)) problems.push(`题目 ${it.id} 引用了未知陷阱 ${c}`);
    if (!it.source || typeof it.source !== "object") problems.push(`题目 ${it.id} 没有来源记录`);
    else problems.push(...admissionProblems(it.id, it.admission, it.source, it.admission_review));
    if (it.admission_review) reviewed++;
    if (!(ITEM_USAGES as readonly string[]).includes(it.usage)) problems.push(`题目 ${it.id} 的用途未知`);
    if (!(Number.isInteger(it.estMinutes) && it.estMinutes >= 1 && it.estMinutes <= 120)) problems.push(`题目 ${it.id} 的预估用时不是 1–120 的整数`);
    if (!Array.isArray(it.hintPath) || it.hintPath.length === 0 || it.hintPath.length > MAX_HINT_PATH || !it.hintPath.every((h) => typeof h === "string" && h.trim() !== "" && h.length <= 4000)) {
      problems.push(`题目 ${it.id} 的提示阶梯不合格`);
    }
    const shape = presentationProblems(it);
    problems.push(...(shape.length ? shape : presentationLeakProblems(it)));
    itemById.set(it.id, it);
  }
  const itemsByNode = new Map<string, PackItem[]>();
  for (const it of [...itemById.values()].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
    for (const n of new Set(it.mainNodes)) {
      const list = itemsByNode.get(n) ?? [];
      list.push(it);
      itemsByNode.set(n, list);
    }
  }
  for (const n of pack.nodes) if (!LOCAL_ID.test(n.id)) problems.push(`节点 ID ${n.id} 不符合平台格式`);
  // 准入依据只能来自决定文件：没有决定文件时不应有任何依据；有时条数必须一致
  const dec = pack.admission_decisions;
  if (dec === undefined) problems.push("缺少 admission_decisions（没有决定文件时为 null）");
  else if (dec === null ? reviewed > 0 : dec.decisions !== reviewed || !/^sha256:[0-9a-f]{64}$/.test(dec.digest)) {
    problems.push("题目的准入依据与 admission_decisions 不一致");
  }
  const scan = pack.hint_scan;
  if (!scan || scan.check !== "C9" || (scan.mode !== "strict" && scan.mode !== "flag") || !Array.isArray(scan.flagged_items)) {
    problems.push("缺少提示原文的 C9 词表检查记录（hint_scan）");
  } else {
    for (const id of scan.flagged_items) if (!itemById.has(id)) problems.push(`hint_scan 标记的题目 ${id} 不在内容包里`);
    if (scan.mode === "strict" && scan.flagged_items.length > 0) problems.push("hint_scan 为严格模式却有标记的题目");
  }
  if (altDigest !== undefined && !/^[0-9a-f]{8}$/.test(altDigest)) problems.push("替代内容包的摘要不是 8 位十六进制");
  const versionTag = altDigest ? `${pack.pack_version}-${altDigest}` : pack.pack_version;
  if (!VERSION_PART.test(versionTag)) problems.push("pack_version 加替代包摘要后超过 SourceVersion 的版本格式");
  if (problems.length) throw new Error(`内容包不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return {
    pack,
    itemById,
    nodeById,
    itemsByNode,
    trapByCode,
    knowledgeNodesOf,
    versionTag,
    itemsSourceVersion: `items:${pack.pack_name}@${versionTag}`,
  };
}

export const DEFAULT_PACK_PATH = new URL("../content/pack.json", import.meta.url);
/** 换一份内容包（只给测试与演练：带合成题面的内容包，PLT-12）。不设时用 DEFAULT_PACK_PATH */
export const PACK_PATH_ENV = "AYA_CONTENT_PACK";

export function loadPack(path: string | URL = DEFAULT_PACK_PATH): LoadedPack {
  return indexPack(JSON.parse(readFileSync(path, "utf8")) as ContentPack);
}

/** 题面叠加文件：给内容包里已有的题加合成题面（只给测试与演练，1.1.0 一致性测试用它跑 complete 题面） */
export const STEM_OVERLAY_FORMAT = "aya-stem-overlay/1";
export const STEM_OVERLAY_ENV = "AYA_STEM_OVERLAY";

export interface StemOverlay {
  format: typeof STEM_OVERLAY_FORMAT;
  note?: string;
  items: Record<string, { stem: RichParagraph[]; option_content?: Record<string, RichInline[]> }>;
}

/** 把叠加文件里的题面放到对应题目上（不改输入）。叠加的题必须在内容包里，且原来没有题面 */
export function applyStemOverlay(pack: ContentPack, overlay: StemOverlay): ContentPack {
  if (overlay?.format !== STEM_OVERLAY_FORMAT || typeof overlay.items !== "object" || overlay.items === null) {
    throw new Error(`题面叠加文件不合格：format 应为 ${STEM_OVERLAY_FORMAT}`);
  }
  const ids = new Set(pack.items.map((i) => i.id));
  const unknown = Object.keys(overlay.items).filter((id) => !ids.has(id));
  if (unknown.length) throw new Error(`题面叠加文件里有内容包没有的题：${unknown.slice(0, 5).join(", ")}`);
  return {
    ...pack,
    items: pack.items.map((it) => {
      const o = overlay.items[it.id];
      if (!o) return it;
      if (it.stem !== undefined) throw new Error(`题目 ${it.id} 已有题面，叠加文件不能覆盖`);
      return { ...it, stem: o.stem, ...(o.option_content !== undefined ? { option_content: o.option_content } : {}) };
    }),
  };
}

export interface PackSource {
  /** 替代内容包（AYA_CONTENT_PACK / --content-pack） */
  packPath?: string;
  /** 题面叠加文件（AYA_STEM_OVERLAY / --stem-overlay） */
  overlayPath?: string;
  /** 候选版开关是否打开（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 / --allow-candidate-contract） */
  candidateAllowed: boolean;
}

/**
 * 启动时选内容包（审查阻断 3）。替代内容包与题面叠加文件只给测试与演练：只有候选版开关打开时才读，
 * 开关没开却设了就拒绝启动（抛错）。用了替代包或叠加文件时，版本号带上这些文件的 sha256 前 8 位
 * （items 段与题面 item_version 都带），平台按（item_ref, item_version）缓存时不会与真实内容包混淆。
 */
export function loadPackFrom(src: PackSource): LoadedPack {
  if (!src.packPath && !src.overlayPath) return loadPack();
  if (!src.candidateAllowed) {
    throw new Error(`替代内容包（${PACK_PATH_ENV}）与题面叠加文件（${STEM_OVERLAY_ENV}）只给测试与演练，需同时打开候选版开关（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）`);
  }
  const hash = createHash("sha256");
  const packText = readFileSync(src.packPath ?? DEFAULT_PACK_PATH, "utf8");
  if (src.packPath) hash.update("pack\0").update(packText);
  let pack = JSON.parse(packText) as ContentPack;
  if (src.overlayPath) {
    const overlayText = readFileSync(src.overlayPath, "utf8");
    hash.update("overlay\0").update(overlayText);
    pack = applyStemOverlay(pack, JSON.parse(overlayText) as StemOverlay);
  }
  return indexPack(pack, hash.digest("hex").slice(0, 8));
}
