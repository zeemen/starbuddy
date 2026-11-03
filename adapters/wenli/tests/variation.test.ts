// generate_variation（spec 4-D 文砺部分，待人确认 #4 默认）：选题，不生成。候选必须同时满足
// 同能力格、同题型（题型编码严格相等）、已准入；没有合格候选时返回空结果与 empty_reason。不调模型。
// 内容包全是 pending_review，线上返回空；选题逻辑用单独的合成“已准入”夹具验证（本文件的 packWith）。全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, SELECT_ALGORITHM } from "../src/adapter.ts";
import { indexPack, questionTypeOf, type ContentPack, type LoadedPack } from "../src/content.ts";
import { context, contextV1, contract, leaves, realPack, type Json } from "./helpers.ts";

/** 合成夹具：在 D1_L1 格（已有 q01 选择、q03 填空）再加两道合成选择题 q11、q12，并按参数设准入 */
function packWith(admitted: string[]): LoadedPack {
  const pack = structuredClone(realPack.pack) as ContentPack;
  const q01 = pack.items.find((i) => i.id === "synthetic-q01")!;
  for (const id of ["synthetic-q11", "synthetic-q12"]) pack.items.push({ ...structuredClone(q01), id, stem: "（合成题）变式选题测试用的合成题面。" });
  for (const it of pack.items) it.admission = admitted.includes(it.id) ? "admitted" : "pending_review";
  for (const e of pack.essays) e.admission = admitted.includes(e.id) ? "admitted" : "pending_review";
  return indexPack(pack);
}
const make = (content: LoadedPack = realPack) => createAdapter({ content, contract, route: null, allowCandidate: true });
const call = async (a: ReturnType<typeof make>, body: unknown) => {
  const r = await a.handle("POST", "generate_variation", body);
  return { status: r.status, body: r.body as Json };
};
const ctx = (v1 = false) => {
  const c = (v1 ? contextV1() : context()) as Json;
  delete c.usage_id; // 非消耗类方法
  return c;
};
const refs = (b: Json) => b.result.candidates.map((c: Json) => c.item_ref.replace("wenli:synthetic-", ""));

test("真实内容包全是 pending_review：返回空，empty_reason = no_admitted_candidate；信封是确定性算法、不调模型", async () => {
  assert.ok([...realPack.pack.items, ...realPack.pack.essays].every((i) => i.admission === "pending_review"));
  for (const v1 of [false, true]) {
    const { status, body: b } = await call(make(), { context: ctx(v1), source_item_ref: "wenli:synthetic-q01", count: 3 });
    assert.equal(status, 200, JSON.stringify(b));
    assert.deepEqual(contract.validate("GenerateVariationResponse", b, v1 ? "1.0.0" : "0.9.0"), { ok: true });
    assert.deepEqual(b.result, { candidates: [], empty_reason: "no_admitted_candidate" });
    assert.deepEqual([b.evidence_ids, b.confidence, b.human_review_required, b.semantics], [[], 1, false, "formal"]);
    assert.equal(b.model_version, `algo:${SELECT_ALGORITHM}`);
    assert.equal(b.source_version, `algo:${SELECT_ALGORITHM};items:wenli-synthetic@0.1.0`);
  }
});

