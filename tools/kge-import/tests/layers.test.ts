// 向量层与模型层的假实现与把关：假嵌入确定性、Top-K 只在知识节点里排序；模型只能从候选里选、节点数 1–3、只收理由码。
// 接口是异步的，请求带路由必需字段（向量 T7、模型 T8，data_class L0）；真实路由的桩调用即抛错，不回落到假实现。
import assert from "node:assert/strict";
import { test } from "node:test";
import { FakeTagModel, modelInput, modelLayer, RoutedTagModel, type ModelChoice, type ModelInput, type TagModel } from "../src/tagging/model.ts";
import { makePipeline } from "../src/tagging/pipeline.ts";
import { buildRuleContext } from "../src/tagging/rules.ts";
import { ROUTE_NOT_CONNECTED, TaggingError } from "../src/tagging/types.ts";
import { buildVectorIndex, cosine, embedRequest, FakeNgramEmbedder, RoutedEmbedder, vectorLayer, type EmbedRequest, type Embedder } from "../src/tagging/vector.ts";
import { synthBank, synthMap } from "./fixtures.ts";

const item = { id: "Q-SYN-U01-D-001", text: "（合成）求抛物线的顶点式与对称轴", mainNodes: [], auxNodes: [], preNodes: [] };
const ROUTE = { routeUrl: "https://route.invalid/v1/", token: "synthetic-token-not-a-secret" };

test("假嵌入：确定性、归一化；相同文本余弦为 1，不相干文本接近 0；请求必须带 T7 / L0", async () => {
  const e = new FakeNgramEmbedder();
  const [a, b, c] = await e.embed(embedRequest(["顶点式", "顶点式", "统计图"]));
  assert.deepEqual(a, b);
  assert.deepEqual(await e.embed(embedRequest(["顶点式"])), await e.embed(embedRequest(["顶点式"])));
  assert.ok(Math.abs(cosine(a!, b!) - 1) < 1e-9);
  assert.ok(cosine(a!, c!) < 0.3);
  assert.equal(e.fake, true);
  assert.deepEqual(embedRequest(["x"]), { task: "T7", data_class: "L0", texts: ["x"] });
  await assert.rejects(e.embed({ task: "T8", data_class: "L0", texts: ["x"] } as unknown as EmbedRequest), TaggingError);
  await assert.rejects(e.embed({ task: "T7", data_class: "L1", texts: ["x"] } as unknown as EmbedRequest), TaggingError);
});

test("向量层：只在知识节点里排 Top-K，第 1 名为主考点候选，其余为次要；置信度 3 位小数；每次请求都带 T7 / L0", async () => {
  const seen: EmbedRequest[] = [];
  const spy: Embedder = { version: "spy-embedder@0.0.0", fake: true,
    embed(req) { seen.push(req); return new FakeNgramEmbedder().embed(req); } };
  const idx = await buildVectorIndex(spy, synthMap().nodes);
  assert.equal(idx.nodes.every((n) => n.level === "node"), true);
  const out = await vectorLayer(item, idx, 2);
  assert.equal(out.length, 2);
  assert.deepEqual(out.map((c) => c.role), ["main", "secondary"]);
  assert.ok(["MATH.FUNC.FUN_VTX.K_FUN_0002", "MATH.FUNC.FUN_VTX.K_FUN_0007"].includes(out[0]!.nodeId));
  for (const c of out) {
    assert.equal(c.layer, "vector");
    assert.equal(c.reasonCode, "vector_similarity");
    assert.equal(c.modelVersion, null);
    assert.equal(Math.round(c.confidence * 1000) / 1000, c.confidence);
  }
  assert.deepEqual(await vectorLayer(item, idx, 2), out, "两次结果相同");
  assert.ok(seen.length >= 2);
  for (const r of seen) assert.deepEqual([r.task, r.data_class], ["T7", "L0"]);
});

const input: ModelInput = modelInput(item.id, item.text, [
  { nodeId: "N.A", name: "甲", score: 0.9 }, { nodeId: "N.B", name: "乙", score: 0.6 }, { nodeId: "N.C", name: "丙", score: 0.3 }, { nodeId: "N.D", name: "丁", score: 0.2 },
]);

