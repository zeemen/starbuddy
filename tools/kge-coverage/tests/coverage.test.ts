// 覆盖矩阵报表单测（specs/kge-02-atoms-tagging/spec.md 设计要点 4）。只用合成夹具：tests/fixtures 下的节点、题目全部合成。
// 夹具的期望值（手算）：
//   K1 = MATH.SYN.SYN_A.K_SYN_0001：主考点 Q1（know）、Q2（understand）、Q3（未标）→ 3；关联 Q5 → 1（Q2 的 node_ids 含 K1 但主考点就是 K1，不算关联）
//   K2 = ...K_SYN_0002：主考点 Q4（apply）→ 1；关联 Q2、Q5 → 2
//   S3 = ...S_SYN_0003：主考点 Q5（synthesize）→ 1；关联 Q4 → 1
//   M4 = ...M_SYN_0004：主考点 0（空节点）；关联 Q5 → 1
//   覆盖 3/4 = 75.0% → 未达标；Q5 有 4 个节点 → 超上限；Q6 无处可标；Q7 主考点落在主题层；Q8 主考点与关联节点不在地图
//   节点数分布：0→1（Q6），1→3（Q1、Q3、Q7），2→3（Q2、Q4、Q8），4→1（Q5）
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync as writeFile } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { computeCoverage, CoverageError, pct } from "../src/coverage.ts";
import { main } from "../src/main.ts";
import { loadMapDir, MapError, mergeMaps, parseInsert, parseMap, parsePgArray } from "../src/map.ts";
import { csvField, toCsv, toMarkdown } from "../src/render.ts";
import { checkTaggingReport, loadTaggingReport, TaggingError } from "../src/tagging.ts";

const FIX = fileURLToPath(new URL("./fixtures/", import.meta.url));
const MAP_DIR = join(FIX, "map");
const TAGGING = join(FIX, "tagging-report.json");
const K1 = "MATH.SYN.SYN_A.K_SYN_0001", K2 = "MATH.SYN.SYN_A.K_SYN_0002", S3 = "MATH.SYN.SYN_B.S_SYN_0003", M4 = "MATH.SYN.SYN_B.M_SYN_0004";

const load = () => ({ map: loadMapDir(MAP_DIR), tagging: loadTaggingReport(TAGGING) });

test("import.sql 解析：单引号加倍、类型转换、NULL、数组字面量", () => {
  const { map } = load();
  assert.equal(map.mapVersionId, "00000000-0000-4000-8000-00000000c0de");
  assert.equal(map.version, "9.9.9-syn");
  assert.equal(map.topic, "syn");
  assert.equal(map.nodes.length, 7);
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  assert.equal(byId.get(K2)!.name, `（合成）节点"二", 带逗号与'单引号'`);
  assert.deepEqual(byId.get(K1)!.cognitiveLevels, ["know", "understand"]);
  assert.equal(byId.get("MATH.SYN")!.kind, null);
  assert.equal(byId.get(K1)!.status, "active");
  assert.deepEqual(parsePgArray('{a,"b, c","d\\"e"}'), ["a", "b, c", 'd"e']);
  assert.deepEqual(parsePgArray("{}"), []);
  const rows = parseInsert("INSERT INTO kge.x (a, b, c) VALUES ('it''s', NULL, 3), ('x'::uuid, 'y', -1.5);", "kge.x");
  assert.deepEqual(rows, [{ a: "it's", b: null, c: 3 }, { a: "x", b: "y", c: -1.5 }]);
  assert.throws(() => parseInsert("INSERT INTO kge.x (a, b) VALUES ('1');", "kge.x"), MapError);
  // 带 status 列时读出停用节点
  const sql = "INSERT INTO kge.map_version (id, version) VALUES ('m', '1');\n" +
    "INSERT INTO kge.knowledge_node (map_version_id, id, level, kind, name, status) VALUES ('m', 'MATH.A.B.C', 'node', 'kp', 'n', 'deprecated');";
  assert.equal(parseMap(sql, { map_version_id: "m", version: "1" }).nodes[0]!.status, "deprecated");
  assert.throws(() => parseMap(sql, { map_version_id: "other" }), /不一致/);
});

