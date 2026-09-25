// 服务端只读内容包（spec 范围第 3 条；待人确认 #2 方案 a）。
// 内容包由 scripts/build-content-pack.ts 从 legacy 生成；答案只在本进程内使用，任何接口都不返回。
import { readFileSync } from "node:fs";
import type { Question, RootCause } from "./core/types.ts";
import { ROOT_CAUSES } from "./core/types.ts";

export const PACK_FORMAT = "aya-content-pack/1";

export type Admission = "admitted" | "pending_review" | "source_unverified" | "rejected";

export interface PackItem extends Question {
  admission: Admission;
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
  source_verified: boolean;
  nodes: { id: string; kind: string; cluster: string }[];
  items: PackItem[];
  traps: { code: string; rootCause: RootCause }[];
}

export interface LoadedPack {
  pack: ContentPack;
  itemById: Map<string, PackItem>;
  trapByCode: Map<string, RootCause>;
  /** SourceVersion 里的 items 段，例如 `items:aya-pack@0.1.0-0ed2fe4` */
  itemsSourceVersion: string;
}

const REVIEW_ORDER = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5"] as const;

/** 准入规则（aya adapter-design §3.1）：审核等级 ≥ Q2 且来源已核才算 admitted。 */
export function admissionOf(reviewStatus: Question["reviewStatus"], sourceVerified: boolean): Admission {
  if (!sourceVerified) return "source_unverified";
  return REVIEW_ORDER.indexOf(reviewStatus) >= 2 ? "admitted" : "pending_review";
}

const LOCAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSION_PART = /^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$/;

/** 检查内容包的引用完整性；不通过就拒绝启动，而不是运行中才发现。 */
export function indexPack(pack: ContentPack): LoadedPack {
  const problems: string[] = [];
  if (pack.format !== PACK_FORMAT) problems.push(`format 应为 ${PACK_FORMAT}`);
  if (!VERSION_PART.test(pack.pack_version)) problems.push("pack_version 不符合 SourceVersion 的版本格式");
  const nodeIds = new Set(pack.nodes.map((n) => n.id));
  const trapByCode = new Map<string, RootCause>();
  for (const t of pack.traps) {
    if (!(ROOT_CAUSES as readonly string[]).includes(t.rootCause)) problems.push(`陷阱 ${t.code} 的根因未知`);
    trapByCode.set(t.code, t.rootCause);
  }
  const itemById = new Map<string, PackItem>();
  for (const it of pack.items) {
    if (!LOCAL_ID.test(it.id)) problems.push(`题目 ID ${it.id} 不符合平台格式`);
    if (itemById.has(it.id)) problems.push(`题目 ${it.id} 重复`);
    if (it.mainNodes.length === 0) problems.push(`题目 ${it.id} 没有主测节点`);
    for (const n of it.mainNodes) if (!nodeIds.has(n)) problems.push(`题目 ${it.id} 的主测节点 ${n} 未登记`);
    const traps = [...(it.options ?? []).flatMap((o) => o.traps ?? []), ...(it.wrongAnswerTraps ?? []).flatMap((w) => w.traps)];
    for (const c of traps) if (!trapByCode.has(c)) problems.push(`题目 ${it.id} 引用了未知陷阱 ${c}`);
    if (it.admission !== admissionOf(it.reviewStatus, pack.source_verified)) problems.push(`题目 ${it.id} 的准入状态与规则不符`);
    itemById.set(it.id, it);
  }
  for (const n of pack.nodes) if (!LOCAL_ID.test(n.id)) problems.push(`节点 ID ${n.id} 不符合平台格式`);
  if (problems.length) throw new Error(`内容包不合格：\n  ${problems.slice(0, 20).join("\n  ")}`);
  return { pack, itemById, trapByCode, itemsSourceVersion: `items:${pack.pack_name}@${pack.pack_version}` };
}

export const DEFAULT_PACK_PATH = new URL("../content/pack.json", import.meta.url);

export function loadPack(path: string | URL = DEFAULT_PACK_PATH): LoadedPack {
  return indexPack(JSON.parse(readFileSync(path, "utf8")) as ContentPack);
}
