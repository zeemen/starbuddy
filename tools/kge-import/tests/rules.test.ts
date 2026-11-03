// 规则层：章节归属（题目 ID 前缀、压轴专项按主测节点）、显式引用（直接命中 0.9）、退到主题（推断 0.6）、
// 自然语言引用解析（正反例）。全部合成数据。
import assert from "node:assert/strict";
import { test } from "node:test";
import { baseName, buildRuleContext, isNaturalLanguageRef, normalizeName, resolveText, routeItem, ruleLayer } from "../src/tagging/rules.ts";
import { RULE_DIRECT, RULE_INFERRED, RULE_TAGGER_VERSION } from "../src/tagging/types.ts";
import { synthBank, synthMap } from "./fixtures.ts";

const ctx = () => buildRuleContext(synthMap(), synthBank().topics);
const q = (id: string) => synthBank().questions.find((x) => x.id === id)!;

test("已导入章节由迁移表推出；知识簇 → 主题节点", () => {
  const c = ctx();
  assert.deepEqual([...c.importedChapters], ["fun"]);
  assert.equal(c.clusterTopic.get("FUN_VTX"), "MATH.FUNC.FUN_VTX");
  assert.equal(c.clusterTopic.get("FUN_MOD"), undefined);
});

test("章节归属：专题注册表优先；题目 ID 前缀；压轴专项按主测节点所在章节投票", () => {
  const c = ctx();
  assert.deepEqual(routeItem(q("Q-FUN-U01-D-001"), c), { chapter: "fun", via: "topic", prefixMismatch: false });
  assert.deepEqual(routeItem({ id: "Q-PYT-U09-D-001", topic: null, mainNodes: [] }, c), { chapter: "pyt", via: "id_prefix", prefixMismatch: false });
  assert.deepEqual(routeItem(q("Q-SPR-QF-S1"), c), { chapter: "fun", via: "main_nodes", prefixMismatch: false });
  assert.deepEqual(routeItem({ id: "Q-SPR-XX-S1", topic: null, mainNodes: ["K-PYT-0001", "K-FUN-0001"] }, c).chapter, "pyt", "平票取第一个主测节点的章节");
  assert.deepEqual(routeItem({ id: "Q-SPR-XX-S1", topic: null, mainNodes: ["勾股定理"] }, c), { chapter: null, via: null, prefixMismatch: false });
  assert.equal(routeItem({ id: "Q-PYT-U01-D-001", topic: "fun", mainNodes: [] }, c).prefixMismatch, true);
});

test("显式引用：主测节点直接命中 0.9（主考点），辅助节点 0.9（次要）；每条候选带层、版本、置信度、理由码", () => {
  const r = ruleLayer(q("Q-FUN-U01-D-001"), ctx(), true);
  assert.deepEqual(r.candidates, [
    { itemId: "Q-FUN-U01-D-001", nodeId: "MATH.FUNC.FUN_CON.K_FUN_0001", role: "main", layer: "rule", confidence: RULE_DIRECT,
      taggerVersion: RULE_TAGGER_VERSION, modelVersion: null, reasonCode: "rule_explicit_ref" },
    { itemId: "Q-FUN-U01-D-001", nodeId: "MATH.FUNC.FUN_VTX.K_FUN_0002", role: "secondary", layer: "rule", confidence: RULE_DIRECT,
      taggerVersion: RULE_TAGGER_VERSION, modelVersion: null, reasonCode: "rule_explicit_aux" },
  ]);
  assert.equal(r.refs.find((x) => x.field === "pre")!.outcome, "text_unmatched", "前置里的自然语言只解析，不产生候选");
});

test("节点未导入：退到知识簇的主题节点，推断 0.6；主题也不在地图里的不产生候选", () => {
  const r = ruleLayer(q("Q-FUN-U02-D-001"), ctx(), true);
  assert.deepEqual(r.candidates.map((c) => [c.nodeId, c.role, c.confidence, c.reasonCode]), [["MATH.FUNC.FUN_VTX", "main", RULE_INFERRED, "rule_chapter_topic"]]);
  const r2 = ruleLayer(q("Q-FUN-U07-D-001"), ctx(), true);
  assert.deepEqual(r2.candidates, []);
  assert.equal(r2.refs[0]!.outcome, "not_in_map");
});

