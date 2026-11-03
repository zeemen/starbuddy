// 契约测试：能力声明、两个契约版本、未实现方法、错误体、候选版开关、响应自检（fail closed）。
// schema 直接取自 packages/contracts，不手写期望结构。全部是合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter } from "../src/adapter.ts";
import { CANDIDATE_CONTRACT_VERSIONS, DEFAULT_CONTRACT_VERSION, loadContract, SUPPORTED_CONTRACT_VERSIONS, type ContractValidator } from "../src/contract.ts";
import { ANSWERS, context, contract, gradeBody, gradeBodyV1, harness, realPack, taxonomy, type Json } from "./helpers.ts";

test("两个契约版本的全部 components.schemas 都能编译（v1.0 的跨文件引用可解析）", () => {
  for (const v of SUPPORTED_CONTRACT_VERSIONS) assert.deepEqual(contract.compileAll(v), [], v);
});

test("capabilities：缺省报 1.0.0（v1.0 已签字，不需候选版开关）；显式 0.9.0 时报 0.9.0；5/6 已实现（teach 未实现，等人设 Prompt 与 3-F）", async () => {
  assert.equal(DEFAULT_CONTRACT_VERSION, "1.0.0");
  const a0 = createAdapter({ content: realPack, taxonomy, contract: loadContract(), route: null, allowCandidate: false });
  const r0 = await a0.handle("GET", "capabilities", undefined);
  assert.equal(r0.status, 200);
  assert.equal((r0.body as Json).contract_version, "1.0.0");
  assert.deepEqual(contract.validate("Capabilities", r0.body, "1.0.0"), { ok: true });
  const a = createAdapter({ content: realPack, taxonomy, contract, route: null }); // helpers 显式钉住 0.9.0
  const r = await a.handle("GET", "capabilities", undefined);
  assert.equal(r.status, 200);
  assert.equal((r.body as Json).contract_version, "0.9.0");
  assert.deepEqual(contract.validate("Capabilities", r.body), { ok: true });
  const m = (r.body as Json).methods;
  for (const k of ["grade", "diagnose", "generate_variation", "plan", "report"]) assert.equal(m[k].implemented, true, k);
  assert.equal(m.teach.implemented, false);
  assert.equal(Object.values(m).filter((x: Json) => x.implemented).length, 5);
  assert.equal(m.grade.notes_code, "history_grading_excluded");
  assert.equal(m.plan.notes_code, "domain_weights_uncalibrated");
  assert.equal(m.report.notes_code, "counts_only");
  const a1 = createAdapter({ content: realPack, taxonomy, contract: loadContract({ defaultVersion: "1.0.0" }), route: null, allowCandidate: true });
  const r1 = await a1.handle("GET", "capabilities", undefined);
  assert.equal((r1.body as Json).contract_version, "1.0.0");
  assert.deepEqual(contract.validate("Capabilities", r1.body, "1.0.0"), { ok: true });
});

test("teach：501 METHOD_NOT_IMPLEMENTED，不伪造结果；jobs：404 JOB_NOT_FOUND；未知路由 404", async () => {
  const a = createAdapter({ content: realPack, taxonomy, contract, route: null });
  for (const m of ["teach"]) {
    const r = await a.handle("POST", m, { context: context() });
    assert.equal(r.status, 501, m);
    assert.equal((r.body as Json).code, "METHOD_NOT_IMPLEMENTED");
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
  }
  const j = await a.handle("GET", "jobs/00000000-0000-4000-8000-000000000001", undefined);
  assert.equal(j.status, 404);
  assert.equal((j.body as Json).code, "JOB_NOT_FOUND");
  assert.equal((await a.handle("POST", "nonsense", {})).status, 404);
});

