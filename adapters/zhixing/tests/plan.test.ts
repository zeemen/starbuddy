// plan（spec 4-D 知行部分）：按“领域权重 × 失分 × 可提升”排序（KGE 第八章）；领域权重是合成值、待教研校准；
// 前置只来自 node_path（强前置边），只有 related 边时不产生任何前置；不调模型、不产分、只输出 ID、编码、数字。
// 全部是合成数据（规则 8）。期望值都是手算的（见每条用例的注释）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { createAdapter, PLAN_ALGORITHM } from "../src/adapter.ts";
import { indexPlanWeights, loadPlanWeights, type PlanWeightsFile } from "../src/plan-weights.ts";
import { admittedPack, context, contextV1, contract, leaves, realPack, taxonomy, type Json } from "./helpers.ts";

const weights = loadPlanWeights(realPack);
const make = (opts: { content?: typeof realPack; planWeights?: ReturnType<typeof loadPlanWeights> } = {}) =>
  createAdapter({ content: opts.content ?? realPack, taxonomy, contract, route: null, allowCandidate: true, ...(opts.planWeights ? { planWeights: opts.planWeights } : {}) });
const call = async (a: ReturnType<typeof make>, body: unknown) => {
  const r = await a.handle("POST", "plan", body);
  return { status: r.status, body: r.body as Json };
};
const ctx = (v1 = true) => {
  const c = (v1 ? contextV1() : context()) as Json;
  delete c.usage_id; // 非消耗类方法
  return c;
};
const ms = (node: string, state: string, p?: number) => ({ skill_node: `zhixing:syn-dim-${node}`, state, ...(p === undefined ? {} : { p_mastery: p }) });
const nodes = (b: Json) => b.result.tasks.map((t: Json) => t.skill_nodes[0].replace("zhixing:syn-dim-", ""));
const prio = (b: Json) => b.result.tasks.map((t: Json) => t.priority);

// 合成领域权重（plan-weights.json）：law 0.3、civic 0.2、nation 0.15、society 0.15、culture 0.1、life 0.1。
// 节点的领域权重 = 采分点分值加权平均（每点 2 分）：
//   zhuanhua：civic、society、culture → (0.2 + 0.15 + 0.1) / 3 = 0.15
//   dingwei：civic、nation、culture → 0.15
//   guifan：society、life、law → (0.15 + 0.1 + 0.3) / 3 = 0.18333
//   shizheng：law → 0.3
//   shenti：没有采分点 → 没有规划单元
test("正常：排序与手算一致（领域权重 × 失分 × 可提升）；信封是确定性算法、没有证据、不置复核", async () => {
  const body = {
    context: ctx(),
    mastery: [
      ms("guifan", "understand"), // 0.18333 × 0.7（缺 p_mastery，understand 默认）× 1 = 0.12833
      ms("dingwei", "execute", 0.2), // 0.15 × (1 − 0.2) × 0.8 = 0.096
      ms("zhuanhua", "stable"), // 0.15 × 0.25 × 0.5 = 0.01875
      ms("shizheng", "understand", 0.3), // 0.3 × 0.7 × 1 = 0.21
    ],
  };
  const { status, body: b } = await call(make(), body);
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("PlanResponse", b, "1.0.0"), { ok: true });
  assert.deepEqual(nodes(b), ["shizheng", "guifan", "dingwei", "zhuanhua"]);
  assert.deepEqual(prio(b), [0.21, 0.128, 0.096, 0.019]);
  assert.deepEqual(b.result.tasks.map((t: Json) => t.rationale_code), ["high_exam_weight", "weak_mastery", "weak_mastery", "weak_mastery"]);
  assert.ok(b.result.tasks.every((t: Json) => t.kind === "practice" && t.estimated_minutes === 8 && t.item_refs === undefined));
  assert.equal(b.model_version, `algo:${PLAN_ALGORITHM}`);
  assert.equal(b.source_version, "algo:zhixing-plan@0.1.0;algo:zhixing-plan-weights@0.1.0-uncalibrated;items:zhixing-synthetic@0.1.0");
  assert.deepEqual([b.confidence, b.human_review_required, b.semantics, b.evidence_ids], [1, false, "formal", []]);
  // 确定性：同一请求体换 key 再算，除 task_id 外逐字节相同
  const again = await call(make(), { ...structuredClone(body), context: ctx() });
  const strip = (x: Json) => x.result.tasks.map(({ task_id: _t, ...rest }: Json) => rest);
  assert.deepEqual(strip(again.body), strip(b));
});

