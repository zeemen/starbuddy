// 库模式只读 SQL 的静态检查：正例是仓库里的 SQL，反例是逐项改坏的版本。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { checkReadonlySql } from "../src/sqlcheck.ts";

const SQL = readFileSync(new URL("../db/readonly-inventory.sql", import.meta.url), "utf8");

test("仓库里的只读 SQL 通过检查", () => {
  assert.deepEqual(checkReadonlySql(SQL), []);
});

const MUTANTS: [string, (s: string) => string, RegExp][] = [
  ["去掉 READ ONLY", (s) => s.replace("BEGIN TRANSACTION READ ONLY;", "BEGIN;"), /开头必须是/],
  ["ROLLBACK 改成 COMMIT", (s) => s.replace(/ROLLBACK;\s*$/, "COMMIT;\n"), /ROLLBACK/],
  ["加一条写语句", (s) => s.replace("ROLLBACK;", "DELETE FROM item_bank;\nROLLBACK;"), /写语句|不允许的语句/],
  ["SELECT *", (s) => s.replace("SELECT section, tbl", "SELECT *, section, tbl"), /SELECT \*/],
  ["查学生表", (s) => s.replace("('rows',    'questions',         NULL),", "('rows',    'profiles',          NULL),"), /学生表 profiles|不在白名单/],
  ["非白名单表", (s) => s.replace("'doc_chunks')", "'doc_chunks', 'jobs')"), /不在白名单：jobs/],
  ["其他元命令", (s) => s.replace("BEGIN TRANSACTION", "\\copy x to 'y'\nBEGIN TRANSACTION"), /元命令|不允许的语句/],
  ["分组列改成作者", (s) => s.replace("('group',   'daofa_item',        'status'),", "('group',   'daofa_item',        'created_by'),"), /不在列白名单：group daofa_item\.created_by/],
  ["分组列改成内容", (s) => s.replace("('group',   'item_bank',         'source'),", "('group',   'item_bank',         'content'),"), /不在列白名单：group item_bank\.content/],
  ["去掉分组上限", (s) => s.replace("GROUP BY 1 ORDER BY 1 LIMIT 50)", "GROUP BY 1)"), /50 组/],
  ["去掉 lock_timeout", (s) => s.replace("SET LOCAL lock_timeout = '2s';\n", ""), /开头必须是/],
  ["去掉 row_security = off", (s) => s.replace("SET LOCAL row_security = off;\n", ""), /开头必须是/],
  ["DO 块", (s) => s.replace("ROLLBACK;", "DO $$ BEGIN END $$;\nROLLBACK;"), /DO 块|不允许的语句/],
];
for (const [name, mutate, expected] of MUTANTS) {
  test(`反例：${name}`, () => {
    const mutated = mutate(SQL);
    assert.notEqual(mutated, SQL, "改坏没有生效");
    assert.match(checkReadonlySql(mutated).join("；"), expected);
  });
}
