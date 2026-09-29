// 题库导入：items.sql 的列名与迁移 0011 / 0024 一致、SQL 转义、确定性（两次、打乱输入顺序都逐字节相同）、
// 状态分流（item_tag / 候选 / 无处可标 / 未导入主题）、六段推导与工作单、报告格式。全部合成数据，不读 legacy。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { buildItems, type BuildInput } from "../src/items/build.ts";
import { insert, ITEM_FAMILY_COLUMNS, ITEM_TAG_COLUMNS, nodeArray, num3, q, SqlError, TAG_CANDIDATE_COLUMNS } from "../src/items/sql.ts";
import { FakeTagModel } from "../src/tagging/model.ts";
import { makePipeline, type PipelineOptions } from "../src/tagging/pipeline.ts";
import { buildRuleContext } from "../src/tagging/rules.ts";
import { FakeNgramEmbedder } from "../src/tagging/vector.ts";
import { MV, synthBank, synthMap, synthSegments } from "./fixtures.ts";

const MIG = new URL("../../../db/migrations/", import.meta.url);

async function input(opts: PipelineOptions = {}, bank = synthBank()): Promise<BuildInput> {
  const map = synthMap();
  return { bank, map, segments: synthSegments(), registeredItems: new Set(bank.questions.map((x) => x.id)),
           pipeline: await makePipeline(map, buildRuleContext(map, bank.topics), opts) };
}
const full = () => input({ embedder: new FakeNgramEmbedder(), model: new FakeTagModel() });
const item = (r: Awaited<ReturnType<typeof buildItems>>, id: string) => r.report.items.find((i) => i.item_id === id)!;

const MIG_FILES = ["0011_kge__knowledge_map.up.sql", "0024_kge__tagging.up.sql"];

/** 从迁移文件里取表的列名（CREATE TABLE 的列定义行与每一条 ALTER TABLE … ADD COLUMN） */
function columnsOf(table: string): Set<string> {
  const cols = new Set<string>();
  for (const f of MIG_FILES) {
    const sql = readFileSync(new URL(f, MIG), "utf8");
    const create = new RegExp(`CREATE TABLE kge\\.${table} \\(([\\s\\S]*?)\\n\\);`).exec(sql);
    if (create) for (const m of create[1]!.matchAll(/^\s{2}([a-z_]+)\s+(uuid|text|numeric|timestamptz|smallint|boolean|jsonb|platform\.|kge\.)/gm)) cols.add(m[1]!);
    for (const alter of sql.matchAll(new RegExp(`ALTER TABLE (?:ONLY )?kge\\.${table}\\s([\\s\\S]*?);`, "g"))) {
      for (const m of alter[1]!.matchAll(/ADD COLUMN (?:IF NOT EXISTS )?([a-z_]+)/g)) cols.add(m[1]!);
    }
  }
  return cols;
}

test("生成的列名都在迁移 0011 / 0024 的表定义里；item_tag 带 0024 的打标来源列", async () => {
  const tag = columnsOf("item_tag");
  for (const c of ITEM_TAG_COLUMNS) assert.ok(tag.has(c), `item_tag.${c}`);
  for (const c of ["tag_confidence", "tag_source", "tagger_version", "tag_model_version", "tag_basis"]) assert.ok(tag.has(c), `item_tag.${c}`);
  const fam = columnsOf("item_family");
  for (const c of ITEM_FAMILY_COLUMNS) assert.ok(fam.has(c), `item_family.${c}`);
  const cand = columnsOf("tag_candidate");
  for (const c of TAG_CANDIDATE_COLUMNS) assert.ok(cand.has(c), `tag_candidate.${c}`);
  for (const c of ["id", "status", "decided_by", "decided_at", "created_at"]) assert.ok(!(TAG_CANDIDATE_COLUMNS as readonly string[]).includes(c), `${c} 用默认值`);
  assert.ok(cand.has("item_source_kind"), "tag_candidate.item_source_kind（迁移 0024）");
  assert.equal(cand.has("student_id"), false, "候选表没有学生键");
  // source_kind 的 CHECK 要收 'legacy'（迁移 0024 放宽）
  const mig24 = readFileSync(new URL(MIG_FILES[1]!, MIG), "utf8");
  assert.match(mig24, /source_kind[\s\S]{0,400}'legacy'/, "迁移 0024 的 source_kind CHECK 应含 'legacy'");
  const r = await buildItems(await full());
  assert.ok(r.sql.includes(`INSERT INTO kge.item_tag (${ITEM_TAG_COLUMNS.join(", ")}) VALUES`));
  assert.ok(r.sql.includes(`INSERT INTO kge.tag_candidate (${TAG_CANDIDATE_COLUMNS.join(", ")}) VALUES`));
  assert.ok(r.sql.includes(`INSERT INTO kge.item_family (${ITEM_FAMILY_COLUMNS.join(", ")}) VALUES`));
});

