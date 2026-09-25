// 不泄露答案（decisions #4；验收：响应和日志里都不出现答案、解析、评分标准）。
// 做法是结构白名单：响应里每个字符串字段，要么是 ID、时间、编码、版本这类受控值，
// 要么就是学生自己的作答原文（只允许出现在作答证据的 payload 里）。标准答案因此无处可放。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LogRecord } from "../src/adapter.ts";
import { normalizeFill } from "../src/core/grading.ts";
import { context, gradeBody, leaves, makeAdapter, realPack, sweepCases, type Json } from "./helpers.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODE = /^[a-z0-9_\-]{1,48}$/; // 枚举、错因编码、维度 ID
const REF = /^aya:[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSIONED = /^(algo:|verifier:|items:)?[a-z0-9][a-z0-9\-]*@[A-Za-z0-9._\-]+(;[a-z]+:[a-z0-9\-]+@[A-Za-z0-9._\-]+)*$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const TIME = "2026-10-08T19:30:00+08:00";

// 数字只能出现在这些字段：分数、置信度、采样点数（结构白名单，任何新增的数字字段都会让测试失败）
const NUMERIC_PATH = /\/(confidence|score|max_score|total|max_total|sampled_points|agreeing_points)$/;

function assertOnlyControlledStrings(body: Json, studentInputs: string[]) {
  for (const [path, v] of leaves(body)) {
    if (typeof v === "number") {
      assert.match(path, NUMERIC_PATH, `数字字段 ${path} 不在白名单里`);
      continue;
    }
    if (typeof v !== "string") continue;
    if (/^\/evidence\/\d+\/payload\/(text|choice\/\d+)$/.test(path)) {
      assert.ok(studentInputs.includes(v), `${path} 只能是学生原文`);
      continue;
    }
    const controlled = UUID.test(v) || CODE.test(v) || REF.test(v) || VERSIONED.test(v) || SEMVER.test(v) || v === TIME;
    assert.ok(controlled, `${path} = ${JSON.stringify(v)} 不是受控值`);
  }
}

test("全部题目：grade 响应里除学生原文外没有任何自由文本（答案、解析、题干、提示都放不进来）", () => {
  const adapter = makeAdapter();
  for (const c of sweepCases()) {
    const b = adapter.handle("POST", "grade", gradeBody(c.item.id, c.response)).body as Json;
    const inputs = "text" in c.response ? [c.response.text] : c.response.choice;
    assertOnlyControlledStrings(b, inputs);
  }
});

test("答错的选择题：响应里不出现正确选项的 key", () => {
  const adapter = makeAdapter();
  for (const item of realPack.pack.items.filter((i) => i.format === "single")) {
    const wrong = item.options!.find((o) => !item.answer.includes(o.key))!;
    const b = adapter.handle("POST", "grade", gradeBody(item.id, { choice: [wrong.key] })).body as Json;
    const choices = leaves(b).filter(([p]) => /payload\/choice/.test(p)).map(([, v]) => v);
    assert.deepEqual(choices, [wrong.key]);
  }
});

test("diagnose 响应同样只有受控值与学生原文", () => {
  const cases = sweepCases().slice(0, 200);
  const b = makeAdapter().handle("POST", "diagnose", {
    context: context(),
    mode: "gap_test",
    attempts: cases.map((c) => ({ item_ref: `aya:${c.item.id}`, occurred_at: TIME, response: c.response })),
  }).body as Json;
  const inputs = cases.flatMap((c) => ("text" in c.response ? [c.response.text] : c.response.choice));
  assertOnlyControlledStrings(b, inputs);
});

test("日志只含方法、状态码、错误码、request_id、耗时，不含作答或答案", () => {
  const logs: LogRecord[] = [];
  const adapter = makeAdapter(realPack, logs);
  const secret = "学生作答原文-SYNTHETIC-7f3a";
  adapter.handle("POST", "grade", gradeBody("Q-EQU-U03-D-001", { text: secret }));
  adapter.handle("POST", "grade", gradeBody("NOT-AN-ITEM", { text: secret }));
  adapter.handle("POST", "grade", { context: { request_id: secret }, attempt: { response: { text: secret } } });
  for (const c of sweepCases().slice(0, 300)) adapter.handle("POST", "grade", gradeBody(c.item.id, c.response));
  assert.ok(logs.length >= 303);
  const allowed = new Set(["method", "status", "code", "request_id", "duration_ms", "error_class"]);
  for (const r of logs) {
    for (const k of Object.keys(r)) assert.ok(allowed.has(k), `日志字段 ${k}`);
    if (r.request_id) assert.match(r.request_id, UUID);
  }
  assert.ok(!JSON.stringify(logs).includes(secret));
  // 日志里的字符串字段只能是已知方法名、错误码、UUID
  const answers = new Set(realPack.pack.items.flatMap((i) => [...i.answer, ...i.answer.map(normalizeFill)]));
  for (const r of logs) {
    assert.match(r.method, /^(capabilities|grade|diagnose|plan|teach|generate_variation|report|jobs|unknown)$/);
    for (const v of Object.values(r)) if (typeof v === "string") assert.ok(!answers.has(v) || v === r.method, `日志里出现了答案 ${v}`);
  }
});

test("全部填空题答错时：响应里除学生作答外，没有任何字符串字段等于标准答案或其归一化形式", () => {
  const adapter = makeAdapter();
  const wrong = "987654321";
  for (const item of realPack.pack.items.filter((i) => i.format === "fill")) {
    const b = adapter.handle("POST", "grade", gradeBody(item.id, { text: wrong })).body as Json;
    assert.notEqual(b.result.verdict, "correct", item.id);
    const secrets = new Set(item.answer.flatMap((a) => [a, normalizeFill(a)]));
    for (const [path, v] of leaves(b)) {
      // 数字叶子已由路径白名单约束（采样点数 8 与答案“8”相同属于巧合），这里只比字符串
      if (typeof v !== "string" || /\/payload\/text$/.test(path)) continue;
      assert.ok(!secrets.has(v), `${item.id} ${path} 等于标准答案`);
    }
  }
});
