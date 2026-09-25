// 信封与效力：规则 3 字段、未准入 → practice + 复核（decisions #11）、边界 → 复核且低置信、diagnose 降权规则、不输出掌握度。
import assert from "node:assert/strict";
import { test } from "node:test";
import { context, contract, gradeBody, keysOf, makeAdapter, realPack, sweepCases, syntheticPack, type Json } from "./helpers.ts";

const adapter = makeAdapter();

test("内容包 721 道题目前全部未准入（Q1、来源待核）", () => {
  assert.equal(realPack.pack.items.length, 721);
  assert.ok(realPack.pack.items.every((i) => i.admission === "source_unverified"));
});

test("未准入题目：所有 grade 结果都是 practice，并要求复核，原因含 content_not_admitted", () => {
  for (const c of sweepCases()) {
    const b = adapter.handle("POST", "grade", gradeBody(c.item.id, c.response)).body as Json;
    assert.equal(b.semantics, "practice");
    assert.equal(b.human_review_required, true);
    assert.ok(b.review_reasons.includes("content_not_admitted"));
    assert.ok(b.evidence.every((e: Json) => e.admission === "source_unverified"));
  }
});

test("已准入的合成题目：答对时是 formal，不需要复核；边界情况仍然复核", () => {
  const pack = syntheticPack([{ answer: ["2x-2"] }], { sourceVerified: true });
  const a = makeAdapter(pack);
  const ok = a.handle("POST", "grade", gradeBody("SYN-0", { text: "2(x-1)" })).body as Json;
  assert.equal(ok.semantics, "formal");
  assert.equal(ok.human_review_required, false);
  assert.equal(ok.review_reasons, undefined);
  assert.equal(ok.result.score.total, 1);
  const edge = a.handle("POST", "grade", gradeBody("SYN-0", { text: "sin(x)" })).body as Json;
  assert.equal(edge.human_review_required, true);
  assert.deepEqual(edge.review_reasons.sort(), ["dimension_unavailable", "verifier_boundary_case"]);
  assert.ok(edge.confidence <= 0.5);
  assert.equal(edge.result.score.total, null);
  assert.deepEqual(edge.result.score.excluded_dimensions, ["answer"]);
  assert.deepEqual(contract.validate("GradeResponse", edge), { ok: true });
});

test("申诉重判：原因含 student_appeal", () => {
  const pack = syntheticPack([{ answer: ["3"] }], { sourceVerified: true });
  const b = makeAdapter(pack).handle("POST", "grade", gradeBody("SYN-0", { text: "3" }, { appeal: true })).body as Json;
  assert.equal(b.human_review_required, true);
  assert.deepEqual(b.review_reasons, ["student_appeal"]);
});

test("grade 的溯源字段：model_version、source_version、验证器、维度", () => {
  const b = adapter.handle("POST", "grade", gradeBody("Q-EQU-U03-D-001", { text: "9,-5" })).body as Json;
  assert.equal(b.model_version, "algo:aya-symbolic-ts@0.1.0");
  assert.equal(b.source_version, `verifier:aya-symbolic-ts@0.1.0;items:aya-pack@${realPack.pack.pack_version}`);
  assert.deepEqual(b.result.verifier, { verifier_id: "aya-symbolic-ts", version: "0.1.0", reason_codes: [] });
  const [d] = b.result.dimensions;
  assert.equal(d.dimension_id, "answer");
  assert.equal(d.judged_by, "verifier");
  assert.equal(d.subjective, false);
  assert.equal(d.rule_ref, "aya-symbolic-ts@0.1.0");
  assert.deepEqual(new Set(d.evidence_ids), new Set(b.evidence_ids));
  assert.deepEqual(b.evidence.map((e: Json) => e.kind), ["response_text", "verifier_trace"]);
});

function diagnose(attempts: Json[]) {
  return adapter.handle("POST", "diagnose", { context: context(), mode: "intake", attempts }).body as Json;
}
const at = (item: string, response: Json, extra: Json = {}) => ({ item_ref: `aya:${item}`, occurred_at: "2026-10-08T19:30:00+08:00", response, ...extra });

test("diagnose：每个主测节点一条观测；答对 demonstrated、答错 not_demonstrated、边界 inconclusive", () => {
  const item = realPack.itemById.get("Q-EQU-U03-D-001")!;
  const b = diagnose([at(item.id, { text: "9,-5" }), at(item.id, { text: "9" }), at(item.id, { text: "2x+" })]);
  assert.deepEqual(b.result.observations.map((o: Json) => o.outcome), ["demonstrated", "not_demonstrated", "inconclusive"]);
  for (const o of b.result.observations) {
    assert.equal(o.skill_node, `aya:${item.mainNodes[0]}`);
    assert.equal(o.algorithm_version, "aya-diag@0.1.0");
  }
  assert.ok(b.review_reasons.includes("verifier_boundary_case"));
  assert.equal(b.model_version, "algo:aya-diag@0.1.0");
});

test("diagnose 降权：答对但自评“猜的” ≤ 0.3；用了 4 级及以上提示 ≤ 0.5；3 级提示不降权", () => {
  const b = diagnose([
    at("Q-EQU-U03-D-001", { text: "9,-5" }, { self_report: "guessed" }),
    at("Q-EQU-U03-D-001", { text: "9,-5" }, { hint_level_used: 4 }),
    at("Q-EQU-U03-D-001", { text: "9,-5" }, { hint_level_used: 3 }),
    at("Q-EQU-U03-D-001", { text: "9" }, { self_report: "guessed" }),
  ]);
  const c = b.result.observations.map((o: Json) => o.confidence);
  assert.ok(c[0] <= 0.3);
  assert.ok(c[1] <= 0.5);
  assert.equal(c[2], 1);
  assert.equal(c[3], 1, "答错时“猜的”不影响“没掌握”的观测");
  assert.equal(b.confidence, Math.min(...c));
});

test("不输出掌握度、θ、能力均分（规则 2）", () => {
  const b = diagnose(sweepCases().slice(0, 40).map((c) => at(c.item.id, c.response)));
  const keys = keysOf(b).join(" ");
  assert.doesNotMatch(keys, /mastery|theta|θ|ability|p_mastery|score/i);
});
