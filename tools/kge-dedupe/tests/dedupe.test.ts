// KGE-08：完全重复、近似（两道门、SimHash 分段索引、总数上限）、题目族候选（只认单一主节点）的单测（全部合成数据）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_CONFIG, type DedupeConfig } from "../src/config.ts";
import { bandLayout, candidatePairs, DedupeError, exactGroups, familyCandidates, familyCode, fingerprintItems, nearPairs,
  representatives } from "../src/dedupe.ts";
import { hamming } from "../src/fingerprint.ts";
import { parseFixture } from "../src/input.ts";
import { analyze } from "../src/report.ts";
import { BASE_PAPER, FIXTURE_RAW, item, OTHER, synthText, textOf, withConfig } from "./helpers.ts";

type Items = Parameters<typeof fingerprintItems>[0];
const fp = (items: Items, cfg = DEFAULT_CONFIG) => fingerprintItems(items, cfg);
const reps = (items: Items, cfg = DEFAULT_CONFIG) => representatives(fp(items, cfg));
const near = (items: Items, cfg: DedupeConfig = DEFAULT_CONFIG) => nearPairs(reps(items, cfg), cfg);
const fam = (items: Items) => familyCandidates(fp(items), "syn");
const NEAR_A = textOf("SYN-NEAR-A"), NEAR_B = textOf("SYN-NEAR-B"), LONE = textOf("SYN-LONE");

test("完全重复：规范文本相同才成组；规范题候选取 ID 最小者；单独一题不成组", () => {
  const g = exactGroups(reps([
    item("SYN-Z", textOf("SYN-DUP-A")), item("SYN-B", textOf("SYN-DUP-B")), item("SYN-C", textOf("SYN-DUP-C")), item("SYN-D", LONE),
  ]));
  assert.equal(g.length, 1);
  assert.deepEqual(g[0]!.items, ["SYN-B", "SYN-C", "SYN-Z"]);
  assert.equal(g[0]!.canonical_candidate, "SYN-B");
  assert.match(g[0]!.group, /^EXACT-[0-9a-f]{12}$/);
  assert.equal(exactGroups(reps([item("SYN-1", NEAR_A), item("SYN-2", NEAR_B)])).length, 0);
  const r = reps([item("SYN-Z", textOf("SYN-DUP-A")), item("SYN-B", textOf("SYN-DUP-B"))]);
  assert.equal(r.repOf.get("SYN-Z"), "SYN-B");
  assert.deepEqual(r.list.map((f) => f.id), ["SYN-B"]);
});

test("近似：单字改动两道门都过；无关文本不算；完全重复只按代表比一次", () => {
  const pairs = near([item("SYN-1", NEAR_A), item("SYN-2", NEAR_B), item("SYN-3", LONE), item("SYN-4", NEAR_A)]);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0]!.a, "SYN-1");
  assert.equal(pairs[0]!.b, "SYN-2");
  assert.ok(pairs[0]!.hamming <= DEFAULT_CONFIG.near.maxHamming && pairs[0]!.jaccard >= DEFAULT_CONFIG.near.minJaccard);
});

test("近似：两道门缺一不成（只过 SimHash 门、只过 Jaccard 门都不算）", () => {
  const items = [item("SYN-1", NEAR_A), item("SYN-2", NEAR_B), item("SYN-3", LONE)];
  assert.deepEqual(near(items, withConfig({ near: { maxHamming: 64 } })).map((p) => `${p.a}~${p.b}`), ["SYN-1~SYN-2"]);
  assert.deepEqual(near(items, withConfig({ near: { minJaccard: 0, maxHamming: 0 } })), []);
  assert.deepEqual(near(items, withConfig({ near: { minJaccard: 1 } })), []);
  assert.equal(near(items, withConfig({ near: { maxHamming: 64, minJaccard: 0 } })).length, 3);
});

test("近似：两道门的边界——距离恰好等于门一、Jaccard 恰好等于门二都算过", () => {
  const items = [item("SYN-1", NEAR_A), item("SYN-2", NEAR_B)];
  const p = near(items)[0]!;
  assert.equal(near(items, withConfig({ near: { maxHamming: p.hamming } })).length, 1);
  assert.equal(near(items, withConfig({ near: { maxHamming: p.hamming - 1 } })).length, 0);
  const f = fp(items);
  const exactJac = [...f[0]!.grams].filter((g) => f[1]!.grams.has(g)).length / new Set([...f[0]!.grams, ...f[1]!.grams]).size;
  assert.equal(near(items, withConfig({ near: { minJaccard: exactJac } })).length, 1);
});

