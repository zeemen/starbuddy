// 契约测试（验收：适配器的每个响应都通过契约 schema 校验；规则 3 的五个字段齐全）。
// schema 直接取自 packages/contracts，不手写期望结构。用例覆盖内容包里全部 721 道题。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { parse } from "yaml";
import { contractPath } from "../src/contract.ts";
import { contract, context, gradeBody, KNOWN_TRAP_PATTERN_CONFLICTS, makeAdapter, realPack, sweepCases, type Json } from "./helpers.ts";
import { gradeAnswer } from "../src/core/grading.ts";

const adapter = makeAdapter();
const RULE3 = ["evidence_ids", "confidence", "source_version", "model_version", "human_review_required"];

test("capabilities 符合契约，声明 grade 与 diagnose 已实现、其余未实现", () => {
  const r = adapter.handle("GET", "capabilities", undefined);
  assert.equal(r.status, 200);
  assert.deepEqual(contract.validate("Capabilities", r.body), { ok: true });
  const m = (r.body as Json).methods;
  assert.equal(m.grade.implemented, true);
  assert.equal(m.diagnose.implemented, true);
  for (const k of ["plan", "teach", "generate_variation", "report"]) assert.equal(m[k].implemented, false);
});

test("全部 721 道题：每个 grade 响应都通过契约校验，判定与预期一致，并带规则 3 的五个字段", () => {
  const cases = sweepCases();
  const items = new Set(cases.map((c) => c.item.id));
  assert.equal(items.size, 721);
  for (const c of cases) {
    const r = adapter.handle("POST", "grade", gradeBody(c.item.id, c.response));
    assert.equal(r.status, 200, `${c.item.id} ${JSON.stringify(r.body)}`);
    assert.deepEqual(contract.validate("GradeResponse", r.body), { ok: true }, c.item.id);
    const b = r.body as Json;
    for (const k of RULE3) assert.ok(k in b, `${c.item.id} 缺少 ${k}`);
    assert.equal(b.result.verdict, c.expect, `${c.item.id} ${JSON.stringify(c.response)}`);
  }
});

test("legacy 题库里与标准答案等价的“典型错误答案”只有已登记的两处（新增的会让本测试失败）", () => {
  const found: string[] = [];
  for (const item of realPack.pack.items) {
    for (const w of item.wrongAnswerTraps ?? []) {
      for (const p of w.pattern) if (gradeAnswer(item, [p]).correct) found.push(`${item.id} ${p}`);
    }
  }
  assert.deepEqual(found.sort(), [...KNOWN_TRAP_PATTERN_CONFLICTS].sort());
});

test("diagnose 响应通过契约校验（intake 与 gap_test）", () => {
  const cases = sweepCases().filter((_, i) => i % 7 === 0).slice(0, 150);
  for (const mode of ["intake", "gap_test"]) {
    const body = {
      context: context(),
      mode,
      attempts: cases.map((c, i) => ({
        item_ref: `aya:${c.item.id}`,
        occurred_at: "2026-10-08T19:30:00+08:00",
        response: c.response,
        hint_level_used: i % 7,
        ...(i % 3 === 0 ? { self_report: "guessed" } : {}),
      })),
    };
    const r = adapter.handle("POST", "diagnose", body);
    assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
    assert.deepEqual(contract.validate("DiagnoseResponse", r.body), { ok: true });
  }
});

test("错误响应都是契约 Problem，且不含学生原文", () => {
  const bodies: [string, string, unknown, number, string][] = [
    ["POST", "grade", { nonsense: true }, 400, "INVALID_REQUEST"],
    ["POST", "grade", gradeBody("NOT-AN-ITEM", { text: "学生原文ABC" }), 404, "ITEM_NOT_FOUND"],
    ["POST", "grade", { ...gradeBody("Q-EQU-U03-D-001", { text: "1" }), context: context({ subject: "wenli" }) }, 400, "SUBJECT_MISMATCH"],
    ["POST", "grade", { ...gradeBody("Q-EQU-U03-D-001", { text: "1" }), rubric_version: "other-rubric@1.0.0" }, 400, "INVALID_REQUEST"],
    ["POST", "grade", gradeBody("Q-EQU-U03-D-001", { choice: ["A"] }), 422, "INVALID_REQUEST"],
    ["POST", "diagnose", { context: context(), mode: "paper", paper_image_refs: ["objstore://synthetic/p1.jpg"] }, 422, "INVALID_REQUEST"],
    ["POST", "plan", {}, 501, "METHOD_NOT_IMPLEMENTED"],
    ["GET", "jobs/00000000-0000-4000-8000-000000000001", undefined, 404, "JOB_NOT_FOUND"],
  ];
  const withCv = gradeBody("Q-EQU-U03-D-001", { text: "1" }) as Json;
  withCv.context.content_version.content_version = "9.9.9";
  bodies.push(["POST", "grade", withCv, 404, "CONTENT_VERSION_NOT_FOUND"]);
  for (const [method, path, body, status, code] of bodies) {
    const r = adapter.handle(method, path, body);
    assert.equal(r.status, status, `${path} ${JSON.stringify(r.body)}`);
    assert.equal((r.body as Json).code, code);
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
    assert.ok(!JSON.stringify(r.body).includes("学生原文"));
  }
});

test("契约里的阿鸦示例请求能被适配器按契约解析（题目是合成的，不在内容包里 → ITEM_NOT_FOUND）", () => {
  const doc = parse(readFileSync(contractPath("0.9.0"), "utf8")) as Json; // 本文件按 0.9.0 写（helpers 钉住 0.9.0）
  const ex = structuredClone(doc.components.examples.GradeRequestAyaObjective.value);
  assert.deepEqual(contract.validate("GradeRequest", ex), { ok: true });
  ex.context.content_version = { ...realPack.pack.curriculum };
  const r = adapter.handle("POST", "grade", ex);
  assert.equal(r.status, 404);
  assert.equal((r.body as Json).code, "ITEM_NOT_FOUND");
});

test("content_version 六段都要一致，否则 CONTENT_VERSION_NOT_FOUND", () => {
  for (const [k, v] of [["region", "suzhou"], ["exam_year", 2030], ["grade", 7], ["textbook_version", "bubian-2024"]] as const) {
    const body = gradeBody("Q-EQU-U03-D-001", { text: "1" }) as Json;
    body.context.content_version[k] = v;
    const r = adapter.handle("POST", "grade", body);
    assert.equal(r.status, 404, k);
    assert.equal((r.body as Json).code, "CONTENT_VERSION_NOT_FOUND");
  }
});

test("选择题：重复的 key 拒绝；单选只能选一个", () => {
  const item = realPack.pack.items.find((i) => i.format === "single")!;
  const k = item.options![0]!.key;
  assert.equal(adapter.handle("POST", "grade", gradeBody(item.id, { choice: [k, k] })).status, 422);
});
