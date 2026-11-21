// 内容包与登记脚本：合成、非真题；一律 pending_review；五维量表每维 L1–L4 分值与锚点；能力格 ID 符合 kge.node_id（CHN）；
// 登记 SQL 只增不改。全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { registeredAdmission, registrationSql } from "../../../tools/content-register/wenli.ts";
import { indexPack, KGE_NODE_ID, VERSIONED_NAME } from "../src/content.ts";
import { leaves, realPack } from "./helpers.ts";

test("内容包标注“合成、非真题”；题目、作文题全部 pending_review 且标注合成", () => {
  const p = realPack.pack;
  assert.equal(p.synthetic, true);
  assert.match(p.notice, /合成/);
  assert.match(p.notice, /非真题/);
  for (const it of [...p.items, ...p.essays]) {
    assert.equal(it.synthetic, true);
    assert.equal(it.admission, "pending_review");
  }
  for (const it of p.items) assert.match(it.stem, /^（合成题）/);
  for (const e of p.essays) assert.match(e.title, /^（合成题）/);
});

test("2 道作文题（wenli:synthetic-essay-0N，带最低字数）；8–12 道客观题（选择、填空，wenli:synthetic-qNN，带标准答案）", () => {
  const p = realPack.pack;
  assert.equal(p.essays.length, 2);
  for (const e of p.essays) {
    assert.match(e.id, /^synthetic-essay-0\d$/);
    assert.ok(Number.isInteger(e.min_chars) && e.min_chars > 0);
    assert.equal(e.rubric_version, "wenli-essay-5d@0.1.0");
  }
  assert.ok(p.items.length >= 8 && p.items.length <= 12);
  assert.deepEqual(new Set(p.items.map((i) => i.type)), new Set(["choice", "fill"]));
  for (const it of p.items) {
    assert.match(it.id, /^synthetic-q\d{2}$/);
    if (it.type === "choice") assert.ok(it.answer.every((a) => a in it.options));
    else assert.ok(it.answer.length > 0);
  }
});

test("五维量表 wenli-essay-5d@0.1.0：审题立意、内容材料、结构层次、语言表达、卷面书写，每维 L1–L4 分值递增、L4 等于满分；加字数规范维", () => {
  const r = realPack.pack.rubric;
  assert.equal(r.rubric_version, "wenli-essay-5d@0.1.0");
  assert.match(r.rubric_version, VERSIONED_NAME);
  assert.deepEqual(r.dimensions.map((d) => d.label), ["审题立意", "内容材料", "结构层次", "语言表达", "卷面书写"]);
  for (const d of r.dimensions) {
    assert.deepEqual(d.levels.map((l) => l.level), ["L1", "L2", "L3", "L4"]);
    for (let i = 1; i < 4; i++) assert.ok(d.levels[i]!.score > d.levels[i - 1]!.score);
    assert.equal(d.levels[3]!.score, d.max_score);
    for (const l of d.levels) assert.match(l.anchor, /^（合成锚点）/);
  }
  assert.deepEqual(r.dimensions.filter((d) => d.requires_image).map((d) => d.dimension_id), ["juanmian_shuxie"]);
  assert.equal(r.convention.rule_ref, "wenli-convention-rules@0.1.0");
  assert.equal(r.dimensions.reduce((s, d) => s + d.max_score, 0) + r.convention.max_score, r.total_score);
});

test("合成能力格 8–12 个：ID 符合 kge.node_id、CHN 命名空间；每格属于一个带 ROI 权重的域；每道题挂一个已登记的格", () => {
  const p = realPack.pack;
  assert.ok(p.cells.length >= 8 && p.cells.length <= 12);
  for (const c of p.cells) {
    assert.match(c.id, KGE_NODE_ID);
    assert.ok(c.id.startsWith("CHN.SYN."), c.id);
    assert.ok(realPack.domainByCode.get(c.domain)!.roi > 0);
  }
  for (const it of [...p.items, ...p.essays]) assert.ok(realPack.cellById.has(it.cell), it.id);
});

test("节点 ID 正则与迁移 0011 的 kge.node_id 域逐字一致；学科前缀与 kge.subject_prefix('wenli') 一致", () => {
  const sql = readFileSync(new URL("../../../db/migrations/0011_kge__knowledge_map.up.sql", import.meta.url), "utf8");
  const m = /CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)/.exec(sql);
  assert.ok(m);
  assert.equal(KGE_NODE_ID.source, m[1]);
  assert.match(sql, /WHEN 'wenli' THEN 'CHN'/);
});

test("没有真实学生数据的形态：没有手机号、身份证号、邮箱、真实学校名（规则 8）", () => {
  const text = JSON.stringify(realPack.pack);
  assert.ok(!/1[3-9]\d{9}/.test(text), "手机号");
  assert.ok(!/\d{17}[\dXx]/.test(text), "身份证号");
  assert.ok(!/[\w.]+@[\w-]+\.\w+/.test(text.replace(/"[a-z0-9\-]+@[\w.\-]+"/g, "")), "邮箱");
  for (const [, v] of leaves(realPack.pack)) {
    if (typeof v === "string" && /中学|小学/.test(v)) assert.fail(`出现学校名：${v}`);
  }
});

test("内容包不完整时拒绝加载", () => {
  const broken: [(p: typeof realPack.pack) => void, RegExp][] = [
    [(p) => (p.items[0]!.cell = "CHN.SYN.NOPE"), /未登记/],
    [(p) => (p.cells[0]!.id = "chn.syn.d1"), /kge\.node_id/],
    [(p) => (p.cells[0]!.id = "MATH.SYN.D1_L1"), /CHN/],
    [(p) => (p.rubric.dimensions[0]!.levels[1]!.score = 2), /递增/],
    [(p) => (p.rubric.dimensions[0]!.max_score = 11), /L4 分值必须等于满分|总分/],
    [(p) => (p.rubric.rubric_version = "rubric:wenli-essay-5d@0.1.0"), /VersionedName/],
    [(p) => (p.essays[0]!.rubric_version = "wenli-essay-5d@9.9.9"), /不一致/],
    [(p) => ((p.items[0] as { answer: string[] }).answer = ["Z"]), /标准答案/],
    [(p) => p.items.splice(0, 5), /8–12/],
    [(p) => (p.notice = "内容"), /合成/],
    [(p) => (p.rubric.convention.rule_ref = "规则"), /rule_ref/],
  ];
  for (const [mutate, re] of broken) {
    const p = structuredClone(realPack.pack);
    mutate(p);
    assert.throws(() => indexPack(p), re, String(mutate));
  }
});

test("登记 SQL：只输出 SQL，题目一律 pending_review，只增不改；能力格与全部题目都登记", () => {
  const sql = registrationSql(realPack);
  assert.match(sql, /^-- 生成自 adapters\/wenli\/content\/pack\.json/);
  const all = [...realPack.pack.items, ...realPack.pack.essays];
  assert.equal((sql.match(/::platform\.admission/g) ?? []).length, all.length);
  assert.ok(!sql.includes("'admitted'"));
  assert.equal((sql.match(/ON CONFLICT/g) ?? []).length, 3);
  assert.ok(!/\bUPDATE\b|\bDELETE\b/.test(sql));
  assert.equal(registeredAdmission("admitted"), "pending_review");
  assert.equal(registeredAdmission("rejected"), "rejected");
  for (const it of all) assert.ok(sql.includes(`('${it.id}', '${it.cell}'`));
  for (const c of realPack.pack.cells) assert.ok(sql.includes(`('${c.id}')`));
});