test("低置信度一端：没有掌握记录或 unseen → kind diagnose、not_yet_assessed，失分取中性值 0.5；已星亮（transfer、retain）不排任务", async () => {
  const { body: b } = await call(make(), { context: ctx(), mastery: [ms("guifan", "transfer", 0.9), ms("zhuanhua", "retain"), ms("dingwei", "unseen")] });
  // shizheng 0.3 × 0.5 = 0.15；dingwei 0.15 × 0.5 = 0.075；guifan、zhuanhua 已星亮 → 可提升 0
  assert.deepEqual(nodes(b), ["shizheng", "dingwei"]);
  assert.deepEqual(prio(b), [0.15, 0.075]);
  assert.ok(b.result.tasks.every((t: Json) => t.kind === "diagnose" && t.rationale_code === "not_yet_assessed"));
  // 空 mastery：四个有题的节点都是 diagnose；同分（dingwei、zhuanhua 都是 0.075）按内容包节点顺序
  const empty = await call(make(), { context: ctx(false), mastery: [] });
  assert.deepEqual(contract.validate("PlanResponse", empty.body), { ok: true });
  assert.deepEqual(nodes(empty.body), ["shizheng", "guifan", "dingwei", "zhuanhua"]);
  assert.deepEqual(prio(empty.body), [0.15, 0.092, 0.075, 0.075]);
});

test("领域权重确实参与排序：把 culture 调到 1.0 后 zhuanhua、dingwei 升到前面（手算）", async () => {
  const file = structuredClone(weights.file) as PlanWeightsFile;
  file.domains.find((d) => d.code === "culture")!.weight = 1;
  const w = indexPlanWeights(file, realPack);
  // zhuanhua、dingwei：(0.2 + 0.15 + 1) / 3 = 0.45 → × 0.5 = 0.225；shizheng 0.15；guifan 0.18333 × 0.5 = 0.092
  const { body: b } = await call(make({ planWeights: w }), { context: ctx(), mastery: [] });
  assert.deepEqual(nodes(b), ["dingwei", "zhuanhua", "shizheng", "guifan"]);
  assert.deepEqual(prio(b), [0.225, 0.225, 0.15, 0.092]);
});

test("约束：focus_skill_nodes 只在其中排；时间预算内按优先级截断，放不下的及其后的都不要", async () => {
  const focus = await call(make(), { context: ctx(), mastery: [], constraints: { focus_skill_nodes: ["zhixing:syn-dim-zhuanhua", "zhixing:syn-dim-nope"] } });
  assert.deepEqual(nodes(focus.body), ["zhuanhua"]);
  const budget = await call(make(), { context: ctx(), mastery: [], constraints: { time_budget_minutes: 20 } });
  assert.deepEqual(nodes(budget.body), ["shizheng", "guifan"]);
  const tiny = await call(make(), { context: ctx(), mastery: [], constraints: { time_budget_minutes: 5 } });
  assert.deepEqual(tiny.body.result.tasks, []);
  assert.deepEqual(contract.validate("PlanResponse", tiny.body, "1.0.0"), { ok: true });
});

test("item_refs 只取已准入的道法题（至多 3 道、按题号），分钟数 = 8 × 题数；未准入的题不给学生", async () => {
  const a = make({ content: admittedPack(["synthetic-m3-01", "synthetic-m3-02", "synthetic-m3-04"]) });
  const { body: b } = await call(a, { context: ctx(), mastery: [] });
  const byNode = Object.fromEntries(b.result.tasks.map((t: Json) => [t.skill_nodes[0], t]));
  // guifan 在 01、02、04；dingwei 在 01、02、03（03 未准入）；shizheng 只在 04
  assert.deepEqual(byNode["zhixing:syn-dim-guifan"].item_refs, ["zhixing:synthetic-m3-01", "zhixing:synthetic-m3-02", "zhixing:synthetic-m3-04"]);
  assert.equal(byNode["zhixing:syn-dim-guifan"].estimated_minutes, 24);
  assert.deepEqual(byNode["zhixing:syn-dim-dingwei"].item_refs, ["zhixing:synthetic-m3-01", "zhixing:synthetic-m3-02"]);
  assert.deepEqual(byNode["zhixing:syn-dim-shizheng"].item_refs, ["zhixing:synthetic-m3-04"]);
  assert.equal(b.semantics, "formal");
  // 真实内容包全是 pending_review：任何任务都不带 item_refs
  const real = await call(make(), { context: ctx(), mastery: [] });
  assert.ok(real.body.result.tasks.every((t: Json) => !("item_refs" in t)));
});