test("假模型：只从候选里选主考点与至多 3 个节点，输出理由码与假模型版本；入参带 T8 / L0", async () => {
  assert.deepEqual([input.task, input.data_class], ["T8", "L0"]);
  const out = await modelLayer(new FakeTagModel(), input);
  assert.deepEqual(out.map((c) => [c.nodeId, c.role]), [["N.A", "main"], ["N.B", "secondary"], ["N.C", "secondary"]]);
  for (const c of out) {
    assert.equal(c.modelVersion, "model:fake-a/fake-tagger@0.0.0");
    assert.equal(c.reasonCode, "model_choice");
    assert.ok(input.candidates.some((x) => x.nodeId === c.nodeId));
  }
  assert.deepEqual(await modelLayer(new FakeTagModel(), { ...input, candidates: [] }), [], "没有候选时不调用模型");
  assert.equal(new FakeTagModel().fake, true);
  await assert.rejects(modelLayer(new FakeTagModel(), { ...input, task: "T7" } as unknown as ModelInput), TaggingError, "任务不是 T8");
  await assert.rejects(modelLayer(new FakeTagModel(), { ...input, data_class: "L2" } as unknown as ModelInput), TaggingError, "数据等级不是 L0");
});

class Evil implements TagModel {
  readonly taggerVersion = "aya-tagger-model@0.1.0";
  readonly fake = true;
  modelVersion = "model:fake-a/evil@0.0.0";
  private readonly out: ModelChoice;
  constructor(out: ModelChoice) { this.out = out; }
  async choose(): Promise<ModelChoice> { return this.out; }
}

test("模型把关：候选之外的节点、节点数不在 1–3、主考点不在清单、自由文本理由、非法模型版本，一律拒绝", async () => {
  const ok: ModelChoice = { mainNodeId: "N.A", nodeIds: ["N.A"], confidence: 0.9, reasonCode: "model_choice" };
  const bad: [string, TagModel][] = [
    ["主考点在候选外", new Evil({ ...ok, mainNodeId: "N.X", nodeIds: ["N.X"] })],
    ["次要节点在候选外", new Evil({ ...ok, nodeIds: ["N.A", "N.X"] })],
    ["节点超过 3 个", new Evil({ ...ok, nodeIds: ["N.A", "N.B", "N.C", "N.D"] })],
    ["节点为空", new Evil({ ...ok, nodeIds: [] })],
    ["主考点不在清单", new Evil({ ...ok, nodeIds: ["N.B"] })],
    ["节点重复", new Evil({ ...ok, nodeIds: ["N.A", "N.A"] })],
    ["自由文本理由", new Evil({ ...ok, reasonCode: "因为题目提到了顶点" })],
    ["置信度越界", new Evil({ ...ok, confidence: 1.5 })],
  ];
  for (const [what, m] of bad) await assert.rejects(modelLayer(m, input), TaggingError, what);
  const wrongVersion = new Evil(ok);
  wrongVersion.modelVersion = "gpt-x";
  await assert.rejects(modelLayer(wrongVersion, input), TaggingError);
  assert.equal((await modelLayer(new Evil(ok), input)).length, 1);
});

