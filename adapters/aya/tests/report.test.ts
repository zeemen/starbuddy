// report（spec 设计要点第 3 节、验收标准 report 各条）。全部是合成数据（规则 8）：证据 ID、作答 ID、作答原文都是编造的。
import assert from "node:assert/strict";
import { test } from "node:test";
import { HIGHLIGHT_CODES, REPORT_ALGORITHM, trendOf, STATE_ORDER } from "../src/methods/report.ts";
import { contextV1, contractV1, keysOf, leaves, makeAdapterV1, realPack, type Json } from "./helpers.ts";

const adapter = makeAdapterV1();
const WINDOW = { from: "2026-10-05T00:00:00+08:00", to: "2026-10-12T00:00:00+08:00" };
const SK = "aya:P-MUL-0812";
const SK2 = "aya:P-LEQ-0409";
const eid = (n: number) => `e0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const aid = (n: number) => `a0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const SECRET = "学生作答原文-SYNTHETIC-report-9c1d";

function ev(n: number, attempt: number, occurred_at: string, extra: Record<string, unknown> = {}) {
  return {
    evidence_id: eid(n),
    attempt_id: aid(attempt),
    kind: "response_text",
    occurred_at,
    item_ref: "aya:Q-MUL-U03-D-001",
    skill_node: SK,
    admission: "source_unverified",
    payload: { text: SECRET },
    ...extra,
  };
}
const trace = (n: number, attempt: number, occurred_at: string) => ({
  ...ev(n, attempt, occurred_at),
  kind: "verifier_trace",
  payload: { sampled_points: 8, agreeing_points: 0, reason_codes: [] },
});
function attr(evidenceIds: string[], extra: Record<string, unknown> = {}) {
  return {
    skill_node: SK,
    subject_category: "yunsuan_cuowu",
    cross_subject_category: "computation_or_grammar",
    confidence: 0.8,
    evidence_ids: evidenceIds,
    algorithm_version: "aya-trap-attr@0.1.0-tax0.9.0",
    ...extra,
  };
}
function report(extra: Record<string, unknown>, a = adapter) {
  const r = a.handle("POST", "report", { context: contextV1(), window: WINDOW, audience: "parent", ...extra });
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  assert.deepEqual(contractV1.validate("ReportResponse", r.body, "1.0.0"), { ok: true });
  return r.body as Json;
}

// ---------- 正常 ----------
test("report 正常：按能力点给 trend 与 attempts、按大类与编码给错因分布；只有字段与证据引用，没有原文、没有 forecast、没有文本", () => {
  const b = report({
    evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00"), trace(2, 1, "2026-10-08T19:30:00+08:00"), ev(3, 2, "2026-10-09T19:30:00+08:00", { skill_node: SK2, item_ref: "aya:Q-LEQ-U02-D-002" })],
    error_attributions: [attr([eid(1)]), attr([eid(3)], { skill_node: SK2, confidence: 0.6 })],
    mastery_history: [
      { skill_node: SK, state: "understand", at: "2026-10-01T20:00:00+08:00" },
      { skill_node: SK, state: "execute", at: "2026-10-09T20:00:00+08:00" },
    ],
    learner_context: [{ field_id: "outer.g1.weekly_available_time", source_type: "self", value: 300 }],
  });
  assert.deepEqual(b.result, {
    skill_summary: [
      { skill_node: SK2, trend: "new", attempts: 1 },
      { skill_node: SK, trend: "up", attempts: 1 },
    ],
    error_distribution: [{ cross_subject_category: "computation_or_grammar", subject_category: "yunsuan_cuowu", count: 2 }],
    highlight_codes: ["skills_improved"],
  });
  // 计入统计的证据：两条作答证据（验证器轨迹不计入 attempts，也没有被错因引用）
  assert.deepEqual(b.evidence_ids, [eid(1), eid(3)]);
  assert.equal(b.confidence, 0.6); // 所用错因记录置信度的最小值
  assert.equal(b.semantics, "practice"); // 计入的证据未准入
  assert.equal(b.human_review_required, false);
  assert.equal(b.model_version, `algo:${REPORT_ALGORITHM}`);
  assert.ok(!JSON.stringify(b).includes(SECRET));
  assert.ok(!keysOf(b).includes("forecast"));
  for (const c of b.result.highlight_codes) assert.ok((HIGHLIGHT_CODES as readonly string[]).includes(c));
  // 除编码、ID、版本外没有字符串（结论句无处可放）
  for (const [path, v] of leaves(b)) {
    if (typeof v === "string") assert.match(v, /^[a-z0-9_:;@.\-A-Z]+$/, path);
  }
});

