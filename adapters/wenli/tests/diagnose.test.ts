// diagnose（原生自适应测评）：客观题确定性判分 → SkillObservation；按作答证据等级加权（A 1.0、B 0.7）；
// C 级（未确认的拍照作答）不判分、列入 ocr_confirmation_required；不调模型。全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { DIAGNOSE_ALGORITHM } from "../src/adapter.ts";
import { attempt, attemptId, contextV1, contract, diagnoseBody, harness, type Json } from "./helpers.ts";

async function diagnose(body: unknown) {
  const h = await harness();
  try {
    const r = await h.adapter.handle("POST", "diagnose", body);
    assert.equal(h.stub.calls.length, 0, "diagnose 不调模型");
    return { status: r.status, body: r.body as Json };
  } finally {
    await h.close();
  }
}

/** 平台随请求带来的证据等级标记（interaction_signal；合成） */
const levelMarker = (a: Json, level: "a" | "b" | "c", id: string) => ({
  evidence_id: id,
  kind: "interaction_signal",
  occurred_at: a.occurred_at,
  item_ref: a.item_ref,
  admission: "pending_review",
  payload: { signal_code: `evidence_level_${level}` },
});
const ocrRecord = (a: Json, id: string) => ({
  evidence_id: id,
  kind: "ocr_transcription",
  occurred_at: a.occurred_at,
  item_ref: a.item_ref,
  admission: "pending_review",
  payload: { text: "合成转写" },
  media_ref: "objstore://synthetic/wenli/photo-0001.jpg",
});

test("选择按选项、填空按规范化匹配：对 → demonstrated，错 → not_demonstrated；键盘作答置信度 1.0；local_level 是能力格的层", async () => {
  const attempts = [
    attempt("synthetic-q01", { choice: ["B"] }, 0),
    attempt("synthetic-q02", { choice: ["a"] }, 1),
    attempt("synthetic-q03", { text: " 露出地面的树桩。" }, 2),
    attempt("synthetic-q06", { text: "旧知识" }, 3),
    attempt("synthetic-q09", { text: "施耐庵" }, 4),
    attempt("synthetic-q05", { choice: ["0"] }, 5),
    attempt("synthetic-q07", { choice: ["A", "B"] }, 6),
  ];
  const { status, body } = await diagnose(diagnoseBody(attempts));
  assert.equal(status, 200, JSON.stringify(body));
  assert.deepEqual(contract.validate("DiagnoseResponse", body), { ok: true });
  const obs = body.result.observations;
  assert.deepEqual(obs.map((o: Json) => o.outcome), ["demonstrated", "not_demonstrated", "demonstrated", "demonstrated", "not_demonstrated", "demonstrated", "not_demonstrated"]);
  assert.deepEqual(obs.map((o: Json) => o.skill_node), ["wenli:CHN.SYN.D1_L1", "wenli:CHN.SYN.D1_L2", "wenli:CHN.SYN.D1_L1", "wenli:CHN.SYN.D2_L2", "wenli:CHN.SYN.D4_L1", "wenli:CHN.SYN.D2_L1", "wenli:CHN.SYN.D3_L1"]);
  assert.deepEqual(obs.map((o: Json) => o.local_level), ["L1", "L2", "L1", "L2", "L1", "L1", "L1"]);
  for (const o of obs) {
    assert.equal(o.confidence, 1);
    assert.equal(o.algorithm_version, DIAGNOSE_ALGORITHM);
    assert.equal(o.evidence_ids.length, 1);
  }
  assert.deepEqual(body.result.error_attributions, []);
  assert.equal(body.result.ocr_confirmation_required, undefined);
  // 作答证据：选择题 response_choice（原样），填空题 response_text（原样）
  assert.deepEqual(body.evidence.map((e: Json) => e.kind), ["response_choice", "response_choice", "response_text", "response_text", "response_text", "response_choice", "response_choice"]);
  assert.deepEqual(body.evidence[1].payload, { choice: ["a"] });
  assert.deepEqual(body.evidence[2].payload, { text: " 露出地面的树桩。" });
  assert.equal(body.model_version, `algo:${DIAGNOSE_ALGORITHM}`);
  assert.equal(body.source_version, `algo:${DIAGNOSE_ALGORITHM};items:wenli-synthetic@0.1.0`);
  assert.equal(body.confidence, 1);
  assert.deepEqual(body.review_reasons, ["content_not_admitted"]);
  assert.equal(body.semantics, "practice");
});

