// plan（specs/adapter-aya-methods-sprint4/spec.md 设计要点第 1 节、验收标准 plan 各条）。全部是合成数据（规则 8）：
// 学生、请求、幂等键是固定的示例 UUID；掌握快照是编造的；合成内容包的题目挂在真实节点上，题目内容为空。
import assert from "node:assert/strict";
import { test } from "node:test";
import type { PackItem } from "../src/content.ts";
import { PLAN_ALGORITHM, PLAN_MAX_TASKS, RATIONALE_CODES } from "../src/methods/plan.ts";
import { contextV1, contractV1, makeAdapterV1, realPack, syntheticPack, type Json } from "./helpers.ts";

const nodeOf = (board: string, kind: string, skip = 0) => realPack.pack.nodes.filter((n) => n.board === board && n.kind === kind)[skip]!.id;
const FN_K = nodeOf("函数", "K"); // 函数板块占比 0.26 → high_exam_weight
const ST_K = nodeOf("统计", "K"); // 统计 0.07 → weak_mastery
const ST_K2 = nodeOf("统计", "K", 1);
const ST_K3 = nodeOf("统计", "K", 2);
const PR_K = nodeOf("概率", "K"); // 概率 0.05

/** 每个节点三道练习题（D、T、L 各一）、一道 R 题；admitted 由 sourceVerified 决定 */
function packFor(nodes: string[], opts: { sourceVerified?: boolean; noR?: string[]; links?: [string, string][] } = {}) {
  const items: Partial<PackItem>[] = [];
  for (const n of nodes) {
    for (const usage of ["D", "T", "L"] as const) items.push({ mainNodes: [n], usage, estMinutes: 3, format: "fill" });
    if (!(opts.noR ?? []).includes(n)) items.push({ mainNodes: [n], usage: "R", estMinutes: 2, format: "single", options: [{ key: "A" }, { key: "B" }], answer: ["A"] });
  }
  const links = opts.links ?? [];
  return syntheticPack(items, {
    sourceVerified: opts.sourceVerified ?? true,
    skillLinks: links.length > 0 ? { map_ref: "aya-map@1.0.0", links: links.map(([skill_node, node_id]) => ({ skill_node, node_id })) } : undefined,
  });
}

function planBody(mastery: Json[], extra: Record<string, unknown> = {}) {
  return { context: contextV1(), mastery, ...extra };
}
const run = (adapter: ReturnType<typeof makeAdapterV1>, body: unknown) => {
  const r = adapter.handle("POST", "plan", body);
  assert.equal(r.status, 200, JSON.stringify(r.body).slice(0, 300));
  assert.deepEqual(contractV1.validate("PlanResponse", r.body, "1.0.0"), { ok: true });
  return r.body as Json;
};

// ---------- 正常 ----------
test("plan 正常：复测在前、练习在后，能力点都来自请求的 mastery，unseen 与内容包外的能力点不出现", () => {
  const adapter = makeAdapterV1(packFor([FN_K, ST_K, PR_K]));
  const b = run(adapter, planBody([
    { skill_node: `aya:${FN_K}`, state: "execute", p_mastery: 0.4 },
    { skill_node: `aya:${ST_K}`, state: "stable", next_retest_at: "2026-10-12T08:00:00+08:00" },
    { skill_node: `aya:${PR_K}`, state: "unseen" },
    { skill_node: "aya:K-NOT-IN-PACK-0001", state: "execute" },
  ]));
  const tasks = b.result.tasks;
  assert.deepEqual(tasks.map((t: Json) => [t.kind, t.skill_nodes[0], t.rationale_code]), [
    ["retest", `aya:${ST_K}`, "retest_scheduled"],
    ["practice", `aya:${FN_K}`, "high_exam_weight"],
  ]);
  // 复测优先选 R 题；练习取同节点 D、T、L 题（按题号前 3 道）
  assert.equal(tasks[0].item_refs.length, 1);
  assert.equal(tasks[0].estimated_minutes, 2);
  assert.equal(tasks[1].item_refs.length, 3);
  assert.equal(tasks[1].estimated_minutes, 9);
  for (const t of tasks) assert.ok(RATIONALE_CODES.includes(t.rationale_code));
  assert.equal(b.model_version, `algo:${PLAN_ALGORITHM}`);
  assert.match(b.source_version, /^algo:aya-plan@0\.1\.0;items:aya-pack@/);
  assert.deepEqual(b.evidence_ids, []);
  assert.equal(b.human_review_required, false);
  // 题目都已准入 → formal、1.0
  assert.equal(b.semantics, "formal");
  assert.equal(b.confidence, 1);
});

