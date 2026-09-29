// 内容包与登记脚本：合成、非真题；一律 pending_review；采分点字段对齐 KGE kge.scoring_point；登记 SQL 只增不改。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registeredAdmission, registrationSql } from "../scripts/register-content.ts";
import { indexPack, itemNodes, VERSIONED_NAME } from "../src/content.ts";
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
      assert.deepEqual(Object.keys(pt).sort(), ["common_errors", "keyword_family", "material_linkage", "partial_credit_rule", "point_id", "required_logic", "score", "skill_node", "subject_dimension"]);
      assert.match(pt.point_id, /^[a-z0-9_\-]{1,48}$/);
      assert.deepEqual(Object.keys(pt.keyword_family).sort(), ["accepted", "colloquial", "core"]);
      assert.ok(["all_or_nothing", "proportional", "stepwise"].includes(pt.partial_credit_rule.mode));
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