test("SimHash 分段：k+1 段、覆盖 64 位；k ≥ 64 不分段", () => {
  for (const k of [0, 1, 3, 12, 63]) {
    const l = bandLayout(k)!;
    assert.equal(l.length, k + 1);
    assert.equal(l.reduce((s, b) => s + b.width, 0), 64);
    assert.equal(l[0]!.start, 0);
    for (let i = 1; i < l.length; i++) assert.equal(l[i]!.start, l[i - 1]!.start + l[i - 1]!.width);
  }
  assert.equal(bandLayout(64), null);
});

/** 确定性伪随机 64 位数（xorshift64*） */
function rng(seed: bigint): () => bigint {
  let s = seed;
  const M = (1n << 64n) - 1n;
  return () => {
    s ^= s >> 12n;
    s = (s ^ (s << 25n)) & M;
    s ^= s >> 27n;
    return (s * 0x2545f4914f6cdd1dn) & M;
  };
}

test("SimHash 分段索引不漏（鸽笼原理）：距离 ≤ k 的对全部进候选，且每对只给一次", () => {
  const next = rng(0x9e3779b97f4a7c15n);
  for (const k of [1, 2, 3, 12]) {
    const hashes: bigint[] = [];
    for (let t = 0; t < 300; t++) {
      const h = next();
      let g = h;
      const flips = new Set<number>();
      while (flips.size < k) flips.add(Number(next() % 64n));
      for (const b of flips) g ^= 1n << BigInt(b);
      hashes.push(h, g);
    }
    const seen = new Set<string>();
    for (const [i, j] of candidatePairs(hashes, k)) {
      assert.ok(i < j);
      const key = `${String(i)},${String(j)}`;
      assert.ok(!seen.has(key), `候选对 ${key} 重复`);
      seen.add(key);
    }
    for (let i = 0; i < hashes.length; i++) {
      for (let j = i + 1; j < hashes.length; j++) {
        if (hamming(hashes[i]!, hashes[j]!) <= k) assert.ok(seen.has(`${String(i)},${String(j)}`), `k=${String(k)} 漏了距离 ≤ k 的对`);
      }
    }
  }
});

test("近似对按 ID 排序输出（不依赖分段索引给出的顺序）", () => {
  const texts = [...BASE_PAPER, ...OTHER];
  const items = texts.flatMap((t, i) => {
    const cp = [...t];
    const k = Math.floor(cp.length / 2);
    cp[k] = cp[k] === "甲" ? "乙" : "甲";
    const id = `SYN-V${String(i).padStart(2, "0")}`;
    return [item(`${id}a`, t), item(`${id}b`, cp.join(""))];
  });
  const got = near(items).map((p) => `${p.a}~${p.b}`);
  assert.ok(got.length >= 10);
  assert.deepEqual(got, [...got].sort());
  assert.ok(got.every((x) => /^SYN-V(\d\d)a~SYN-V\1b$/.test(x)));
});

test("近似对总数设上限：超出报错（不截断）", () => {
  const items = [0, 1, 2, 3, 4].map((i) => item(`SYN-${String(i)}`, NEAR_A.replace("图书馆", `图书馆${"甲乙丙丁戊"[i]!}`)));
  const loose = withConfig({ near: { maxHamming: 64, minJaccard: 0.5 } });
  assert.equal(near(items, loose).length, 10);
  assert.throws(() => near(items, { ...loose, limits: { maxNearPairs: 9 } }), DedupeError);
  assert.equal(near(items, { ...loose, limits: { maxNearPairs: 10 } }).length, 10);
});

test("规模：5000 道不相干的合成题在时限内跑完（分段索引，不全量两两）", () => {
  const items = Array.from({ length: 5000 }, (_, i) => item(`SYN-R${String(i)}`, synthText(i, 40)));
  const t0 = Date.now();
  const r = analyze({ source: "fixture", subject: "syn", nodeSystem: "fixture", synthetic: true, items, legacyFamilies: new Map() }, DEFAULT_CONFIG);
  const ms = Date.now() - t0;
  assert.equal(r.counts.items, 5000);
  assert.equal(r.counts.near_pairs, 0);
  assert.ok(ms < 30000, `用时 ${String(ms)} ms`);
});

