// diagnose（mode = process）：只把请求里已有的判分证据汇总成 SkillObservation。不调模型，不写关于学生的陈述，
// 不输出掌握度。全部是合成数据。
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { DIAGNOSE_ALGORITHM, PROCESS_HIT_CONFIDENCE, PROCESS_INCONCLUSIVE_CONFIDENCE } from "../src/adapter.ts";
import { ANSWERS, attemptId, context, contextV1, contract, gradeBodyV1, harness, TIME, type Harness, type Json } from "./helpers.ts";

let h: Harness;
before(async () => {
  h = await harness();
});
after(() => h.close());

const attempt = (text: string, extra: Record<string, unknown> = {}) => ({ item_ref: "zhixing:synthetic-m3-01", occurred_at: TIME, response: { text }, ...extra });

test("没有既往判分证据：每个节点一条 inconclusive 观测，低置信；不调模型", async () => {
  const before = h.stub.calls.length;
  const r = await h.adapter.handle("POST", "diagnose", { context: context(), mode: "process", attempts: [attempt(ANSWERS.partial01)] });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const b = r.body as Json;
  assert.deepEqual(contract.validate("DiagnoseResponse", b), { ok: true });
  assert.equal(h.stub.calls.length, before, "diagnose 不调模型");
  assert.deepEqual(b.result.observations.map((o: Json) => [o.skill_node, o.outcome]), [
    ["zhixing:syn-dim-zhuanhua", "inconclusive"],
    ["zhixing:syn-dim-dingwei", "inconclusive"],
    ["zhixing:syn-dim-guifan", "inconclusive"],
  ]);
  for (const o of b.result.observations) {
    assert.equal(o.confidence, PROCESS_INCONCLUSIVE_CONFIDENCE);
    assert.equal(o.algorithm_version, DIAGNOSE_ALGORITHM);
    assert.deepEqual(o.evidence_ids, [b.evidence[0].evidence_id]);
  }
  assert.deepEqual(b.result.error_attributions, []);
  assert.equal(b.human_review_required, true);
  assert.deepEqual(b.review_reasons, ["low_confidence", "content_not_admitted"]);
  assert.equal(b.semantics, "practice");
  assert.equal(b.model_version, `algo:${DIAGNOSE_ALGORITHM}`);
  // 不输出掌握度：结果里没有 p_mastery / mastery / state 之类的字段
  assert.ok(!/mastery|p_mastery|"state"/.test(JSON.stringify(b.result)));
});

test("v1.0：先 grade，再把 grade 的证据作为 prior_evidence 交给 diagnose → 有命中片段的节点为 partial；作答证据 ID 与 grade 相同", async () => {
  const g = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
  const graded = (await h.adapter.handle("POST", "grade", g)).body as Json;
  assert.equal(graded.result.verdict, "partial");
  const before = h.stub.calls.length;
  const body = { context: contextV1(), mode: "process", attempts: [g.attempt], prior_evidence: graded.evidence };
  const r = await h.adapter.handle("POST", "diagnose", body);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const b = r.body as Json;
  assert.deepEqual(contract.validate("DiagnoseResponse", b, "1.0.0"), { ok: true });
  assert.equal(h.stub.calls.length, before);
  assert.equal(b.evidence[0].evidence_id, graded.evidence[0].evidence_id, "同一道作答只记一次证据");
  assert.equal(b.evidence[0].attempt_id, g.attempt.attempt_id);
  const byNode = Object.fromEntries(b.result.observations.map((o: Json) => [o.skill_node, o]));
  // partial01：p1（转化节点）核心命中、p2（定位节点）可接受表述，都有片段；p3（规范节点）未命中
  assert.equal(byNode["zhixing:syn-dim-zhuanhua"].outcome, "partial");
  assert.equal(byNode["zhixing:syn-dim-dingwei"].outcome, "partial");
  assert.equal(byNode["zhixing:syn-dim-guifan"].outcome, "inconclusive");
  assert.equal(byNode["zhixing:syn-dim-zhuanhua"].confidence, PROCESS_HIT_CONFIDENCE);
  const spanIds = graded.evidence.filter((e: Json) => e.kind === "scoring_point_span").map((e: Json) => e.evidence_id);
  assert.ok(byNode["zhixing:syn-dim-zhuanhua"].evidence_ids.some((id: string) => spanIds.includes(id)));
  // 引用的既有证据也列在信封 evidence_ids 里
  for (const id of spanIds) assert.ok(b.evidence_ids.includes(id));
  assert.ok(b.confidence <= PROCESS_INCONCLUSIVE_CONFIDENCE);
});

test("既往证据与作答对不上（原文不同、片段越界、节点不属于该题）→ 422", async () => {
  const g = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
  const graded = (await h.adapter.handle("POST", "grade", g)).body as Json;
  const mutations: ((ev: Json[]) => void)[] = [
    (ev) => (ev[0].payload.text = "被改过的原文"),
    (ev) => (ev.find((e) => e.kind === "scoring_point_span").payload.span_end = 9999),
    (ev) => (ev.find((e) => e.kind === "scoring_point_span").skill_node = "zhixing:syn-dim-shizheng"),
  ];
  for (const m of mutations) {
    const prior = structuredClone(graded.evidence);
    m(prior);
    const r = await h.adapter.handle("POST", "diagnose", { context: contextV1(), mode: "process", attempts: [g.attempt], prior_evidence: prior });
    assert.equal(r.status, 422, JSON.stringify(r.body));
    assert.ok(!JSON.stringify(r.body).includes(ANSWERS.partial01));
  }
});

test("其他模式不支持：v0.9 → 422 INVALID_REQUEST，v1.0 → 422 MODE_NOT_SUPPORTED；没有作答 → 422；重复 attempt_id → 422", async () => {
  for (const mode of ["intake", "gap_test", "paper"]) {
    const r = await h.adapter.handle("POST", "diagnose", { context: context(), mode, attempts: [attempt("x")] });
    assert.equal(r.status, 422);
    assert.equal((r.body as Json).code, "INVALID_REQUEST");
    const r1 = await h.adapter.handle("POST", "diagnose", { context: contextV1(), mode, attempts: [attempt("x", { attempt_id: attemptId(9) })] });
    assert.equal(r1.status, 422);
    assert.equal((r1.body as Json).code, "MODE_NOT_SUPPORTED");
  }
  const empty = await h.adapter.handle("POST", "diagnose", { context: context(), mode: "process", attempts: [] });
  assert.equal(empty.status, 422);
  const dup = await h.adapter.handle("POST", "diagnose", {
    context: contextV1(),
    mode: "process",
    attempts: [attempt("甲", { attempt_id: attemptId(3) }), attempt("乙", { attempt_id: attemptId(3) })],
  });
  assert.equal(dup.status, 422);
});

test("多道作答：每道作答都有观测，证据 ID 互不相同", async () => {
  const r = await h.adapter.handle("POST", "diagnose", {
    context: context(),
    mode: "process",
    attempts: [attempt(ANSWERS.partial01), { ...attempt("同学们在奉献中实现人生价值。"), item_ref: "zhixing:synthetic-m3-02" }],
  });
  const b = r.body as Json;
  assert.equal(r.status, 200);
  assert.equal(new Set(b.evidence_ids).size, 2);
  for (const e of b.evidence) assert.ok(b.result.observations.some((o: Json) => o.evidence_ids.includes(e.evidence_id)));
});
