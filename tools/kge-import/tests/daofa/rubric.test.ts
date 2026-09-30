// 冲刺 4-K 采分点入原子层与内容包一致性检查的单测（全部合成数据）。
// 变异：原子层导出的每个参与比对的字段（含关键词三档、给分规则三个子字段、常见错误三个子字段）、采分点的增删、
// rubric_version 与题目 ID 的改动，都必须被 diffRubric 发现。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import type { ContentPack } from "../../../../adapters/zhixing/src/content.ts";
import { atomsFromPack, COMPARED_FIELDS, diffRubric, EXPORT_FORMAT, parseExport, renderScoringSql, type ScoringAtom } from "../../src/daofa/rubric.ts";

const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const pack = (): ContentPack => JSON.parse(readFileSync(`${REPO}adapters/zhixing/content/pack.json`, "utf8")) as ContentPack;
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

test("内容包 → 原子：合成 4 题、字段对齐 kge.scoring_point；历史题拒绝", () => {
  const atoms = atomsFromPack(pack());
  assert.equal(new Set(atoms.map((a) => a.item_id)).size, 4);
  assert.equal(atoms.length, pack().items.reduce((s, i) => s + i.points.length, 0));
  assert.ok(atoms.every((a) => a.rubric_version === "zhixing-synthetic@0.1.0" && a.node_ids.every((n) => n.startsWith("MORAL."))));
  assert.deepEqual(Object.keys(atoms[0]!).sort(), ["common_errors", "item_id", "keyword_family", "material_linkage", "node_ids", "partial_credit_rule",
    "point_id", "required_logic", "rubric_version", "score", "subject_dimension"]);
  const h = pack();
  h.items[0]!.discipline = "history";
  assert.throws(() => atomsFromPack(h), /不是道法题/);
  const bad = pack();
  bad.items[0]!.points[0]!.score = 0.3;
  assert.throws(() => atomsFromPack(bad), /内容包不合格/, "与适配器同一套校验");
});

test("采分点 SQL：只新增（ON CONFLICT DO NOTHING）+ 逐字段核对已有行；节点必须是目标版本的观点", () => {
  const atoms = atomsFromPack(pack());
  const vps = new Set(atoms.flatMap((a) => a.node_ids));
  const sql = renderScoringSql(atoms, { mapVersionId: "00000000-0000-4000-8000-000000000001", viewpointIds: vps, synthetic: true, source: "t" });
  assert.match(sql, /^SET LOCAL ROLE starbuddy_kge;$/m);
  assert.match(sql, /ON CONFLICT \(subject, item_id, rubric_version, point_id\) DO NOTHING;/);
  assert.match(sql, /只能新增不能修改/);
  assert.match(sql, /starbuddy\.allow_synthetic/);
  assert.doesNotMatch(sql, /\bUPDATE\b/);
  assert.equal(renderScoringSql(atoms, { mapVersionId: "00000000-0000-4000-8000-000000000001", viewpointIds: vps, synthetic: true, source: "t" }), sql, "确定性");
  const fewer = new Set([...vps].slice(1));
  assert.throws(() => renderScoringSql(atoms, { mapVersionId: "x", viewpointIds: fewer, synthetic: true, source: "t" }), /不是目标地图版本里的观点/);
});

