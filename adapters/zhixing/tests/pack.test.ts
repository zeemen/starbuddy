// 内容包与登记脚本：合成、非真题；一律 pending_review；采分点字段对齐 KGE kge.scoring_point；登记 SQL 只增不改。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registeredAdmission, registrationSql } from "../../../tools/content-register/zhixing.ts";
import { readFileSync } from "node:fs";
import { indexPack, itemNodes, KGE_NODE_ID, partialCreditProblems, VERSIONED_NAME } from "../src/content.ts";
import { leaves, realPack } from "./helpers.ts";

test("内容包是合成题（3–5 道，zhixing:synthetic-m3-0N），明确标注非真题，全部 pending_review", () => {
  const p = realPack.pack;
  assert.equal(p.synthetic, true);
  assert.match(p.notice, /合成/);
  assert.match(p.notice, /非真题/);
  assert.ok(p.items.length >= 3 && p.items.length <= 5);
  for (const it of p.items) {
    assert.match(it.id, /^synthetic-m3-0\d$/);
    assert.equal(it.synthetic, true);
    assert.equal(it.admission, "pending_review");
    assert.equal(it.discipline, "daofa");
    assert.match(it.title, /合成/);
    assert.match(it.material_text, /合成材料/);
  }
});

test("rubric_version 是契约 VersionedName；source_version 里写作 rubric:<名>@<版本>", () => {
  for (const it of realPack.pack.items) {
    assert.match(it.rubric_version, VERSIONED_NAME);
    assert.ok(!it.rubric_version.startsWith("rubric:"));
  }
});

test("采分点字段对齐 KGE scoring_point：point_id 小写、score、subject_dimension、keyword_family 三档、required_logic、material_linkage、partial_credit_rule、common_errors", () => {
  for (const it of realPack.pack.items) {
    for (const pt of it.points) {
      assert.deepEqual(Object.keys(pt).sort(), ["common_errors", "keyword_family", "material_linkage", "node_ids", "partial_credit_rule", "point_id", "required_logic", "score", "skill_node", "subject_dimension"]);
      assert.match(pt.point_id, /^[a-z0-9_\-]{1,48}$/);
      assert.deepEqual(Object.keys(pt.keyword_family).sort(), ["accepted", "colloquial", "core"]);
      assert.ok(["all_or_nothing", "proportional", "stepwise"].includes(pt.partial_credit_rule.mode));
      assert.deepEqual(Object.keys(pt.partial_credit_rule).sort(), ["allowed_scores", "description", "mode"]);
      assert.deepEqual(partialCreditProblems(pt.partial_credit_rule, pt.score), []);
      // node_ids：kge.node_id 格式（迁移 0011），MORAL 命名空间，1–8 个
      assert.ok(pt.node_ids.length >= 1 && pt.node_ids.length <= 8);
      for (const id of pt.node_ids) {
        assert.match(id, /^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$/);
        assert.ok(id.startsWith("MORAL.SYN."), id);
      }
    }
  }
});

test("能力视图节点：五个判分维度各一个合成节点；每道题的节点都已登记", () => {
  assert.deepEqual(realPack.pack.nodes.map((n) => n.dimension), ["shenti", "dingwei", "zhuanhua", "guifan", "shizheng"]);
  for (const it of realPack.pack.items) for (const n of itemNodes(it)) assert.ok(realPack.nodeIds.has(n));
});

test("没有真实学生数据的形态：没有手机号、身份证号、邮箱、真实学校名（规则 8）", () => {
  const text = JSON.stringify(realPack.pack);
  assert.ok(!/1[3-9]\d{9}/.test(text), "手机号");
  assert.ok(!/\d{17}[\dXx]/.test(text), "身份证号");
  assert.ok(!/[\w.]+@[\w-]+\.\w+/.test(text.replace(/"[a-z0-9\-]+@[\w.\-]+"/g, "")), "邮箱");
  for (const [, v] of leaves(realPack.pack)) {
    if (typeof v === "string" && /中学|小学/.test(v)) assert.match(v, /合成材料|虚构/, "学校名只出现在标注为虚构的合成材料里");
  }
});