test("拍照已确认（B 级）→ 置信度 0.7；C 级与只有拍照识别证据（没有确认）→ 不判分，列入 ocr_confirmation_required", async () => {
  const a0 = attempt("synthetic-q01", { choice: ["B"] }, 0);
  const a1 = attempt("synthetic-q04", { text: "不亦说乎" }, 1);
  const a2 = attempt("synthetic-q09", { text: "吴承恩" }, 2);
  const a3 = attempt("synthetic-q10", { choice: ["A"] }, 3);
  const prior = [
    ocrRecord(a1, "e0000000-0000-4000-8000-000000000001"),
    levelMarker(a1, "b", "e0000000-0000-4000-8000-000000000002"),
    levelMarker(a2, "c", "e0000000-0000-4000-8000-000000000003"),
    ocrRecord(a3, "e0000000-0000-4000-8000-000000000004"),
  ];
  const body = diagnoseBody([a0, a1, a2, a3], { prior_evidence: prior }) as Json;
  body.context.data_class = "L3";
  const r = await diagnose(body);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.deepEqual(contract.validate("DiagnoseResponse", r.body), { ok: true });
  const obs = r.body.result.observations;
  assert.deepEqual(obs.map((o: Json) => [o.skill_node, o.outcome, o.confidence]), [
    ["wenli:CHN.SYN.D1_L1", "demonstrated", 1],
    ["wenli:CHN.SYN.D2_L1", "demonstrated", 0.7],
  ]);
  assert.deepEqual(r.body.result.ocr_confirmation_required, ["wenli:synthetic-q09", "wenli:synthetic-q10"]);
  // 待确认的作答仍有作答证据（平台核对要求每道作答对应一条），但不被任何观测引用
  assert.equal(r.body.evidence.length, 4);
  const cited = new Set(obs.flatMap((o: Json) => o.evidence_ids));
  assert.ok(!cited.has(r.body.evidence[2].evidence_id) && !cited.has(r.body.evidence[3].evidence_id));
  assert.equal(r.body.confidence, 0.7);
  // 既有证据（标记）不被引用：evidence_ids 只含本次新证据
  assert.deepEqual(r.body.evidence_ids, r.body.evidence.map((e: Json) => e.evidence_id));
});

test("全部是待确认的拍照作答：没有观测，整体置信度 0", async () => {
  const a0 = attempt("synthetic-q01", { choice: ["B"] }, 0);
  const r = await diagnose(diagnoseBody([a0], { prior_evidence: [levelMarker(a0, "c", "e0000000-0000-4000-8000-000000000011")] }));
  assert.equal(r.status, 200);
  assert.deepEqual(r.body.result.observations, []);
  assert.deepEqual(r.body.result.ocr_confirmation_required, ["wenli:synthetic-q01"]);
  assert.equal(r.body.confidence, 0);
});

test("同一道作答有多个等级标记取最严；有拍照识别证据时最高只认 B", async () => {
  const a0 = attempt("synthetic-q01", { choice: ["B"] }, 0);
  const a1 = attempt("synthetic-q02", { choice: ["B"] }, 1);
  const prior = [
    levelMarker(a0, "a", "e0000000-0000-4000-8000-000000000021"),
    levelMarker(a0, "b", "e0000000-0000-4000-8000-000000000022"),
    ocrRecord(a1, "e0000000-0000-4000-8000-000000000023"),
    levelMarker(a1, "a", "e0000000-0000-4000-8000-000000000024"),
  ];
  const body = diagnoseBody([a0, a1], { prior_evidence: prior }) as Json;
  body.context.data_class = "L3";
  const r = await diagnose(body);
  assert.deepEqual(r.body.result.observations.map((o: Json) => o.confidence), [0.7, 0.7]);
});

