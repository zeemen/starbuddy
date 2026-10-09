// 幂等（ADR 05 第 2 条；规则 4）：同 key 同请求体 → 同一结果与同一批 evidence_id（模型只调一次）；
// 同 key 不同请求体 → 409；并发的同 key 请求共享一次模型调用。全部是合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { ANSWERS, contract, gradeBody, gradeBodyV1, harness, type Json } from "./helpers.ts";
import { keywordJudge, ok } from "./route-stub.ts";
import { realPack } from "./helpers.ts";

test("同 key 同请求体重试：同一结果、同一批 evidence_id，模型只调一次；request_id 变化不影响", async () => {
  const h = await harness();
  try {
    const body = gradeBody("synthetic-m3-01", ANSWERS.partial01) as Json;
    const a = (await h.adapter.handle("POST", "grade", body)).body as Json;
    const retry = structuredClone(body);
    retry.context.request_id = "00000000-0000-4000-8000-00000000ffff";
    const b = (await h.adapter.handle("POST", "grade", retry)).body as Json;
    assert.deepEqual(b, a);
    assert.equal(h.stub.calls.length, 1);
    // appeal 缺省与 appeal: false 视为同一请求体
    const explicit = { ...structuredClone(body), appeal: false };
    const c = (await h.adapter.handle("POST", "grade", explicit)).body as Json;
    assert.deepEqual(c.evidence_ids, a.evidence_ids);
    assert.equal(h.stub.calls.length, 1);
  } finally {
    await h.close();
  }
});

test("模型两次回答不同也不影响：重试直接用第一次的判定（缓存只存判定，不存原文）", async () => {
  let n = 0;
  const judge = keywordJudge(realPack);
  const h = await harness({
    handler: (req) => {
      n++;
      return n === 1 ? judge(req) : ok(req, "not json");
    },
  });
  try {
    const body = gradeBody("synthetic-m3-01", ANSWERS.full01);
    const a = (await h.adapter.handle("POST", "grade", body)).body as Json;
    const b = (await h.adapter.handle("POST", "grade", body)).body as Json;
    assert.equal(a.result.verdict, "correct");
    assert.deepEqual(b, a);
  } finally {
    await h.close();
  }
});

test("同 key 换了请求体：409 IDEMPOTENCY_CONFLICT，不调模型，不回显作答", async () => {
  const h = await harness();
  try {
    const body = gradeBody("synthetic-m3-01", ANSWERS.partial01) as Json;
    assert.equal((await h.adapter.handle("POST", "grade", body)).status, 200);
    for (const mutate of [(b: Json) => (b.attempt.response.text = ANSWERS.full01), (b: Json) => (b.attempt.elapsed_sec = 30), (b: Json) => (b.appeal = true)]) {
      const changed = structuredClone(body);
      mutate(changed);
      const r = await h.adapter.handle("POST", "grade", changed);
      assert.equal(r.status, 409);
      assert.equal((r.body as Json).code, "IDEMPOTENCY_CONFLICT");
      assert.ok(!JSON.stringify(r.body).includes(ANSWERS.full01));
    }
    assert.equal(h.stub.calls.length, 1);
  } finally {
    await h.close();
  }
});

test("并发的同 key 请求：只调一次模型，结果相同", async () => {
  const judge = keywordJudge(realPack);
  const h = await harness({ handler: async (req) => ({ ...(await judge(req)), delayMs: 100 }) });
  try {
    const body = gradeBody("synthetic-m3-01", ANSWERS.partial01);
    const [a, b] = await Promise.all([h.adapter.handle("POST", "grade", body), h.adapter.handle("POST", "grade", body)]);
    assert.deepEqual(a, b);
    assert.equal(h.stub.calls.length, 1);
  } finally {
    await h.close();
  }
});

test("v1.0：进程重启后（新实例）同一作答、同样的模型判定 → 同一批 evidence_id（按作答内容派生）", async () => {
  const body = gradeBodyV1("synthetic-m3-01", ANSWERS.partial01);
  const h1 = await harness();
  const h2 = await harness();
  try {
    const a = (await h1.adapter.handle("POST", "grade", body)).body as Json;
    const b = (await h2.adapter.handle("POST", "grade", body)).body as Json;
    assert.deepEqual(b.evidence_ids, a.evidence_ids);
    assert.equal(new Set(a.evidence_ids).size, a.evidence_ids.length);
  } finally {
    await h1.close();
    await h2.close();
  }
});

