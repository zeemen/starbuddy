// KGE-08：合成样例端到端——预期分组、坐标系与提示、旧题组冲突、报告与清单不含题干（任一 8 字片段）、与输入顺序无关。
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_CONFIG } from "../src/config.ts";
import { fromLegacy, NODE_SYSTEM, parseFixture } from "../src/input.ts";
import { canonicalText } from "../src/normalize.ts";
import { analyze, renderWorklist, stableJson } from "../src/report.ts";
import { FIXTURE_RAW, RAW_ITEMS, textOf } from "./helpers.ts";

const REPORT = analyze(parseFixture(FIXTURE_RAW), DEFAULT_CONFIG);
const JSON_OUT = stableJson(REPORT);
const MD_OUT = renderWorklist(REPORT);

test("合成样例：完全重复、近似、族候选、待看、同卷与相似卷全部符合预期", () => {
  const r = REPORT;
  assert.equal(r.synthetic, true);
  assert.deepEqual(r.counts, {
    items: 62, unique_texts: 30, exact_groups: 13, exact_duplicate_items: 32, near_pairs: 3, family_candidates: 1, family_items: 3,
    review_templates: 3, legacy_families: 0, legacy_family_conflicts: 0, papers: 4, paper_pairs_same: 1, paper_pairs_similar: 5,
  });
  assert.deepEqual(r.exact_groups[0]!.items, ["SYN-DUP-A", "SYN-DUP-B", "SYN-DUP-C"]);
  assert.ok(r.exact_groups.some((g) => g.items.join() === "SYN-H05,SYN-K05,SYN-S05,SYN-W06"));
  // 族内只改数字的两题也可能同时是近似对（两者都只是候选，由教研分别勾选）
  assert.deepEqual(r.near_pairs.map((p) => `${p.a}~${p.b}`), ["SYN-FAM-1~SYN-FAM-3", "SYN-MULTI-1~SYN-MULTI-2", "SYN-NEAR-A~SYN-NEAR-B"]);
  assert.deepEqual(r.family_candidates.map((f) => f.items.join()), ["SYN-FAM-1,SYN-FAM-2,SYN-FAM-3"]);
  assert.equal(r.family_candidates[0]!.main_node, "SYN.GEO.AREA");
  assert.match(r.family_candidates[0]!.code_suggestion, /^FAM_syn_[0-9a-f]{12}_[0-9a-f]{8}$/);
  // SYN-FAM-4 与前三题模板相同但主节点不同；SYN-CROSS-1/2 同理；SYN-MULTI-1/2 是双主节点：都不成族，列待看
  assert.deepEqual(r.review_templates.map((t) => `${t.reasons.join()}:${t.groups.map((g) => g.items.join("+")).join(" | ")}`).sort(), [
    "cross_node:SYN-CROSS-2 | SYN-CROSS-1", "cross_node:SYN-FAM-1+SYN-FAM-2+SYN-FAM-3 | SYN-FAM-4", "multi_node:SYN-MULTI-1+SYN-MULTI-2",
  ]);
  assert.deepEqual(r.paper_pairs.map((p) => `${p.a}~${p.b}:${p.verdict}:${p.failed.join("/")}`), [
    "SYN-PAPER-H~SYN-PAPER-K:similar:ordered_ratio/last_item",
    "SYN-PAPER-H~SYN-PAPER-S:similar:ordered_ratio/last_item",
    "SYN-PAPER-H~SYN-PAPER-W:similar:ordered_ratio/last_item/out_of_order",
    "SYN-PAPER-K~SYN-PAPER-S:same:",
    "SYN-PAPER-K~SYN-PAPER-W:similar:out_of_order",
    "SYN-PAPER-S~SYN-PAPER-W:similar:out_of_order",
  ]);
  assert.deepEqual(r.papers.find((p) => p.paper_id === "SYN-PAPER-W")!.items.slice(4, 6), ["SYN-W05", "SYN-W06"]);
});

