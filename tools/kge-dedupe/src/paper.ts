// 同卷识别（KGE-08 范围 1，文本侧；版面 pHash 属上传通道，本步不做）。试卷 = 按序号排好的题目指纹列表。
// 宁严勿松：“同卷”只认规范文本 SHA-256 一致的题——题数差、按序一致占比、首尾题一致、无换序四个条件全部满足才判同卷（可复用解析）。
// 近似匹配（两道门）只用于“相似卷”（部分命中，不复用解析）。
import type { DedupeConfig } from "./config.ts";
import { cmp, type Fingerprint, type NearPair, type Reps } from "./dedupe.ts";

export interface Paper { paperId: string; items: Fingerprint[] }
export type ItemMatch = (a: Fingerprint, b: Fingerprint) => boolean;
export type PaperVerdict = "same" | "similar" | "none";
export interface PaperComparison {
  a: string;
  b: string;
  verdict: PaperVerdict;
  count_a: number;
  count_b: number;
  /** 按序且规范文本一致的题数（同卷只看它） */
  ordered_matches: number;
  ordered_ratio: number;
  /** 按序匹配（一致或近似）的题数（只用于相似卷） */
  loose_ordered_matches: number;
  loose_ratio: number;
  first_match: boolean;
  last_match: boolean;
  out_of_order: number;
  /** 没判“同卷”的原因（判同卷时为空） */
  failed: string[];
}

export function buildPapers(fps: readonly Fingerprint[]): Paper[] {
  const byPaper = new Map<string, Fingerprint[]>();
  for (const f of fps) {
    if (f.paperId === undefined) continue;
    const l = byPaper.get(f.paperId);
    if (l) l.push(f);
    else byPaper.set(f.paperId, [f]);
  }
  return [...byPaper.keys()].sort(cmp).map((paperId) => ({ paperId, items: byPaper.get(paperId)!.sort((x, y) => x.seq! - y.seq!) }));
}

/** 同卷用：规范文本完全一致 */
export const exactMatch: ItemMatch = (x, y) => x.sha === y.sha;

/** 相似卷用：规范文本一致，或两者的代表之间是近似对（两道门都过） */
export function looseMatcher(reps: Reps, near: readonly NearPair[]): ItemMatch {
  const pairs = new Set(near.map((p) => `${p.a}\u0000${p.b}`));
  return (x, y) => {
    if (x.sha === y.sha) return true;
    const rx = reps.repOf.get(x.id), ry = reps.repOf.get(y.id);
    if (rx === undefined || ry === undefined) return false;
    return pairs.has(`${rx}\u0000${ry}`) || pairs.has(`${ry}\u0000${rx}`);
  };
}

/** 按序匹配的题数（最长公共子序列，匹配关系由 match 定） */
export function orderedMatches(a: readonly Fingerprint[], b: readonly Fingerprint[], match: ItemMatch): number {
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i]![j] = match(a[i - 1]!, b[j - 1]!) ? dp[i - 1]![j - 1]! + 1 : Math.max(dp[i - 1]![j]!, dp[i]![j - 1]!);
    }
  }
  return dp[a.length]![b.length]!;
}

const round4 = (x: number): number => Math.round(x * 10000) / 10000;

export function comparePapers(pa: Paper, pb: Paper, loose: ItemMatch, cfg: DedupeConfig): PaperComparison {
  const a = pa.items, b = pb.items;
  const denom = Math.max(a.length, b.length);
  const lcs = orderedMatches(a, b, exactMatch);
  const ratio = denom === 0 ? 0 : lcs / denom;
  const looseLcs = orderedMatches(a, b, loose);
  const looseRatio = denom === 0 ? 0 : looseLcs / denom;
  const first = a.length > 0 && b.length > 0 && exactMatch(a[0]!, b[0]!);
  const last = a.length > 0 && b.length > 0 && exactMatch(a[a.length - 1]!, b[b.length - 1]!);
  // 完全一致、却不在按序匹配里的题 = 换了顺序
  const shasB = new Set(b.map((y) => y.sha)), shasA = new Set(a.map((x) => x.sha));
  const matchedA = a.filter((x) => shasB.has(x.sha)).length;
  const matchedB = b.filter((y) => shasA.has(y.sha)).length;
  const outOfOrder = Math.max(matchedA, matchedB) - lcs;
  const failed: string[] = [];
  if (Math.abs(a.length - b.length) > cfg.paper.maxCountDiff) failed.push("count_diff");
  if (ratio < cfg.paper.minOrderedRatio) failed.push("ordered_ratio");
  if (cfg.paper.requireFirstLast && !first) failed.push("first_item");
  if (cfg.paper.requireFirstLast && !last) failed.push("last_item");
  if (outOfOrder > cfg.paper.maxOutOfOrder) failed.push("out_of_order");
  const verdict: PaperVerdict = failed.length === 0 ? "same" : looseRatio >= cfg.paper.similarMinRatio && looseLcs > 0 ? "similar" : "none";
  return {
    a: pa.paperId, b: pb.paperId, verdict, count_a: a.length, count_b: b.length, ordered_matches: lcs, ordered_ratio: round4(ratio),
    loose_ordered_matches: looseLcs, loose_ratio: round4(looseRatio), first_match: first, last_match: last, out_of_order: outOfOrder, failed,
  };
}

/** 所有试卷两两比较；只列出“同卷”与“相似卷” */
export function paperPairs(papers: readonly Paper[], loose: ItemMatch, cfg: DedupeConfig): PaperComparison[] {
  const out: PaperComparison[] = [];
  for (let i = 0; i < papers.length; i++) {
    for (let j = i + 1; j < papers.length; j++) {
      const c = comparePapers(papers[i]!, papers[j]!, loose, cfg);
      if (c.verdict !== "none") out.push(c);
    }
  }
  return out;
}