test("不同 key：v0.9 下作答证据 ID 不同；diagnose 与 generate_variation 同 key 不同体也是 409", async () => {
  const h = await harness();
  try {
    const x = (await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01))).body as Json;
    const y = (await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01))).body as Json;
    assert.notDeepEqual(x.evidence_ids, y.evidence_ids);
    const d = gradeBody("synthetic-m3-01", ANSWERS.partial01) as Json;
    const diag = { context: d.context, mode: "process", attempts: [d.attempt] };
    assert.equal((await h.adapter.handle("POST", "diagnose", diag)).status, 200);
    const diag2 = structuredClone(diag);
    diag2.attempts[0].elapsed_sec = 5;
    assert.equal((await h.adapter.handle("POST", "diagnose", diag2)).status, 409);
    const v = { context: { ...d.context, idempotency_key: "synthetic-zhixing-var-key-0001" }, source_item_ref: "zhixing:synthetic-m3-01", count: 3 } as Json;
    delete v.context.usage_id;
    assert.equal((await h.adapter.handle("POST", "generate_variation", v)).status, 200);
    assert.equal((await h.adapter.handle("POST", "generate_variation", { ...v, count: 2 })).status, 409);
  } finally {
    await h.close();
  }
});

// ---------- 缓存满时的淘汰（审查意见第 10 条） ----------
test("缓存满：优先淘汰已完成的条目，进行中的条目不淘汰（同 key 重试仍共享那一次模型调用）", async () => {
  const judge = keywordJudge(realPack);
  let delay = 0;
  const h = await harness({ idempotencyCacheSize: 2, handler: async (req) => ({ ...(await judge(req)), delayMs: delay }) });
  try {
    const done = gradeBody("synthetic-m3-01", ANSWERS.partial01);
    assert.equal((await h.adapter.handle("POST", "grade", done)).status, 200);
    delay = 150;
    const slow = gradeBody("synthetic-m3-01", ANSWERS.full01);
    const slowRun = h.adapter.handle("POST", "grade", slow);
    await new Promise((r) => setTimeout(r, 30));
    // 第三个 key：缓存已满（1 条已完成 + 1 条进行中）→ 淘汰已完成的那条，不动进行中的
    delay = 0;
    assert.equal((await h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.miss01))).status, 200);
    const callsBefore = h.stub.calls.length;
    const slowRetry = h.adapter.handle("POST", "grade", slow);
    const [a, b] = await Promise.all([slowRun, slowRetry]);
    assert.equal(a.status, 200);
    assert.deepEqual(b, a);
    assert.equal(h.stub.calls.length, callsBefore, "进行中的条目还在：重试没有再调模型");
    // 被淘汰的已完成条目：同 key 再来会重新计算（多调一次模型），不报冲突
    const again = await h.adapter.handle("POST", "grade", done);
    assert.equal(again.status, 200);
    assert.equal(h.stub.calls.length, callsBefore + 1);
  } finally {
    await h.close();
  }
});

test("缓存满且全是进行中的请求：新请求回 429 RATE_LIMITED（retryable，稍后用同一个 key 重试），不调模型", async () => {
  const judge = keywordJudge(realPack);
  // 确定性（W13 T09）：模型桩挂在一个闸门上，两次调用都到达桩之后才发第三个请求，最后再放行；不靠固定的等待时长
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const h = await harness({ idempotencyCacheSize: 2, handler: async (req) => {
    await gate;
    return judge(req);
  } });
  try {
    const runs = [h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.partial01)), h.adapter.handle("POST", "grade", gradeBody("synthetic-m3-01", ANSWERS.full01))];
    for (let i = 0; h.stub.calls.length < 2; i++) {
      assert.ok(i < 1000, "两次模型调用没有到达桩");
      await new Promise((r) => setTimeout(r, 5));
    }
    const busyBody = gradeBody("synthetic-m3-01", ANSWERS.miss01);
    const busy = await h.adapter.handle("POST", "grade", busyBody);
    assert.equal(busy.status, 429);
    const pb = busy.body as Json;
    assert.equal(pb.code, "RATE_LIMITED");
    assert.equal(pb.retryable, true);
    assert.deepEqual(contract.validate("Problem", pb), { ok: true });
    assert.deepEqual(contract.validate("Problem", pb, "1.0.0"), { ok: true });
    assert.equal(h.stub.calls.length, 2, "被拒的请求没有调模型");
    release();
    for (const r of await Promise.all(runs)) assert.equal(r.status, 200);
    // 进行中的完成之后，同一个 key 重试成功
    assert.equal((await h.adapter.handle("POST", "grade", busyBody)).status, 200);
  } finally {
    release(); // 断言失败时也放行，免得挂住的模型调用拖住关闭
    await h.close();
  }
});