test("SQL 转义：单引号加倍、反斜杠按字面、控制字符拒绝；置信度 3 位小数、越界拒绝；空表不写 INSERT", async () => {
  assert.equal(q("O'Neil\\n"), "'O''Neil\\n'");
  assert.throws(() => q("a\nb"), SqlError);
  assert.throws(() => q("a\u0000"), SqlError);
  assert.equal(num3(0.9), "0.900");
  assert.equal(num3(null), "NULL");
  assert.throws(() => num3(1.2), SqlError);
  assert.equal(nodeArray(["A.B", "C'D"]), "ARRAY['A.B', 'C''D']::kge.node_id[]");
  assert.deepEqual(insert("kge.item_tag", ["a"], []), ["-- kge.item_tag：无"]);
  assert.throws(() => insert("kge.x", ["a", "b"], [["1"]]), SqlError);
});

test("事务与角色：单事务、standard_conforming_strings、以 starbuddy_kge 写入；不写六段、不含题目文本", async () => {
  const r = await buildItems(await full());
  const lines = r.sql.split("\n");
  assert.equal(lines.filter((l) => l === "BEGIN;").length, 1);
  assert.equal(lines.at(-2), "COMMIT;");
  assert.ok(r.sql.includes("SET LOCAL standard_conforming_strings = on;"));
  assert.ok(r.sql.includes("SET LOCAL ROLE starbuddy_kge;"));
  assert.equal(/INSERT INTO platform\./.test(r.sql), false, "items.sql 不写 platform.*（六段由登记脚本写）");
  for (const out of [r.sql, r.worklistMd, r.reportJson]) {
    assert.equal(out.includes("（合成题）"), false, "题目文本不落盘");
    assert.equal(out.includes("二次函数的概念 判断"), false);
  }
});

test("确定性：同一输入两次逐字节相同；打乱题目与专题顺序也相同", async () => {
  const a = await buildItems(await full());
  const b = await buildItems(await full());
  assert.equal(a.sql, b.sql);
  assert.equal(a.worklistMd, b.worklistMd);
  assert.equal(a.reportJson, b.reportJson);
  const bank = synthBank();
  bank.questions.reverse();
  bank.topics.reverse();
  const c = await buildItems(await input({ embedder: new FakeNgramEmbedder(), model: new FakeTagModel() }, bank));
  assert.equal(c.sql, a.sql);
  assert.equal(c.reportJson, a.reportJson);
  assert.equal(c.worklistMd, a.worklistMd);
});

test("默认只跑规则层：没有 item_tag，全部进候选（B 级）；--rules-only-a 时规则直接命中写 item_tag", async () => {
  const r = await buildItems(await input());
  assert.equal(r.report.counts.item_tag, 0);
  assert.equal(r.sql.includes("-- kge.item_tag：无"), true);
  assert.equal(r.report.counts.grade.A, 0);
  assert.ok(r.report.counts.candidate_rows > 0);
  assert.deepEqual(r.report.run.layers, ["rule"]);
  const ra = await buildItems(await input({ rulesOnlyA: true }));
  assert.equal(item(ra, "Q-FUN-U01-D-001").status, "item_tag");
  assert.equal(ra.report.run.rules_only_a, true);
});

