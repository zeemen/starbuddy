// KGE-08：同卷识别正反例（换序、缺题、多题、首尾不同、部分命中），宁严勿松。全部合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_CONFIG, type DedupeConfig } from "../src/config.ts";
import { fingerprintItems, nearPairs, representatives } from "../src/dedupe.ts";
import { buildPapers, comparePapers, exactMatch, looseMatcher, orderedMatches, paperPairs, type PaperComparison } from "../src/paper.ts";
import { BASE_PAPER, item, OTHER, withConfig } from "./helpers.ts";

/** 已知卷 K（BASE_PAPER）与新卷 N 比较 */
function compare(newTexts: readonly string[], cfg: DedupeConfig = DEFAULT_CONFIG): PaperComparison {
  const items = [
    ...BASE_PAPER.map((t, i) => item(`SYN-K${String(i + 1)}`, t, [], "SYN-K", i + 1)),
    ...newTexts.map((t, i) => item(`SYN-N${String(i + 1)}`, t, [], "SYN-N", i + 1)),
  ];
  const fps = fingerprintItems(items, cfg);
  const reps = representatives(fps);
  const papers = buildPapers(fps);
  return comparePapers(papers.find((p) => p.paperId === "SYN-K")!, papers.find((p) => p.paperId === "SYN-N")!, looseMatcher(reps, nearPairs(reps, cfg)), cfg);
}
const NEAR_1 = BASE_PAPER[1]!.replace("四象限", "三象限");
const NEAR_7 = BASE_PAPER[7]!.replace("圆柱", "圆锥");
const without = (idx: number[]) => BASE_PAPER.filter((_, i) => !idx.includes(i));
const insertAt = (i: number, ...t: string[]) => [...BASE_PAPER.slice(0, i), ...t, ...BASE_PAPER.slice(i)];
const replaceAt = (i: number, t: string) => BASE_PAPER.map((x, j) => (j === i ? t : x));

test("合成已知卷：12 题，彼此不相干", () => {
  assert.equal(BASE_PAPER.length, 12);
  assert.equal(OTHER.length, 6);
  const reps = representatives(fingerprintItems(BASE_PAPER.map((t, i) => item(`SYN-${String(i)}`, t)), DEFAULT_CONFIG));
  assert.deepEqual(nearPairs(reps, DEFAULT_CONFIG), []);
});

test("同卷：完全相同、写法不同（全半角、空白）都判同卷", () => {
  const c = compare(BASE_PAPER);
  assert.equal(c.verdict, "same");
  assert.deepEqual(c.failed, []);
  assert.equal(compare(BASE_PAPER.map((t) => t.replace(/，/g, " , ").replace(/。/g, "."))).verdict, "same");
});

test("同卷只认规范文本一致：近似题不计入同卷的按序匹配，只计入相似卷", () => {
  assert.notEqual(NEAR_1, BASE_PAPER[1]);
  const one = compare(replaceAt(1, NEAR_1));
  assert.equal(one.ordered_matches, 11);
  assert.equal(one.loose_ordered_matches, 12);
  // 两题近似：按序一致只有 10/12，不判同卷（若把近似也算进同卷，这里会是 12/12 同卷）
  const two = compare(replaceAt(1, NEAR_1).map((t, i) => (i === 7 ? NEAR_7 : t)));
  assert.equal(two.ordered_matches, 10);
  assert.equal(two.loose_ordered_matches, 12);
  assert.deepEqual(two.failed, ["ordered_ratio"]);
  assert.equal(two.verdict, "similar");
  // 首题只是近似：首题不算一致
  const first = compare(replaceAt(0, BASE_PAPER[0]!.replace("最小", "最大")));
  assert.equal(first.first_match, false);
  assert.ok(first.failed.includes("first_item"));
});

test("相似卷用近似匹配：每题都只是近似时，同卷一题不中，仍报相似卷", () => {
  const items = [item("SYN-A1", BASE_PAPER[1]!, [], "SYN-A", 1), item("SYN-B1", NEAR_1, [], "SYN-B", 1)];
  const fps = fingerprintItems(items, DEFAULT_CONFIG);
  const reps = representatives(fps);
  const [pa, pb] = buildPapers(fps);
  const c = comparePapers(pa!, pb!, looseMatcher(reps, nearPairs(reps, DEFAULT_CONFIG)), DEFAULT_CONFIG);
  assert.equal(c.ordered_matches, 0);
  assert.equal(c.loose_ordered_matches, 1);
  assert.equal(c.verdict, "similar");
  assert.equal(comparePapers(pa!, pb!, exactMatch, DEFAULT_CONFIG).verdict, "none");
});

test("占比门的边界：占比恰好等于门槛算过", () => {
  const c = compare(without([5]), withConfig({ paper: { minOrderedRatio: 11 / 12 } }));
  assert.equal(c.verdict, "same");
  const d = compare(without([5]), withConfig({ paper: { minOrderedRatio: 11 / 12 + 1e-9 } }));
  assert.deepEqual(d.failed, ["ordered_ratio"]);
});