test("真实路由的桩：构造时要求路由地址与令牌；调用一律抛“真实路由未接入”，不回落到假实现；令牌不外露", async () => {
  const emb = new RoutedEmbedder({ ...ROUTE, version: "bge-m3@1.0.0" });
  const mod = new RoutedTagModel({ ...ROUTE, taggerVersion: "aya-tagger-model@0.1.0", modelVersion: "model:cn-vendor/tagger@2026-09" });
  assert.deepEqual([emb.fake, mod.fake, emb.hasToken, mod.hasToken], [false, false, true, true]);
  for (const x of [emb, mod]) {
    assert.equal(JSON.stringify(x).includes(ROUTE.token), false, "令牌不进 JSON");
    assert.equal(Object.values(x).includes(ROUTE.token), false, "令牌不在公开字段里");
  }
  const notConnected = (e: unknown) => e instanceof TaggingError && e.message.includes(ROUTE_NOT_CONNECTED) && !e.message.includes(ROUTE.token);
  assert.equal(ROUTE_NOT_CONNECTED, "真实路由未接入（spec 待人确认 #4、#5）");
  await assert.rejects(emb.embed(embedRequest(["（合成）"])), notConnected);
  await assert.rejects(buildVectorIndex(emb, synthMap().nodes), notConnected);
  await assert.rejects(mod.choose(input), notConnected);
  await assert.rejects(modelLayer(mod, input), notConnected);
  // 流水线接上桩：建索引或打标时直接失败，不会悄悄换成假实现
  const map = synthMap();
  const ctx = buildRuleContext(map, synthBank().topics);
  await assert.rejects(makePipeline(map, ctx, { embedder: emb }), notConnected);
  const withModel = await makePipeline(map, ctx, { model: mod });
  assert.deepEqual(withModel.fakeLayers, [], "桩不是假实现");
  const q = synthBank().questions.find((x) => x.id === "Q-FUN-U01-D-001")!;
  await assert.rejects(withModel.tag(q, true), notConnected);
  // 构造参数缺失或不合法
  const bad: (() => unknown)[] = [
    () => new RoutedEmbedder({ routeUrl: "", token: "t", version: "bge-m3@1.0.0" }),
    () => new RoutedEmbedder({ routeUrl: "https://route.invalid/", token: "", version: "bge-m3@1.0.0" }),
    () => new RoutedEmbedder({ routeUrl: "ftp://route.invalid/", token: "t", version: "bge-m3@1.0.0" }),
    () => new RoutedEmbedder({ routeUrl: "https://u:p@route.invalid/", token: "t", version: "bge-m3@1.0.0" }),
    () => new RoutedEmbedder({ routeUrl: "not a url", token: "t", version: "bge-m3@1.0.0" }),
    () => new RoutedEmbedder({ ...ROUTE, version: "BGE M3" }),
    () => new RoutedTagModel({ routeUrl: "https://route.invalid/", token: " ", taggerVersion: "aya-tagger-model@0.1.0", modelVersion: "model:a/b@1" }),
    () => new RoutedTagModel({ ...ROUTE, taggerVersion: "aya-tagger-model@0.1.0", modelVersion: "gpt-x" }),
    () => new RoutedTagModel({ ...ROUTE, taggerVersion: "x", modelVersion: "model:a/b@1" }),
  ];
  for (const f of bad) assert.throws(f, (e: unknown) => e instanceof TaggingError && !(e as Error).message.includes(ROUTE.token));
});

test("流水线：默认只跑规则层；开启后模型只能从规则与向量的候选里选；记下假实现的层", async () => {
  const map = synthMap();
  const ctx = buildRuleContext(map, synthBank().topics);
  const q = synthBank().questions.find((x) => x.id === "Q-FUN-U01-D-001")!;
  const rulesOnly = await makePipeline(map, ctx);
  assert.deepEqual([...rulesOnly.layers], ["rule"]);
  assert.deepEqual(rulesOnly.fakeLayers, []);
  assert.equal((await rulesOnly.tag(q, true)).decision!.grade, "B");
  const seen: ModelInput[] = [];
  const spy: TagModel = { taggerVersion: "aya-tagger-model@0.1.0", modelVersion: "model:fake-a/spy@0.0.0", fake: true,
    choose(i) { seen.push(i); return new FakeTagModel().choose(i); } };
  const full = await makePipeline(map, ctx, { embedder: new FakeNgramEmbedder(), model: spy, topK: 2 });
  assert.deepEqual(full.fakeLayers, ["vector", "model"]);
  const r = await full.tag(q, true);
  assert.deepEqual([...full.layers].sort(), ["model", "rule", "vector"]);
  assert.deepEqual([seen[0]!.task, seen[0]!.data_class], ["T8", "L0"]);
  const allowed = new Set(r.decision!.candidates.filter((c) => c.layer !== "model").map((c) => c.nodeId));
  assert.ok(seen[0]!.candidates.every((c) => allowed.has(c.nodeId)));
  assert.ok(r.decision!.candidates.filter((c) => c.layer === "model").every((c) => allowed.has(c.nodeId)));
  assert.equal(r.decision!.grade, "A", "规则直接命中 + 假模型同选主考点");
  assert.deepEqual(r.decision!.basis.find((b) => b.layer === "model"), { layer: "model", version: "model:fake-a/spy@0.0.0" }, "模型层依据写模型版本");
  assert.equal((await full.tag(q, false)).decision, null, "不在已导入主题里的题不打标");
});
