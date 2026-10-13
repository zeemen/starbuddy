// 题目去重与题目族候选（KGE-08 范围 1）。只出候选：完全重复组 → 规范题候选；近似对 → 人工确认；
// 模板相同且“恰好一个主节点、主节点相同” → 族候选；跨主节点、多主节点或没有主节点的同模板题只列“待看”，不成族。
// 任何归并都不自动发生。
import type { DedupeConfig } from "./config.ts";
import { hammingSplit, jaccard, ngrams, sha256, simhash64, split64, type Split64 } from "./fingerprint.ts";
import type { Item } from "./input.ts";
import { canonicalText, templateText } from "./normalize.ts";

export interface Fingerprint {
  id: string;
  /** 去重、排序后的主节点（坐标系见报告 node_system） */
  mainNodes: string[];
  paperId?: string;
  seq?: number;
  /** 规范文本的 SHA-256 */
  sha: string;
  /** 模板文本的 SHA-256 */
  templateSha: string;
  simhash: bigint;
  split: Split64;
  grams: ReadonlySet<string>;
}

export class DedupeError extends Error {}

export const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
/** 主节点集合的键；输入已在 fingerprintItems 里去重并排序（唯一的规范化处） */
export const nodeKey = (nodes: readonly string[]): string => nodes.join("|");

function pushTo<K, V>(m: Map<K, V[]>, k: K, v: V): void {
  const l = m.get(k);
  if (l) l.push(v);
  else m.set(k, [v]);
}

export function fingerprintItems(items: readonly Item[], cfg: DedupeConfig): Fingerprint[] {
  return items
    .map((it): Fingerprint => {
      const canon = canonicalText(it.text);
      const grams = ngrams(canon, cfg.ngram);
      const simhash = simhash64(grams);
      return {
        id: it.id, mainNodes: [...new Set(it.mainNodes)].sort(cmp), paperId: it.paperId, seq: it.seq,
        sha: sha256(canon), templateSha: sha256(templateText(it.text, cfg.mask)), simhash, split: split64(simhash), grams: new Set(grams),
      };
    })
    .sort((a, b) => cmp(a.id, b.id));
}

/** 按规范文本分组（只算一次，各处共用）：代表（规范题候选）取组内 ID 最小者 */
export interface Reps {
  bySha: Map<string, Fingerprint[]>;
  /** 各组代表，按 ID 排序 */
  list: Fingerprint[];
  /** 题目 ID → 所在组代表的 ID */
  repOf: Map<string, string>;
}

export function representatives(fps: readonly Fingerprint[]): Reps {
  const bySha = new Map<string, Fingerprint[]>();
  for (const f of fps) pushTo(bySha, f.sha, f);
  const repOf = new Map<string, string>();
  for (const l of bySha.values()) {
    l.sort((a, b) => cmp(a.id, b.id));
    for (const f of l) repOf.set(f.id, l[0]!.id);
  }
  const list = [...bySha.values()].map((l) => l[0]!).sort((a, b) => cmp(a.id, b.id));
  return { bySha, list, repOf };
}

export interface ExactGroup { group: string; sha256: string; canonical_candidate: string; items: string[] }

export function exactGroups(reps: Reps): ExactGroup[] {
  return [...reps.bySha.values()]
    .filter((list) => list.length >= 2)
    .map((list) => ({ group: `EXACT-${list[0]!.sha.slice(0, 12)}`, sha256: list[0]!.sha, canonical_candidate: list[0]!.id, items: list.map((f) => f.id) }))
    .sort((a, b) => cmp(a.canonical_candidate, b.canonical_candidate));
}

/** SimHash 分段：k+1 段（鸽笼原理：汉明距离 ≤ k 的两个指纹至少有一段完全相同）；k ≥ 64 时不分段 */
export function bandLayout(k: number): { start: number; width: number }[] | null {
  if (k >= 64) return null;
  const nb = Math.max(1, k + 1);
  const base = Math.floor(64 / nb), extra = 64 % nb;
  const out: { start: number; width: number }[] = [];
  let start = 0;
  for (let b = 0; b < nb; b++) {
    const width = base + (b < extra ? 1 : 0);
    out.push({ start, width });
    start += width;
  }
  return out;
}

