// 图检查的正例与反例（spec 设计要点 7）
import assert from "node:assert/strict";
import { test } from "node:test";
import { findCycles, findOrphans, findTransitiveEdges } from "../src/graph.ts";

test("环：两点环、自环为正例，无环图为反例", () => {
  assert.deepEqual(findCycles(["a", "b", "c"], [["a", "b"], ["b", "a"], ["b", "c"]]), [["a", "b"]]);
  assert.deepEqual(findCycles(["a"], [["a", "a"]]), [["a"]]);
  assert.deepEqual(findCycles(["a", "b", "c"], [["a", "b"], ["b", "c"], ["a", "c"]]), []);
});

test("环：长链不爆栈", () => {
  const nodes = Array.from({ length: 20000 }, (_, i) => `n${i}`);
  const edges = nodes.slice(1).map((n, i) => [nodes[i]!, n] as const);
  assert.deepEqual(findCycles(nodes, edges), []);
});

test("传递边：a→c 可经 a→b→c 推出为正例；只有直连为反例", () => {
  assert.deepEqual(findTransitiveEdges(["a", "b", "c"], [["a", "b"], ["b", "c"], ["a", "c"]]), [["a", "c"]]);
  assert.deepEqual(findTransitiveEdges(["a", "b", "c"], [["a", "b"], ["a", "c"]]), []);
  // 更长的替代路径也算
  assert.deepEqual(
    findTransitiveEdges(["a", "b", "c", "d"], [["a", "b"], ["b", "c"], ["c", "d"], ["a", "d"]]),
    [["a", "d"]],
  );
});

test("传递边：未知节点上的边忽略", () => {
  assert.deepEqual(findTransitiveEdges(["a", "c"], [["a", "x"], ["x", "c"], ["a", "c"]]), []);
});

test("孤点：没有边、没有引用才算", () => {
  assert.deepEqual(findOrphans(["a", "b", "c", "d"], [["a", "b"]], new Set(["c"])), ["d"]);
  assert.deepEqual(findOrphans(["a", "b"], [["a", "b"]], new Set()), []);
});
