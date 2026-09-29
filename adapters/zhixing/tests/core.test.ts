// 移植的纯函数：模型输出校验、确定性后处理（clamp、0.5 倍数、重算、证据位置、矛盾复核）、
// m3ToDimensionRates、scoreLocate（去掉 coaching 文案）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { m3ToDimensionRates } from "../src/core/ability-signal.ts";
import { locateSignalRate, scoreLocate } from "../src/core/locate.ts";
import { parseModelOutput, postProcess, roundHalf, type ModelPoint } from "../src/core/m3-postprocess.ts";
import { itemOf } from "./helpers.ts";

const item = itemOf("synthetic-m3-01"); // 三点各 2 分，满分 6
const mp = (point_id: string, over: Partial<ModelPoint> = {}): ModelPoint => ({
  point_id,
  judgment: "full",
  score_awarded: 2,
  evidence_start: 0,
  evidence_end: 3,
  attribution: "none",
  ...over,
});

test("parseModelOutput：只收约定结构；多字段、重复点、非 JSON、过长都返回 null；允许一层 ```json 代码块", () => {
  const good = { points: [mp("p1")] };
  assert.deepEqual(parseModelOutput(JSON.stringify(good)), good.points);
  assert.deepEqual(parseModelOutput("```json\n" + JSON.stringify(good) + "\n```"), good.points);
  for (const bad of [
    "not json",
    JSON.stringify({ points: [] }),
    JSON.stringify({ points: [mp("p1")], total_score: 2 }),
    JSON.stringify({ points: [{ ...mp("p1"), comment: "x" }] }),
    JSON.stringify({ points: [mp("p1"), mp("p1")] }),
    JSON.stringify({ points: [{ ...mp("p1"), judgment: "完整命中" }] }),
    JSON.stringify({ points: [{ ...mp("P1") }] }),
    JSON.stringify({ points: [{ ...mp("p1"), evidence_start: 1.5 }] }),
    "前缀 " + JSON.stringify(good),
    JSON.stringify(good) + " ".repeat(40_000),
  ]) {
    assert.equal(parseModelOutput(bad), null, bad.slice(0, 60));
  }
});

test("postProcess：clamp 到 [0, 分值]、取 0.5 倍数、重算总分；满分标记", () => {
  assert.equal(roundHalf(1.3), 1.5);
  assert.equal(roundHalf(1.2), 1);
  const r = postProcess(item, [mp("p1", { score_awarded: 9 }), mp("p2", { judgment: "partial", score_awarded: 1.3, attribution: "guifan_cuo" }), mp("p3", { judgment: "miss", score_awarded: -1, attribution: "shenti_cuo", evidence_start: null, evidence_end: null })], 10);
  assert.deepEqual(r.points.map((p) => (p.decided ? p.score : null)), [2, 1.5, 0]);
  assert.equal(r.total, 3.5);
  assert.equal(r.fullScore, false);
  const full = postProcess(item, ["p1", "p2", "p3"].map((id) => mp(id)), 10);
  assert.equal(full.total, 6);
  assert.equal(full.fullScore, true);
});

test("postProcess：证据位置必须落在作答内且非空；得分却没有片段 → 待复核；0 分点的片段不记为证据", () => {
  const cases: [Partial<ModelPoint>, string][] = [
    [{ evidence_start: 0, evidence_end: 11 }, "evidence_out_of_range"],
    [{ evidence_start: 3, evidence_end: 3 }, "evidence_out_of_range"],
    [{ evidence_start: -1, evidence_end: 2 }, "evidence_out_of_range"],
    [{ evidence_start: 2, evidence_end: null }, "evidence_out_of_range"],
    [{ evidence_start: null, evidence_end: null }, "evidence_missing"],
  ];
  for (const [over, reason] of cases) {
    const r = postProcess(item, [mp("p1", over), mp("p2"), mp("p3")], 10);
    assert.deepEqual(r.points[0], { point_id: "p1", decided: false, max: 2, reason });
    assert.equal(r.total, null);
  }
  const zero = postProcess(item, [mp("p1", { judgment: "miss", score_awarded: 0, attribution: "dingwei_cuo", evidence_start: 1, evidence_end: 4 }), mp("p2"), mp("p3")], 10);
  assert.ok(zero.points[0]!.decided && zero.points[0]!.span === null);
});

test("postProcess：判定档、得分、归因矛盾 → 该点待复核（满分却有归因、归因 none 却 0 分、miss 却得分……）", () => {
  const conflicts: [Partial<ModelPoint>, string][] = [
    [{ judgment: "full", score_awarded: 1, attribution: "none" }, "judgment_score_conflict"],
    [{ judgment: "miss", score_awarded: 1, attribution: "dingwei_cuo" }, "judgment_score_conflict"],
    [{ judgment: "partial", score_awarded: 2, attribution: "guifan_cuo" }, "judgment_score_conflict"],
    [{ judgment: "partial", score_awarded: 0, attribution: "guifan_cuo" }, "judgment_score_conflict"],
    [{ judgment: "full", score_awarded: 2, attribution: "zhuanhua_cuo" }, "attribution_score_conflict"],
    [{ judgment: "miss", score_awarded: 0, attribution: "none", evidence_start: null, evidence_end: null }, "attribution_score_conflict"],
    [{ judgment: "partial", score_awarded: 1, attribution: "none" }, "attribution_score_conflict"],
  ];
  for (const [over, reason] of conflicts) {
    const r = postProcess(item, [mp("p1", over), mp("p2"), mp("p3")], 10);
    assert.equal((r.points[0] as { reason?: string }).reason, reason, JSON.stringify(over));
  }
});

test("postProcess：模型输出不合格 → 全部待复核；缺点 → 该点待复核；多出的点忽略", () => {
  assert.ok(postProcess(item, null, 10).points.every((p) => !p.decided && p.reason === "model_output_invalid"));
  const r = postProcess(item, [mp("p1"), mp("p2"), mp("p9")], 10);
  assert.deepEqual(r.points.map((p) => p.decided), [true, true, false]);
  assert.equal((r.points[2] as { reason: string }).reason, "point_missing");
});

test("m3ToDimensionRates（legacy 移植）：整体率为基线，失分维度按比例下拉，最多 50%", () => {
  const s = m3ToDimensionRates(
    [
      { score_awarded: 2, attribution: null },
      { score_awarded: 1, attribution: "guifan_cuo" },
      { score_awarded: 0, attribution: "dingwei_cuo" },
    ],
    6,
  );
  assert.equal(s.overallRate, 0.5);
  assert.deepEqual(s.rates, { shenti: 0.5, dingwei: 0.417, zhuanhua: 0.5, guifan: 0.417 });
  assert.deepEqual(m3ToDimensionRates([], 0), { overallRate: 0, rates: { shenti: 0, dingwei: 0, zhuanhua: 0, guifan: 0 } });
});

test("scoreLocate（legacy 移植，去掉 coaching 与快慢比较）：只给对错", () => {
  assert.deepEqual(scoreLocate({ answer_node_id: "n3" }, "n3"), { correct: true });
  assert.deepEqual(scoreLocate({ answer_node_id: "n3" }, "l6"), { correct: false });
  assert.equal(locateSignalRate(true), 1);
  assert.equal(locateSignalRate(false), 0);
});
