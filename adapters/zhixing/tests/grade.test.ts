// grade（F3 采分点逐点判分）：两段式——模型逐点判定（路由桩）→ 确定性后处理。
// 覆盖：正常（满分、部分、零分）、JSON 不合格、证据位置越界、缺点、矛盾、满分复核、申诉、rubric_version 不符、
// 作答形态、请求到路由的字段。全部是合成数据。
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { PROMPT_SOURCE_VERSION } from "../src/core/m3-prompt.ts";
import { ANSWERS, contract, gradeBody, gradeBodyV1, harness, itemOf, realPack, RUBRIC, type Harness, type Json } from "./helpers.ts";
import { MARKERS, ok, STUB_MODEL_VERSION } from "./route-stub.ts";

let h: Harness;
before(async () => {
  h = await harness();
});
after(() => h.close());

const RULE3 = ["evidence_ids", "confidence", "source_version", "model_version", "human_review_required"];

async function grade(body: unknown) {
  const r = await h.adapter.handle("POST", "grade", body);
  return { status: r.status, body: r.body as Json };
}

function assertSubjectiveEnvelope(b: Json) {
  for (const k of RULE3) assert.ok(k in b, `缺少 ${k}`);
  assert.equal(b.human_review_required, true);
  assert.ok(b.review_reasons.includes("subjective_dimension"));
  assert.ok(b.review_reasons.includes("content_not_admitted"), "内容包全部 pending_review");
  assert.equal(b.semantics, "practice");
  assert.ok(b.confidence <= 0.5);
  for (const d of b.result.dimensions) {
    assert.equal(d.judged_by, "rubric_model");
    assert.equal(d.subjective, true);
    assert.ok(d.confidence <= 0.5);
  }
}

test("正常：三点全中 → correct、满分，强制复核（full_score_second_review），带证据片段", async () => {
  const body = gradeBody("synthetic-m3-01", ANSWERS.full01);
  const { status, body: b } = await grade(body);
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("GradeResponse", b), { ok: true });
  assertSubjectiveEnvelope(b);
  assert.equal(b.result.verdict, "correct");
  assert.deepEqual(b.result.score, { total: 6, max_total: 6, calibrated: false });
  assert.ok(b.review_reasons.includes("full_score_second_review"));
  assert.equal(b.model_version, STUB_MODEL_VERSION, "model_version 取路由回传的快照名");
  assert.equal(b.source_version, `rubric:${RUBRIC};${PROMPT_SOURCE_VERSION};items:zhixing-synthetic@0.1.0`);
  assert.equal(b.result.rubric_version, RUBRIC);
  assert.deepEqual(b.result.scoring_points.map((p: Json) => [p.point_id, p.achieved, p.points_awarded]), [["p1", true, 2], ["p2", true, 2], ["p3", true, 2]]);
  assert.deepEqual(b.result.dimensions.map((d: Json) => d.dimension_id), ["p1", "p2", "p3"], "采分点型 Rubric：dimension_id 取 point_id");
  assert.deepEqual(b.result.error_attributions, []);
  // 证据：作答原文一条 + 每个得分点一个片段（只记位置，不复制原文）
  const [resp, ...spans] = b.evidence;
  assert.equal(resp.kind, "response_text");
  assert.equal(resp.payload.text, ANSWERS.full01);
  assert.equal(spans.length, 3);
  const chars = Array.from(ANSWERS.full01);
  for (const s of spans) {
    assert.equal(s.kind, "scoring_point_span");
    assert.equal(s.payload.source_evidence_id, resp.evidence_id);
    assert.deepEqual(Object.keys(s.payload).sort(), ["source_evidence_id", "span_end", "span_start"]);
    assert.ok(chars.slice(s.payload.span_start, s.payload.span_end).join("").length > 0);
  }
  assert.equal(chars.slice(spans[0].payload.span_start, spans[0].payload.span_end).join(""), "参与民主决策");
  assert.deepEqual(new Set(b.evidence_ids), new Set(b.evidence.map((e: Json) => e.evidence_id)));
});

test("正常：部分得分 → partial，逐点 achieved/points_awarded，错因走映射表", async () => {
  const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.partial01));
  assert.deepEqual(contract.validate("GradeResponse", b), { ok: true });
  assertSubjectiveEnvelope(b);
  assert.equal(b.result.verdict, "partial");
  assert.equal(b.result.score.total, 3);
  assert.deepEqual(b.result.scoring_points.map((p: Json) => [p.point_id, p.achieved, p.points_awarded]), [["p1", true, 2], ["p2", false, 1], ["p3", false, 0]]);
  assert.ok(!b.review_reasons.includes("full_score_second_review"));
  const cats = Object.fromEntries(b.result.error_attributions.map((a: Json) => [a.subject_category, a.cross_subject_category]));
  assert.deepEqual(cats, { dingwei_cuo: "locating_information", guifan_cuo: "expression_convention" });
  for (const a of b.result.error_attributions) {
    assert.ok(a.confidence <= 0.5);
    assert.match(a.algorithm_version, /^zhixing-m3-attr@0\.1\.0-tax/);
  }
  // 0 分点没有片段证据，只引用作答本身
  const p3 = b.result.scoring_points[2];
  assert.deepEqual(p3.evidence_ids, [b.evidence[0].evidence_id]);
});