test("正常：只选同能力格、同题型、已准入的题；按题号排序、按 count 截断；match 只标同能力点", async () => {
  // D1_L1：q01 选择（源题）、q03 填空、q11 选择、q12 选择；D1_L2：q02 选择
  const a = make(packWith(["synthetic-q02", "synthetic-q03", "synthetic-q11", "synthetic-q12"]));
  const { status, body: b } = await call(a, { context: ctx(true), source_item_ref: "wenli:synthetic-q01", count: 5 });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("GenerateVariationResponse", b, "1.0.0"), { ok: true });
  assert.deepEqual(refs(b), ["q11", "q12"], "q03 题型不同、q02 能力格不同，都不选");
  for (const c of b.result.candidates) {
    assert.equal(c.admission, "admitted");
    assert.deepEqual(c.match, { same_skill_node: true, same_error_category: false });
    assert.deepEqual(c.content_version, realPack.pack.curriculum);
  }
  assert.deepEqual(refs((await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q01", count: 1 })).body), ["q11"]);
  // 源题是填空：同格里的选择题一道都不选
  const fill = await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q03", count: 5 });
  assert.deepEqual(fill.body.result, { candidates: [], empty_reason: "no_admitted_candidate" });
});

test("准入过滤：同格同题型但未准入的题不选；已准入的只给 skill_node 时按格选（没有参照题型）", async () => {
  const a = make(packWith(["synthetic-q03", "synthetic-q11"]));
  assert.deepEqual(refs((await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q01", count: 5 })).body), ["q11"], "q12 未准入");
  const byNode = await call(a, { context: ctx(), skill_node: "wenli:CHN.SYN.D1_L1", count: 5 });
  assert.deepEqual(refs(byNode.body), ["q03", "q11"]);
  // skill_node 与源题一致时照常；不一致 → 请求自相矛盾，422
  assert.deepEqual(refs((await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q01", skill_node: "wenli:CHN.SYN.D1_L1", count: 5 })).body), ["q11"]);
  const bad = await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q01", skill_node: "wenli:CHN.SYN.D1_L2", count: 5 });
  assert.equal(bad.status, 422);
  assert.equal(bad.body.code, "INVALID_REQUEST");
});

test("候选不足：全被排除 → all_excluded；未知能力格 → skill_node_unknown；作文按体裁区分题型", async () => {
  const a = make(packWith(["synthetic-q11", "synthetic-essay-02"]));
  const ex = await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q01", exclude_item_refs: ["wenli:synthetic-q11"], count: 3 });
  assert.deepEqual(ex.body.result, { candidates: [], empty_reason: "all_excluded" });
  const unknown = await call(a, { context: ctx(), skill_node: "wenli:CHN.SYN.D9_L9", count: 3 });
  assert.deepEqual(unknown.body.result, { candidates: [], empty_reason: "skill_node_unknown" });
  // 源题本身不算候选（不重推原题）：它是唯一的已准入题时，结果是 no_admitted_candidate
  assert.deepEqual((await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q11", count: 3 })).body.result.empty_reason, "no_admitted_candidate");
  // 作文：essay-01（记叙文，D5_L2）与 essay-02（情境写作，D5_L1）题型、能力格都不同
  assert.equal(questionTypeOf(realPack.essayById.get("synthetic-essay-01")!), "essay_narrative");
  assert.notEqual(questionTypeOf(realPack.essayById.get("synthetic-essay-01")!), questionTypeOf(realPack.essayById.get("synthetic-essay-02")!));
  assert.deepEqual((await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-essay-01", count: 3 })).body.result.empty_reason, "no_admitted_candidate");
  assert.deepEqual(refs((await call(a, { context: ctx(), skill_node: "wenli:CHN.SYN.D5_L1", count: 3 })).body), ["essay-02"]);
});

test("v1.0：编排层给了 item_family 时只在其中选，difficulty 取 prior_difficulty；不在内容包里的族内题不选", async () => {
  const a = make(packWith(["synthetic-q11", "synthetic-q12"]));
  const family = [
    { item_ref: "wenli:synthetic-q12", main_node_id: "CHN.SYN.D1_L1", prior_difficulty: 0.35, license_state: "original" },
    { item_ref: "wenli:synthetic-q99", main_node_id: "CHN.SYN.D1_L1", license_state: "original" },
  ];
  const { status, body: b } = await call(a, { context: ctx(true), source_item_ref: "wenli:synthetic-q01", count: 3, item_family: family });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("GenerateVariationResponse", b, "1.0.0"), { ok: true });
  assert.deepEqual(b.result.candidates, [
    { item_ref: "wenli:synthetic-q12", admission: "admitted", content_version: realPack.pack.curriculum, match: { same_skill_node: true, same_error_category: false }, difficulty: 0.35 },
  ]);
});

test("上游数据不合格：源题不在内容包 → 404；别科引用 → 4xx；幂等：同 key 同体同结果、不同体 409；输出只有编码与 ID", async () => {
  const a = make(packWith(["synthetic-q11", "synthetic-q12"]));
  const missing = await call(a, { context: ctx(), source_item_ref: "wenli:synthetic-q77", count: 3 });
  assert.equal(missing.status, 404);
  assert.equal(missing.body.code, "ITEM_NOT_FOUND");
  for (const body of [
    { context: ctx(), source_item_ref: "aya:synthetic-0301-07", count: 3 },
    { context: ctx(), skill_node: "zhixing:syn-dim-dingwei", count: 3 },
    { context: ctx(), source_item_ref: "wenli:synthetic-q01", exclude_item_refs: ["zhixing:synthetic-m3-01"], count: 3 },
  ]) {
    const r = await call(a, body);
    assert.ok(r.status === 400, `${JSON.stringify(body)} → ${r.status}`);
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
  }
  const body = { context: ctx(), source_item_ref: "wenli:synthetic-q01", count: 3 };
  const r1 = await call(a, body);
  assert.deepEqual((await call(a, structuredClone(body))).body, r1.body);
  const c = await call(a, { ...structuredClone(body), count: 2 });
  assert.equal(c.status, 409);
  assert.equal(c.body.code, "IDEMPOTENCY_CONFLICT");
  const CODE = /^[a-z0-9_]{1,48}$|^wenli:[A-Za-z0-9_.\-]+$|^[0-9a-f-]{36}$|^algo:|^0\.9\.0$|^\d+\.\d+\.\d+$|^[a-z][a-z0-9\-]{1,31}$/;
  for (const [path, v] of leaves(r1.body)) if (typeof v === "string") assert.match(v, CODE, `${path} = ${v}`);
});
