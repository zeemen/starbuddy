// 幂等（ADR 05 第 2 条；验收：同一个 idempotency_key 重试时，返回同一批 evidence_id）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { context, gradeBody, makeAdapter, type Json } from "./helpers.ts";

test("同 key 同请求体重试：同一批 evidence_id；进程重启后（新实例）仍然相同；request_id 变化不影响", () => {
  const body = gradeBody("Q-EQU-U03-D-001", { text: "9" }) as Json;
  const first = makeAdapter().handle("POST", "grade", body).body as Json;
  const again = makeAdapter().handle("POST", "grade", body).body as Json;
  const retry = structuredClone(body);
  retry.context.request_id = "00000000-0000-4000-8000-00000000ffff";
  const retried = makeAdapter().handle("POST", "grade", retry).body as Json;
  assert.equal(first.evidence_ids.length, 2);
  assert.deepEqual(again.evidence_ids, first.evidence_ids);
  assert.deepEqual(retried.evidence_ids, first.evidence_ids);
  assert.deepEqual(again.result.error_attributions, first.result.error_attributions);
  // occurred_at 取自作答本身，重试不变（ADR 05 第 2 条）
  assert.deepEqual(again.evidence.map((e: Json) => e.occurred_at), first.evidence.map((e: Json) => e.occurred_at));
});

test("同一实例内重试直接返回同一响应", () => {
  const a = makeAdapter();
  const body = gradeBody("Q-EQU-U03-D-001", { text: "9,-5" });
  assert.deepEqual(a.handle("POST", "grade", body).body, a.handle("POST", "grade", body).body);
});

test("同 key 换了请求体：IDEMPOTENCY_CONFLICT", () => {
  const a = makeAdapter();
  const body = gradeBody("Q-EQU-U03-D-001", { text: "9" }) as Json;
  assert.equal(a.handle("POST", "grade", body).status, 200);
  const changed = structuredClone(body);
  changed.attempt.response.text = "10";
  const r = a.handle("POST", "grade", changed);
  assert.equal(r.status, 409);
  assert.equal((r.body as Json).code, "IDEMPOTENCY_CONFLICT");
});

test("不同 key：不同的 evidence_id；diagnose 里每道作答的证据 ID 互不相同", () => {
  const a = makeAdapter();
  const x = a.handle("POST", "grade", gradeBody("Q-EQU-U03-D-001", { text: "9" })).body as Json;
  const y = a.handle("POST", "grade", gradeBody("Q-EQU-U03-D-001", { text: "9" })).body as Json;
  assert.notDeepEqual(x.evidence_ids, y.evidence_ids);
  const att = { item_ref: "aya:Q-EQU-U03-D-001", occurred_at: "2026-10-08T19:30:00+08:00", response: { text: "9" } };
  const d = a.handle("POST", "diagnose", { context: context(), mode: "intake", attempts: [att, att, att] }).body as Json;
  assert.equal(new Set(d.evidence_ids).size, 6);
});

test("appeal 缺省与 appeal: false 视为同一请求体", () => {
  const body = gradeBody("Q-EQU-U03-D-001", { text: "9" }) as Json;
  const explicit = { ...structuredClone(body), appeal: false };
  const a = makeAdapter().handle("POST", "grade", body).body as Json;
  const b = makeAdapter().handle("POST", "grade", explicit).body as Json;
  assert.deepEqual(a.evidence_ids, b.evidence_ids);
});
