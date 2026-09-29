// 合并与分级（spec 待人确认 #6 的默认口径）：A 级 = 置信度 ≥ 0.85 且规则层与另一层一致；只跑规则层一律 B（--rules-only-a 放宽）；
// 小题节点数上限 3；去重；无处可标。全部合成候选。
import assert from "node:assert/strict";
import { test } from "node:test";
import { dedupe, mergeAndGrade, type MergeOptions } from "../src/tagging/merge.ts";
import type { Candidate, Layer, Role } from "../src/tagging/types.ts";

const ID = "Q-SYN-U01-D-001";
const c = (nodeId: string, role: Role, layer: Layer, confidence: number, reasonCode = `${layer}_x`): Candidate => ({
  itemId: ID, nodeId, role, layer, confidence, reasonCode,
  taggerVersion: layer === "rule" ? "aya-tagger-rules@0.1.0" : layer === "vector" ? "fake-ngram-embedder@0.0.0" : "aya-tagger-model@0.1.0",
  modelVersion: layer === "model" ? "model:fake-a/fake-tagger@0.0.0" : null,
});
const opts = (layers: Layer[], rulesOnlyA = false): MergeOptions => ({ layers: new Set<Layer>(["rule", ...layers]), rulesOnlyA });

test("只跑规则层：规则直接命中 0.9 也是 B 级（没有另一层一致），进候选", () => {
  const d = mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9)], opts([]));
  assert.equal(d.grade, "B");
  assert.deepEqual(d.bReasons, ["no_layer_agreement"]);
  assert.equal(d.mainNodeId, "N.A");
  assert.equal(d.source, "rule");
});

test("--rules-only-a：只跑规则层时规则直接命中算 A；推断 0.6 仍是 B；开了别的层就不放宽", () => {
  assert.equal(mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9)], opts([], true)).grade, "A");
  const low = mergeAndGrade(ID, [c("N.T", "main", "rule", 0.6)], opts([], true));
  assert.deepEqual([low.grade, low.bReasons], ["B", ["rule_below_threshold"]]);
  assert.equal(mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9)], opts(["model"], true)).grade, "B");
});

test("A 级：规则层主考点 ≥ 0.85，且模型层或向量层把同一节点作主考点", () => {
  const withModel = mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9), c("N.A", "main", "model", 0.88)], opts(["model"]));
  assert.deepEqual([withModel.grade, withModel.source, withModel.confidence, withModel.bReasons], ["A", "rule", 0.9, []]);
  const withVector = mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9), c("N.A", "main", "vector", 0.31)], opts(["vector"]));
  assert.equal(withVector.grade, "A");
});

test("B 级：另一层选了别的主考点；规则只推断 0.6；没有规则层候选", () => {
  const disagree = mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9), c("N.B", "main", "model", 0.88)], opts(["model"]));
  assert.deepEqual([disagree.grade, disagree.mainNodeId, disagree.bReasons], ["B", "N.A", ["no_layer_agreement"]]);
  const inferred = mergeAndGrade(ID, [c("N.T", "main", "rule", 0.6), c("N.T", "main", "model", 0.88)], opts(["model"]));
  assert.deepEqual([inferred.grade, inferred.bReasons, inferred.source, inferred.confidence], ["B", ["rule_below_threshold"], "model", 0.88]);
  const noRule = mergeAndGrade(ID, [c("N.V", "main", "vector", 0.5), c("N.V", "main", "model", 0.88)], opts(["vector", "model"]));
  assert.deepEqual([noRule.grade, noRule.bReasons], ["B", ["no_rule_main"]]);
});

test("主考点：取主考点候选里置信度最高的；平手按候选出现顺序（legacy 引用顺序）", () => {
  const d = mergeAndGrade(ID, [c("N.Z", "main", "rule", 0.9), c("N.A", "main", "rule", 0.9), c("N.T", "main", "rule", 0.6)], opts([]));
  assert.equal(d.mainNodeId, "N.Z");
  assert.deepEqual(d.nodeIds, ["N.Z", "N.A", "N.T"]);
  const noMain = mergeAndGrade(ID, [c("N.S", "secondary", "rule", 0.9)], opts([], true));
  assert.deepEqual([noMain.mainNodeId, noMain.grade, noMain.bReasons], ["N.S", "B", ["no_main_candidate", "no_rule_main"]]);
});

test("节点数上限 3（含主考点）：超出截断并降为 B 级；向量层的次要候选不进节点清单", () => {
  const cs = [c("N.A", "main", "rule", 0.9), c("N.B", "secondary", "rule", 0.9), c("N.C", "secondary", "rule", 0.9), c("N.D", "secondary", "rule", 0.6),
              c("N.A", "main", "model", 0.88)];
  const d = mergeAndGrade(ID, cs, opts(["model"]));
  assert.deepEqual([d.nodeIds, d.overCap, d.grade, d.bReasons], [["N.A", "N.B", "N.C"], true, "B", ["over_cap"]]);
  const vec = mergeAndGrade(ID, [c("N.A", "main", "rule", 0.9), c("N.A", "main", "vector", 0.4), c("N.V1", "secondary", "vector", 0.39),
                                 c("N.V2", "secondary", "vector", 0.38), c("N.V3", "secondary", "vector", 0.37)], opts(["vector"]));
  assert.deepEqual([vec.nodeIds, vec.overCap, vec.grade], [["N.A"], false, "A"]);
  for (const x of [d, vec]) assert.ok(x.nodeIds.length >= 1 && x.nodeIds.length <= 3);
});

test("去重：同一（节点, 角色, 层）留最高置信度；同层同节点既主又次只留主", () => {
  const out = dedupe([c("N.A", "secondary", "rule", 0.9), c("N.A", "main", "rule", 0.6), c("N.A", "main", "rule", 0.9), c("N.B", "main", "vector", 0.2),
                      c("N.B", "main", "vector", 0.3)]);
  assert.deepEqual(out.map((x) => [x.nodeId, x.role, x.layer, x.confidence]), [["N.A", "main", "rule", 0.9], ["N.B", "main", "vector", 0.3]]);
});

test("无处可标：没有任何候选", () => {
  const d = mergeAndGrade(ID, [], opts(["vector", "model"]));
  assert.deepEqual([d.status, d.grade, d.mainNodeId, d.nodeIds], ["untaggable", null, null, []]);
});

test("候选必须属于同一道题", () => {
  assert.throws(() => mergeAndGrade("Q-OTHER", [c("N.A", "main", "rule", 0.9)], opts([])));
});
