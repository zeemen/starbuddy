// 对合成夹具（假 legacy 目录）跑完整盘点：逐项核对计数、隐私、确定性、legacy 不被改动。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { buildInventory } from "../src/main.ts";
import type { Inventory } from "../src/model.ts";
import { longCjkRuns, renderConclusionsMd, renderInventoryMd, renderJson } from "../src/report.ts";

const FIX = new URL("./fixtures/", import.meta.url).pathname;
const AYA = join(FIX, "aya");
const WENLI = join(FIX, "wenli");

function digest(dir: string): string {
  const h = createHash("sha256");
  const walk = (d: string) => {
    for (const name of readdirSync(d).sort()) {
      const p = join(d, name);
      if (statSync(p).isDirectory()) walk(p);
      else h.update(p).update(readFileSync(p));
    }
  };
  walk(dir);
  return h.digest("hex");
}

const before = [digest(AYA), digest(WENLI)];
const inv: Inventory = await buildInventory(AYA, WENLI, [], (p) => p.slice(FIX.length));

function v(key: string): unknown {
  for (const s of inv.subjects) for (const g of s.groups) for (const f of g.findings) if (f.key === key) return f.value;
  throw new Error(`没有这个键：${key}`);
}
function sample(key: string): string[] {
  for (const s of inv.subjects) for (const g of s.groups) for (const f of g.findings) if (f.key === key) return f.samples ?? [];
  throw new Error(`没有这个键：${key}`);
}

test("数学：节点、类型与字段", () => {
  assert.equal(v("math.topics"), 2);
  assert.equal(v("math.nodes"), 8);
  assert.equal(v("math.nodes.by_kind"), "C 1，K 4，S 3");
  assert.equal(v("math.kge_field.scope"), "无（0/8）");
  assert.equal(v("math.codes.kind_vs_type"), "C");
  assert.equal(v("math.codes.kind_vs_db"), "C");
  assert.equal(v("math.codes.kge_style"), "0/8");
});

test("数学：前置边、环、传递边、悬空边、孤点", () => {
  assert.equal(v("math.prereq.total"), 7);
  assert.equal(v("math.prereq.id_edges"), 5);
  assert.equal(v("math.prereq.text"), 1);
  assert.deepEqual(sample("math.prereq.text"), ["一元一次方程"]);
  assert.equal(v("math.prereq.dangling"), 1);
  assert.equal(v("math.graph.cycles"), 1);
  assert.deepEqual(sample("math.graph.cycles"), ["K-FIX-0904、K-FIX-0905"]);
  assert.equal(v("math.graph.transitive"), 1);
  assert.deepEqual(sample("math.graph.transitive"), ["K-FIX-0901 → K-FIX-0903"]);
  assert.equal(v("math.graph.orphans"), 2);
  assert.deepEqual(sample("math.graph.orphans"), ["K-FIX-0906", "K-FIX-0907"]);
});

test("数学：题目、主考点、六段标识、陷阱码、能力格", () => {
  assert.equal(v("math.items"), 6);
  assert.equal(v("math.items.sprint"), 1);
  assert.equal(v("math.items.node_refs_unresolved"), 1);
  assert.equal(v("math.nodes.main_lt3"), "7（87.5%）");
  assert.equal(v("math.nodes.main_zero"), "4（50.0%）");
  assert.equal(v("math.item.six.region"), "1/6");
  assert.equal(v("math.item.six.grade"), "0/6");
  assert.equal(v("math.g9fn.nodes"), 7);
  assert.equal(v("math.g9fn.main_lt3"), "6/7");
  assert.equal(v("math.traps.bad_root"), 1);
  assert.equal(v("math.traps.used"), "1/2（50.0%）");
  assert.equal(v("math.traps.unknown_refs"), 1);
  assert.equal(v("math.grid.empty"), 2);
});

test("语文：能力格、20 格、篇目、错因", () => {
  assert.equal(v("chn.grid.cells"), 4);
  assert.equal(v("chn.grid.db_enum_match"), "是");
  assert.equal(v("chn.grid.legacy20"), 1);
  assert.deepEqual(sample("chn.grid.legacy20"), ["docs/old-grid.md"]);
  assert.equal(v("chn.catalog.rows"), 2);
  assert.equal(v("chn.catalog.overlap"), "1 / 0 / 0");
  assert.equal(v("chn.must_recite.db"), 1);
  assert.equal(v("chn.reading_tags.mapping"), "1/2（50.0%）");
  assert.equal(v("chn.reading_tags.ts_vs_json"), "1 / 1");
  assert.equal(v("chn.error_types.db_match"), "是");
});

