// 契约 v1.0 候选版（specs/plugin-api-v1.0-step2/spec.md 第 3、5 条）：迁移期同时支持 0.9.0 与 1.0.0，
// 按请求的 context.contract_version 校验与应答；能力声明报默认版本（待人确认 #2：0.9.0）。
// 全部是合成数据（CLAUDE.md 规则 8）：学生、请求、作答 ID 都是固定的示例 UUID。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { parse } from "yaml";
import { createAdapter } from "../src/adapter.ts";
import { BOUNDARY_MAX_CONFIDENCE } from "../src/verifier.ts";
import { contractPath, loadContract, SUPPORTED_CONTRACT_VERSIONS } from "../src/contract.ts";
import { contract, context, gradeBody, makeAdapter, realPack, sweepCases, taxonomy, type Json, type Response } from "./helpers.ts";

const adapter = makeAdapter();
const RULE3 = ["evidence_ids", "confidence", "source_version", "model_version", "human_review_required"];
const RESPONSE_KINDS = new Set(["response_text", "response_choice", "asr_transcription"]);

let seq = 0;
/** v1.0 的请求上下文：幂等键必须是小写 UUIDv4 */
function contextV1(overrides: Record<string, unknown> = {}) {
  seq++;
  const n = String(seq).padStart(12, "0");
  return context({ contract_version: "1.0.0", idempotency_key: `5a000000-0000-4000-8000-${n}`, ...overrides });
}
const attemptId = (i: number) => `a1000000-0000-4000-8000-${String(i).padStart(12, "0")}`;

function gradeBodyV1(item: string, response: Response, extra: Record<string, unknown> = {}) {
  const b = gradeBody(item, response, extra) as Json;
  b.context = contextV1();
  b.attempt.attempt_id = attemptId(1);
  return b;
}

const fillItem = realPack.pack.items.find((i) => i.format === "fill")!;
const choiceItem = realPack.pack.items.find((i) => i.format === "single")!;

// ---------- 契约加载：跨文件引用 ----------
test("ajv 能编译两个版本的全部 components.schemas（v1.0 的跨文件引用可解析）", () => {
  for (const v of SUPPORTED_CONTRACT_VERSIONS) {
    const names = contract.schemaNames(v);
    assert.ok(names.length > 40, `${v} 的 schema 太少：${names.length}`);
    assert.deepEqual(contract.compileAll(v), [], v);
  }
  // 确认检查覆盖到了引用服务契约的那几个请求 schema
  for (const name of ["GradeRequest", "DiagnoseRequest", "GradeResponse", "DiagnoseResponse", "Capabilities", "Problem"]) {
    assert.ok(contract.schemaNames("1.0.0").includes(name), name);
  }
});

test("v1.0 的跨文件引用确实生效：learner_context 与 node_path 按洞察、知识服务契约校验", () => {
  const base = {
    context: contextV1(),
    mode: "gap_test",
    attempts: [{ attempt_id: attemptId(1), item_ref: `aya:${fillItem.id}`, occurred_at: "2026-10-08T19:30:00+08:00", response: { text: "1" } }],
  };
  const ok = {
    ...base,
    learner_context: [{ field_id: "inner.g3.node_mastery", source_type: "inferred", value: "understand", claim_id: "00000000-0000-4000-8000-0000000000f8" }],
    node_path: [{ from_id: "MA.ALG.EXP_0001", to_id: "MA.ALG.EXP_0002", depth: 1 }],
  };
  assert.deepEqual(contract.validate("DiagnoseRequest", ok, "1.0.0"), { ok: true });
  // 洞察服务契约 ContextEntry：推断条目必须带 claim_id
  const noClaim = structuredClone(ok) as Json;
  delete noClaim.learner_context[0].claim_id;
  assert.equal(contract.validate("DiagnoseRequest", noClaim, "1.0.0").ok, false);
  // 知识服务契约 PathStep：不允许多余字段
  const extraStep = structuredClone(ok) as Json;
  extraStep.node_path[0].note = "free text";
  assert.equal(contract.validate("DiagnoseRequest", extraStep, "1.0.0").ok, false);
});

test("加载时核对每份插件契约的 info.version", () => {
  for (const v of SUPPORTED_CONTRACT_VERSIONS) {
    const doc = parse(readFileSync(contractPath(v), "utf8")) as Json;
    assert.equal(doc.info.version, v);
  }
});

// ---------- 能力声明 ----------
test("能力声明默认报 0.9.0；默认版本设为 1.0.0 时报 1.0.0 并通过 v1.0 schema", () => {
  const r0 = adapter.handle("GET", "capabilities", undefined);
  assert.equal((r0.body as Json).contract_version, "0.9.0");
  assert.deepEqual(contract.validate("Capabilities", r0.body, "0.9.0"), { ok: true });
  const v1 = createAdapter({ content: realPack, taxonomy, contract: loadContract({ defaultVersion: "1.0.0" }), allowCandidate: true });
  const r1 = v1.handle("GET", "capabilities", undefined);
  assert.equal(r1.status, 200);
  assert.equal((r1.body as Json).contract_version, "1.0.0");
  assert.deepEqual(contract.validate("Capabilities", r1.body, "1.0.0"), { ok: true });
});