test("v1.0：标记按 attempt_id 对应；证据带 attempt_id；intake 与 gap_test 都支持", async () => {
  const a0 = { ...attempt("synthetic-q01", { choice: ["B"] }, 0), attempt_id: attemptId(1) };
  const a1 = { ...attempt("synthetic-q03", { text: "树墩" }, 0), attempt_id: attemptId(2) };
  // 两道作答同一时刻：标记按 attempt_id 区分
  const marker = { ...levelMarker(a1, "b", "e0000000-0000-4000-8000-000000000031"), attempt_id: attemptId(2), item_ref: "wenli:synthetic-q03" };
  for (const mode of ["intake", "gap_test"]) {
    const h = await harness();
    try {
      const r = await h.adapter.handle("POST", "diagnose", { context: contextV1(), mode, attempts: [a0, a1], prior_evidence: [marker] });
      assert.equal(r.status, 200, JSON.stringify(r.body));
      const b = r.body as Json;
      assert.deepEqual(contract.validate("DiagnoseResponse", b, "1.0.0"), { ok: true });
      assert.deepEqual(b.result.observations.map((o: Json) => o.confidence), [1, 0.7]);
      assert.deepEqual(b.evidence.map((e: Json) => e.attempt_id), [attemptId(1), attemptId(2)]);
    } finally {
      await h.close();
    }
  }
});

test("不支持的请求：paper / process 模式、整卷照片、作文题、作答形态与题型不符、非法选项、原图引用、重复 attempt_id", async () => {
  const h = await harness();
  try {
    const ok = attempt("synthetic-q01", { choice: ["B"] });
    const cases: [unknown, number, string][] = [
      [diagnoseBody([ok], { mode: "paper" }), 422, "INVALID_REQUEST"],
      [diagnoseBody([ok], { mode: "process" }), 422, "INVALID_REQUEST"],
      [diagnoseBody([ok], { paper_image_refs: ["objstore://synthetic/p1.jpg"] }), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-essay-01", { text: "合成作文" })]), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-q01", { text: "B" })]), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-q03", { choice: ["A"] })]), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-q01", { choice: ["选B"] })]), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-q01", { image_ref: "objstore://synthetic/a.jpg" })]), 422, "INVALID_REQUEST"],
      [diagnoseBody([attempt("synthetic-q99", { choice: ["A"] })]), 404, "ITEM_NOT_FOUND"],
      [diagnoseBody([{ ...ok, attempt_id: attemptId(9) }, { ...attempt("synthetic-q02", { choice: ["B"] }, 1), attempt_id: attemptId(9) }]), 422, "INVALID_REQUEST"],
      [diagnoseBody([]), 422, "INVALID_REQUEST"],
    ];
    for (const [body, status, code] of cases) {
      const r = await h.adapter.handle("POST", "diagnose", body);
      assert.equal(r.status, status, JSON.stringify(r.body));
      assert.equal((r.body as Json).code, code);
      assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
    }
    const v1 = { context: contextV1(), mode: "paper", attempts: [{ ...ok, attempt_id: attemptId(3) }] };
    assert.equal(((await h.adapter.handle("POST", "diagnose", v1)).body as Json).code, "MODE_NOT_SUPPORTED");
    assert.equal(h.stub.calls.length, 0);
  } finally {
    await h.close();
  }
});

test("确定性：同样的作答（不同 key）两次得到相同的观测", async () => {
  const attempts = [attempt("synthetic-q08", { choice: ["A"] }, 0), attempt("synthetic-q04", { text: "不亦乐乎" }, 1)];
  const x = (await diagnose(diagnoseBody(attempts))).body;
  const y = (await diagnose(diagnoseBody(attempts))).body;
  const strip = (b: Json) => b.result.observations.map((o: Json) => [o.skill_node, o.outcome, o.confidence]);
  assert.deepEqual(strip(x), strip(y));
  assert.deepEqual(strip(x), [["wenli:CHN.SYN.D3_L2", "demonstrated", 1], ["wenli:CHN.SYN.D2_L1", "not_demonstrated", 1]]);
});