test("正常：全部未命中 → incorrect，总分 0", async () => {
  const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.miss01));
  assertSubjectiveEnvelope(b);
  assert.equal(b.result.verdict, "incorrect");
  assert.equal(b.result.score.total, 0);
  assert.equal(b.evidence.length, 1);
});

for (const [label, marker, reasonCheck] of [
  ["模型输出不是 JSON", MARKERS.notJson, "all"],
  ["模型输出多了自由文本字段（不合 schema）", MARKERS.extraField, "all"],
  ["证据位置越界", MARKERS.outOfRange, "all"],
  ["缺一个采分点", MARKERS.missingPoint, "last"],
  ["判定档与得分、归因矛盾（full 却 0 分、归因 none）", MARKERS.conflict, "first"],
] as const) {
  test(`低置信 / 待复核：${label} → needs_review，相关采分点 achieved = null，分数为 null`, async () => {
    const { status, body: b } = await grade(gradeBody("synthetic-m3-01", `${ANSWERS.partial01}${marker}`));
    assert.equal(status, 200);
    assert.deepEqual(contract.validate("GradeResponse", b), { ok: true });
    assertSubjectiveEnvelope(b);
    assert.equal(b.result.verdict, "needs_review");
    assert.equal(b.result.score.total, null);
    for (const r of ["verifier_boundary_case", "dimension_unavailable"]) assert.ok(b.review_reasons.includes(r), r);
    const nulls = b.result.scoring_points.filter((p: Json) => p.achieved === null).map((p: Json) => p.point_id);
    const expected = reasonCheck === "all" ? ["p1", "p2", "p3"] : reasonCheck === "last" ? ["p3"] : ["p1"];
    assert.deepEqual(nulls, expected);
    assert.deepEqual(b.result.score.excluded_dimensions, expected);
    for (const p of b.result.scoring_points.filter((p: Json) => p.achieved === null)) assert.ok(!("points_awarded" in p));
    for (const d of b.result.dimensions.filter((d: Json) => expected.includes(d.dimension_id))) assert.equal(d.score, null);
    // 待复核的点不产出错因
    const decided = new Set(b.result.scoring_points.filter((p: Json) => p.achieved !== null).map((p: Json) => p.point_id));
    if (decided.size === 0) assert.deepEqual(b.result.error_attributions, []);
  });
}

test("模型输出包在 ```json 代码块里：照常解析", async () => {
  const { body: b } = await grade(gradeBody("synthetic-m3-01", `${ANSWERS.partial01}${MARKERS.fenced}`));
  assert.equal(b.result.verdict, "partial");
});

test("clamp 与 0.5 倍数：超出分值的分数截到分值，1.3 取 1.5；重算总分", async () => {
  const pts = [
    { point_id: "p1", judgment: "full", score_awarded: 7, evidence_start: 0, evidence_end: 2, attribution: "none" },
    { point_id: "p2", judgment: "partial", score_awarded: 1.3, evidence_start: 2, evidence_end: 4, attribution: "guifan_cuo" },
    { point_id: "p3", judgment: "miss", score_awarded: -3, evidence_start: null, evidence_end: null, attribution: "shenti_cuo" },
  ];
  await h.stub.setHandler((req) => ok(req, JSON.stringify({ points: pts })));
  try {
    const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.partial01));
    assert.deepEqual(b.result.scoring_points.map((p: Json) => p.points_awarded), [2, 1.5, 0]);
    assert.equal(b.result.score.total, 3.5);
  } finally {
    h.stub.setHandler((await import("./route-stub.ts")).keywordJudge(realPack));
  }
});

test("得分却没有证据片段 → 该点转复核", async () => {
  const pts = itemOf("synthetic-m3-01").points.map((p) => ({ point_id: p.point_id, judgment: "full", score_awarded: p.score, evidence_start: null, evidence_end: null, attribution: "none" }));
  h.stub.setHandler((req) => ok(req, JSON.stringify({ points: pts })));
  try {
    const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.full01));
    assert.equal(b.result.verdict, "needs_review");
    assert.ok(b.result.scoring_points.every((p: Json) => p.achieved === null));
  } finally {
    h.stub.setHandler((await import("./route-stub.ts")).keywordJudge(realPack));
  }
});