test("状态分流：A 级写 item_tag（待审草稿、来源 legacy、版权 unverified、打标来源与版本、依据），B 级进候选，无处可标、未导入主题进工作单", async () => {
  const r = await buildItems(await full());
  const a = item(r, "Q-FUN-U01-D-001");
  assert.deepEqual([a.status, a.grade, a.main_node_id, a.layer, a.confidence], ["item_tag", "A", "MATH.FUNC.FUN_CON.K_FUN_0001", "rule", 0.9]);
  assert.ok(a.node_ids.length >= 1 && a.node_ids.length <= 3 && a.node_ids[0] === a.main_node_id);
  const row = r.sql.split("\n").find((l) => l.includes("'Q-FUN-U01-D-001'") && l.includes("'pending'"))!;
  assert.ok(row.startsWith(`  ('${MV}', 'aya', 'Q-FUN-U01-D-001', 'MATH.FUNC.FUN_CON.K_FUN_0001', ARRAY[`));
  assert.ok(row.includes("'understand', NULL, 'legacy', 'unverified', 'pending', 0.900, 'rule', 'aya-tagger-rules@0.1.0', NULL, '[{\"layer\":\"rule\""), row);
  // A 级依据：规则层 + 参与一致的非规则层（这里假模型同选主考点），报告与 SQL 相同
  assert.deepEqual(a.tag_basis.map((b) => b.layer).filter((l) => l !== "vector"), ["rule", "model"]);
  assert.deepEqual(a.tag_basis[0], { layer: "rule", version: "aya-tagger-rules@0.1.0" });
  assert.deepEqual(a.tag_basis.at(-1), { layer: "model", version: "model:fake-a/fake-tagger@0.0.0" });
  assert.ok(row.endsWith(`, '${JSON.stringify(a.tag_basis)}'::jsonb),`) || row.endsWith(`, '${JSON.stringify(a.tag_basis)}'::jsonb);`), row);
  assert.equal(r.sql.includes("'Q-FUN-U01-D-001', 'MATH"), true);
  assert.equal(/tag_candidate[\s\S]*'Q-FUN-U01-D-001'/.test(r.sql.split("INSERT INTO kge.tag_candidate")[1] ?? ""), false, "A 级不进候选表");

  const b = item(r, "Q-FUN-U02-D-001");
  assert.deepEqual([b.status, b.grade], ["candidate", "B"]);
  assert.ok(b.b_reasons.includes("rule_below_threshold"));
  const candSql = r.sql.split("INSERT INTO kge.tag_candidate")[1]!;
  assert.ok(candSql.includes(`'Q-FUN-U02-D-001', 'MATH.FUNC.FUN_VTX', 'main', 'rule', 0.600, 'aya-tagger-rules@0.1.0', NULL, 'rule_chapter_topic', 'B', 'legacy')`));
  assert.ok(candSql.includes("'model', 0.880, 'aya-tagger-model@0.1.0', 'model:fake-a/fake-tagger@0.0.0', 'model_choice', 'B', 'legacy')"));
  assert.deepEqual(b.tag_basis, [], "B 级没有依据");
  for (const l of candSql.split("\n").filter((x) => x.startsWith("  ("))) assert.ok(/, 'legacy'\)[,;]$/.test(l), l);

  assert.equal(item(r, "Q-PYT-U01-D-001").status, "out_of_scope");
  assert.ok(r.worklistMd.includes("pyt（1 题）：Q-PYT-U01-D-001"));
  assert.equal(r.report.counts.out_of_scope_by_chapter.pyt, 1);

  const rulesOnly = await buildItems(await input());
  assert.equal(item(rulesOnly, "Q-FUN-U03-D-001").status, "untaggable");
  assert.ok(rulesOnly.worklistMd.includes("Q-FUN-U03-D-001：K-PYT-0001（在未导入章节）"));
});

test("节点数上限：合并后超过 3 个节点的题截断、B 级，进工作单", async () => {
  const r = await buildItems(await input({ model: new FakeTagModel() }));
  const x = item(r, "Q-FUN-U05-D-001");
  assert.deepEqual([x.over_cap, x.grade, x.node_ids.length], [true, "B", 3]);
  assert.ok(r.worklistMd.includes("Q-FUN-U05-D-001：合并后超过 3 个节点"));
  for (const i of r.report.items) assert.ok(i.node_ids.length <= 3);
});

test("六段：按章节映射表推出；压轴专项推不出进工作单；A 级但六段未定只写候选（grade A）", async () => {
  const r = await buildItems(await full());
  assert.deepEqual(item(r, "Q-FUN-U01-D-001").six_segments, {
    region: "nanjing", exam_year: 2027, grade: 9, textbook_version: "suke-2024", subject: "aya", content_version: "0.1.0", term: "lower", chapter: "fun" });
  assert.equal(item(r, "Q-PYT-U01-D-001").six_segments!.grade, 8);
  const s = item(r, "Q-SPR-QF-V1");
  assert.deepEqual([s.six_segments, s.chapter, s.route, s.grade, s.status], [null, "fun", "main_nodes", "A", "candidate"]);
  assert.ok(r.worklistMd.includes("Q-SPR-QF-V1：压轴专项题没有章节"));
  assert.ok(r.worklistMd.includes("Q-SPR-QF-V1：A 级（MATH.FUNC.FUN_VTX.K_FUN_0002），但六段未定"));
  assert.ok(r.sql.includes("'Q-SPR-QF-V1', 'MATH.FUNC.FUN_VTX.K_FUN_0002', 'main', 'rule', 0.900, 'aya-tagger-rules@0.1.0', NULL, 'rule_explicit_ref', 'A', 'legacy')"));
  assert.equal(r.report.counts.six_segments_missing, 2);
});

