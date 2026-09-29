// plan（确定性学科处方）：移植 recommendTargets 选目标（每域一个、取最低未掌握层）→ PlanTask；item_refs 只取内容包里已准入的题；
// 时间预算内按优先级截断；不调模型、不写文字、不含提分。全部是合成数据（规则 8）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { PLAN_ALGORITHM } from "../src/adapter.ts";
import { admittedPack, contextV1, contract, harness, leaves, planBody, type Json } from "./helpers.ts";

async function plan(body: unknown, content?: ReturnType<typeof admittedPack>) {
  const h = await harness(content ? { content } : {});
  try {
    const r = await h.adapter.handle("POST", "plan", body);
    assert.equal(h.stub.calls.length, 0, "plan 不调模型");
    return { status: r.status, body: r.body as Json };
  } finally {
    await h.close();
  }
}

const m = (cell: string, state: string, p?: number) => ({ skill_node: `wenli:CHN.SYN.${cell}`, state, ...(p === undefined ? {} : { p_mastery: p }) });
const cells = (b: Json) => b.result.tasks.map((t: Json) => t.skill_nodes[0].replace("wenli:CHN.SYN.", ""));

test("没有任何掌握数据：取前三个域的最低层，任务类型 diagnose，理由 cell_not_yet_assessed；信封是确定性算法", async () => {
  const { status, body } = await plan(planBody([]));
  assert.equal(status, 200, JSON.stringify(body));
  assert.deepEqual(contract.validate("PlanResponse", body), { ok: true });
  assert.deepEqual(cells(body), ["D1_L1", "D2_L1", "D3_L1"]);
  for (const t of body.result.tasks) {
    assert.equal(t.kind, "diagnose");
    assert.equal(t.rationale_code, "cell_not_yet_assessed");
    assert.equal(t.estimated_minutes, 5);
    assert.equal(t.item_refs, undefined, "内容包全是 pending_review：不给学生未准入的题");
  }
  assert.deepEqual(body.result.tasks.map((t: Json) => t.priority), [1, 0.667, 0.333]);
  assert.deepEqual([body.model_version, body.source_version], [`algo:${PLAN_ALGORITHM}`, `algo:${PLAN_ALGORITHM};items:wenli-synthetic@0.1.0`]);
  assert.deepEqual([body.confidence, body.human_review_required, body.semantics, body.evidence_ids], [1, false, "formal", []]);
});

test("recommendTargets：已掌握（transfer / retain）的层跳过；按离最优练习窗（0.4–0.6）的距离排序；每域至多一个", async () => {
  const mastery = [
    m("D1_L1", "transfer"), // D1 → 下一层 L2（有数据：execute 0.5 → 距离 0）
    m("D1_L2", "execute"),
    m("D2_L1", "retain"),
    m("D2_L2", "retain"), // D2 全部掌握 → 不出现
    m("D3_L1", "understand", 0.1), // 距离 0.3
    m("D4_L1", "stable", 0.8), // 距离 0.2
    m("D5_L1", "understand"), // proxy 0.3 → 距离 0.1
  ];
  const { body } = await plan(planBody(mastery, { time_budget_minutes: 600 }));
  assert.deepEqual(cells(body), ["D1_L2", "D5_L1", "D4_L1", "D3_L1"]);
  assert.ok(body.result.tasks.every((t: Json) => t.kind === "practice" && t.rationale_code === "zone_of_proximal_development" && t.estimated_minutes === 10));
});

test("时间预算内按优先级截断：放不下的任务及其后的都不要；预算太小时没有任务", async () => {
  assert.deepEqual(cells((await plan(planBody([], { time_budget_minutes: 12 }))).body), ["D1_L1", "D2_L1"]);
  assert.deepEqual(cells((await plan(planBody([], { time_budget_minutes: 600 }))).body), ["D1_L1", "D2_L1", "D3_L1", "D4_L1", "D5_L1"]);
  const tiny = (await plan(planBody([], { time_budget_minutes: 4 }))).body;
  assert.deepEqual(tiny.result.tasks, []);
  assert.deepEqual(contract.validate("PlanResponse", tiny), { ok: true });
});