test("语文：引用第三方包、内置模块或 legacy 以外文件的模块被拒绝加载并记录", () => {
  const chinese = inv.subjects.find((s) => s.subject === "chinese")!;
  assert.ok(chinese.loadErrors.some((e) => e.includes("reading-methods.ts") && e.includes("zod")));
  assert.ok(chinese.loadErrors.some((e) => e.includes("writing-syllabus.ts") && e.includes("node:fs")));
  assert.ok(chinese.loadErrors.some((e) => e.includes("classical-catalog.ts") && e.includes("legacy 目录以外")));
  assert.equal(v("chn.reading_methods"), 0);
  assert.equal(v("chn.writing.units"), 0);
  assert.equal(v("chn.classical.rows"), 0);
});

test("道法：页码、版次、Rubric、时政、读本", () => {
  assert.equal(v("moral.nodes"), 3);
  assert.equal(v("moral.pages.parsable"), "1/2");
  assert.equal(v("moral.pages.edition_bound"), "1/3");
  assert.equal(v("moral.pages.pending"), 1);
  assert.equal(v("moral.points"), 2);
  assert.equal(v("moral.points.material_linkage"), "1/2");
  assert.equal(v("moral.rubric_version"), "1/2");
  assert.equal(v("moral.items.node_refs_unresolved"), 1);
  assert.equal(v("moral.nodes.unreferenced"), "2/3（66.7%）");
  assert.equal(v("moral.news"), 2);
  assert.equal(v("moral.news.validity"), "无");
  assert.equal(v("moral.reader.files"), 1);
  assert.match(String(v("moral.reader.verdict")), /^未入库/);
});

test("六个问题每科都有回答，四项阻断都有核验", () => {
  for (const q of ["counts", "missing_fields", "orphans_cycles", "legacy_codes"]) {
    assert.deepEqual(inv.answers.filter((a) => a.question === q).map((a) => a.subject).sort(), ["chinese", "math", "moral"]);
  }
  assert.equal(inv.answers.filter((a) => a.question === "moral_pages_edition").length, 1);
  assert.equal(inv.answers.filter((a) => a.question === "reader_ingested").length, 1);
  assert.deepEqual(inv.blockers.map((b) => b.id).sort(), ["chinese-ability-kp", "math-node-list", "moral-pages-edition", "moral-reader"]);
  const keys = new Set(inv.subjects.flatMap((s) => s.groups.flatMap((g) => g.findings.map((f) => f.key))));
  for (const b of inv.blockers) for (const e of b.evidence) assert.ok(keys.has(e), `阻断核验引用了不存在的键 ${e}`);
});

test("结论字段清单：算法、证据引用、不推断清单", () => {
  const diag = inv.conclusions.find((c) => c.symbol === "DiagnoseResultSchema")!;
  assert.equal(diag.algorithm, "含模型调用");
  assert.equal(diag.hasEvidenceRef, true);
  assert.equal(diag.hasConfidence, true);
  const flagged = inv.conclusions.flatMap((c) => c.fields.filter((f) => f.forbidden).map((f) => `${c.symbol}.${f.field}`)).sort();
  assert.deepEqual(flagged, [
    "CalibrationState.abilityBand",
    "DiagnoseResultSchema.rank_range",
    "DiagnoseResultSchema.student_label",
    "profiles.segment",
    "student_seg_t.基础薄弱",
    "student_seg_t.高分冲刺",
  ]);
  const ability = inv.conclusions.find((c) => c.symbol === "ability_state")!;
  assert.deepEqual(ability.fields.map((f) => f.field), ["student_id", "domain", "mastery", "data_confidence"]);
  assert.equal(ability.hasEvidenceRef, false, "student_id 不算证据引用");
  assert.ok(!inv.conclusions.some((c) => c.symbol === "ExamPaper"), "组卷结构不是结论");
});

test("输出不含样卷文字与原文片段", () => {
  for (const text of [renderInventoryMd(inv), renderJson(inv), renderConclusionsMd(inv)]) {
    assert.ok(!text.includes("虚构样卷甲同学"));
    assert.ok(!text.includes("虚构的样卷作答文字"));
    assert.deepEqual(longCjkRuns(text), []);
  }
});

test("确定性：再跑一次 JSON 逐字节相同", async () => {
  const again = await buildInventory(AYA, WENLI, [], (p) => p.slice(FIX.length));
  assert.equal(renderJson(again), renderJson(inv));
});

test("legacy 目录运行前后没有任何变化", () => {
  assert.deepEqual([digest(AYA), digest(WENLI)], before);
});