test("错误响应都是契约 Problem，且不含学生原文", async () => {
  const h = await harness();
  try {
    const cases: [string, unknown, number, string][] = [
      ["grade", { nonsense: true }, 400, "INVALID_REQUEST"],
      ["grade", gradeBody("synthetic-m3-99", "学生原文ABC"), 404, "ITEM_NOT_FOUND"],
      ["grade", { ...gradeBody("synthetic-m3-01", "学生原文ABC"), context: context({ subject: "aya" }) }, 400, "SUBJECT_MISMATCH"],
      ["grade", gradeBody("synthetic-m3-01", "学生原文ABC", { rubric_version: "other@1.0.0" }), 400, "INVALID_REQUEST"],
      ["diagnose", { context: context(), mode: "paper", paper_image_refs: ["objstore://synthetic/p1.jpg"] }, 422, "INVALID_REQUEST"],
    ];
    const cv = gradeBody("synthetic-m3-01", "学生原文ABC") as Json;
    cv.context.content_version.content_version = "9.9.9";
    cases.push(["grade", cv, 404, "CONTENT_VERSION_NOT_FOUND"]);
    for (const [m, body, status, code] of cases) {
      const r = await h.adapter.handle("POST", m, body);
      assert.equal(r.status, status, `${m} ${JSON.stringify(r.body)}`);
      assert.equal((r.body as Json).code, code);
      assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
      assert.ok(!JSON.stringify(r.body).includes("学生原文"));
    }
  } finally {
    await h.close();
  }
});

test("契约版本：不支持的版本 → CONTRACT_VERSION_UNSUPPORTED；候选版开关关着时 1.0.0（已签字）照常、1.1.0（候选版）被拒；v1.0 幂等键必须是 UUIDv4", async () => {
  assert.deepEqual([...CANDIDATE_CONTRACT_VERSIONS], ["1.1.0"]);
  const h = await harness({ allowCandidate: false });
  try {
    const bad = gradeBody("synthetic-m3-01", ANSWERS.partial01) as Json;
    bad.context.contract_version = "0.8.0";
    assert.equal(((await h.adapter.handle("POST", "grade", bad)).body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
    const v1 = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
    const r1 = await h.adapter.handle("POST", "grade", v1);
    assert.equal(r1.status, 200, JSON.stringify(r1.body).slice(0, 300));
    assert.equal((r1.body as Json).contract_version, "1.0.0");
    const v09 = gradeBody("synthetic-m3-01", ANSWERS.partial01);
    assert.equal((await h.adapter.handle("POST", "grade", v09)).status, 200);
    const v11 = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
    v11.context.contract_version = "1.1.0";
    assert.equal(((await h.adapter.handle("POST", "grade", v11)).body as Json).code, "CONTRACT_VERSION_UNSUPPORTED");
  } finally {
    await h.close();
  }
  const h1 = await harness();
  try {
    const v1 = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
    v1.context.idempotency_key = "synthetic-readable-key-0001";
    assert.equal((await h1.adapter.handle("POST", "grade", v1)).status, 400);
    // v1.0：item_ref 前缀必须与学科一致（契约 N7）
    const wrong = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
    wrong.attempt.item_ref = "aya:synthetic-m3-01";
    assert.equal((await h1.adapter.handle("POST", "grade", wrong)).status, 400);
  } finally {
    await h1.close();
  }
});

test("响应自检（fail closed）：适配器产出的响应不合契约时回 500 INTERNAL，不把坏响应发出去", async () => {
  // 用一个把 GradeResponse 判为不合格的校验器包一层，模拟“实现与契约不一致”
  const failing: ContractValidator = {
    ...contract,
    validate: (schema, value, version) => (schema === "GradeResponse" ? { ok: false, errors: ["/ synthetic"] } : contract.validate(schema, value, version)),
  };
  const { keywordJudge, startRouteStub } = await import("./route-stub.ts");
  const stub = await startRouteStub(keywordJudge(realPack));
  try {
    const { routeConfig } = await import("./helpers.ts");
    const a = createAdapter({ content: realPack, taxonomy, contract: failing, route: routeConfig(stub) });
    const r = await a.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01));
    assert.equal(r.status, 500);
    assert.equal((r.body as Json).code, "INTERNAL");
    assert.ok(!JSON.stringify(r.body).includes(ANSWERS.partial01));
  } finally {
    await stub.close();
  }
});
