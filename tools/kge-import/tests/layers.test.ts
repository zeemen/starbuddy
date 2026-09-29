// 向量层与模型层的假实现与把关：假嵌入确定性、Top-K 只在知识节点里排序；模型只能从候选里选、节点数 1–3、只收理由码。
import assert from "node:assert/strict";
import { test } from "node:test";
import { FakeTagModel, modelLayer, type ModelChoice, type ModelInput, type TagModel } from "../src/tagging/model.ts";
import { makePipeline } from "../src/tagging/pipeline.ts";
import { buildRuleContext } from "../src/tagging/rules.ts";
import { TaggingError } from "../src/tagging/types.ts";
import { buildVectorIndex, cosine, FakeNgramEmbedder, vectorLayer } from "../src/tagging/vector.ts";
import { synthBank, synthMap } from "./fixtures.ts";

const item = { id: "Q-SYN-U01-D-001", text: "（合成）求抛物线的顶点式与对称轴", mainNodes: [], auxNodes: [], preNodes: [] };

test("假嵌入：确定性、归一化；相同文本余弦为 1，不相干文本接近 0", () => {
  const e = new FakeNgramEmbedder();
  const [a, b, c] = e.embed(["顶点式", "顶点式", "统计图"]);
  assert.deepEqual(a, b);
  assert.deepEqual(e.embed(["顶点式"]), e.embed(["顶点式"]));
  assert.ok(Math.abs(cosine(a!, b!) - 1) < 1e-9);
  assert.ok(cosine(a!, c!) < 0.3);
});

test("向量层：只在知识节点里排 Top-K，第 1 名为主考点候选，其余为次要；置信度 3 位小数", () => {
  const idx = buildVectorIndex(new FakeNgramEmbedder(), synthMap().nodes);
  assert.equal(idx.nodes.every((n) => n.level === "node"), true);
  const out = vectorLayer(item, idx, 2);
  assert.equal(out.length, 2);
  assert.deepEqual(out.map((c) => c.role), ["main", "secondary"]);
  assert.ok(["MATH.FUNC.FUN_VTX.K_FUN_0002", "MATH.FUNC.FUN_VTX.K_FUN_0007"].includes(out[0]!.nodeId));
  for (const c of out) {
    assert.equal(c.layer, "vector");
    assert.equal(c.reasonCode, "vector_similarity");
    assert.equal(c.modelVersion, null);
    assert.equal(Math.round(c.confidence * 1000) / 1000, c.confidence);
  }
  assert.deepEqual(vectorLayer(item, idx, 2), out, "两次结果相同");
});

const input: ModelInput = { itemId: item.id, text: item.text, candidates: [
  { nodeId: "N.A", name: "甲", score: 0.9 }, { nodeId: "N.B", name: "乙", score: 0.6 }, { nodeId: "N.C", name: "丙", score: 0.3 }, { nodeId: "N.D", name: "丁", score: 0.2 },
] };

test("假模型：只从候选里选主考点与至多 3 个节点，输出理由码与假模型版本", () => {
  const out = modelLayer(new FakeTagModel(), input);
  assert.deepEqual(out.map((c) => [c.nodeId, c.role]), [["N.A", "main"], ["N.B", "secondary"], ["N.C", "secondary"]]);
  for (const c of out) {
    assert.equal(c.modelVersion, "model:fake-a/fake-tagger@0.0.0");
    assert.equal(c.reasonCode, "model_choice");
    assert.ok(input.candidates.some((x) => x.nodeId === c.nodeId));
  }
  assert.deepEqual(modelLayer(new FakeTagModel(), { ...input, candidates: [] }), [], "没有候选时不调用模型");
});

class Evil implements TagModel {
  readonly taggerVersion = "aya-tagger-model@0.1.0";
  modelVersion = "model:fake-a/evil@0.0.0";
  private readonly out: ModelChoice;
  constructor(out: ModelChoice) { this.out = out; }
  choose(): ModelChoice { return this.out; }
}

test("模型把关：候选之外的节点、节点数不在 1–3、主考点不在清单、自由文本理由、非法模型版本，一律拒绝", () => {
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
  for (const [what, m] of bad) assert.throws(() => modelLayer(m, input), TaggingError, what);
  const wrongVersion = new Evil(ok);
  wrongVersion.modelVersion = "gpt-x";
  assert.throws(() => modelLayer(wrongVersion, input), TaggingError);
  assert.equal(modelLayer(new Evil(ok), input).length, 1);
});

test("流水线：默认只跑规则层；开启后模型只能从规则与向量的候选里选", () => {
  const map = synthMap();
  const ctx = buildRuleContext(map, synthBank().topics);
  const q = synthBank().questions.find((x) => x.id === "Q-FUN-U01-D-001")!;
  const rulesOnly = makePipeline(map, ctx);
  assert.deepEqual([...rulesOnly.layers], ["rule"]);
  assert.equal(rulesOnly.tag(q, true).decision!.grade, "B");
  const seen: string[][] = [];
  const spy: TagModel = { taggerVersion: "aya-tagger-model@0.1.0", modelVersion: "model:fake-a/spy@0.0.0",
    choose(i) { seen.push(i.candidates.map((c) => c.nodeId)); return new FakeTagModel().choose(i); } };
  const full = makePipeline(map, ctx, { embedder: new FakeNgramEmbedder(), model: spy, topK: 2 });
  const r = full.tag(q, true);
  assert.deepEqual([...full.layers].sort(), ["model", "rule", "vector"]);
  const allowed = new Set(r.decision!.candidates.filter((c) => c.layer !== "model").map((c) => c.nodeId));
  assert.ok(seen[0]!.every((n) => allowed.has(n)));
  assert.ok(r.decision!.candidates.filter((c) => c.layer === "model").every((c) => allowed.has(c.nodeId)));
  assert.equal(r.decision!.grade, "A", "规则直接命中 + 假模型同选主考点");
  assert.equal(full.tag(q, false).decision, null, "不在已导入主题里的题不打标");
});
