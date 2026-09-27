// 文本解析工具：括号配对、顶层字段、库模式输出、原文片段检查
import assert from "node:assert/strict";
import { test } from "node:test";
import { topLevelKeys } from "../src/conclusions.ts";
import { extractBlock } from "../src/legacy.ts";
import { parseDbOutput } from "../src/main.ts";
import { longCjkRuns } from "../src/report.ts";

test("extractBlock：跳过字符串与注释里的括号", () => {
  const text = `const X = [ { a: "]" }, // ]\n /* ] */ { b: '[' } ];\nconst Y = 1;`;
  assert.equal(extractBlock(text, /const X\s*=/)?.trim(), `{ a: "]" }, // ]\n /* ] */ { b: '[' }`);
  assert.equal(extractBlock(text, /const Z\s*=/), null);
  assert.equal(extractBlock("create table t (a int, b numeric(3,1))", /create table t\s*/, "("), "a int, b numeric(3,1)");
});

test("topLevelKeys：只取顶层字段", () => {
  assert.deepEqual(topLevelKeys("\n  a: string;\n  b?: { c: number; d: string };\n  readonly e: number[]\n"), ["a", "b", "e"]);
  assert.deepEqual(topLevelKeys(`x: z.object({ y: z.string() }), // 注释 w: 1\n "q": z.number()`), ["x", "q"]);
});

test("parseDbOutput：section|key|value，值里可以有竖线", () => {
  assert.deepEqual(parseDbOutput("wenli", "rows|item_bank|3\ncolumns|t|a,b\n\ngroup|t.k|{\"a|b\":1}\n"), [
    { database: "wenli", key: "rows:item_bank", value: "3" },
    { database: "wenli", key: "columns:t", value: "a,b" },
    { database: "wenli", key: "group:t.k", value: "{\"a|b\":1}" },
  ]);
});

test("longCjkRuns：40 个以上连续汉字为正例，带标点的句子为反例", () => {
  assert.equal(longCjkRuns("甲".repeat(40)).length, 1);
  assert.equal(longCjkRuns("甲".repeat(39)).length, 0);
  assert.equal(longCjkRuns(`${"甲".repeat(30)}，${"乙".repeat(30)}`).length, 0);
});