test("矩阵数字：主考点按认知层次计数、关联题数、合计", () => {
  const { map, tagging } = load();
  const r = computeCoverage(map, tagging);
  assert.deepEqual(r.rows.map((x) => x.node.id), [K1, K2, M4, S3], "只含知识节点层，按码点排序");
  const row = (id: string) => r.rows.find((x) => x.node.id === id)!;
  assert.deepEqual(row(K1).byLevel, { know: 1, understand: 1, apply: 0, synthesize: 0, unlabeled: 1 });
  assert.equal(row(K1).mainTotal, 3);
  assert.equal(row(K1).relatedTotal, 1);
  assert.deepEqual(row(K2).byLevel, { know: 0, understand: 0, apply: 1, synthesize: 0, unlabeled: 0 });
  assert.equal(row(K2).relatedTotal, 2);
  assert.deepEqual(row(S3).byLevel, { know: 0, understand: 0, apply: 0, synthesize: 1, unlabeled: 0 });
  assert.equal(row(S3).relatedTotal, 1);
  assert.equal(row(M4).mainTotal, 0);
  assert.equal(row(M4).relatedTotal, 1);
  assert.equal(r.itemCount, 8);
  // CSV 与手算逐字节一致（含名称的引号转义，规则同 Postgres COPY csv）
  assert.equal(toCsv(r), [
    "node_id,name,kind,know,understand,apply,synthesize,unlabeled,main_total,related_total,covered",
    `${K1},（合成）节点一,kp,1,1,0,0,1,3,1,yes`,
    `${K2},"（合成）节点""二"", 带逗号与'单引号'",kp,0,0,1,0,0,1,2,yes`,
    `${M4},（合成）节点四,method,0,0,0,0,0,0,1,no`,
    `${S3},（合成）节点三,ability_kp,0,0,0,1,0,1,1,yes`,
    "",
  ].join("\n"));
});

test("空节点与覆盖门槛：没有主考点题的节点列出并标红；全覆盖时达标", () => {
  const { map, tagging } = load();
  const r = computeCoverage(map, tagging);
  assert.deepEqual(r.uncovered.map((n) => n.id), [M4]);
  assert.equal(r.coveredCount, 3);
  assert.equal(r.meetsThreshold, false);
  const md = toMarkdown(r);
  assert.match(md, /有主考点题的节点：3 \/ 4（75\.0%），门槛 100%：\*\*【红】\*\* 未达标/);
  assert.match(md, /- \*\*【红】\*\* `MATH\.SYN\.SYN_B\.M_SYN_0004` （合成）节点四（method）/);
  // 给 M4 补一道主考点题 → 达标
  const full = checkTaggingReport({ ...JSON.parse(readFileSync(TAGGING, "utf8")),
    items: [...JSON.parse(readFileSync(TAGGING, "utf8")).items, { item_id: "SYN-Q-009", main_node_id: M4, node_ids: [], confidence: 0.9, layer: "rule", grade: "A", cognitive_level: null }] });
  const r2 = computeCoverage(map, full);
  assert.equal(r2.meetsThreshold, true);
  assert.deepEqual(r2.uncovered, []);
  assert.match(toMarkdown(r2), /4 \/ 4（100\.0%），门槛 100%：达标/);
  // 没有矩阵行时按未达标
  assert.equal(computeCoverage({ ...map, nodes: map.nodes.filter((n) => n.level !== "node") }, tagging).meetsThreshold, false);
});

test("题目节点数分布：{主考点} ∪ node_ids 去重；超过 3 个的标出", () => {
  const { map, tagging } = load();
  const r = computeCoverage(map, tagging);
  assert.deepEqual(r.nodeCountDistribution, [{ key: "0", count: 1 }, { key: "1", count: 3 }, { key: "2", count: 3 }, { key: "4", count: 1 }]);
  assert.deepEqual(r.overLimitItems, [{ item_id: "SYN-Q-005", nodes: 4, overCap: false }]);
  const md = toMarkdown(r);
  assert.match(md, /\| 4 \*\*【红】\*\* \| 1 \| 12\.5% \|/);
  assert.match(md, /- \*\*【红】\*\* `SYN-Q-005`：4 个节点/);
  assert.match(md, /小题节点数超过 3 个的题（KGE :746，含合并前超限后截断的）：1 道 \*\*【红】\*\*/);
});

test("候选层占比、题目来源层、A/B 占比与需要核对的题", () => {
  const { map, tagging } = load();
  const r = computeCoverage(map, tagging);
  assert.deepEqual(r.candidateShares, [{ key: "rule", count: 5 }, { key: "vector", count: 3 }, { key: "model", count: 2 }]);
  assert.deepEqual(r.itemLayerShares, [{ key: "rule", count: 3 }, { key: "vector", count: 2 }, { key: "model", count: 2 }, { key: "none", count: 1 }]);
  assert.deepEqual(r.gradeShares, [{ key: "A", count: 2 }, { key: "B", count: 5 }, { key: "none", count: 1 }]);
  const md = toMarkdown(r);
  assert.match(md, /\| 规则 \| 5 \| 50\.0% \|\n\| 向量 \| 3 \| 30\.0% \|\n\| 模型 \| 2 \| 20\.0% \|\n\| 合计 \| 10 \| 100\.0% \|/);
  assert.match(md, /\| A 级 \| 2 \| 25\.0% \|\n\| B 级 \| 5 \| 62\.5% \|\n\| 未分级 \| 1 \| 12\.5% \|/);
  assert.deepEqual(r.unplaceable, ["SYN-Q-006"]);
  assert.deepEqual(r.mainNotNodeLevel, [{ item_id: "SYN-Q-007", node_id: "MATH.SYN.SYN_A" }]);
  assert.deepEqual(r.mainNotInMap, [{ item_id: "SYN-Q-008", node_id: "MATH.SYN.SYN_C.K_SYN_0099" }]);
  assert.deepEqual(r.relatedNotInMap, [{ item_id: "SYN-Q-008", node_id: "MATH.SYN.SYN_C.K_SYN_0098" }]);
  assert.equal(pct(1, 3), "33.3%");
  assert.equal(pct(0, 0), "—");
});

