// 内容包与登记脚本。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registeredAdmission, registrationSql } from "../scripts/register-content.ts";
import { indexPack } from "../src/content.ts";
import { keysOf, realPack } from "./helpers.ts";

test("内容包：721 道题、959 个节点、429 个陷阱码，可追溯到 legacy 提交", () => {
  const p = realPack.pack;
  assert.equal(p.items.length, 721);
  assert.equal(p.nodes.length, 959);
  assert.equal(p.traps.length, 429);
  assert.match(p.legacy_commit, /^[0-9a-f]{40}$/);
  assert.ok(p.pack_version.endsWith(p.legacy_commit.slice(0, 7)));
});

test("内容包格式 2：题目带用途、预估用时、提示阶梯；仍然没有题干、解析、前置关系（spec 设计要点第 0 节）", () => {
  const p = realPack.pack;
  assert.equal(p.format, "aya-content-pack/2");
  const keys = new Set(keysOf(p.items));
  for (const k of ["stem", "analysis", "figure", "text", "prerequisites", "preNodes", "auxNodes", "abilities", "difficulty"]) assert.ok(!keys.has(k), k);
  for (const k of ["stem", "prerequisites", "name", "core", "evidence"]) assert.ok(!new Set(keysOf(p.nodes)).has(k), `节点 ${k}`);
  const count = (f: (i: (typeof p.items)[number]) => string) =>
    p.items.reduce<Record<string, number>>((m, i) => ((m[f(i)] = (m[f(i)] ?? 0) + 1), m), {});
  // spec 现状一节核实过的数字（legacy 0ed2fe45）
  assert.deepEqual(count((i) => i.usage), { D: 419, T: 119, R: 126, P: 29, L: 28 });
  assert.deepEqual(count((i) => String(i.hintPath.length)), { "6": 600, "7": 121 });
  assert.ok(p.items.every((i) => Number.isInteger(i.estMinutes) && i.estMinutes >= 1));
  assert.equal(realPack.itemsByNode.size, 452);
  // 板块占比标注为 Q1 推导值；对应表在 KGE-01 导入阿鸦图谱之前为空
  assert.equal(p.board_exam_share.status, "q1_derived_uncalibrated");
  assert.ok(p.nodes.every((n) => n.board in p.board_exam_share.shares));
  assert.deepEqual(p.skill_links, { map_ref: null, links: [] });
});

test("内容包格式 2 的完整性检查：用途、用时、提示阶梯、板块、对应表不合格时拒绝加载", () => {
  const cases: [string, (p: ReturnType<typeof structuredClone<typeof realPack.pack>>) => void, RegExp][] = [
    ["用途", (p) => ((p.items[0] as { usage: string }).usage = "X"), /用途未知/],
    ["用时", (p) => (p.items[0]!.estMinutes = 0), /预估用时/],
    ["提示阶梯为空", (p) => (p.items[0]!.hintPath = []), /提示阶梯/],
    ["提示阶梯超过 7 级", (p) => (p.items[0]!.hintPath = Array(8).fill("（合成）")), /提示阶梯/],
    ["板块没有占比", (p) => (p.nodes[0]!.board = "不存在的板块"), /没有中考占比/],
    ["占比未标注", (p) => ((p.board_exam_share as { status: string }).status = "calibrated"), /q1_derived_uncalibrated/],
    ["对应表缺地图版本", (p) => (p.skill_links = { map_ref: null, links: [{ skill_node: p.nodes[0]!.id, node_id: "MA.ALG.EXP_0001" }] }), /地图版本/],
    ["对应表知识节点格式", (p) => (p.skill_links = { map_ref: "aya-map@1.0.0", links: [{ skill_node: p.nodes[0]!.id, node_id: "not-a-node" }] }), /KGE NodeId/],
    ["对应表阿鸦节点未登记", (p) => (p.skill_links = { map_ref: "aya-map@1.0.0", links: [{ skill_node: "K-NOPE-0000", node_id: "MA.ALG.EXP_0001" }] }), /未登记/],
  ];
  for (const [name, mutate, re] of cases) {
    const broken = structuredClone(realPack.pack);
    mutate(broken);
    assert.throws(() => indexPack(broken), re, name);
  }
});

test("内容包引用不完整时拒绝加载", () => {
  const broken = structuredClone(realPack.pack);
  broken.items[0]!.mainNodes = ["K-DOES-NOT-EXIST"];
  assert.throws(() => indexPack(broken), /未登记/);
  const badTrap = structuredClone(realPack.pack);
  badTrap.traps = badTrap.traps.slice(1);
  assert.throws(() => indexPack(badTrap), /未知陷阱/);
});

test("登记 SQL：以 starbuddy_learning 身份写入，全部题目登记为 source_unverified，只增不改", () => {
  const sql = registrationSql(realPack);
  assert.match(sql, /SET LOCAL ROLE starbuddy_learning;/);
  assert.equal((sql.match(/::platform\.admission\)/g) ?? []).length, 721);
  assert.equal((sql.match(/'source_unverified'::platform\.admission/g) ?? []).length, 721);
  assert.equal((sql.match(/ON CONFLICT .* DO NOTHING;/g) ?? []).length, 3);
  assert.doesNotMatch(sql, /\b(UPDATE|DELETE|TRUNCATE|DROP)\b/);
  assert.doesNotMatch(sql, /'admitted'::platform\.admission/);
});

test("登记不绕过发布闸门：内容包里 admitted 的题也只登记为 pending_review", () => {
  assert.equal(registeredAdmission("admitted"), "pending_review");
  assert.equal(registeredAdmission("source_unverified"), "source_unverified");
  assert.equal(registeredAdmission("rejected"), "rejected");
});

test("适配器源码不引用测试代码、不连数据库（规则 1）", async () => {
  const { readdirSync, readFileSync } = await import("node:fs");
  const walk = (d: string): string[] =>
    readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(`${d}/${e.name}`) : [`${d}/${e.name}`]));
  for (const f of walk(new URL("../src", import.meta.url).pathname)) {
    const src = readFileSync(f, "utf8");
    assert.doesNotMatch(src, /from\s+"[^"]*tests\//, f);
    assert.doesNotMatch(src, /from\s+"(pg|postgres|@supabase\/[^"]*)"/, f);
    assert.doesNotMatch(src, /\bplatform\.[a-z_]+/, f);
  }
});