// ---------- grade：正常 ----------
test("v1.0 grade（填空、选择）→ v1.0 响应：通过 v1.0 schema，作答与轨迹证据带请求的 attempt_id", () => {
  const cases: [string, Response][] = [
    [fillItem.id, { text: fillItem.answer[0]! }],
    [choiceItem.id, { choice: [...choiceItem.answer] }],
  ];
  for (const [item, response] of cases) {
    const body = gradeBodyV1(item, response);
    const r = adapter.handle("POST", "grade", body);
    assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
    const b = r.body as Json;
    assert.equal(b.contract_version, "1.0.0");
    assert.deepEqual(contract.validate("GradeResponse", b, "1.0.0"), { ok: true });
    for (const k of RULE3) assert.ok(k in b, `缺少 ${k}`);
    assert.equal(b.result.verdict, "correct");
    const responses = b.evidence.filter((e: Json) => RESPONSE_KINDS.has(e.kind));
    assert.equal(responses.length, 1);
    for (const e of b.evidence) assert.equal(e.attempt_id, body.attempt.attempt_id);
    // v1.0 的响应不合 v0.9（contract_version 常量不同）：版本确实按请求选了
    assert.equal(contract.validate("GradeResponse", b, "0.9.0").ok, false);
  }
});

test("v1.0 全题扫一遍抽样：每个 grade 响应都通过 v1.0 schema", () => {
  const cases = sweepCases().filter((_, i) => i % 11 === 0);
  for (const c of cases) {
    const r = adapter.handle("POST", "grade", gradeBodyV1(c.item.id, c.response));
    assert.equal(r.status, 200, c.item.id);
    assert.deepEqual(contract.validate("GradeResponse", r.body, "1.0.0"), { ok: true }, c.item.id);
    assert.equal((r.body as Json).result.verdict, c.expect, c.item.id);
  }
});

// ---------- grade：低置信度 ----------
test("v1.0 低置信度：无法解析的填空 → needs_review、置信度不超过边界上限、需人工复核", () => {
  const r = adapter.handle("POST", "grade", gradeBodyV1(fillItem.id, { text: "2x+" }));
  assert.equal(r.status, 200);
  const b = r.body as Json;
  assert.deepEqual(contract.validate("GradeResponse", b, "1.0.0"), { ok: true });
  assert.equal(b.result.verdict, "needs_review");
  assert.ok(b.confidence <= BOUNDARY_MAX_CONFIDENCE);
  assert.equal(b.human_review_required, true);
  assert.ok(b.review_reasons.includes("verifier_boundary_case"));
});

// ---------- diagnose ----------
test("v1.0 diagnose（intake、gap_test）→ v1.0 响应，每道作答的证据带各自的 attempt_id", () => {
  const cases = sweepCases().filter((_, i) => i % 13 === 0).slice(0, 60);
  for (const mode of ["intake", "gap_test"]) {
    const body = {
      context: contextV1(),
      mode,
      attempts: cases.map((c, i) => ({
        attempt_id: attemptId(i + 1),
        item_ref: `aya:${c.item.id}`,
        occurred_at: "2026-10-08T19:30:00+08:00",
        response: c.response,
        ...(i % 3 === 0 ? { self_report: "guessed" } : {}),
      })),
    };
    const r = adapter.handle("POST", "diagnose", body);
    assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
    const b = r.body as Json;
    assert.equal(b.contract_version, "1.0.0");
    assert.deepEqual(contract.validate("DiagnoseResponse", b, "1.0.0"), { ok: true });
    for (const k of RULE3) assert.ok(k in b, `缺少 ${k}`);
    const responses = b.evidence.filter((e: Json) => RESPONSE_KINDS.has(e.kind));
    assert.deepEqual(responses.map((e: Json) => e.attempt_id), body.attempts.map((a) => a.attempt_id));
    const ids = new Set(body.attempts.map((a) => a.attempt_id));
    for (const e of b.evidence) assert.ok(ids.has(e.attempt_id));
  }
});

// ---------- 失败 ----------
test("v1.0 请求缺 attempt_id、幂等键不是 UUIDv4 → 400 INVALID_REQUEST", () => {
  const noAttempt = gradeBodyV1(fillItem.id, { text: "1" });
  delete noAttempt.attempt.attempt_id;
  const badKey = gradeBodyV1(fillItem.id, { text: "1" });
  badKey.context.idempotency_key = "synthetic-test-key-000000000001";
  for (const body of [noAttempt, badKey]) {
    const r = adapter.handle("POST", "grade", body);
    assert.equal(r.status, 400);
    assert.equal((r.body as Json).code, "INVALID_REQUEST");
    assert.deepEqual(contract.validate("Problem", r.body, "1.0.0"), { ok: true });
  }
});

