// generate_variation（spec 设计要点第 2 节、验收标准 generate_variation 各条）。选题逻辑用合成内容包覆盖（真实内容包 721 道
// 全部未准入，一律空结果）。全部是合成数据（规则 8）：合成题挂在真实节点与真实陷阱码上，没有题目内容。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { PackItem } from "../src/content.ts";
import { VARIANT_ALGORITHM } from "../src/methods/variation.ts";
import { contextV1, contractV1, makeAdapterV1, realPack, syntheticPack, type Json } from "./helpers.ts";

const trapOf = (root: string) => realPack.pack.traps.find((t) => t.rootCause === root)!.code;
const YUNSUAN = trapOf("运算错误"); // → yunsuan_cuowu
const GAINIAN = trapOf("概念错误"); // → gainian_cuowu
const [N1, N2, N3, N4] = realPack.pack.nodes.slice(0, 4).map((n) => n.id) as [string, string, string, string];

const fill = (node: string, traps: string[], extra: Partial<PackItem> = {}): Partial<PackItem> => ({
  mainNodes: [node],
  format: "fill",
  answer: ["1"],
  ...(traps.length ? { wrongAnswerTraps: [{ pattern: ["0"], traps }] } : {}),
  ...extra,
});
// SYN-0 原题；SYN-1 同节点同错因同用途；SYN-2 只同节点（用途 T）；SYN-3 只同错因；SYN-4 都不同；
// SYN-5 同节点但未准入（Q1）；SYN-6 同节点单选（用途 D，题型过滤用）；SYN-7 只同节点、概念错误
const pack = syntheticPack(
  [
    fill(N1, [YUNSUAN]),
    fill(N1, [YUNSUAN]),
    fill(N1, [], { usage: "T" }),
    fill(N2, [YUNSUAN]),
    fill(N3, [GAINIAN]),
    fill(N1, [YUNSUAN], { reviewStatus: "Q1" }),
    { mainNodes: [N1], format: "single", answer: ["A"], options: [{ key: "A" }, { key: "B", traps: [GAINIAN] }] },
    fill(N1, [GAINIAN], { usage: "T" }),
  ],
  { sourceVerified: true },
);
const adapter = makeAdapterV1(pack);

function gv(extra: Record<string, unknown>, a = adapter) {
  const r = a.handle("POST", "generate_variation", { context: contextV1(), count: 10, ...extra });
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  assert.deepEqual(contractV1.validate("GenerateVariationResponse", r.body, "1.0.0"), { ok: true });
  return r.body as Json;
}
const refs = (b: Json) => b.result.candidates.map((c: Json) => c.item_ref);

// ---------- 正常 ----------
test("按原题选：候选全部 admitted、不含原题与 exclude_item_refs、每个候选至少同节点或同错因；排序：同节点且同错因 → 同用途 → 题号", () => {
  const b = gv({ source_item_ref: "aya:SYN-0" });
  assert.deepEqual(refs(b), ["aya:SYN-1", "aya:SYN-3", "aya:SYN-6", "aya:SYN-2", "aya:SYN-7"]);
  for (const c of b.result.candidates) {
    assert.equal(c.admission, "admitted");
    assert.equal(pack.itemById.get(c.item_ref.slice(4))!.admission, "admitted");
    assert.ok(c.match.same_skill_node || c.match.same_error_category);
    assert.deepEqual(c.content_version, pack.pack.curriculum);
    assert.ok(!("difficulty" in c)); // 不是来自 item_family：不输出难度
  }
  assert.deepEqual(b.result.candidates[0].match, { same_skill_node: true, same_error_category: true });
  assert.deepEqual(b.result.candidates[1].match, { same_skill_node: false, same_error_category: true });
  assert.ok(!("empty_reason" in b.result));
  assert.equal(b.semantics, "formal");
  assert.equal(b.confidence, 1);
  assert.deepEqual(b.evidence_ids, []);
  assert.equal(b.model_version, `algo:${VARIANT_ALGORITHM}`);
  const ex = gv({ source_item_ref: "aya:SYN-0", exclude_item_refs: ["aya:SYN-1", "aya:SYN-6"], count: 2 });
  assert.deepEqual(refs(ex), ["aya:SYN-3", "aya:SYN-2"]);
});

test("按节点选（不给原题）只按节点匹配；给了 subject_error_category 时错因按它匹配", () => {
  assert.deepEqual(refs(gv({ skill_node: `aya:${N2}` })), ["aya:SYN-3"]);
  // N4 上没有合成题：候选只能靠同错因
  const byCat = gv({ skill_node: `aya:${N4}`, subject_error_category: "yunsuan_cuowu" });
  assert.deepEqual(refs(byCat), ["aya:SYN-0", "aya:SYN-1", "aya:SYN-3"]);
  for (const c of byCat.result.candidates) assert.deepEqual(c.match, { same_skill_node: false, same_error_category: true });
  // 原题 + 指定错因：错因改按指定的算
  const narrowed = gv({ source_item_ref: "aya:SYN-0", subject_error_category: "gainian_cuowu" });
  assert.deepEqual(refs(narrowed), ["aya:SYN-6", "aya:SYN-7", "aya:SYN-1", "aya:SYN-4", "aya:SYN-2"]);
});