test("缺题、多题：差 1 题且占比够、首尾匹配 → 同卷；差 2 题 → 只是相似卷", () => {
  const miss1 = compare(without([5]));
  assert.equal(miss1.verdict, "same");
  assert.equal(miss1.ordered_matches, 11);
  assert.equal(miss1.ordered_ratio, Math.round((11 / 12) * 10000) / 10000); // 分母是两卷中较大的题数
  assert.equal(compare(insertAt(6, OTHER[0]!)).verdict, "same");
  const miss2 = compare(without([4, 7]));
  assert.equal(miss2.verdict, "similar");
  assert.ok(miss2.failed.includes("count_diff"));
  const extra2 = compare(insertAt(6, OTHER[0]!, OTHER[1]!));
  assert.equal(extra2.verdict, "similar");
  assert.ok(extra2.failed.includes("count_diff"));
});

test("题数差单独把关：占比门放宽到 0.8 时，差 2 题仍不判同卷", () => {
  const c = compare(without([4, 7]), withConfig({ paper: { minOrderedRatio: 0.8 } }));
  assert.deepEqual(c.failed, ["count_diff"]);
  assert.equal(c.verdict, "similar");
});

test("按序匹配占比单独把关：题数相同、首尾匹配，中间换掉 2 题（10/12）→ 相似卷", () => {
  const c = compare(replaceAt(5, OTHER[2]!).map((t, i) => (i === 8 ? OTHER[3]! : t)));
  assert.equal(c.count_b, 12);
  assert.equal(c.ordered_matches, 10);
  assert.deepEqual(c.failed, ["ordered_ratio"]);
  assert.equal(c.verdict, "similar");
});

test("换序：相邻两题对调 → 相似卷（宁严勿松，不复用解析）", () => {
  const swapped = [...BASE_PAPER];
  [swapped[4], swapped[5]] = [swapped[5]!, swapped[4]!];
  const c = compare(swapped);
  assert.equal(c.ordered_matches, 11);
  assert.equal(c.out_of_order, 1);
  assert.deepEqual(c.failed, ["out_of_order"]);
  assert.equal(c.verdict, "similar");
});

test("首尾不同：首题或尾题换掉 → 相似卷；缺首题同样不判同卷", () => {
  const first = compare(replaceAt(0, OTHER[4]!));
  assert.deepEqual(first.failed, ["first_item"]);
  assert.equal(first.verdict, "similar");
  const last = compare(replaceAt(11, OTHER[5]!));
  assert.deepEqual(last.failed, ["last_item"]);
  assert.equal(last.verdict, "similar");
  const noFirst = compare(without([0]));
  assert.ok(noFirst.failed.includes("first_item"));
  assert.equal(noFirst.verdict, "similar");
  // 关掉首尾要求时，首题换掉的卷能过（说明是首尾这一条挡下的）
  assert.equal(compare(replaceAt(0, OTHER[4]!), withConfig({ paper: { requireFirstLast: false } })).verdict, "same");
});

test("部分命中：一半相同 → 相似卷；只有两题相同或完全不相干 → 不报", () => {
  const half = compare([...BASE_PAPER.slice(0, 6), ...OTHER]);
  assert.equal(half.verdict, "similar");
  assert.equal(half.ordered_matches, 6);
  const few = compare([BASE_PAPER[0]!, BASE_PAPER[1]!, ...OTHER]);
  assert.equal(few.ordered_matches, 2);
  assert.equal(few.verdict, "none");
  assert.equal(compare(OTHER).verdict, "none");
});

test("按序匹配（LCS）：顺序敏感", () => {
  const fps = fingerprintItems(BASE_PAPER.slice(0, 4).map((t, i) => item(`SYN-${String(i)}`, t)), DEFAULT_CONFIG);
  assert.equal(orderedMatches(fps, fps, exactMatch), 4);
  assert.equal(orderedMatches(fps, [...fps].reverse(), exactMatch), 1);
  assert.equal(orderedMatches(fps, [], exactMatch), 0);
});

test("所有试卷两两比较：只列同卷与相似卷，按试卷 ID 排序", () => {
  const items = [
    ...BASE_PAPER.map((t, i) => item(`SYN-A${String(i)}`, t, [], "SYN-A", i + 1)),
    ...BASE_PAPER.map((t, i) => item(`SYN-B${String(i)}`, t, [], "SYN-B", i + 1)),
    ...OTHER.map((t, i) => item(`SYN-C${String(i)}`, t, [], "SYN-C", i + 1)),
  ];
  const fps = fingerprintItems(items, DEFAULT_CONFIG);
  const reps = representatives(fps);
  const pairs = paperPairs(buildPapers(fps), looseMatcher(reps, nearPairs(reps, DEFAULT_CONFIG)), DEFAULT_CONFIG);
  assert.deepEqual(pairs.map((p) => `${p.a}~${p.b}:${p.verdict}`), ["SYN-A~SYN-B:same"]);
});
