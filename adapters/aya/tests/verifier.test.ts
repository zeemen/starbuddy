// 验证器 v0 测试（验收：六类边界原因码都有用例，边界情况返回 needs_review 而不是对或错）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { gradeAnswer } from "../src/core/grading.ts";
import { areEquivalent } from "../src/core/symbolic.ts";
import type { PackItem } from "../src/content.ts";
import { verifyChoice, verifyFill } from "../src/verifier.ts";
import { realPack } from "./helpers.ts";

const fill = (answer: string[], extra: Partial<PackItem> = {}): PackItem => ({
  id: "SYN-FILL",
  format: "fill",
  answer,
  mainNodes: ["K-SYN-0001"],
  usage: "D",
  estMinutes: 1,
  hintPath: ["（合成）提示"],
  reviewStatus: "Q1",
  admission: "source_unverified",
  source: { source_ref: null, source_type: "unknown", license_status: "unverified" },
  ...extra,
});

test("正例：数值归一与等价写法判对", () => {
  const cases: [string[], string, number][] = [
    [["2x-2"], "2(x-1)", 1],
    [["x^2-2x+1"], "(x-1)^2", 1],
    [["x^2-2x+1"], "(x-1)²", 1],
    [["-1/2"], "-0.5", 1],
    [["9,-5"], "-5，9", 1],
    [["√8"], "2√2", 1],
    [["0.75"], "3/4", 1],
  ];
  for (const [ans, given, conf] of cases) {
    const v = verifyFill(fill(ans), given);
    assert.equal(v.verdict, "correct", `${given} vs ${ans}`);
    assert.equal(v.confidence, conf, `${given} vs ${ans}`);
    assert.deepEqual(v.reasonCodes, []);
  }
});

test("符号等价时置信度 = 一致点占比，并报告采样点数", () => {
  // 1/x 在全部 8 个采样点有限；sqrt(x) 在 2 个负采样点无定义，只剩 6 个一致点
  const a = verifyFill(fill(["2/x"]), "1/x+1/x");
  assert.equal(a.verdict, "correct");
  assert.equal(a.sampled, 8);
  assert.equal(a.agreeing, 8);
  const b = verifyFill(fill(["2sqrt(x)"]), "sqrt(x)+sqrt(x)");
  assert.equal(b.verdict, "correct");
  assert.equal(b.agreeing, 6);
  assert.equal(b.confidence, 6 / 8);
});

test("反例：确定不等价判错，置信度 1", () => {
  for (const [ans, given] of [[["2x-2"], "2x+2"], [["9,-5"], "9"], [["18"], "17"], [["3"], ""]] as [string[], string][]) {
    const v = verifyFill(fill(ans), given);
    assert.equal(v.verdict, "incorrect", `${given} vs ${ans}`);
    assert.equal(v.confidence, 1);
  }
});

const boundary: [string, string[], string][] = [
  ["trig_or_multiletter_function", ["sin(x)"], "sin(x)*1"],
  ["trig_or_multiletter_function", ["2x"], "log(x)"],
  // legacy 把 sin 当成 s·i·n，会误判这两个式子等价
  ["trig_or_multiletter_function", ["2sin(x)"], "sin(2x)"],
  ["equation_or_inequality_solving", ["x>3"], "3<x"],
  ["equation_or_inequality_solving", ["2"], "x+1=3"],
  ["proof_or_multi_answer", ["x+1,x-1"], "x-1,x+1+0"],
  ["proof_or_multi_answer", ["±2"], "2或-2"],
  ["parse_failure", ["2x"], "2x+"],
  ["parse_failure", ["无解"], "没有解"],
  ["sampling_insufficient", ["sqrt(x-5)"], "(x-5)^(1/2)"],
  ["no_reference_answer", [], "3"],
];

test("六类边界原因码：都返回 needs_review，置信度 ≤ 0.5，不下对错结论", () => {
  const seen = new Set<string>();
  for (const [code, ans, given] of boundary) {
    const v = verifyFill(fill(ans), given);
    assert.equal(v.verdict, "needs_review", `${code}: ${given} vs ${JSON.stringify(ans)}`);
    assert.ok(v.reasonCodes.includes(code as never), `${code}: got ${v.reasonCodes}`);
    assert.ok(v.confidence <= 0.5);
    assert.deepEqual(v.trapsHit, []);
    seen.add(code);
  }
  assert.equal(seen.size, 6);
});

test("边界检测不影响精确匹配：与标准答案归一后相同的作答仍判对", () => {
  assert.equal(verifyFill(fill(["x>3"]), "x>3").verdict, "correct");
  assert.equal(verifyFill(fill(["k=9", "9"]), "k=9").verdict, "correct");
});

test("与 legacy 一致：legacy 判对的作答，本验证器一定判对；legacy 命中的陷阱，本验证器同样命中", () => {
  let n = 0;
  for (const item of realPack.pack.items) {
    const inputs: string[][] =
      item.format === "fill"
        ? [...item.answer.map((a) => [a]), ...(item.wrongAnswerTraps ?? []).flatMap((w) => w.pattern.map((p) => [p]))]
        : [item.answer, ...(item.options ?? []).map((o) => [o.key])];
    for (const given of inputs) {
      const legacy = gradeAnswer(item, given);
      const v = item.format === "fill" ? verifyFill(item, given[0]!) : verifyChoice(item, given);
      if (legacy.correct) assert.equal(v.verdict, "correct", `${item.id} ${given}`);
      else if (legacy.trapsHit.length) {
        assert.equal(v.verdict, "incorrect", `${item.id} ${given}`);
        assert.deepEqual(v.trapsHit, [...new Set(legacy.trapsHit)]);
      }
      n++;
    }
  }
  assert.ok(n > 2500, `只覆盖了 ${n} 个作答`);
});

test("legacy areEquivalent 的判定没有因拆出 sampleCompare 而改变", () => {
  assert.equal(areEquivalent("2x-2", "2(x-1)"), true);
  assert.equal(areEquivalent("sqrt(x-5)", "(x-5)^(1/2)"), false); // 一致点不足 4 个
  assert.equal(areEquivalent("x", "x+1"), false);
  assert.equal(areEquivalent("2x+", "2x"), false);
});

test("多答案且都是常量：按数值多重集比对（legacy 判错的等价写法也判对）", () => {
  const item = fill(["√8,-1"]);
  assert.equal(gradeAnswer(item, ["-1，2√2"]).correct, false);
  assert.equal(verifyFill(item, "-1，2√2").verdict, "correct");
  assert.equal(verifyFill(item, "-1，2√3").verdict, "incorrect");
});
