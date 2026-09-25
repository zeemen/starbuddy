// 映射单元测试：陷阱码 → 十二根因 → 映射表阿鸦编码 → 跨科大类（spec 映射规则 grade 表 error_attributions 行）。
import assert from "node:assert/strict";
import { test } from "node:test";
import { trapAttributionAlgorithm, TRAP_ATTRIBUTION_CONFIDENCE } from "../src/adapter.ts";
import { ROOT_CAUSES } from "../src/core/types.ts";
import { gradeBody, makeAdapter, realPack, sweepCases, syntheticPack, taxonomy, type Json } from "./helpers.ts";

test("十二根因都能在映射表里找到阿鸦编码与跨科大类", () => {
  const expected: Record<string, [string, string]> = {
    概念错误: ["gainian_cuowu", "knowledge"],
    知识缺失: ["zhishi_queshi", "knowledge"],
    审题错误: ["shenti_cuowu", "reading_the_question"],
    表征错误: ["biaozheng_cuowu", "thinking_method"],
    方法错误: ["fangfa_cuowu", "thinking_method"],
    推理错误: ["tuili_cuowu", "thinking_method"],
    运算错误: ["yunsuan_cuowu", "computation_or_grammar"],
    分类遗漏: ["fenlei_yilou", "thinking_method"],
    表达错误: ["biaoda_cuowu", "expression_convention"],
    检验错误: ["jianyan_cuowu", "strategy_and_time"],
    时间问题: ["shijian_wenti", "strategy_and_time"],
    心理与习惯: ["xinli_xiguan", "strategy_and_time"],
  };
  for (const rc of ROOT_CAUSES) {
    const e = taxonomy.byRootCause.get(rc)!;
    assert.deepEqual([e.code, e.crossSubject], expected[rc], rc);
  }
  assert.equal(taxonomy.byRootCause.get("心理与习惯")!.sensitive, true);
});

test("内容包里全部 429 个陷阱码都能映射到跨科大类", () => {
  assert.equal(realPack.pack.traps.length, 429);
  for (const t of realPack.pack.traps) assert.ok(taxonomy.byRootCause.get(t.rootCause), t.code);
});

test("全部题目：答错且命中陷阱时，错因与陷阱逐一对应；置信度 0.8；算法版本带映射表版本", () => {
  const adapter = makeAdapter();
  let withTraps = 0;
  for (const c of sweepCases()) {
    const b = adapter.handle("POST", "grade", gradeBody(c.item.id, c.response)).body as Json;
    const attrs = b.result.error_attributions as Json[];
    if (c.expect !== "incorrect" || !c.traps?.length) {
      assert.deepEqual(attrs, [], `${c.item.id}：没有命中陷阱时不产出错因`);
      continue;
    }
    withTraps++;
    const expectedCodes = new Set(
      c.traps.map((t) => taxonomy.byRootCause.get(realPack.trapByCode.get(t)!)!).filter((e) => !e.sensitive).map((e) => e.code),
    );
    assert.deepEqual(new Set(attrs.map((a) => a.subject_category)), expectedCodes, c.item.id);
    const responseEvidence = b.evidence.find((e: Json) => e.kind !== "verifier_trace").evidence_id;
    for (const a of attrs) {
      assert.equal(a.confidence, TRAP_ATTRIBUTION_CONFIDENCE);
      assert.equal(a.algorithm_version, trapAttributionAlgorithm(taxonomy.version));
      assert.equal(a.algorithm_version, "aya-trap-attr@0.1.0-tax0.9.0");
      assert.equal(a.cross_subject_category, [...taxonomy.byRootCause.values()].find((e) => e.code === a.subject_category)!.crossSubject);
      assert.deepEqual(a.evidence_ids, [responseEvidence]);
      assert.equal(a.skill_node, `aya:${c.item.mainNodes[0]}`);
    }
  }
  assert.ok(withTraps > 1000, `命中陷阱的用例只有 ${withTraps} 个`);
});

test("答错但没有命中预标注陷阱：不猜错因", () => {
  const adapter = makeAdapter();
  const b = adapter.handle("POST", "grade", gradeBody("Q-EQU-U03-D-001", { text: "123" })).body as Json;
  assert.equal(b.result.verdict, "incorrect");
  assert.deepEqual(b.result.error_attributions, []);
});

test("敏感类“心理与习惯”不自动归因；同一根因的多个陷阱只产出一条", () => {
  const pack = syntheticPack(
    [{ answer: ["5"], wrongAnswerTraps: [{ pattern: ["6"], traps: ["T-PSY", "T-OPR-1", "T-OPR-2"] }] }],
    {
      traps: [
        { code: "T-PSY", rootCause: "心理与习惯" },
        { code: "T-OPR-1", rootCause: "运算错误" },
        { code: "T-OPR-2", rootCause: "运算错误" },
      ],
    },
  );
  const b = makeAdapter(pack).handle("POST", "grade", gradeBody("SYN-0", { text: "6" })).body as Json;
  assert.deepEqual(
    b.result.error_attributions.map((a: Json) => [a.subject_category, a.cross_subject_category]),
    [["yunsuan_cuowu", "computation_or_grammar"]],
  );
});