test("退到主题的候选：同一题已有该主题下的节点被直接命中时丢掉（父子不同时进节点清单）", () => {
  const r = ruleLayer(q("Q-FUN-U06-D-001"), ctx(), true);
  assert.deepEqual(r.candidates.map((c) => c.nodeId), ["MATH.FUNC.FUN_VTX.K_FUN_0002"]);
  assert.equal(r.refs.find((x) => x.ref === "P-FUN-0003")!.outcome, "topic_fallback", "引用记录保留");
});

test("未导入章节的旧编码不产生候选；不在已导入主题里的题不产生候选但照样解析引用", () => {
  const r = ruleLayer(q("Q-FUN-U03-D-001"), ctx(), true);
  assert.deepEqual(r.candidates, []);
  assert.equal(r.refs[0]!.outcome, "other_chapter");
  const out = ruleLayer(q("Q-PYT-U01-D-001"), ctx(), false);
  assert.deepEqual(out.candidates, []);
  assert.equal(out.refs.length, 1);
});

test("自然语言引用（正例）：主测节点里的名称与地图节点同名 → 推断候选 0.6", () => {
  const r = ruleLayer(q("Q-FUN-U04-D-001"), ctx(), true);
  assert.deepEqual(r.candidates.map((c) => [c.nodeId, c.role, c.confidence, c.reasonCode]),
    [["MATH.FUNC.FUN_VTX.M_FUN_0006", "main", RULE_INFERRED, "rule_text_name"]]);
  const pre = r.refs.find((x) => x.field === "pre")!;
  assert.equal(pre.outcome, "text_unmatched");
  assert.deepEqual(pre.text!.legacyExact, ["K-PYT-0001"], "前置“勾股定理”对不上地图，但给出 legacy 同名节点建议");
});

test("自然语言引用解析：全角、空白、结尾括号说明都规范化；同名不唯一不产生候选", () => {
  const c = ctx();
  assert.equal(normalizeName(" 顶点 式 "), "顶点式");
  assert.equal(baseName("数据的集中趋势（平均数、中位数、众数）"), "数据的集中趋势");
  assert.equal(resolveText("顶点式（一般形式）", c).mapNode, "MATH.FUNC.FUN_VTX.K_FUN_0002");
  assert.equal(resolveText("ＡＢ对称轴", c).mapNode, null);
  const dup = buildRuleContext({ ...synthMap(), nodes: [...synthMap().nodes,
    { id: "MATH.FUNC.FUN_CON.K_FUN_0099", level: "node", parentId: "MATH.FUNC.FUN_CON", kind: "kp", name: "顶点式", includes: [], examples: [], cognitiveLevels: [] }] },
    synthBank().topics);
  const t = resolveText("顶点式", dup);
  assert.equal(t.mapNode, null);
  assert.deepEqual(t.mapAmbiguous, ["MATH.FUNC.FUN_CON.K_FUN_0099", "MATH.FUNC.FUN_VTX.K_FUN_0002"]);
});

test("自然语言引用（反例）：单字不做部分匹配；部分包含只给建议、不产生候选；形似旧编码的不当自然语言", () => {
  const c = ctx();
  const one = resolveText("比", c);
  assert.deepEqual([one.mapNode, one.legacyExact, one.legacyPartial], [null, [], []]);
  const partial = resolveText("二次函数的概念与图像", c);
  assert.equal(partial.mapNode, null);
  assert.deepEqual(partial.legacyPartial, ["K-FUN-0001"]);
  assert.equal(isNaturalLanguageRef("K-FUN-9999", c), false, "形似旧编码（悬空）");
  assert.equal(isNaturalLanguageRef("K-FUN-0001", c), false);
  assert.equal(isNaturalLanguageRef("对照实验思想", c), true);
  const r = ruleLayer({ id: "Q-FUN-U09-D-001", mainNodes: ["K-FUN-9999", "二次函数的概念与图像"], auxNodes: [], preNodes: [] }, c, true);
  assert.deepEqual(r.candidates, []);
  assert.deepEqual(r.refs.map((x) => x.outcome), ["dangling", "text_unmatched"]);
});