test("路由报告 model_mismatch：结果照常，复核原因加 low_confidence", async () => {
  const { keywordJudge } = await import("./route-stub.ts");
  const base = keywordJudge(realPack);
  h.stub.setHandler(async (req) => {
    const r = await base(req);
    return { ...r, body: { ...(r.body as object), model_mismatch: true } };
  });
  try {
    const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.partial01));
    assert.ok(b.review_reasons.includes("low_confidence"));
  } finally {
    h.stub.setHandler(base);
  }
});

test("申诉：复核原因含 student_appeal", async () => {
  const { body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.partial01, { appeal: true }));
  assert.ok(b.review_reasons.includes("student_appeal"));
});

test("发给路由的请求：T2、原样的 data_class、Prompt 模板与版本、usage_id、UUIDv4 幂等键、9 秒时限；不带供应商信息", async () => {
  const before = h.stub.calls.length;
  const body = gradeBody("synthetic-m3-02", "同学们积极参与社会公益，在奉献中实现人生价值，还节约资源。");
  (body.context as Json).data_class = "L3";
  await grade(body);
  const call = h.stub.calls[before]!;
  assert.equal(call.task, "T2");
  assert.equal(call.data_class, "L3");
  assert.equal(call.subject, "zhixing");
  assert.equal(call.prompt_template, "zhixing-m3-grade");
  assert.equal(call.prompt_version, "0.1.0");
  assert.equal(call.usage_id, (body.context as Json).usage_id);
  assert.match(call.idempotency_key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(call.deadline_ms, 9000);
  assert.deepEqual(call.messages.map((m) => m.role), ["system", "user"]);
  assert.deepEqual(Object.keys(call).sort(), ["data_class", "deadline_ms", "idempotency_key", "messages", "prompt_template", "prompt_version", "subject", "task", "usage_id"]);
  assert.equal(h.stub.authHeaders.at(-1), `Bearer ${h.stub.token}`);
});

test("rubric_version 与内容包不符 → 400 INVALID_REQUEST，不调模型", async () => {
  const before = h.stub.calls.length;
  for (const rv of ["zhixing-synthetic@0.2.0", "other-rubric@0.1.0"]) {
    const { status, body: b } = await grade(gradeBody("synthetic-m3-01", ANSWERS.full01, { rubric_version: rv }));
    assert.equal(status, 400);
    assert.equal(b.code, "INVALID_REQUEST");
  }
  // 带 rubric: 前缀的写法不符合契约 VersionedName
  const { status } = await grade(gradeBody("synthetic-m3-01", ANSWERS.full01, { rubric_version: "rubric:zhixing-synthetic@0.1.0" }));
  assert.equal(status, 400);
  assert.equal(h.stub.calls.length, before);
});

test("只接 zhixing: 题；不在内容包里 → 404；只接文字作答；空作答、超长作答 → 422", async () => {
  const other = gradeBody("synthetic-m3-01", "x") as Json;
  other.attempt.item_ref = "aya:synthetic-m3-01";
  assert.equal((await grade(other)).body.code, "SUBJECT_MISMATCH");
  assert.equal((await grade(gradeBody("synthetic-m3-99", "x"))).body.code, "ITEM_NOT_FOUND");
  const choice = gradeBody("synthetic-m3-01", "x") as Json;
  choice.attempt.response = { choice: ["A"] };
  assert.equal((await grade(choice)).status, 422);
  const choiceV1 = gradeBodyV1("synthetic-m3-01", "x");
  choiceV1.attempt.response = { choice: ["A"] };
  const r = await grade(choiceV1);
  assert.equal(r.status, 422);
  assert.equal(r.body.code, "MODE_NOT_SUPPORTED");
  assert.equal((await grade(gradeBody("synthetic-m3-01", "   "))).status, 422);
  assert.equal((await grade(gradeBody("synthetic-m3-01", "字".repeat(4001)))).status, 422);
});

test("v1.0：带 scoring_points 时必须与内容包一致；v1.0 证据带 attempt_id", async () => {
  const good = gradeBodyV1("synthetic-m3-03", "要坚定文化自信，推动文化创造性转化。");
  good.scoring_points = itemOf("synthetic-m3-03").points.map((p) => ({ point_id: p.point_id, score: p.score, node_ids: ["DF.SYN.A"], partial_credit: "stepwise" }));
  const r = await grade(good);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.deepEqual(contract.validate("GradeResponse", r.body, "1.0.0"), { ok: true });
  assert.equal(r.body.contract_version, "1.0.0");
  assert.ok(r.body.evidence.every((e: Json) => e.attempt_id === good.attempt.attempt_id));
  const bad = gradeBodyV1("synthetic-m3-03", "要坚定文化自信。");
  bad.scoring_points = [{ point_id: "p1", score: 3, node_ids: ["DF.SYN.A"], partial_credit: "stepwise" }];
  const rb = await grade(bad);
  assert.equal(rb.status, 400);
});