test("一致性检查：相同为空；每个字段、每个子字段、增删采分点、改版本与题目 ID 都能被发现", () => {
  const atoms = atomsFromPack(pack());
  const exported = (): ScoringAtom[] => clone(atoms);
  assert.deepEqual(diffRubric(atoms, exported()), []);
  // 键顺序不同（库里 jsonb 会重排键）不算差异；别的 rubric_version 不参与比对
  const reordered = exported().map((a) => ({ ...a, keyword_family: Object.fromEntries(Object.entries(a.keyword_family as object).reverse()) }));
  assert.deepEqual(diffRubric(atoms, [...reordered, { ...exported()[0]!, rubric_version: "zhixing-synthetic@0.0.9", score: 99 }]), []);

  const mutations: [string, (a: ScoringAtom) => void, string][] = [
    ["score", (a) => { a.score += 0.5; }, "score"],
    ["node_ids 换节点", (a) => { a.node_ids = ["MORAL.SYN.LIFE.VALUE_DEDICATION"]; }, "node_ids"],
    ["node_ids 多一个", (a) => { a.node_ids = [...a.node_ids, "MORAL.SYN.LIFE.VALUE_DEDICATION"]; }, "node_ids"],
    ["subject_dimension", (a) => { a.subject_dimension = "other"; }, "subject_dimension"],
    ["subject_dimension 变 null", (a) => { a.subject_dimension = null; }, "subject_dimension"],
    ["keyword_family.core", (a) => { (a.keyword_family as { core: string[] }).core.push("x"); }, "keyword_family"],
    ["keyword_family.accepted", (a) => { (a.keyword_family as { accepted: string[] }).accepted = []; }, "keyword_family"],
    ["keyword_family.colloquial", (a) => { (a.keyword_family as { colloquial: string[] }).colloquial.reverse().push("y"); }, "keyword_family"],
    ["required_logic", (a) => { a.required_logic = a.required_logic === null ? "（合成）" : null; }, "required_logic"],
    ["material_linkage", (a) => { a.material_linkage = a.material_linkage === null ? "（合成）" : `${a.material_linkage}。`; }, "material_linkage"],
    ["partial_credit_rule.mode", (a) => { (a.partial_credit_rule as { mode: string }).mode = "proportional"; }, "partial_credit_rule"],
    ["partial_credit_rule.allowed_scores", (a) => { (a.partial_credit_rule as { allowed_scores: number[] }).allowed_scores = [0, a.score]; }, "partial_credit_rule"],
    ["partial_credit_rule.description", (a) => { (a.partial_credit_rule as { description: string }).description += "。"; }, "partial_credit_rule"],
    ["common_errors[].type", (a) => { (a.common_errors as { type: string }[])[0]!.type = "shenti_cuo"; }, "common_errors"],
    ["common_errors[].pattern", (a) => { (a.common_errors as { pattern: string }[])[0]!.pattern += "。"; }, "common_errors"],
    ["common_errors[].note", (a) => { (a.common_errors as { note: string }[])[0]!.note += "。"; }, "common_errors"],
    ["common_errors 多一条", (a) => { (a.common_errors as unknown[]).push({ type: "guifan_cuo", pattern: "p", note: "n" }); }, "common_errors"],
  ];
  // 所有参与比对的字段都有变异覆盖
  assert.deepEqual([...new Set(mutations.map((m) => m[2]))].sort(), [...COMPARED_FIELDS].sort());
  for (const [name, mutate, field] of mutations) {
    for (const idx of [0, atoms.length - 1]) {
      const e = exported();
      mutate(e[idx]!);
      const d = diffRubric(atoms, e);
      assert.ok(d.length > 0 && d.every((x) => x.field === field), `${name}（第 ${idx} 个）应只报 ${field}：${JSON.stringify(d)}`);
    }
  }
  const missing = exported().slice(1);
  assert.deepEqual(diffRubric(atoms, missing).map((x) => x.detail), ["内容包有、原子层没有"]);
  const extra = [...exported(), { ...exported()[0]!, point_id: "p9" }];
  assert.deepEqual(diffRubric(atoms, extra).map((x) => x.detail), ["原子层有、内容包没有"]);
  const otherVersion = exported().map((a) => ({ ...a, rubric_version: "zhixing-synthetic@0.2.0" }));
  assert.equal(diffRubric(atoms, otherVersion).length, atoms.length, "同版本在原子层里一个都没有");
  const renamed = exported();
  renamed[0]!.item_id = "synthetic-m3-99";
  assert.deepEqual(diffRubric(atoms, renamed).map((x) => x.detail).sort(), ["内容包有、原子层没有", "原子层有、内容包没有"]);
  const dup = [...exported(), exported()[0]!];
  assert.ok(diffRubric(atoms, dup).some((x) => x.detail === "原子层导出里重复"));
  // 报告只含 ID 与字段名，不回显内容
  const d = diffRubric(atoms, [{ ...exported()[0]!, material_linkage: "（合成）不该出现在报告里" }, ...exported().slice(1)]);
  assert.ok(!JSON.stringify(d).includes("不该出现在报告里"));
});

test("原子层导出的读取：格式、学科、字段类型不对都拒绝", () => {
  const good = { format: EXPORT_FORMAT, subject: "zhixing", points: atomsFromPack(pack()) };
  assert.equal(parseExport(JSON.stringify(good)).points.length, good.points.length);
  assert.throws(() => parseExport("{"), /不是 JSON/);
  assert.throws(() => parseExport(JSON.stringify({ ...good, format: "x" })), /format/);
  assert.throws(() => parseExport(JSON.stringify({ ...good, subject: "aya" })), /学科/);
  assert.throws(() => parseExport(JSON.stringify({ ...good, points: [{ ...good.points[0], score: "2" }] })), /score/);
  assert.throws(() => parseExport(JSON.stringify({ ...good, points: [{ ...good.points[0], node_ids: "MORAL.X.Y.Z" }] })), /node_ids/);
});
