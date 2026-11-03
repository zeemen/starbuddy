// 不泄露（规则 3、规则 13；契约 Problem.detail）：响应里除学生原文外只有受控值（ID、编码、版本、时间、数字白名单）；
// 作文题、写作要求、量表锚点、客观题题面、选项、标准答案、模型原文都不出现在响应与日志里。
// 写法同 adapters/zhixing/tests/leak.test.ts。全部是合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LogRecord } from "../src/adapter.ts";
import { attempt, context, diagnoseBody, ESSAYS, gradeBody, harness, leaves, planBody, realPack, TIME, type Json } from "./helpers.ts";
import { MARKERS } from "./route-stub.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODE = /^[A-Za-z0-9_\-]{1,48}$/;
const REF = /^wenli:[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
const VERSIONED = /^((algo|model|rubric|prompt|items):)?[a-z0-9][a-z0-9\-/]*(\/[A-Za-z0-9._\-]+)?@[A-Za-z0-9._\-]+(;[a-z]+:[a-z0-9\-]+@[A-Za-z0-9._\-]+)*$/;
const SEMVER = /^\d+\.\d+\.\d+$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
const NUMERIC_PATH = /\/(confidence|score|max_score|total|max_total|low|high|span_start|span_end|exam_year|grade|priority|estimated_minutes)$/;

/** 内容包里不能外泄的文字：作文题与要求、量表锚点、客观题题面、选项、填空答案 */
const SECRETS = [
  ...realPack.pack.essays.flatMap((e) => [e.title, e.prompt_text]),
  ...realPack.pack.rubric.dimensions.flatMap((d) => [d.label, ...d.levels.map((l) => l.anchor)]),
  realPack.pack.rubric.convention.label,
  ...realPack.pack.items.flatMap((it) => [it.stem, ...(it.type === "choice" ? Object.values(it.options) : it.answer.split(/[｜|]/))]),
  ...realPack.pack.domains.map((d) => d.label),
];

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
    const controlled = UUID.test(v) || CODE.test(v) || REF.test(v) || VERSIONED.test(v) || SEMVER.test(v) || v === TIME || RFC3339.test(v);
    assert.ok(controlled, `${path} = ${JSON.stringify(v)} 不是受控值`);
    for (const s of SECRETS) assert.ok(!v.includes(s), `${path} 含内容包文字`);
  }
}

test("grade：正常、一类卷、各种不可判情况的响应里都只有受控值与学生原文", async () => {
  const h = await harness();
  try {
    const texts = [ESSAYS.long, ESSAYS.short, ...Object.values(MARKERS).filter((m) => m !== MARKERS.unavailable).map((m) => ESSAYS.long + m)];
    for (const text of texts) {
      for (const e of realPack.pack.essays) {
        const r = await h.adapter.handle("POST", "grade", gradeBody(e.id, text));
        assert.equal(r.status, 200);
        assertOnlyControlledStrings(r.body, [text]);
      }
    }
  } finally {
    await h.close();
  }
});

test("diagnose 与 plan 同样只有受控值与学生原文", async () => {
  const h = await harness();
  try {
    const attempts = realPack.pack.items.map((it, i) => attempt(it.id, it.type === "choice" ? { choice: ["A"] } : { text: "合成作答" }, i));
    const d = await h.adapter.handle("POST", "diagnose", diagnoseBody(attempts));
    assert.equal(d.status, 200);
    assertOnlyControlledStrings(d.body, ["A", "合成作答"]);
    const p = await h.adapter.handle("POST", "plan", planBody([], { time_budget_minutes: 600 }));
    assert.equal(p.status, 200);
    assertOnlyControlledStrings(p.body, []);
  } finally {
    await h.close();
  }
});

test("日志只含方法、状态码、错误码、request_id、耗时、路由失败类别；不含作文、题面、模型输出", async () => {
  const h = await harness();
  try {
    const secret = "学生作文原文-SYNTHETIC-5d2c";
    const logs: LogRecord[] = h.logs;
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-essay-01", secret));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-essay-01", secret + MARKERS.notJson));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-essay-01", secret + MARKERS.unavailable));
    await h.adapter.handle("POST", "grade", gradeBody("synthetic-essay-99", secret));
    await h.adapter.handle("POST", "grade", { context: { request_id: secret }, attempt: { response: { text: secret } } });
    await h.adapter.handle("POST", "diagnose", diagnoseBody([attempt("synthetic-q03", { text: secret })]));
    await h.adapter.handle("POST", "plan", planBody([]));
    assert.ok(logs.length >= 7);
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

test("作文题与量表锚点只进模型路由（作为 Prompt），不进响应；客观题标准答案不进 Prompt 与响应", async () => {
  const h = await harness();
  try {
    const r = await h.adapter.handle("POST", "grade", gradeBody("synthetic-essay-01", ESSAYS.long));
    const prompt = h.stub.calls.at(-1)!.messages.map((m) => m.content).join("\n");
    const essay = realPack.essayById.get("synthetic-essay-01")!;
    assert.ok(prompt.includes(essay.prompt_text));
    assert.ok(prompt.includes(realPack.pack.rubric.dimensions[0]!.levels[3]!.anchor));
    for (const it of realPack.pack.items) assert.ok(!prompt.includes(it.stem));
    const body = JSON.stringify(r.body);
    assert.ok(!body.includes(essay.prompt_text) && !body.includes(essay.title));
    const ctx = context() as Json;
    assert.ok(!JSON.stringify(ctx).includes(essay.title));
  } finally {
    await h.close();
  }
});