test("坐标系与提示：报告写明节点坐标系；清单提示按 KGE 节点重算、族偏宽与完全重复的代价", () => {
  assert.equal(REPORT.node_system, NODE_SYSTEM.fixture);
  assert.match(MD_OUT, /节点坐标系：fixture/);
  assert.match(MD_OUT, /落库须按 KGE 节点（kge\.knowledge_node）重算/);
  assert.match(MD_OUT, /repeat_family/);
  assert.match(MD_OUT, /复测也会因为找不到新族而落空/);
  assert.match(MD_OUT, /掌握计算会把重复题当成新族/);
  assert.ok(!MD_OUT.includes("六、冲突"), "合成样例没有旧题组，不列冲突节");
});

test("--aya 口径：读旧题组（aya-sprint-*），标出同时在旧题组与新候选里的题", () => {
  const q = (id: string, text: string, mainNodes: string[]) => ({ id, topic: null, unit: "", unitName: "", text, mainNodes, auxNodes: [], preNodes: [] });
  const inp = fromLegacy({
    topics: [],
    sprintCategories: [{ id: "dp", name: "（合成）专项", questionIds: ["a1", "a3", "a5"] }],
    questions: [
      q("a1", textOf("SYN-FAM-1"), ["K-1"]), q("a2", textOf("SYN-FAM-2"), ["K-1"]),
      q("a3", textOf("SYN-NEAR-A"), ["K-2"]), q("a4", textOf("SYN-NEAR-B"), ["K-2"]),
      q("a5", textOf("SYN-LONE"), ["K-3"]),
    ],
  });
  const r = analyze(inp, DEFAULT_CONFIG);
  assert.equal(r.node_system, NODE_SYSTEM.aya);
  assert.equal(r.counts.legacy_families, 1);
  assert.deepEqual(r.legacy_family_conflicts.map((x) => `${x.id}:${x.legacy_family}:${String(x.family_candidate !== null)}:${x.near_with.join("+")}`),
    ["a1:aya-sprint-dp:true:", "a3:aya-sprint-dp:false:a4"]);
  assert.match(r.family_candidates[0]!.code_suggestion, /^FAM_aya_/);
  const md = renderWorklist(r);
  assert.match(md, /六、冲突：同时在旧题组（aya-sprint-\*）与新候选里的题/);
  assert.match(md, /`a1`：旧题组 `aya-sprint-dp`；新候选：族候选/);
  assert.match(md, /节点坐标系：legacy-aya/);
});

test("报告与工作清单不含题干：样例题干（原文与规范文本）的任一 8 字片段都不出现", () => {
  const outputs = [JSON_OUT, MD_OUT];
  for (const it of RAW_ITEMS) {
    for (const t of [it.text, canonicalText(it.text)]) {
      const cp = Array.from(t);
      for (let i = 0; i + 8 <= cp.length; i++) {
        const frag = cp.slice(i, i + 8).join("");
        for (const o of outputs) assert.ok(!o.includes(frag), `${it.id} 的题干片段“${frag}”出现在产物里`);
      }
    }
  }
  assert.ok(!/"(text|stem|canonical|template)"\s*:/.test(JSON_OUT));
});

test("报告形状：指纹是十六进制、阈值写进报告、键按字典序", () => {
  for (const it of REPORT.items) {
    assert.match(it.sha256, /^[0-9a-f]{64}$/);
    assert.match(it.template_sha256, /^[0-9a-f]{64}$/);
    assert.match(it.simhash, /^[0-9a-f]{16}$/);
  }
  assert.deepEqual(REPORT.config, DEFAULT_CONFIG);
  const keys = Object.keys(JSON.parse(JSON_OUT) as object);
  assert.deepEqual(keys, [...keys].sort());
  assert.ok(JSON_OUT.endsWith("}\n"));
  assert.match(MD_OUT, /- \[ \] `SYN-PAPER-K` ↔ `SYN-PAPER-S`：同卷/);
  assert.match(MD_OUT, /族码建议 `FAM_syn_[0-9a-f]{12}_[0-9a-f]{8}`/);
});

test("与输入顺序无关：打乱输入顺序，报告与清单逐字节相同", () => {
  const inp = parseFixture(FIXTURE_RAW);
  const shuffled = { ...inp, items: [...inp.items].reverse() };
  const rotated = { ...inp, items: [...inp.items.slice(17), ...inp.items.slice(0, 17)] };
  assert.equal(stableJson(analyze(shuffled, DEFAULT_CONFIG)), JSON_OUT);
  assert.equal(renderWorklist(analyze(rotated, DEFAULT_CONFIG)), MD_OUT);
});