test("plan 同一请求两次调用：响应逐字节相同（换新的适配器实例也相同：不读系统时钟、不留状态）", () => {
  const pack = packFor([FN_K, ST_K]);
  const body = planBody([
    { skill_node: `aya:${FN_K}`, state: "understand" },
    { skill_node: `aya:${ST_K}`, state: "retain", next_retest_at: "2026-10-20T08:00:00Z" },
  ]);
  const a = JSON.stringify(makeAdapterV1(pack).handle("POST", "plan", body).body);
  const b = JSON.stringify(makeAdapterV1(pack).handle("POST", "plan", structuredClone(body)).body);
  const adapter = makeAdapterV1(pack);
  const c1 = JSON.stringify(adapter.handle("POST", "plan", body).body);
  const c2 = JSON.stringify(adapter.handle("POST", "plan", body).body);
  assert.equal(a, b);
  assert.equal(c1, c2);
  assert.equal(a, c1);
});

test("plan 复测按 next_retest_at 的时刻升序：+08:00 与 Z 混用时不按字符串排序；复测优先级都高于练习", () => {
  const adapter = makeAdapterV1(packFor([FN_K, ST_K, ST_K2, ST_K3]));
  // 时刻：08:00+08:00 = 00:00Z < 23:30-01:00 = 00:30Z < 01:00Z；按字符串排会是 23:30-01:00、01:00Z、08:00+08:00
  // （其中 01:00Z 与 08:00+08:00 两条的字符串顺序与时刻顺序正好相反）
  const b = run(adapter, planBody([
    { skill_node: `aya:${ST_K}`, state: "stable", next_retest_at: "2026-10-12T01:00:00Z" },
    { skill_node: `aya:${ST_K2}`, state: "transfer", next_retest_at: "2026-10-12T08:00:00+08:00" },
    { skill_node: `aya:${ST_K3}`, state: "retain", next_retest_at: "2026-10-11T23:30:00-01:00" },
    { skill_node: `aya:${FN_K}`, state: "execute", p_mastery: 0.01 },
  ]));
  const tasks = b.result.tasks;
  assert.deepEqual(tasks.map((t: Json) => t.skill_nodes[0]), [`aya:${ST_K2}`, `aya:${ST_K3}`, `aya:${ST_K}`, `aya:${FN_K}`]);
  const retests = tasks.filter((t: Json) => t.kind === "retest");
  const practice = tasks.filter((t: Json) => t.kind === "practice");
  assert.equal(retests.length, 3);
  for (const r of retests) {
    assert.ok(r.priority >= 0.85 && r.priority <= 1);
    for (const p of practice) assert.ok(r.priority > p.priority);
  }
  for (const p of practice) assert.ok(p.priority >= 0.1 && p.priority <= 0.8);
  // 复测没有 R 题时用 T / L 题
  const noR = makeAdapterV1(packFor([ST_K], { noR: [ST_K] }));
  const t = run(noR, planBody([{ skill_node: `aya:${ST_K}`, state: "stable", next_retest_at: "2026-10-12T01:00:00Z" }])).result.tasks;
  assert.equal(t.length, 1);
  assert.equal(t[0].kind, "retest");
  assert.equal(t[0].item_refs.length, 2); // T、L 各一道
  // stable 但没有 next_retest_at：不排任务
  assert.deepEqual(run(noR, planBody([{ skill_node: `aya:${ST_K}`, state: "stable" }])).result.tasks, []);
});

test("plan 练习的先后：薄弱度（1 − p_mastery，缺省按状态）× 板块占比；理由编码取最主要的一个", () => {
  const adapter = makeAdapterV1(packFor([ST_K, ST_K2, FN_K]));
  const b = run(adapter, planBody([
    { skill_node: `aya:${ST_K}`, state: "execute", p_mastery: 0.6 },
    { skill_node: `aya:${ST_K2}`, state: "execute", p_mastery: 0.2 },
    { skill_node: `aya:${FN_K}`, state: "understand" },
  ]));
  const order = b.result.tasks.map((t: Json) => [t.skill_nodes[0], t.rationale_code]);
  assert.deepEqual(order, [
    [`aya:${FN_K}`, "high_exam_weight"],
    [`aya:${ST_K2}`, "weak_mastery"],
    [`aya:${ST_K}`, "weak_mastery"],
  ]);
  const p = b.result.tasks.map((t: Json) => t.priority);
  assert.ok(p[0] > p[1] && p[1] > p[2]);
});