test("exam_config.question_type_codes 非空时只选题型在列表内的题", () => {
  const onlyChoice = gv({ source_item_ref: "aya:SYN-0", exam_config: { cohort: 2027, full_score: 120, question_type_codes: ["single_choice"] } });
  assert.deepEqual(refs(onlyChoice), ["aya:SYN-6"]);
  const noneLeft = gv({ source_item_ref: "aya:SYN-0", exam_config: { cohort: 2027, full_score: 120, question_type_codes: ["multi_choice"] } });
  assert.deepEqual(noneLeft.result, { candidates: [], empty_reason: "no_admitted_candidate" });
});

test("带 item_family：候选都在 item_family 里，restricted 排除，difficulty 等于 prior_difficulty；不在内容包的族内题丢弃", () => {
  const b = gv({
    source_item_ref: "aya:SYN-0",
    item_family: [
      { item_ref: "aya:SYN-2", main_node_id: "MA.ALG.SYN_0001", prior_difficulty: 0.35, license_state: "original" },
      { item_ref: "aya:SYN-1", main_node_id: "MA.ALG.SYN_0001", prior_difficulty: 0.6, license_state: "restricted" },
      { item_ref: "aya:SYN-3", main_node_id: "MA.ALG.SYN_0002", license_state: "licensed" },
      { item_ref: "aya:SYN-5", main_node_id: "MA.ALG.SYN_0001", prior_difficulty: 0.2, license_state: "public" },
      { item_ref: "aya:NOT-IN-PACK", main_node_id: "MA.ALG.SYN_0001", prior_difficulty: 0.5, license_state: "original" },
    ],
  });
  assert.deepEqual(refs(b), ["aya:SYN-3", "aya:SYN-2"]);
  assert.ok(!("difficulty" in b.result.candidates[0])); // 族内没有 prior_difficulty
  assert.equal(b.result.candidates[1].difficulty, 0.35);
  // 族里全是受限或未准入的题 → 空
  const empty = gv({ source_item_ref: "aya:SYN-0", item_family: [{ item_ref: "aya:SYN-1", main_node_id: "MA.ALG.SYN_0001", license_state: "restricted" }] });
  assert.deepEqual(empty.result, { candidates: [], empty_reason: "no_admitted_candidate" });
});

// ---------- 空结果（低置信度的对应情形：没有合格候选时不硬给） ----------
test("三种 empty_reason：节点不在内容包 → skill_node_unknown；匹配的已准入题全被排除 → all_excluded；其他 → no_admitted_candidate", () => {
  assert.deepEqual(gv({ skill_node: "aya:K-NOT-A-NODE-0000" }).result, { candidates: [], empty_reason: "skill_node_unknown" });
  const all = gv({ skill_node: `aya:${N2}`, exclude_item_refs: ["aya:SYN-3"] });
  assert.deepEqual(all.result, { candidates: [], empty_reason: "all_excluded" });
  const onlyUnadmitted = syntheticPack([fill(N1, [YUNSUAN]), fill(N1, [YUNSUAN], { reviewStatus: "Q1" })], { sourceVerified: true });
  const none = gv({ source_item_ref: "aya:SYN-0" }, makeAdapterV1(onlyUnadmitted));
  assert.deepEqual(none.result, { candidates: [], empty_reason: "no_admitted_candidate" });
  for (const b of [all, none]) {
    assert.equal(b.semantics, "formal");
    assert.equal(b.confidence, 1);
    assert.equal(b.human_review_required, false);
  }
});

test("真实内容包：721 道全部未准入，按原题或节点选一律返回 no_admitted_candidate", () => {
  const real = makeAdapterV1();
  const items = realPack.pack.items.filter((_, i) => i % 9 === 0);
  assert.ok(items.length >= 80);
  for (const it of items) {
    const b = gv({ source_item_ref: `aya:${it.id}`, count: 5 }, real);
    assert.deepEqual(b.result, { candidates: [], empty_reason: "no_admitted_candidate" }, it.id);
    const byNode = gv({ skill_node: `aya:${it.mainNodes[0]}`, count: 5 }, real);
    assert.deepEqual(byNode.result, { candidates: [], empty_reason: "no_admitted_candidate" }, it.id);
  }
});

// ---------- 上游失败 ----------
test("坏请求：原题不在内容包 → 404；错因编码不在映射表 → 422；族内有别的学科 → 400；前缀不符由 schema 拒 → 400；0.9.0 → 501", () => {
  const cases: [Record<string, unknown>, number, string][] = [
    [{ source_item_ref: "aya:NOT-IN-PACK" }, 404, "ITEM_NOT_FOUND"],
    [{ skill_node: `aya:${N1}`, subject_error_category: "not_a_code" }, 422, "INVALID_REQUEST"],
    [{ skill_node: `aya:${N1}`, item_family: [{ item_ref: "wenli:Q-1", main_node_id: "MA.ALG.SYN_0001", license_state: "original" }] }, 400, "SUBJECT_MISMATCH"],
    [{ source_item_ref: "zhixing:Q-1" }, 400, "INVALID_REQUEST"],
    [{ skill_node: `aya:${N1}`, exclude_item_refs: ["wenli:Q-1"] }, 400, "INVALID_REQUEST"],
  ];
  for (const [extra, status, code] of cases) {
    const r = adapter.handle("POST", "generate_variation", { context: contextV1(), count: 2, ...extra });
    assert.equal(r.status, status, JSON.stringify(extra));
    assert.equal((r.body as Json).code, code);
    assert.deepEqual(contractV1.validate("Problem", r.body, "1.0.0"), { ok: true });
  }
  const v09 = adapter.handle("POST", "generate_variation", { context: contextV1({ contract_version: "0.9.0", idempotency_key: "synthetic-gv-key" }), count: 2, skill_node: `aya:${N1}` });
  assert.equal(v09.status, 501);
});