test("不支持的契约版本 → 400 CONTRACT_VERSION_UNSUPPORTED（两版的 Problem 都接受），不回显学生原文", () => {
  for (const version of ["0.8.0", "2.0.0", "1.0.0-rc"]) {
    for (const path of ["grade", "diagnose"]) {
      const body = gradeBody(fillItem.id, { text: "学生原文ABC" }) as Json;
      body.context.contract_version = version;
      if (path === "diagnose") {
        body.mode = "intake";
        body.attempts = [body.attempt];
        delete body.attempt;
        delete body.rubric_version;
      }
      const r = adapter.handle("POST", path, body);
      assert.equal(r.status, 400, `${path} ${version}`);
      assert.equal((r.body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
      for (const v of SUPPORTED_CONTRACT_VERSIONS) assert.deepEqual(contract.validate("Problem", r.body, v), { ok: true });
      assert.ok(!JSON.stringify(r.body).includes("学生原文"));
    }
  }
  // contract_version 缺失或类型不对：按默认版本做 schema 校验 → INVALID_REQUEST
  const missing = gradeBody(fillItem.id, { text: "1" }) as Json;
  delete missing.context.contract_version;
  assert.equal((adapter.handle("POST", "grade", missing).body as Json).code, "INVALID_REQUEST");
});

test("v1.0 上游内容缺失：题目不在内容包 → 404 ITEM_NOT_FOUND；契约里的 v1.0 阿鸦示例能按 v1.0 解析", () => {
  const doc = parse(readFileSync(contractPath("1.0.0"), "utf8")) as Json;
  const ex = structuredClone(doc.components.examples.GradeRequestAyaObjective.value);
  assert.deepEqual(contract.validate("GradeRequest", ex, "1.0.0"), { ok: true });
  ex.context.content_version = { ...realPack.pack.curriculum };
  const r = adapter.handle("POST", "grade", ex);
  assert.equal(r.status, 404);
  assert.equal((r.body as Json).code, "ITEM_NOT_FOUND");
  assert.deepEqual(contract.validate("Problem", r.body, "1.0.0"), { ok: true });
});

// ---------- 幂等与 v0.9 不变 ----------
test("v1.0 幂等：同 key 同请求体（换 request_id）证据 ID 不变；同 key 换版本或换作答 → 409", () => {
  const body = gradeBodyV1(fillItem.id, { text: "1" });
  const r1 = adapter.handle("POST", "grade", body).body as Json;
  const retry = structuredClone(body);
  retry.context.request_id = "00000000-0000-4000-8000-00000000abcd";
  const r2 = adapter.handle("POST", "grade", retry).body as Json;
  assert.deepEqual(r2.evidence_ids, r1.evidence_ids);
  const other = structuredClone(body);
  other.attempt.response = { text: "2" };
  const c = adapter.handle("POST", "grade", other);
  assert.equal(c.status, 409);
  assert.equal((c.body as Json).code, "IDEMPOTENCY_CONFLICT");
  const v09 = structuredClone(body);
  v09.context.contract_version = "0.9.0";
  const c2 = adapter.handle("POST", "grade", v09);
  assert.equal(c2.status, 409);
  assert.equal((c2.body as Json).code, "IDEMPOTENCY_CONFLICT");
});

test("v0.9 行为不变：响应报 0.9.0、不带 attempt_id（即使请求带了），通过 v0.9 schema", () => {
  const body = gradeBody(fillItem.id, { text: "1" }) as Json;
  body.attempt.attempt_id = attemptId(9);
  const r = adapter.handle("POST", "grade", body);
  assert.equal(r.status, 200);
  const b = r.body as Json;
  assert.equal(b.contract_version, "0.9.0");
  assert.deepEqual(contract.validate("GradeResponse", b, "0.9.0"), { ok: true });
  for (const e of b.evidence) assert.ok(!("attempt_id" in e));
});

test("候选版开关没开：声明 1.0.0 的请求返回 CONTRACT_VERSION_UNSUPPORTED，0.9.0 照常", () => {
  const closed = makeAdapter(realPack, undefined, false);
  const r = closed.handle("POST", "grade", gradeBodyV1(fillItem.id, { text: fillItem.answer[0]! }));
  assert.equal(r.status, 400);
  assert.equal((r.body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
  const ok = closed.handle("POST", "grade", gradeBody(fillItem.id, { text: fillItem.answer[0]! }));
  assert.equal(ok.status, 200);
});

test("v1.0 不支持的作答形态报 MODE_NOT_SUPPORTED，0.9.0 仍报 INVALID_REQUEST", () => {
  const latex = adapter.handle("POST", "grade", gradeBodyV1(fillItem.id, { latex: "42" } as unknown as Response));
  assert.equal(latex.status, 422);
  assert.equal((latex.body as Json).code, "MODE_NOT_SUPPORTED");
  assert.deepEqual(contract.validate("Problem", latex.body, "1.0.0"), { ok: true });
  const photoV1 = adapter.handle("POST", "grade", gradeBodyV1(fillItem.id, { image_ref: "objstore://synthetic/p.jpg" } as unknown as Response));
  assert.equal((photoV1.body as Json).code, "MODE_NOT_SUPPORTED");
  const photoV0 = adapter.handle("POST", "grade", gradeBody(fillItem.id, { image_ref: "objstore://synthetic/p.jpg" } as unknown as Response));
  assert.equal((photoV0.body as Json).code, "INVALID_REQUEST");
});