test("plan 时间预算：按优先级累加 estimated_minutes，超出就停；没有预算时最多 8 个任务", () => {
  const nodes = realPack.pack.nodes.filter((n) => n.board === "统计").slice(0, 12).map((n) => n.id);
  const pack = packFor(nodes);
  const adapter = makeAdapterV1(pack);
  const mastery = nodes.map((n, i) => ({ skill_node: `aya:${n}`, state: "execute", p_mastery: i / 20 }));
  for (const budget of [1, 8, 9, 10, 17, 18, 40]) {
    const tasks = run(adapter, planBody(mastery, { constraints: { time_budget_minutes: budget } })).result.tasks;
    const sum = tasks.reduce((s: number, t: Json) => s + t.estimated_minutes, 0);
    assert.ok(sum <= budget, `预算 ${budget} 超出：${sum}`);
    assert.equal(tasks.length, Math.floor(budget / 9), `预算 ${budget}`);
  }
  assert.equal(run(adapter, planBody(mastery)).result.tasks.length, PLAN_MAX_TASKS);
});

test("plan 所有 item_ref 都在内容包里；question_type_codes 非空时题型都在列表内；focus_skill_nodes 只在其中排", () => {
  const nodes = realPack.pack.nodes.filter((n) => realPack.itemsByNode.has(n.id)).slice(0, 40).map((n) => n.id);
  const mastery = nodes.map((n, i) => ({ skill_node: `aya:${n}`, state: i % 3 === 0 ? "understand" : "execute" }));
  const adapter = makeAdapterV1();
  const all = run(adapter, planBody(mastery)).result.tasks;
  assert.ok(all.length > 0);
  for (const t of all) for (const ref of t.item_refs) assert.ok(realPack.itemById.has(ref.slice(4)), ref);
  const code = { single: "single_choice", fill: "fill_blank", multi: "multi_choice" } as const;
  for (const codes of [["fill_blank"], ["single_choice", "multi_choice"]]) {
    const tasks = run(adapter, planBody(mastery, { exam_config: { cohort: 2027, full_score: 120, question_type_codes: codes } })).result.tasks;
    assert.ok(tasks.length > 0);
    for (const t of tasks) for (const ref of t.item_refs) assert.ok(codes.includes(code[realPack.itemById.get(ref.slice(4))!.format]), ref);
  }
  const focus = run(adapter, planBody(mastery, { constraints: { focus_skill_nodes: [`aya:${nodes[1]}`, `aya:${nodes[2]}`] } })).result.tasks;
  assert.ok(focus.length > 0);
  for (const t of focus) assert.ok([`aya:${nodes[1]}`, `aya:${nodes[2]}`].includes(t.skill_nodes[0]));
});

test("plan 前置：有 node_path 与对应表时前置边与 prerequisite_task_ids 一致、后续节点 ≥ 3 为 prerequisite_of_many；去掉 node_path 后都没有", () => {
  const links: [string, string][] = [
    [ST_K, "MA.STA.SYN_0001"],
    [ST_K2, "MA.STA.SYN_0002"],
    [PR_K, "MA.PRB.SYN_0003"],
  ];
  const pk = packFor([ST_K, ST_K2, PR_K], { links });
  const adapter = makeAdapterV1(pk);
  const mastery = [
    { skill_node: `aya:${ST_K}`, state: "execute", p_mastery: 0.5 },
    { skill_node: `aya:${ST_K2}`, state: "execute", p_mastery: 0.5 },
    { skill_node: `aya:${PR_K}`, state: "understand", p_mastery: 0.5 },
  ];
  const node_path = [
    { from_id: "MA.STA.SYN_0001", to_id: "MA.STA.SYN_0002", depth: 1 },
    { from_id: "MA.STA.SYN_0001", to_id: "MA.PRB.SYN_0003", depth: 1 },
    { from_id: "MA.STA.SYN_0001", to_id: "MA.STA.SYN_0009", depth: 2 },
  ];
  const b = run(adapter, planBody(mastery, { node_path }));
  const byNode = new Map(b.result.tasks.map((t: Json) => [t.skill_nodes[0], t]));
  const root = byNode.get(`aya:${ST_K}`) as Json;
  assert.equal(root.rationale_code, "prerequisite_of_many");
  assert.deepEqual((byNode.get(`aya:${ST_K2}`) as Json).prerequisite_task_ids, [root.task_id]);
  assert.deepEqual((byNode.get(`aya:${PR_K}`) as Json).prerequisite_task_ids, [root.task_id]);
  assert.ok(!("prerequisite_task_ids" in root));
  // 后续节点数让前置节点排在最前
  assert.equal(b.result.tasks[0].skill_nodes[0], `aya:${ST_K}`);
  assert.equal(b.source_version, `algo:aya-plan@0.1.0;graph:aya-map@1.0.0;${pk.itemsSourceVersion}`);

  const noPath = run(adapter, planBody(mastery));
  for (const t of noPath.result.tasks) {
    assert.ok(!("prerequisite_task_ids" in t));
    assert.notEqual(t.rationale_code, "prerequisite_of_many");
  }
  assert.doesNotMatch(noPath.source_version, /graph:/);
  // 没有对应表（真实内容包，KGE-01 之前）：node_path 用不上，同样没有前置
  const real = run(makeAdapterV1(), planBody([{ skill_node: `aya:${ST_K}`, state: "execute" }], { node_path }));
  for (const t of real.result.tasks) assert.ok(!("prerequisite_task_ids" in t));
  assert.doesNotMatch(real.source_version, /graph:/);
});