test("report 三种受众输出同样的字段；learner_context 不参与计算", () => {
  const body = { evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00")], mastery_history: [{ skill_node: SK, state: "execute", at: "2026-10-01T00:00:00Z" }] };
  const outs = (["student", "parent", "teacher"] as const).map((audience) => report({ ...body, audience }));
  const withCtx = report({ ...body, learner_context: [{ field_id: "inner.g3.node_mastery", source_type: "inferred", value: "stable", claim_id: "00000000-0000-4000-8000-0000000000f7" }] });
  for (const o of [...outs, withCtx]) {
    assert.deepEqual(o.result, outs[0].result);
    assert.deepEqual(o.evidence_ids, outs[0].evidence_ids);
  }
});

test("report attempts：同一 attempt_id 的多条证据只计一次；窗外证据不计入、不进 evidence_ids（按时刻比较，from 含、to 不含）", () => {
  const b = report({
    evidence: [
      ev(1, 1, "2026-10-08T19:30:00+08:00"),
      ev(2, 1, "2026-10-08T19:30:00+08:00", { kind: "response_choice", payload: { choice: ["A"] } }), // 同一道作答的另一条作答证据
      ev(3, 2, "2026-10-05T00:00:00+08:00"), // 恰在 from：计入
      ev(4, 3, "2026-10-04T16:30:00Z"), // = 10-05 00:30+08:00：窗内（字符串比较会误判为窗前）
      ev(5, 4, "2026-10-12T00:00:00+08:00"), // 恰在 to：不计
      ev(6, 5, "2026-10-04T23:59:59+08:00"), // 窗前
      ev(7, 6, "2026-10-11T16:30:00Z"), // = 10-12 00:30+08:00：窗后（字符串比较会误判为窗内）
    ],
  });
  assert.deepEqual(b.result.skill_summary, [{ skill_node: SK, trend: "new", attempts: 3 }]);
  assert.deepEqual(b.evidence_ids, [eid(1), eid(2), eid(3), eid(4)]);
});

test("report 错因：敏感类（心理与习惯）不计入分布；引用的证据全部在窗外的错因不计入", () => {
  const b = report({
    evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00"), ev(9, 9, "2026-09-01T10:00:00+08:00")],
    error_attributions: [
      attr([eid(1)]),
      attr([eid(1)], { subject_category: "xinli_xiguan", cross_subject_category: "strategy_and_time", confidence: 0.1 }),
      attr([eid(9)], { subject_category: "gainian_cuowu", cross_subject_category: "knowledge", confidence: 0.2 }),
    ],
  });
  assert.deepEqual(b.result.error_distribution, [{ cross_subject_category: "computation_or_grammar", subject_category: "yunsuan_cuowu", count: 1 }]);
  assert.equal(b.confidence, 0.8); // 没计入的错因不影响置信度
  assert.deepEqual(b.evidence_ids, [eid(1)]);
});

test("report 七种 mastery_history 组合：up、flat、down、new，以及窗内无新记录、记录恰在 from、记录在 to 之后", () => {
  const F = Date.parse(WINDOW.from);
  const T = Date.parse(WINDOW.to);
  const at = (s: string) => Date.parse(s);
  type S = (typeof STATE_ORDER)[number];
  const cases: [string, [S, string][], string][] = [
    ["up", [["understand", "2026-10-01T00:00:00+08:00"], ["execute", "2026-10-08T00:00:00+08:00"]], "up"],
    ["flat", [["execute", "2026-10-01T00:00:00+08:00"], ["execute", "2026-10-08T00:00:00+08:00"]], "flat"],
    ["down", [["stable", "2026-10-01T00:00:00+08:00"], ["execute", "2026-10-08T00:00:00+08:00"], ["understand", "2026-10-09T00:00:00+08:00"]], "down"],
    ["窗前没有记录", [["execute", "2026-10-06T00:00:00+08:00"], ["stable", "2026-10-08T00:00:00+08:00"]], "new"],
    ["窗内没有新记录（只有平台附上的窗前基线）", [["stable", "2026-09-20T00:00:00+08:00"]], "flat"],
    ["记录恰在 from 算窗内，不算基线", [["execute", "2026-10-05T00:00:00+08:00"]], "new"],
    ["恰在 to 与 to 之后的记录不计（计入就会成 up）", [["execute", "2026-10-01T00:00:00+08:00"], ["understand", "2026-10-08T00:00:00+08:00"], ["retain", "2026-10-12T00:00:00+08:00"]], "down"],
    // 附加：基线取窗前**时刻**最后一条。10:00Z = 18:00+08:00 晚于 12:00+08:00，按字符串会取错成 execute 而得 up
    ["基线按时刻取（时区混用）", [["transfer", "2026-10-04T10:00:00Z"], ["execute", "2026-10-04T12:00:00+08:00"], ["stable", "2026-10-08T00:00:00+08:00"]], "down"],
  ];
  assert.ok(cases.length >= 7);
  for (const [name, pts, want] of cases) {
    assert.equal(trendOf(pts.map(([state, a]) => ({ state, at: at(a) })), F, T), want, name);
    // 经适配器同样成立（窗内有记录的能力点才出现在 skill_summary；只有基线时靠作答证据带出来）
    const b = report({
      evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00")],
      mastery_history: pts.map(([state, a]) => ({ skill_node: SK, state, at: a })),
    });
    assert.equal(b.result.skill_summary[0].trend, want, name);
  }
});

test("report highlight_codes：至少一个 up → skills_improved；至少一个 down → skills_declined；窗内没有作答证据 → no_activity", () => {
  const b = report({
    evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00"), ev(2, 2, "2026-10-08T19:40:00+08:00", { skill_node: SK2 })],
    mastery_history: [
      { skill_node: SK, state: "understand", at: "2026-10-01T00:00:00+08:00" },
      { skill_node: SK, state: "stable", at: "2026-10-08T00:00:00+08:00" },
      { skill_node: SK2, state: "stable", at: "2026-10-01T00:00:00+08:00" },
      { skill_node: SK2, state: "execute", at: "2026-10-08T00:00:00+08:00" },
    ],
  });
  assert.deepEqual(b.result.highlight_codes, ["skills_improved", "skills_declined"]);
  const idle = report({ evidence: [trace(3, 3, "2026-10-08T19:30:00+08:00"), ev(4, 4, "2026-09-01T00:00:00+08:00")] });
  assert.deepEqual(idle.result, { skill_summary: [], error_distribution: [], highlight_codes: ["no_activity"] });
  assert.deepEqual(idle.evidence_ids, []);
  assert.equal(idle.semantics, "formal");
  assert.equal(idle.confidence, 1);
});

// ---------- 低置信度 ----------
test("report 低置信度：confidence 取所用错因记录的最小值；全部证据已准入时 formal，有未准入的 practice", () => {
  const admitted = report({
    evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00", { admission: "admitted" })],
    error_attributions: [attr([eid(1)], { confidence: 0.35 }), attr([eid(1)], { confidence: 0.9, subject_category: "gainian_cuowu", cross_subject_category: "knowledge" })],
  });
  assert.equal(admitted.confidence, 0.35);
  assert.equal(admitted.semantics, "formal");
  const mixed = report({
    evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00", { admission: "admitted" }), ev(2, 2, "2026-10-08T19:40:00+08:00", { admission: "pending_review" })],
  });
  assert.equal(mixed.semantics, "practice");
  assert.equal(mixed.confidence, 1);
});

// ---------- 上游失败 ----------
test("report 坏请求：证据、错因、掌握记录里出现别的学科 → 400 SUBJECT_MISMATCH；to ≤ from → 422；0.9.0 → 501；都不回显原文", () => {
  const bad: [Record<string, unknown>, number, string][] = [
    [{ evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00", { skill_node: "wenli:R3-L2" })] }, 400, "SUBJECT_MISMATCH"],
    [{ evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00", { item_ref: "zhixing:Q-1" })] }, 400, "SUBJECT_MISMATCH"],
    [{ error_attributions: [attr([eid(1)], { skill_node: "wenli:R3-L2" })] }, 400, "SUBJECT_MISMATCH"],
    [{ mastery_history: [{ skill_node: "echo:S-1", state: "execute", at: "2026-10-08T00:00:00+08:00" }] }, 400, "SUBJECT_MISMATCH"],
    [{ window: { from: WINDOW.from, to: WINDOW.from } }, 422, "INVALID_REQUEST"],
    [{ window: { from: WINDOW.to, to: WINDOW.from } }, 422, "INVALID_REQUEST"],
    // 同一时刻的不同写法也算 to ≤ from
    [{ window: { from: "2026-10-05T00:00:00+08:00", to: "2026-10-04T16:00:00Z" } }, 422, "INVALID_REQUEST"],
    [{ window: { from: "2026-10-05", to: "2026-10-12" } }, 400, "INVALID_REQUEST"],
  ];
  for (const [extra, status, code] of bad) {
    const r = adapter.handle("POST", "report", { context: contextV1(), window: WINDOW, audience: "parent", evidence: [ev(1, 1, "2026-10-08T19:30:00+08:00")], ...extra });
    assert.equal(r.status, status, JSON.stringify(extra));
    assert.equal((r.body as Json).code, code);
    assert.ok(!JSON.stringify(r.body).includes(SECRET));
    assert.deepEqual(contractV1.validate("Problem", r.body, "1.0.0"), { ok: true });
  }
  const v09 = adapter.handle("POST", "report", { context: contextV1({ contract_version: "0.9.0", idempotency_key: "synthetic-report-key" }), window: WINDOW, audience: "parent" });
  assert.equal(v09.status, 501);
  assert.ok(realPack.pack.items.length > 0);
});