/** 候选对（i < j）：至少一段完全相同；每对只给一次（在它第一次相同的那一段给出）。k ≥ 64 时给出全部对 */
export function* candidatePairs(hashes: readonly bigint[], k: number): Generator<[number, number]> {
  const n = hashes.length;
  const layout = bandLayout(k);
  if (layout === null) {
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) yield [i, j];
    return;
  }
  const bands = hashes.map((h) => layout.map(({ start, width }) => (h >> BigInt(start)) & ((1n << BigInt(width)) - 1n)));
  for (let b = 0; b < layout.length; b++) {
    const buckets = new Map<bigint, number[]>();
    for (let i = 0; i < n; i++) pushTo(buckets, bands[i]![b]!, i);
    for (const l of buckets.values()) {
      for (let x = 0; x < l.length; x++) {
        for (let y = x + 1; y < l.length; y++) {
          const i = l[x]!, j = l[y]!;
          let earlier = false;
          for (let e = 0; e < b && !earlier; e++) earlier = bands[i]![e] === bands[j]![e];
          if (!earlier) yield [i, j];
        }
      }
    }
  }
}

export interface NearPair { a: string; b: string; hamming: number; jaccard: number }

const round4 = (x: number): number => Math.round(x * 10000) / 10000;

/** 近似对：在各完全重复组的代表之间，经 SimHash 分段索引取候选；两道门都过才算（门一 SimHash 距离，门二 n-gram Jaccard）。
 *  总数超过 limits.maxNearPairs 即报错（不截断） */
export function nearPairs(reps: Reps, cfg: DedupeConfig): NearPair[] {
  const list = reps.list;
  const out: NearPair[] = [];
  for (const [i, j] of candidatePairs(list.map((f) => f.simhash), cfg.near.maxHamming)) {
    const a = list[i]!, b = list[j]!;
    const d = hammingSplit(a.split, b.split);
    if (d > cfg.near.maxHamming) continue; // 门一
    const jac = jaccard(a.grams, b.grams);
    if (jac < cfg.near.minJaccard) continue; // 门二
    out.push({ a: a.id, b: b.id, hamming: d, jaccard: round4(jac) });
    if (out.length > cfg.limits.maxNearPairs) {
      throw new DedupeError(`近似对超过上限 ${String(cfg.limits.maxNearPairs)}（不截断；请先查输入或收紧阈值）`);
    }
  }
  return out.sort((x, y) => cmp(x.a, y.a) || cmp(x.b, y.b));
}

export interface FamilyCandidate { code_suggestion: string; main_node: string; template_sha256: string; items: string[] }
export interface ReviewTemplate { template_sha256: string; reasons: string[]; groups: { main_nodes: string[]; items: string[] }[] }

/** 族码建议：FAM_<学科>_<模板哈希 12 位>_<主节点 ID 的 SHA-256 前 8 位>，符合 kge.item_family.code 的形状；只是建议，由教研定 */
export const familyCode = (subject: string, templateSha: string, mainNode: string): string =>
  `FAM_${subject}_${templateSha.slice(0, 12)}_${sha256(mainNode).slice(0, 8)}`;

export function familyCandidates(fps: readonly Fingerprint[], subject: string): { families: FamilyCandidate[]; review: ReviewTemplate[] } {
  const byTemplate = new Map<string, Fingerprint[]>();
  for (const f of fps) pushTo(byTemplate, f.templateSha, f);
  const families: FamilyCandidate[] = [];
  const review: ReviewTemplate[] = [];
  for (const tpl of [...byTemplate.keys()].sort(cmp)) {
    const members = byTemplate.get(tpl)!;
    // 模板相同但规范文本都一样 = 完全重复，不是题目族
    if (new Set(members.map((f) => f.sha)).size < 2) continue;
    const byNode = new Map<string, Fingerprint[]>();
    for (const f of members) pushTo(byNode, nodeKey(f.mainNodes), f);
    const keys = [...byNode.keys()].sort(cmp);
    const single = (k: string): boolean => byNode.get(k)![0]!.mainNodes.length === 1;
    const reasons: string[] = [];
    if (keys.filter(single).length > 1) reasons.push("cross_node");
    if (keys.some((k) => byNode.get(k)![0]!.mainNodes.length > 1)) reasons.push("multi_node");
    if (keys.some((k) => byNode.get(k)![0]!.mainNodes.length === 0)) reasons.push("no_node");
    if (reasons.length > 0) {
      review.push({
        template_sha256: tpl, reasons,
        groups: keys.map((k) => ({ main_nodes: byNode.get(k)![0]!.mainNodes, items: byNode.get(k)!.map((f) => f.id).sort(cmp) })),
      });
    }
    for (const k of keys) {
      if (!single(k)) continue; // 只认单一主节点
      const list = byNode.get(k)!;
      if (new Set(list.map((f) => f.sha)).size < 2) continue;
      families.push({ code_suggestion: familyCode(subject, tpl, k), main_node: k, template_sha256: tpl, items: list.map((f) => f.id).sort(cmp) });
    }
  }
  return { families, review };
}