test("确定性：题目与节点顺序打乱、重复运行，输出逐字节相同；CLI 两次产出一致", () => {
  const { map, tagging } = load();
  const a = computeCoverage(map, tagging);
  const shuffled = computeCoverage({ ...map, nodes: [...map.nodes].reverse() }, { ...tagging, items: [...tagging.items].reverse() });
  assert.equal(toMarkdown(shuffled), toMarkdown(a));
  assert.equal(toCsv(shuffled), toCsv(a));
  const dir = mkdtempSync(join(tmpdir(), "kge-coverage-"));
  try {
    const log = console.log;
    console.log = () => {};
    try {
      assert.equal(main(["--map", MAP_DIR, "--tagging", TAGGING, "--out", join(dir, "1")]), 0);
      assert.equal(main(["--map", MAP_DIR, "--tagging", TAGGING, "--out", join(dir, "2")]), 0);
    } finally {
      console.log = log;
    }
    for (const f of ["coverage-9.9.9-syn.md", "coverage-9.9.9-syn.csv"]) {
      assert.equal(readFileSync(join(dir, "1", f), "utf8"), readFileSync(join(dir, "2", f), "utf8"), f);
    }
    assert.equal(readFileSync(join(dir, "1", "coverage-9.9.9-syn.csv"), "utf8"), toCsv(a));
    assert.doesNotMatch(readFileSync(join(dir, "1", "coverage-9.9.9-syn.md"), "utf8"), new RegExp(dir.replaceAll("/", "\\/")), "报表不含本机路径");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("输入核对：版本不符、字段不合法、参数缺失时拒绝", () => {
  const { map, tagging } = load();
  assert.throws(() => computeCoverage(map, { ...tagging, map_version: "0.0.1" }), CoverageError);
  assert.doesNotThrow(() => computeCoverage(map, { ...tagging, map_version: map.mapVersionId }), "也接受地图版本 ID");
  const base = { map_version: "v", items: [{ item_id: "X", main_node_id: null, node_ids: [] }] };
  assert.doesNotThrow(() => checkTaggingReport(base));
  assert.throws(() => checkTaggingReport({ ...base, items: [{ ...base.items[0], layer: "guess" }] }), TaggingError);
  assert.throws(() => checkTaggingReport({ ...base, items: [{ ...base.items[0], grade: "C" }] }), TaggingError);
  assert.throws(() => checkTaggingReport({ ...base, items: [{ ...base.items[0], confidence: 1.2 }] }), TaggingError);
  assert.throws(() => checkTaggingReport({ ...base, items: [{ ...base.items[0], cognitive_level: "remember" }] }), TaggingError);
  assert.throws(() => checkTaggingReport({ ...base, items: [base.items[0], base.items[0]] }), /重复/);
  assert.throws(() => checkTaggingReport({ ...base, candidates_by_layer: { rule: -1 } }), TaggingError);
  assert.equal(csvField("a,b"), '"a,b"');
  assert.equal(csvField('a"b'), '"a""b"');
  assert.equal(csvField("ab"), "ab");
  const err = console.error;
  console.error = () => {};
  try {
    assert.equal(main(["--map", MAP_DIR]), 2);
    assert.equal(main(["--bogus"]), 2);
    const dir = mkdtempSync(join(tmpdir(), "kge-coverage-"));
    try {
      const bad = join(dir, "t.json");
      writeFile(bad, JSON.stringify({ ...JSON.parse(readFileSync(TAGGING, "utf8")), map_version: "0.0.1" }));
      assert.equal(main(["--map", MAP_DIR, "--tagging", bad, "--out", join(dir, "o")]), 1);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  } finally {
    console.error = err;
  }
});

test("打标器正式格式（kge-tagging-report/1）：未导入主题的题不计；over_cap 标出；scope = item_tag 只算写进 item_tag 的题", () => {
  // 手算：SYN-R-004 out_of_scope 不计；all 范围 4 道：K1←R-001、K2←R-002（B 级候选）、S3←R-005；M4 只作关联（R-002）
  //   R-002 的 node_ids 已截断到 3 个但 over_cap = true → 标出；R-003 无处可标
  //   item_tag 范围只有 R-001、R-005：K2 也变成空节点，覆盖 2/4
  const map = loadMapDir(MAP_DIR);
  const t = loadTaggingReport(join(FIX, "tagging-report-v1.json"));
  assert.equal(t.map_version, "9.9.9-syn");
  assert.equal(t.map_version_id, map.mapVersionId);
  const all = computeCoverage(map, t);
  assert.equal(all.itemCount, 4);
  assert.equal(all.outOfScope, 1);
  // 正式格式没有 cognitive_level：按导入器的规则推——S3 只声明了 apply → 记 apply；K1 声明两个、K2 没声明 → 未标
  assert.deepEqual(all.rows.map((x) => [x.node.id, x.mainTotal, x.relatedTotal, x.byLevel.unlabeled, x.byLevel.apply]),
    [[K1, 1, 0, 1, 0], [K2, 1, 0, 1, 0], [M4, 0, 1, 0, 0], [S3, 1, 1, 0, 1]]);
  assert.deepEqual(all.overLimitItems, [{ item_id: "SYN-R-002", nodes: 3, overCap: true }]);
  assert.deepEqual(all.unplaceable, ["SYN-R-003"]);
  assert.deepEqual(all.candidateShares, [{ key: "rule", count: 4 }, { key: "vector", count: 2 }, { key: "model", count: 0 }]);
  assert.deepEqual(all.candidateRowShares, [{ key: "rule", count: 1 }, { key: "vector", count: 2 }, { key: "model", count: 0 }]);
  const md = toMarkdown(all);
  assert.match(md, /未导入主题、不打标的题：1 道/);
  assert.match(md, /`SYN-R-002`：3 个节点（合并前超过上限，node_ids 已截断）/);
  assert.match(md, /\| 写进 kge\.tag_candidate 的候选 \| 条数 \| 占比 \|/);
  const only = computeCoverage(map, t, "item_tag");
  assert.equal(only.itemCount, 2);
  assert.equal(only.excludedByScope, 2);
  assert.equal(only.coveredCount, 2);
  assert.deepEqual(only.uncovered.map((n) => n.id), [K2, M4]);
  assert.deepEqual(only.overLimitItems, []);
  assert.match(toMarkdown(only), /只算写进 kge\.item_tag 的题.*2 道题；其余 2 道不计/);
  // 地图版本 ID 对不上、未知格式都拒绝
  assert.throws(() => computeCoverage(map, { ...t, map_version_id: "00000000-0000-4000-8000-000000000000" }), CoverageError);
  const raw = JSON.parse(readFileSync(join(FIX, "tagging-report-v1.json"), "utf8"));
  assert.throws(() => checkTaggingReport({ ...raw, format: "kge-tagging-report/2" }), TaggingError);
  assert.throws(() => checkTaggingReport({ ...raw, items: [{ ...raw.items[0], status: "maybe" }] }), TaggingError);
  // 没有 counts 时按各题 candidates 现算
  const { counts: _c, ...noCounts } = raw;
  assert.deepEqual(checkTaggingReport(noCounts).candidates_by_layer, { rule: 1 });
});

test("多个专题种子合成一张图：同一地图版本才能合并，共用节点只留一份", () => {
  const map = loadMapDir(MAP_DIR);
  const a = { ...map, topic: "syn", nodes: map.nodes.filter((n) => !n.id.startsWith("MATH.SYN.SYN_B")) };
  const b = { ...map, topic: "syb", nodes: map.nodes.filter((n) => n.id === "MATH.SYN" || n.id.startsWith("MATH.SYN.SYN_B")) };
  const m = mergeMaps([a, b]);
  assert.equal(m.nodes.length, map.nodes.length);
  assert.equal(m.topic, "syb、syn");
  assert.throws(() => mergeMaps([a, { ...b, mapVersionId: "other" }]), MapError);
  assert.throws(() => mergeMaps([a, { ...b, nodes: [{ ...b.nodes[0]!, name: "改名" }] }]), /定义不同/);
  assert.throws(() => mergeMaps([]), MapError);
  const log = console.log;
  console.log = () => {};
  const dir = mkdtempSync(join(tmpdir(), "kge-coverage-"));
  try {
    assert.equal(main(["--map", MAP_DIR, "--map", MAP_DIR, "--tagging", TAGGING, "--scope", "item_tag", "--out", dir]), 0);
  } finally {
    console.log = log;
    rmSync(dir, { recursive: true, force: true });
  }
  const err = console.error;
  console.error = () => {};
  try {
    assert.equal(main(["--map", MAP_DIR, "--tagging", TAGGING, "--scope", "some"]), 2);
  } finally {
    console.error = err;
  }
});
