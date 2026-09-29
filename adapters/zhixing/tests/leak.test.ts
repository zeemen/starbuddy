// 不泄露（规则 3、规则 13；契约 Problem.detail）：响应里除学生原文外只有受控值（ID、编码、版本、时间、数字白名单）；
// 题面、材料、Rubric 关键词、常见失分形态、模型原文都不出现在响应与日志里。全部是合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LogRecord } from "../src/adapter.ts";
import { ANSWERS, context, gradeBody, harness, leaves, realPack, TIME, type Json } from "./helpers.ts";
import { MARKERS } from "./route-stub.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODE = /^[a-z0-9_\-]{1,48}$/;
const REF = /^zhixing:[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSIONED = /^((algo|model|rubric|prompt|items):)?[a-z0-9][a-z0-9\-/]*(\/[A-Za-z0-9._\-]+)?@[A-Za-z0-9._\-]+(;[a-z]+:[a-z0-9\-]+@[A-Za-z0-9._\-]+)*$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const NUMERIC_PATH = /\/(confidence|score|max_score|total|max_total|points_awarded|span_start|span_end|exam_year|grade)$/;

/** 内容包里不能外泄的文字：题面、材料、设问、Rubric 关键词与说明、常见失分形态 */
const SECRETS = realPack.pack.items.flatMap((it) => [
  it.title,
  it.material_text,
  it.prompt_question,
  ...it.points.flatMap((p) => [
    ...p.keyword_family.core,
    ...p.keyword_family.accepted,
    ...p.keyword_family.colloquial,
    p.partial_credit_rule.description,
    ...(p.required_logic ? [p.required_logic] : []),
    ...(p.material_linkage ? [p.material_linkage] : []),
    ...p.common_errors.flatMap((e) => [e.pattern, e.note]),
  ]),
]);

function assertOnlyControlledStrings(body: Json, studentInputs: string[]) {
  for (const [path, v] of leaves(body)) {
    if (typeof v === "number") {
      assert.match(path, NUMERIC_PATH, `数字字段 ${path} 不在白名单里`);
      continue;
    }
    if (typeof v !== "string") continue;
    if (/^\/evidence\/\d+\/payload\/text$/.test(path)) {
      assert.ok(studentInputs.includes(v), `${path} 只能是学生原文`);
      continue;
    }
    const controlled = UUID.test(v) || CODE.test(v) || REF.test(v) || VERSIONED.test(v) || SEMVER.test(v) || v === TIME;
    assert.ok(controlled, `${path} = ${JSON.stringify(v)} 不是受控值`);
    for (const s of SECRETS) assert.ok(!v.includes(s), `${path} 含内容包文字`);
  }
}

test("grade：正常、部分、零分、各种待复核情况的响应里都只有受控值与学生原文", async () => {
  const h = await harness();
  try {
    const answers = [ANSWERS.full01, ANSWERS.partial01, ANSWERS.miss01, ...Object.values(MARKERS).filter((m) => m !== MARKERS.unavailable).map((m) => ANSWERS.partial01 + m)];
    for (const text of answers) {
      for (const item of realPack.pack.items) {
        const r = await h.adapter.handle("POST", "grade", gradeBody(item.id, text));
        assert.equal(r.status, 200);
        assertOnlyControlledStrings(r.body, [text]);
      }
    }
  } finally {
    await h.close();
  }
});

test("diagnose 与 generate_variation 同样只有受控值与学生原文", async () => {
  const h = await harness();
  try {
    const d = await h.adapter.handle("POST", "diagnose", {
      context: context(),
      mode: "process",
      attempts: [{ item_ref: "zhixing:synthetic-m3-01", occurred_at: TIME, response: { text: ANSWERS.partial01 } }],
    });
    assertOnlyControlledStrings(d.body, [ANSWERS.partial01]);
    const c = context() as Json;
    delete c.usage_id;
    const v = await h.adapter.handle("POST", "generate_variation", { context: c, source_item_ref: "zhixing:synthetic-m3-01", count: 2 });
    assertOnlyControlledStrings(v.body, []);
  } finally {
    await h.close();
  }
});

test("日志只含方法、状态码、错误码、request_id、耗时、路由失败类别；不含作答、题面、模型输出", async () => {
  const h = await harness();
  try {
    const secret = "学生作答原文-SYNTHETIC-7f3a";
    const logs: LogRecord[] = h.logs;
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", secret));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", secret + MARKERS.notJson));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", secret + MARKERS.unavailable));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-99", secret));
    await h.adapter.handle("POST", "grade", { context: { request_id: secret }, attempt: { response: { text: secret } } });
    assert.ok(logs.length >= 5);
    const allowed = new Set(["method", "status", "code", "request_id", "duration_ms", "error_class", "route_failure"]);
    for (const r of logs) {
      for (const k of Object.keys(r)) assert.ok(allowed.has(k), `日志字段 ${k}`);
      if (r.request_id) assert.match(r.request_id, UUID);
      assert.match(r.method, /^(capabilities|grade|diagnose|plan|teach|generate_variation|report|jobs|unknown)$/);
    }
    const text = JSON.stringify(logs);
    assert.ok(!text.includes(secret));
    for (const s of SECRETS) assert.ok(!text.includes(s));
    assert.ok(logs.some((r) => r.route_failure === "unavailable"));
  } finally {
    await h.close();
  }
});

test("题面与 Rubric 只进模型路由（作为 Prompt），不进响应", async () => {
  const h = await harness();
  try {
    const r = await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01));
    const prompt = h.stub.calls.at(-1)!.messages.map((m) => m.content).join("\n");
    const item = realPack.itemById.get("synthetic-m3-01")!;
    assert.ok(prompt.includes(item.material_text));
    assert.ok(prompt.includes(item.points[0]!.keyword_family.core[0]!));
    const body = JSON.stringify(r.body);
    assert.ok(!body.includes(item.material_text) && !body.includes(item.prompt_question));
  } finally {
    await h.close();
  }
});
