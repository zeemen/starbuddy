// report（spec 4-D 文砺部分，待人确认 #3 默认）：只给逐能力格作答计数、错因逐类计数与证据 ID；不写结论句与评价（规则 11），
// 没有等级、没有 highlight_codes；trend 是契约必填编码，只比较平台给的 mastery_history 的状态序。不调模型。
// 注意：契约 ReportFields 没有“量表逐维”的字段、ReportRequest 也不带判分维度得分，五维量表逐维统计做不了（lessons 待人确认）。
// 全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, REPORT_ALGORITHM } from "../src/adapter.ts";
import { attempt, attemptId, context, contextV1, contract, ESSAYS, gradeBodyV1, harness, leaves, realPack, type Json } from "./helpers.ts";

const make = () => createAdapter({ content: realPack, contract, route: null, allowCandidate: true });
const call = async (a: ReturnType<typeof make>, body: unknown) => {
  const r = await a.handle("POST", "report", body);
  return { status: r.status, body: r.body as Json };
};
const ctx = (v1 = true) => {
  const c = (v1 ? contextV1() : context()) as Json;
  delete c.usage_id; // 非消耗类方法
  return c;
};
const WINDOW = { from: "2026-10-05T00:00:00+08:00", to: "2026-10-12T00:00:00+08:00" };
const eid = (n: number) => `e3000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const choice = (n: number, item: string, at: string, att: number, admission = "pending_review") => ({
  evidence_id: eid(n),
  attempt_id: attemptId(att),
  kind: "response_choice",
  occurred_at: at,
  item_ref: `wenli:${item}`,
  admission,
  payload: { choice: ["A"] },
});
const attr = (code: string, cross: string, confidence: number, ids: string[]) => ({
  skill_node: "wenli:CHN.SYN.D1_L1",
  subject_category: code,
  cross_subject_category: cross,
  confidence,
  evidence_ids: ids,
  algorithm_version: "wenli-attr@0.1.0",
});
const summary = (b: Json) => Object.fromEntries(b.result.skill_summary.map((s: Json) => [s.skill_node.replace("wenli:CHN.SYN.", ""), [s.trend, s.attempts]]));

test("正常：diagnose 与 grade 产出的证据 → 逐能力格作答计数；证据 ID 全部来自请求；未准入 → practice；不调模型", async () => {
  const h = await harness();
  try {
    const diag = (await h.adapter.handle("POST", "diagnose", {
      context: contextV1(),
      mode: "gap_test",
      attempts: [
        attempt("synthetic-q01", { choice: ["B"] }, 0, { attempt_id: attemptId(1) }), // D1_L1
        attempt("synthetic-q03", { text: "树墩" }, 1, { attempt_id: attemptId(2) }), // D1_L1
        attempt("synthetic-q05", { choice: ["A"] }, 2, { attempt_id: attemptId(3) }), // D2_L1
      ],
    })).body as Json;
    const g = gradeBodyV1("synthetic-essay-01", ESSAYS.long) as Json;
    g.attempt.attempt_id = attemptId(4);
    const graded = (await h.adapter.handle("POST", "grade", g)).body as Json;
    assert.ok(Array.isArray(graded.evidence), JSON.stringify(graded).slice(0, 200));
    const calls = h.stub.calls.length;
    const evidence = [...diag.evidence, ...graded.evidence];
    const r = await h.adapter.handle("POST", "report", { context: ctx(), window: WINDOW, audience: "parent", evidence });
    const b = r.body as Json;
    assert.equal(r.status, 200, JSON.stringify(b));
    assert.equal(h.stub.calls.length, calls, "report 不调模型");
    assert.deepEqual(contract.validate("ReportResponse", b, "1.0.0"), { ok: true });
    // 作文的作答证据与片段证据属于同一道作答（attempt 4）→ D5_L2 只记 1 次
    assert.deepEqual(summary(b), { D1_L1: ["new", 2], D2_L1: ["new", 1], D5_L2: ["new", 1] });
    assert.deepEqual(b.result.error_distribution, []);
    assert.deepEqual([...b.evidence_ids].sort(), evidence.map((e: Json) => e.evidence_id).sort());
    assert.deepEqual([b.confidence, b.human_review_required, b.semantics], [1, false, "practice"]);
    assert.equal(b.model_version, `algo:${REPORT_ALGORITHM}`);
    assert.equal(b.source_version, `algo:${REPORT_ALGORITHM};items:wenli-synthetic@0.1.0`);
    assert.ok(!JSON.stringify(b).includes(ESSAYS.long.slice(0, 20)), "不回显作文原文");
  } finally {
    await h.close();
  }
});

test("时间窗 [from, to) 按时刻比较（+08:00 与 Z 混用）；窗外证据不计入也不进 evidence_ids；同一道作答只算一次", async () => {
  const evidence = [
    choice(1, "synthetic-q07", "2026-10-06T10:00:00+08:00", 1), // D3_L1
    choice(2, "synthetic-q07", "2026-10-06T02:00:00Z", 1), // 同一道作答
    choice(3, "synthetic-q07", "2026-10-04T15:59:59Z", 2), // 窗前
    choice(4, "synthetic-q08", "2026-10-11T16:00:00Z", 3), // = to，窗外
    choice(5, "synthetic-q08", "2026-10-11T15:59:59Z", 4), // 窗内最后一秒，D3_L2
    { evidence_id: eid(6), kind: "interaction_signal", occurred_at: "2026-10-06T10:00:00+08:00", item_ref: "wenli:synthetic-q07", admission: "pending_review", payload: { signal_code: "evidence_level_b" } },
  ];
  const { status, body: b } = await call(make(), { context: ctx(), window: WINDOW, audience: "student", evidence });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(summary(b), { D3_L1: ["new", 1], D3_L2: ["new", 1] });
  assert.deepEqual(b.evidence_ids, [eid(1), eid(2), eid(5)], "信号类证据不计入");
});

test("trend 只比较平台给的掌握状态序：窗前最后一条为基线；没有基线 → new；窗内没有记录 → flat", async () => {
  const hist = (cell: string, state: string, at: string) => ({ skill_node: `wenli:CHN.SYN.${cell}`, state, at });
  const mastery_history = [
    hist("D1_L1", "understand", "2026-10-01T00:00:00+08:00"),
    hist("D1_L1", "execute", "2026-10-08T00:00:00+08:00"), // up
    hist("D2_L1", "stable", "2026-09-30T00:00:00+08:00"),
    hist("D2_L1", "execute", "2026-10-07T00:00:00+08:00"), // down
    hist("D3_L1", "execute", "2026-10-01T00:00:00+08:00"), // flat（窗内没有记录）
    hist("D4_L1", "execute", "2026-10-06T00:00:00+08:00"), // new
    hist("D5_L1", "retain", "2026-10-12T00:00:00+08:00"), // = to，不看；也没有别的记录 → 不出现
  ];
  const { status, body: b } = await call(make(), { context: ctx(false), window: WINDOW, audience: "teacher", mastery_history });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("ReportResponse", b), { ok: true });
  assert.deepEqual(summary(b), { D1_L1: ["up", 0], D2_L1: ["down", 0], D3_L1: ["flat", 0], D4_L1: ["new", 0] });
  assert.equal(b.semantics, "formal");
});

test("低置信度：错因记录置信度低时整体置信度随之降低；错因逐类计数只列出现过的语文编码（含阅读错因标签），按映射表顺序", async () => {
  const evidence = [choice(1, "synthetic-q01", "2026-10-06T10:00:00+08:00", 1, "admitted")];
  const error_attributions = [
    attr("dingwei", "locating_information", 0.4, [eid(1)]),
    attr("shenti", "reading_the_question", 0.2, [eid(1)]),
    attr("prompt_type_confuse", "reading_the_question", 0.3, [eid(1)]),
    attr("shenti", "reading_the_question", 0.4, [eid(1)]),
  ];
  const { body: b } = await call(make(), { context: ctx(), window: WINDOW, audience: "parent", evidence, error_attributions });
  assert.equal(b.confidence, 0.2);
  assert.deepEqual(b.result.error_distribution, [
    { cross_subject_category: "reading_the_question", subject_category: "shenti", count: 2 },
    { cross_subject_category: "locating_information", subject_category: "dingwei", count: 1 },
    { cross_subject_category: "reading_the_question", subject_category: "prompt_type_confuse", count: 1 },
  ]);
  assert.deepEqual([b.semantics, b.human_review_required, b.evidence_ids], ["formal", false, [eid(1)]]);
});

test("上游数据不合格：别科引用 → 400 SUBJECT_MISMATCH；道法错因编码或大类与映射表不符 → 422；时间窗倒置 → 422；都不回显", async () => {
  const good = choice(1, "synthetic-q01", "2026-10-06T10:00:00+08:00", 1);
  const cases: [Json, number, string][] = [
    [{ evidence: [{ ...good, item_ref: "zhixing:synthetic-m3-01" }] }, 400, "SUBJECT_MISMATCH"],
    [{ evidence: [{ ...good, skill_node: "aya:K-QF-0301" }] }, 400, "SUBJECT_MISMATCH"],
    [{ mastery_history: [{ skill_node: "zhixing:syn-dim-dingwei", state: "execute", at: "2026-10-06T00:00:00+08:00" }] }, 400, "SUBJECT_MISMATCH"],
    // 道法的“定位错”（MORAL 口径 dingwei_cuo）不是语文的“定位性”（CHN 口径 dingwei），不混算
    [{ error_attributions: [attr("dingwei_cuo", "locating_information", 0.4, [eid(1)])] }, 422, "INVALID_REQUEST"],
    [{ error_attributions: [attr("dingwei", "knowledge", 0.4, [eid(1)])] }, 422, "INVALID_REQUEST"],
    [{ window: { from: WINDOW.to, to: WINDOW.from } }, 422, "INVALID_REQUEST"],
    [{ context: { ...ctx(), content_version: { ...realPack.pack.curriculum, content_version: "9.9.9" } } }, 404, "CONTENT_VERSION_NOT_FOUND"],
    [{ forecast: { low: 80, high: 90 } }, 400, "INVALID_REQUEST"],
  ];
  for (const [patch, status, code] of cases) {
    const r = await call(make(), { context: ctx(), window: WINDOW, audience: "parent", evidence: [good], ...patch });
    assert.equal(r.status, status, `${JSON.stringify(patch).slice(0, 80)} → ${JSON.stringify(r.body)}`);
    assert.equal(r.body.code, code);
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
  }
});

test("不含自由文本：三种受众输出同样的字段；所有字符串都是编码、ID 或版本；没有 highlight_codes；幂等 409", async () => {
  const a = make();
  const evidence = [
    { evidence_id: eid(1), kind: "ocr_transcription", occurred_at: "2026-10-06T10:00:00+08:00", item_ref: "wenli:synthetic-essay-02", admission: "pending_review", payload: { text: "（合成）作文转写原文" } },
  ];
  const error_attributions = [attr("biaoda_guifan", "expression_convention", 0.4, [eid(1)])];
  const results: Json[] = [];
  for (const audience of ["student", "parent", "teacher"]) {
    const body = { context: ctx(), window: WINDOW, audience, evidence, error_attributions };
    const r = await call(a, body);
    assert.equal(r.status, 200, JSON.stringify(r.body));
    results.push(r.body);
    assert.deepEqual((await call(a, structuredClone(body))).body, r.body);
    assert.equal((await call(a, { ...structuredClone(body), audience: audience === "student" ? "parent" : "student" })).status, 409);
  }
  assert.deepEqual(results[1], results[0]);
  assert.deepEqual(results[2], results[0]);
  const b = results[0];
  assert.deepEqual(Object.keys(b.result).sort(), ["error_distribution", "skill_summary"]);
  assert.deepEqual(summary(b), { D5_L1: ["new", 1] });
  for (const [path, v] of leaves(b)) {
    if (typeof v !== "string") continue;
    assert.match(v, /^[a-z0-9_]{1,48}$|^wenli:[A-Za-z0-9_.\-]+$|^[0-9a-f-]{36}$|^algo:[a-z0-9\-]+@[0-9.]+(;items:[a-z0-9\-]+@[0-9.]+)?$|^1\.0\.0$/, `${path} = ${v}`);
    assert.ok(!/[一-鿿]/.test(v), `${path} 出现中文`);
  }
});
