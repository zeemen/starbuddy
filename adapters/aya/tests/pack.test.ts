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

test("内容包只含判分需要的字段：没有题干、解析、提示阶梯", () => {
  const keys = new Set(keysOf(realPack.pack.items));
  for (const k of ["stem", "analysis", "hintPath", "figure", "text"]) assert.ok(!keys.has(k), k);
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