test("重点节点排最前（focus_requested）；已掌握或不认识的重点节点忽略；不认识的掌握节点忽略", async () => {
  const { body } = await plan(planBody([m("D4_L2", "retain"), { skill_node: "wenli:CHN.SYN.ZZ_L9", state: "execute" }], {
    focus_skill_nodes: ["wenli:CHN.SYN.D3_L2", "wenli:CHN.SYN.D4_L2", "wenli:CHN.SYN.NOPE"],
  }));
  assert.deepEqual(cells(body), ["D3_L2", "D1_L1", "D2_L1", "D3_L1"]);
  assert.equal(body.result.tasks[0].rationale_code, "focus_requested");
  assert.equal(body.result.tasks[0].priority, 1);
});

test("全部掌握：没有任务", async () => {
  const all = ["D1_L1", "D1_L2", "D2_L1", "D2_L2", "D3_L1", "D3_L2", "D4_L1", "D4_L2", "D5_L1", "D5_L2"].map((c) => m(c, "transfer"));
  assert.deepEqual((await plan(planBody(all))).body.result.tasks, []);
});

test("item_refs 只取内容包里已准入的题（合成的已准入包）；预计分钟取题目用时之和与下限的较大者", async () => {
  const pack = admittedPack(["synthetic-q01", "synthetic-q03", "synthetic-q04", "synthetic-essay-02"]);
  const { body } = await plan(planBody([m("D1_L1", "understand", 0.5)], { time_budget_minutes: 600 }), pack);
  const byCell = new Map(body.result.tasks.map((t: Json) => [t.skill_nodes[0], t]));
  assert.deepEqual((byCell.get("wenli:CHN.SYN.D1_L1") as Json).item_refs, ["wenli:synthetic-q01", "wenli:synthetic-q03"]);
  assert.deepEqual((byCell.get("wenli:CHN.SYN.D2_L1") as Json).item_refs, ["wenli:synthetic-q04"], "q05 未准入，不给");
  assert.deepEqual((byCell.get("wenli:CHN.SYN.D5_L1") as Json).item_refs, ["wenli:synthetic-essay-02"]);
  assert.equal((byCell.get("wenli:CHN.SYN.D3_L1") as Json).item_refs, undefined);
  for (const t of body.result.tasks) for (const ref of t.item_refs ?? []) assert.ok(pack.itemById.has(ref.slice(6)) || pack.essayById.has(ref.slice(6)));
});

test("确定性：同一输入（不同 key）任务相同（task_id 按 key 派生）；结果里没有文字、没有提分字段", async () => {
  const input = [m("D1_L1", "execute", 0.45), m("D3_L1", "understand")];
  const a = (await plan(planBody(input, { time_budget_minutes: 30, exam_date: "2027-06-15" }))).body;
  const b = (await plan(planBody(input, { time_budget_minutes: 30, exam_date: "2027-06-15" }))).body;
  const strip = (x: Json) => x.result.tasks.map(({ task_id: _t, ...rest }: Json) => rest);
  assert.deepEqual(strip(a), strip(b));
  assert.notEqual(a.result.tasks[0].task_id, b.result.tasks[0].task_id);
  for (const [path, v] of leaves(a.result)) {
    assert.doesNotMatch(path, /gain|score|rank|forecast|comment|note|text/i);
    if (typeof v === "string") assert.match(v, /^([0-9a-f-]{36}|[a-z_]+|wenli:[A-Za-z0-9_.\-]+)$/, `${path} 不是编码`);
  }
});

test("请求校验：别的学科的节点 → 400；v1.0 请求按 1.0.0 应答", async () => {
  const bad = await plan(planBody([{ skill_node: "aya:K-1", state: "execute" }]));
  assert.equal(bad.status, 400);
  assert.equal(bad.body.code, "SUBJECT_MISMATCH");
  const c = contextV1() as Json;
  delete c.usage_id;
  const v1 = await plan({ context: c, mastery: [m("D1_L1", "execute")], constraints: { time_budget_minutes: 20 } });
  assert.equal(v1.status, 200, JSON.stringify(v1.body));
  assert.equal(v1.body.contract_version, "1.0.0");
  assert.deepEqual(contract.validate("PlanResponse", v1.body, "1.0.0"), { ok: true });
});