test("题目族：模板相同且单一主节点相同 → 候选族；规范文本全相同不成族；族码带学科前缀", () => {
  const { families, review } = fam([
    item("SYN-F1", textOf("SYN-FAM-1"), ["SYN.GEO.AREA"]), item("SYN-F2", textOf("SYN-FAM-2"), ["SYN.GEO.AREA"]),
    item("SYN-F3", textOf("SYN-FAM-3"), ["SYN.GEO.AREA"]),
    item("SYN-D1", textOf("SYN-DUP-A"), ["SYN.ALG.EQ1"]), item("SYN-D2", textOf("SYN-DUP-B"), ["SYN.ALG.EQ1"]),
  ]);
  assert.equal(families.length, 1);
  assert.deepEqual(families[0]!.items, ["SYN-F1", "SYN-F2", "SYN-F3"]);
  assert.equal(families[0]!.main_node, "SYN.GEO.AREA");
  assert.match(families[0]!.code_suggestion, /^FAM_syn_[0-9a-f]{12}_[0-9a-f]{8}$/);
  assert.match(families[0]!.code_suggestion, /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/);
  assert.equal(families[0]!.code_suggestion, familyCode("syn", families[0]!.template_sha256, "SYN.GEO.AREA"));
  assert.deepEqual(review, []);
  // 族码随学科与主节点变化
  assert.notEqual(familyCode("aya", "0".repeat(64), "N1"), familyCode("syn", "0".repeat(64), "N1"));
  assert.notEqual(familyCode("aya", "0".repeat(64), "N1"), familyCode("aya", "0".repeat(64), "N2"));
});

test("题目族：跨主节点的同模板只列待看，不成族", () => {
  const cross = fam([item("SYN-X1", textOf("SYN-CROSS-1"), ["SYN.NUM.ORDER"]), item("SYN-X2", textOf("SYN-CROSS-2"), ["SYN.NUM.MULT"])]);
  assert.deepEqual(cross.families, []);
  assert.equal(cross.review.length, 1);
  assert.deepEqual(cross.review[0]!.reasons, ["cross_node"]);
  assert.deepEqual(cross.review[0]!.groups, [
    { main_nodes: ["SYN.NUM.MULT"], items: ["SYN-X2"] }, { main_nodes: ["SYN.NUM.ORDER"], items: ["SYN-X1"] },
  ]);
});

test("题目族反例：双主节点（集合相同）不成族，列待看；没有主节点也不成族", () => {
  const two = fam([
    item("SYN-S1", textOf("SYN-FAM-1"), ["SYN.B", "SYN.A"]), item("SYN-S2", textOf("SYN-FAM-2"), ["SYN.A", "SYN.B", "SYN.A"]),
  ]);
  assert.deepEqual(two.families, []);
  assert.equal(two.review.length, 1);
  assert.deepEqual(two.review[0]!.reasons, ["multi_node"]);
  assert.deepEqual(two.review[0]!.groups, [{ main_nodes: ["SYN.A", "SYN.B"], items: ["SYN-S1", "SYN-S2"] }]);
  const none = fam([item("SYN-N1", textOf("SYN-FAM-1")), item("SYN-N2", textOf("SYN-FAM-2"))]);
  assert.deepEqual(none.families, []);
  assert.deepEqual(none.review[0]!.reasons, ["no_node"]);
  // 单一主节点的两题照样成族，同模板的多主节点题列待看
  const mixed = fam([
    item("SYN-M1", textOf("SYN-FAM-1"), ["SYN.A"]), item("SYN-M2", textOf("SYN-FAM-2"), ["SYN.A"]),
    item("SYN-M3", textOf("SYN-FAM-3"), ["SYN.A", "SYN.B"]),
  ]);
  assert.deepEqual(mixed.families.map((f) => f.items.join()), ["SYN-M1,SYN-M2"]);
  assert.deepEqual(mixed.review[0]!.reasons, ["multi_node"]);
});

test("题目族：同一模板在两个主节点下各有两题 → 两个族、族码不同，并列待看", () => {
  const { families, review } = fam([
    item("SYN-A1", textOf("SYN-FAM-1"), ["SYN.GEO.AREA"]), item("SYN-A2", textOf("SYN-FAM-2"), ["SYN.GEO.AREA"]),
    item("SYN-P1", textOf("SYN-FAM-3"), ["SYN.GEO.PERIM"]), item("SYN-P2", textOf("SYN-FAM-4"), ["SYN.GEO.PERIM"]),
  ]);
  assert.equal(families.length, 2);
  assert.equal(new Set(families.map((f) => f.code_suggestion)).size, 2);
  assert.equal(review.length, 1);
  assert.deepEqual(review[0]!.reasons, ["cross_node"]);
});

test("样例解析后的指纹只算一次分组（representatives 与报告计数一致）", () => {
  const inp = parseFixture(FIXTURE_RAW);
  const r = representatives(fingerprintItems(inp.items, DEFAULT_CONFIG));
  assert.equal(r.list.length, analyze(inp, DEFAULT_CONFIG).counts.unique_texts);
});
