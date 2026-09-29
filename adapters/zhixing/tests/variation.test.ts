// generate_variation：选题，不生成。只从 admission = admitted 的题里按同节点或同错因选；没有候选时返回空与 empty_reason。
// 内容包全是 pending_review，线上返回空；选题逻辑用单独的合成“已准入”夹具验证（helpers.admittedPack）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter } from "../src/adapter.ts";
import { admittedPack, context, contextV1, contract, realPack, taxonomy, type Json } from "./helpers.ts";

const vctx = (v1 = false) => {
  const c = (v1 ? contextV1() : context()) as Json;
  delete c.usage_id; // 非消耗类方法
  return c;
};
const make = (pack = realPack) => createAdapter({ content: pack, taxonomy, contract, route: null, allowCandidate: true });
const call = async (a: ReturnType<typeof make>, body: unknown) => {
  const r = await a.handle("POST", "generate_variation", body);
  return { status: r.status, body: r.body as Json };
};

test("真实内容包全是 pending_review：返回空，empty_reason = no_admitted_candidate，不调模型", async () => {
  assert.ok(realPack.pack.items.every((i) => i.admission === "pending_review"));
  const { status, body: b } = await call(make(), { context: vctx(), source_item_ref: "zhixing:synthetic-m3-01", subject_error_category: "zhuanhua_cuo", count: 3 });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("GenerateVariationResponse", b), { ok: true });
  assert.deepEqual(b.result, { candidates: [], empty_reason: "no_admitted_candidate" });
  assert.deepEqual(b.evidence_ids, []);
  assert.equal(b.human_review_required, false);
  assert.equal(b.model_version, "algo:zhixing-item-select@0.1.0");
  assert.equal(b.source_version, "algo:zhixing-item-select@0.1.0;items:zhixing-synthetic@0.1.0");
});

test("合成已准入夹具：同节点或同错因的已准入题才入选；未准入的即使匹配也不选；按 count 截断", async () => {
  // 01 的节点：转化、定位、规范；02 的节点：转化、规范、定位；03：定位、转化；04：时政、规范
  const a = make(admittedPack(["synthetic-m3-02", "synthetic-m3-03"]));
  const { body: b } = await call(a, { context: vctx(), source_item_ref: "zhixing:synthetic-m3-01", count: 5 });
  assert.deepEqual(contract.validate("GenerateVariationResponse", b), { ok: true });
  assert.deepEqual(b.result.candidates.map((c: Json) => c.item_ref), ["zhixing:synthetic-m3-02", "zhixing:synthetic-m3-03"]);
  for (const c of b.result.candidates) {
    assert.equal(c.admission, "admitted");
    assert.equal(c.match.same_skill_node, true);
    assert.deepEqual(c.content_version, realPack.pack.curriculum);
  }
  const one = await call(a, { context: vctx(), source_item_ref: "zhixing:synthetic-m3-01", count: 1 });
  assert.equal(one.body.result.candidates.length, 1);
});

test("按节点：只给 skill_node 时选包含该节点的已准入题；按错因：同错因也算匹配，两者都匹配的排前面", async () => {
  const a = make(admittedPack(["synthetic-m3-02", "synthetic-m3-03", "synthetic-m3-04"]));
  const byNode = await call(a, { context: vctx(), skill_node: "zhixing:syn-dim-shizheng", count: 5 });
  assert.deepEqual(byNode.body.result.candidates.map((c: Json) => c.item_ref), ["zhixing:synthetic-m3-04"]);
  // 04 的常见失分含 shenti_cuo；02 也含 shenti_cuo 且含定位节点
  const both = await call(a, { context: vctx(), skill_node: "zhixing:syn-dim-dingwei", subject_error_category: "shenti_cuo", count: 5 });
  const refs = both.body.result.candidates.map((c: Json) => c.item_ref);
  assert.equal(refs[0], "zhixing:synthetic-m3-02");
  assert.ok(refs.includes("zhixing:synthetic-m3-04"));
  const m04 = both.body.result.candidates.find((c: Json) => c.item_ref === "zhixing:synthetic-m3-04").match;
  assert.deepEqual(m04, { same_skill_node: false, same_error_category: true });
});

test("排除做过的题 → all_excluded；未知节点 → skill_node_unknown；源题不在内容包 → 404", async () => {
  const a = make(admittedPack(["synthetic-m3-02"]));
  const ex = await call(a, { context: vctx(), source_item_ref: "zhixing:synthetic-m3-01", exclude_item_refs: ["zhixing:synthetic-m3-02"], count: 3 });
  assert.deepEqual(ex.body.result, { candidates: [], empty_reason: "all_excluded" });
  const unknown = await call(a, { context: vctx(), skill_node: "zhixing:syn-dim-nope", count: 3 });
  assert.deepEqual(unknown.body.result, { candidates: [], empty_reason: "skill_node_unknown" });
  const missing = await call(a, { context: vctx(), source_item_ref: "zhixing:synthetic-m3-99", count: 3 });
  assert.equal(missing.status, 404);
  assert.equal(missing.body.code, "ITEM_NOT_FOUND");
});

test("v1.0：编排层给了 item_family 时只在其中选；契约里的知行示例请求能被解析", async () => {
  const a = make(admittedPack(["synthetic-m3-02", "synthetic-m3-03"]));
  const body = {
    context: vctx(true),
    source_item_ref: "zhixing:synthetic-m3-01",
    count: 3,
    item_family: [{ item_ref: "zhixing:synthetic-m3-03", main_node_id: "DF.SYN.A", prior_difficulty: 0.4, license_state: "original" }],
  };
  const r = await call(a, body);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.deepEqual(contract.validate("GenerateVariationResponse", r.body, "1.0.0"), { ok: true });
  assert.deepEqual(r.body.result.candidates.map((c: Json) => c.item_ref), ["zhixing:synthetic-m3-03"]);
  const { readFileSync } = await import("node:fs");
  const { parse } = await import("yaml");
  const { contractPath } = await import("../src/contract.ts");
  const doc = parse(readFileSync(contractPath("1.0.0"), "utf8")) as Json;
  const ex = structuredClone(doc.components.examples.GenerateVariationRequestZhixing.value);
  const er = await call(make(), ex);
  assert.equal(er.status, 200, JSON.stringify(er.body));
  assert.deepEqual(er.body.result, doc.components.examples.GenerateVariationResponseZhixingEmpty.value.result);
});