test("内容包不完整时拒绝加载", () => {
  const broken = structuredClone(realPack.pack);
  broken.items[0]!.points[0]!.skill_node = "syn-dim-nope";
  assert.throws(() => indexPack(broken), /未登记/);
  const badSum = structuredClone(realPack.pack);
  badSum.items[0]!.points[0]!.score = 3;
  assert.throws(() => indexPack(badSum), /不等于满分/);
  const upper = structuredClone(realPack.pack);
  upper.items[0]!.points[0]!.point_id = "P1";
  assert.throws(() => indexPack(upper), /小写/);
  const rv = structuredClone(realPack.pack);
  rv.items[0]!.rubric_version = "rubric:zhixing-synthetic@0.1.0";
  assert.throws(() => indexPack(rv), /VersionedName/);
});

test("节点 ID 正则与迁移 0011 的 kge.node_id 域逐字一致", () => {
  const sql = readFileSync(new URL("../../../db/migrations/0011_kge__knowledge_map.up.sql", import.meta.url), "utf8");
  const m = /CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)/.exec(sql);
  assert.ok(m);
  assert.equal(KGE_NODE_ID.source, m[1]);
});

test("给分规则校验：allowed_scores 升序、含 0 与满分、0.5 倍数、不超过分值；all_or_nothing 只允许 0 与满分", () => {
  const bad: [unknown, RegExp][] = [
    [{ mode: "stepwise", allowed_scores: [0, 2, 1], description: "x" }, /升序/],
    [{ mode: "stepwise", allowed_scores: [1, 2], description: "x" }, /含 0/],
    [{ mode: "stepwise", allowed_scores: [0, 1], description: "x" }, /含满分/],
    [{ mode: "stepwise", allowed_scores: [0, 0.3, 2], description: "x" }, /0\.5 倍数/],
    [{ mode: "stepwise", allowed_scores: [0, 2, 3], description: "x" }, /含满分|超出/],
    [{ mode: "all_or_nothing", allowed_scores: [0, 1, 2], description: "x" }, /all_or_nothing/],
    [{ mode: "guess", allowed_scores: [0, 2], description: "x" }, /mode/],
    [{ mode: "stepwise", description: "x" }, /allowed_scores/],
  ];
  for (const [rule, re] of bad) {
    const pk = structuredClone(realPack.pack);
    (pk.items[0]!.points[0] as { partial_credit_rule: unknown }).partial_credit_rule = rule;
    assert.throws(() => indexPack(pk), re, JSON.stringify(rule));
  }
  const good = structuredClone(realPack.pack);
  good.items[0]!.points[0]!.partial_credit_rule = { mode: "all_or_nothing", allowed_scores: [0, 2], description: "合成" };
  assert.doesNotThrow(() => indexPack(good));
});

test("node_ids 校验：格式、MORAL 命名空间、1–8 个、不重复", () => {
  for (const ids of [[], ["moral.syn.x"], ["MATH.SYN.X"], ["MORAL"], Array.from({ length: 9 }, (_, i) => `MORAL.SYN.N${i}`), ["MORAL.SYN.A", "MORAL.SYN.A"]]) {
    const pk = structuredClone(realPack.pack);
    pk.items[0]!.points[0]!.node_ids = ids;
    assert.throws(() => indexPack(pk), /node_ids/, JSON.stringify(ids));
  }
});

test("登记 SQL：只输出 SQL，题目一律 pending_review，只增不改", () => {
  const sql = registrationSql(realPack);
  assert.match(sql, /^-- 生成自 adapters\/zhixing\/content\/pack\.json/);
  assert.equal((sql.match(/::platform\.admission/g) ?? []).length, realPack.pack.items.length);
  assert.ok(!sql.includes("'admitted'"));
  assert.equal((sql.match(/ON CONFLICT/g) ?? []).length, 3);
  assert.ok(!/\bUPDATE\b|\bDELETE\b/.test(sql));
  assert.equal(registeredAdmission("admitted"), "pending_review");
  assert.equal(registeredAdmission("rejected"), "rejected");
  for (const it of realPack.pack.items) assert.ok(sql.includes(`'${it.id}'`));
});