test("映射表与 legacy chapterLabel 不一致：该章节推不出六段", async () => {
  const bank = synthBank();
  bank.topics[0]!.chapterLabel = "苏科版九上 · 第5章";
  const r = await buildItems(await input({}, bank));
  assert.equal(item(r, "Q-FUN-U01-D-001").six_segments, null);
  assert.ok(r.worklistMd.includes("与 legacy chapterLabel“苏科版九上 · 第5章”不一致"));
});

test("一题多章节：只登记首次出现（年级最早）的章节", async () => {
  const bank = synthBank();
  bank.questions.push({ ...bank.questions.find((x) => x.id === "Q-FUN-U01-D-001")!, topic: "pyt" });
  const r = await buildItems(await input({}, bank));
  assert.equal(item(r, "Q-FUN-U01-D-001").six_segments!.grade, 8);
  assert.ok(r.worklistMd.includes("Q-FUN-U01-D-001：另见于 fun，只登记首次出现的 pyt"));
});

test("题组：压轴专项按组生成 item_family（确定性 ID），主考点取组内直接命中最多的节点；item_tag 挂到题组", async () => {
  const r = await buildItems(await input({ rulesOnlyA: true }));
  assert.equal(r.report.families.length, 1);
  const f = r.report.families[0]!;
  assert.deepEqual([f.code, f.main_node_id, f.item_ids], ["aya-sprint-qf", "MATH.FUNC.FUN_VTX.K_FUN_0002", ["Q-SPR-QF-S1", "Q-SPR-QF-V1"]]);
  assert.match(f.id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-8[0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal((await buildItems(await input())).report.families[0]!.id, f.id);
  assert.equal(item(r, "Q-SPR-QF-V1").family_code, "aya-sprint-qf");
});

test("未在内容包登记的题不写库，进工作单", async () => {
  const i = await input({ rulesOnlyA: true });
  const r = await buildItems({ ...i, registeredItems: new Set(["Q-FUN-U02-D-001"]) });
  assert.equal(r.sql.includes("'Q-FUN-U01-D-001'"), false);
  assert.ok(r.worklistMd.includes("## 未在内容包登记的题"));
  assert.equal(item(r, "Q-FUN-U01-D-001").registered, false);
});

test("报告格式：format、层与版本、计数自洽；只含 ID、节点、置信度与理由码", async () => {
  const r = await buildItems(await full());
  const rep = JSON.parse(r.reportJson) as typeof r.report;
  assert.deepEqual(rep, r.report, "每题一行的写法仍是标准 JSON");
  assert.equal(r.reportJson.split("\n").filter((l) => l.startsWith("    {\"item_id\"")).length, rep.items.length);
  assert.equal(rep.format, "kge-tagging-report/1");
  assert.deepEqual(rep.run.layers, ["rule", "vector", "model"]);
  assert.equal(rep.run.model_version, "model:fake-a/fake-tagger@0.0.0");
  const c = rep.counts;
  assert.equal(c.items_total, rep.items.length);
  assert.equal(c.items_total, c.item_tag + c.candidate_items + c.untaggable + c.items_out_of_scope);
  assert.equal(c.grade.A + c.grade.B + c.untaggable, c.items_in_scope);
  assert.equal(c.candidate_rows, c.candidate_rows_by_layer.rule + c.candidate_rows_by_layer.vector + c.candidate_rows_by_layer.model);
  assert.equal((r.sql.match(/^ {2}\('/gm) ?? []).length, c.item_tag + c.candidate_rows + c.families);
  assert.deepEqual(rep.items.map((i) => i.item_id), [...rep.items.map((i) => i.item_id)].sort());
  assert.equal(rep.text_refs.total, 3);
  assert.equal(rep.text_refs.map_matched, 1);
  assert.ok(r.reportJson.endsWith("}\n"));
});