test("前置：只有 related 边（node_path 为空或没有）时不产生任何 prerequisite_task_ids；只认 node_path 的强前置边，成环的边跳过", async () => {
  const base = { mastery: [] };
  for (const node_path of [undefined, [], [{ from_id: "MATH.FUNC.QUAD.VERTEX_FORM", to_id: "MATH.FUNC.QUAD.GRAPH", depth: 1 }]]) {
    const { body: b } = await call(make(), { context: ctx(), ...base, ...(node_path ? { node_path } : {}) });
    assert.equal(b.result.tasks.length, 4);
    assert.ok(b.result.tasks.every((t: Json) => !("prerequisite_task_ids" in t)), JSON.stringify(node_path));
  }
  // 显式前置：未成年人网络保护（shizheng，04 p1）→ 保护主体（guifan，04 p2）；反向边会成环，跳过；
  // 同一节点内部的边（society.rule_awareness → life.value_dedication 都在 guifan）不产生自环
  const node_path = [
    { from_id: "MORAL.SYN.LAW.MINOR_ONLINE_PROTECTION", to_id: "MORAL.SYN.LAW.MINOR_PROTECTION_PARTIES", depth: 1 },
    { from_id: "MORAL.SYN.LAW.MINOR_PROTECTION_PARTIES", to_id: "MORAL.SYN.LAW.MINOR_ONLINE_PROTECTION", depth: 2 },
    { from_id: "MORAL.SYN.SOCIETY.RULE_AWARENESS", to_id: "MORAL.SYN.LIFE.VALUE_DEDICATION", depth: 1 },
  ];
  const { status, body: b } = await call(make(), { context: ctx(), ...base, node_path });
  assert.equal(status, 200, JSON.stringify(b));
  assert.deepEqual(contract.validate("PlanResponse", b, "1.0.0"), { ok: true });
  const byNode = Object.fromEntries(b.result.tasks.map((t: Json) => [t.skill_nodes[0].replace("zhixing:syn-dim-", ""), t]));
  assert.deepEqual(byNode.guifan.prerequisite_task_ids, [byNode.shizheng.task_id]);
  for (const n of ["shizheng", "dingwei", "zhuanhua"]) assert.equal(byNode[n].prerequisite_task_ids, undefined, n);
  // 前置不改变排序（排序只看领域权重 × 失分 × 可提升）
  assert.deepEqual(nodes(b), ["shizheng", "guifan", "dingwei", "zhuanhua"]);
});

test("上游数据不合格：掌握快照或关注节点不是知行的 → 400 SUBJECT_MISMATCH；内容版本不符 → 404；多出字段 → 400；都不回显", async () => {
  const cases: [unknown, number, string][] = [
    [{ context: ctx(), mastery: [{ skill_node: "aya:K-QF-0301", state: "execute" }] }, 400, "SUBJECT_MISMATCH"],
    [{ context: ctx(), mastery: [], constraints: { focus_skill_nodes: ["wenli:CHN.SYN.D1_L1"] } }, 400, "SUBJECT_MISMATCH"],
    [{ context: { ...ctx(), content_version: { ...realPack.pack.curriculum, content_version: "9.9.9" } }, mastery: [] }, 404, "CONTENT_VERSION_NOT_FOUND"],
    [{ context: ctx(), mastery: [], commentary: "自由文本" }, 400, "INVALID_REQUEST"],
    [{ context: ctx(), mastery: [ms("guifan", "execute", 1.5)] }, 400, "INVALID_REQUEST"],
  ];
  for (const [body, status, code] of cases) {
    const r = await call(make(), body);
    assert.equal(r.status, status, JSON.stringify(r.body));
    assert.equal(r.body.code, code);
    assert.deepEqual(contract.validate("Problem", r.body), { ok: true });
    assert.ok(!JSON.stringify(r.body).includes("自由文本"));
  }
  // 不认识的知行节点只跳过，不报错
  const unknown = await call(make(), { context: ctx(), mastery: [ms("nope", "execute")] });
  assert.equal(unknown.status, 200);
});

test("幂等：同 key 同体 → 同一批 task_id；同 key 不同体 → 409；输出里只有编码、数字与 ID（没有自由文本）", async () => {
  const a = make();
  const body = { context: ctx(), mastery: [ms("guifan", "execute")], constraints: { time_budget_minutes: 30 } };
  const r1 = await call(a, body);
  const r2 = await call(a, structuredClone(body));
  assert.deepEqual(r2.body, r1.body);
  const r3 = await call(a, { ...structuredClone(body), constraints: { time_budget_minutes: 20 } });
  assert.equal(r3.status, 409);
  assert.equal(r3.body.code, "IDEMPOTENCY_CONFLICT");
  const CODE = /^[a-z0-9_]{1,48}$|^zhixing:[A-Za-z0-9_.\-]+$|^[0-9a-f-]{36}$|^algo:|^1\.0\.0$/;
  for (const [path, v] of leaves(r1.body)) {
    if (typeof v === "string") assert.match(v, CODE, `${path} = ${v}`);
  }
});

test("领域权重文件：未标注“待教研校准”、权重越界、有采分点不落在任何领域 → 拒绝加载", () => {
  const good = () => structuredClone(weights.file) as PlanWeightsFile;
  const bad: [string, (f: PlanWeightsFile) => void][] = [
    ["缺少待教研校准", (f) => (f.notice = "合成值")],
    ["权重越界", (f) => (f.domains[0]!.weight = 1.5)],
    ["领域缺失", (f) => (f.domains = f.domains.filter((d) => d.code !== "law"))],
    ["前缀不在 MORAL 命名空间", (f) => (f.domains[0]!.node_prefix = "CHN.SYN.D1")],
    ["非合成却未校准", (f) => (f.synthetic = false)],
  ];
  assert.doesNotThrow(() => indexPlanWeights(good(), realPack));
  for (const [name, mutate] of bad) {
    const f = good();
    mutate(f);
    assert.throws(() => indexPlanWeights(f, realPack), /plan 领域权重不合格/, name);
  }
  assert.equal(weights.file.calibration, "pending_research_calibration");
  assert.match(weights.file.notice, /待教研校准/);
});