test("plan 的 learner_context 不参与计算：换了画像上下文，除了由指纹派生的 task_id，结果完全相同", () => {
  const adapter = makeAdapterV1(packFor([FN_K, ST_K]));
  const mastery = [
    { skill_node: `aya:${FN_K}`, state: "execute" },
    { skill_node: `aya:${ST_K}`, state: "stable", next_retest_at: "2026-10-12T08:00:00+08:00" },
  ];
  const ctxA = [{ field_id: "inner.g3.node_mastery", source_type: "inferred", value: "execute", claim_id: "00000000-0000-4000-8000-0000000000f7" }];
  const ctxB = [{ field_id: "outer.g1.weekly_available_time", source_type: "self", value: 30 }];
  const strip = (b: Json) => b.result.tasks.map(({ task_id: _t, ...rest }: Json) => rest);
  const a = run(adapter, planBody(mastery, { learner_context: ctxA }));
  const b = run(adapter, planBody(mastery, { learner_context: ctxB }));
  assert.deepEqual(strip(a), strip(b));
  assert.notEqual(a.result.tasks[0].task_id, b.result.tasks[0].task_id);
});

// ---------- 低置信度 ----------
test("plan 低置信度：引用了未准入的题 → semantics = practice、confidence = 0.5，不置 human_review_required", () => {
  const b = run(makeAdapterV1(packFor([FN_K], { sourceVerified: false })), planBody([{ skill_node: `aya:${FN_K}`, state: "execute" }]));
  assert.equal(b.result.tasks.length, 1);
  assert.equal(b.semantics, "practice");
  assert.equal(b.confidence, 0.5);
  assert.equal(b.human_review_required, false);
  assert.ok(!("review_reasons" in b));
  // 真实内容包：721 道全部未准入
  const real = run(makeAdapterV1(), planBody([{ skill_node: "aya:P-LEQ-0409", state: "execute" }]));
  assert.equal(real.result.tasks.length, 1);
  assert.equal(real.semantics, "practice");
  assert.equal(real.confidence, 0.5);
});

test("plan mastery 为空 → 空任务，formal、1.0", () => {
  const b = run(makeAdapterV1(), planBody([]));
  assert.deepEqual(b.result.tasks, []);
  assert.equal(b.semantics, "formal");
  assert.equal(b.confidence, 1);
});

// ---------- 上游失败 ----------
test("plan 坏请求：同一能力点出现两次 → 422；别的学科前缀 → 400（schema 先拒）；0.9.0 → 501", () => {
  const adapter = makeAdapterV1();
  const dup = adapter.handle("POST", "plan", planBody([
    { skill_node: "aya:P-LEQ-0409", state: "execute" },
    { skill_node: "aya:P-LEQ-0409", state: "understand" },
  ]));
  assert.equal(dup.status, 422);
  assert.equal((dup.body as Json).code, "INVALID_REQUEST");
  const other = adapter.handle("POST", "plan", planBody([{ skill_node: "wenli:R3-L2", state: "execute" }]));
  assert.equal(other.status, 400);
  const focus = adapter.handle("POST", "plan", planBody([], { constraints: { focus_skill_nodes: ["zhixing:X-1"] } }));
  assert.equal(focus.status, 400);
  const v09 = adapter.handle("POST", "plan", { ...planBody([]), context: contextV1({ contract_version: "0.9.0", idempotency_key: "synthetic-plan-key-1" }) });
  assert.equal(v09.status, 501);
  assert.equal((v09.body as Json).code, "METHOD_NOT_IMPLEMENTED");
  for (const r of [dup, other, focus, v09]) assert.deepEqual(contractV1.validate("Problem", r.body, "1.0.0"), { ok: true });
});
