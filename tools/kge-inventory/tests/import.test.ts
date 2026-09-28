// KGE-01 骨架导入的映射规则（specs/kge-01-skeleton-import/spec.md 验收最后一条）。全部合成节点，不读 legacy。
import assert from "node:assert/strict";
import { test } from "node:test";
import { build, ImportError, type LegacyNode, type Overrides } from "../src/import/build.ts";

const n = (id: string, cluster: string, prerequisites: string[] = []): LegacyNode =>
  ({ id, name: `（合成）${id}`, kind: id[0]!, cluster, clusterName: `（合成）${cluster}`, core: "（合成）", prerequisites });
const OPTS = { topic: "fun", version: "0.1.0" };
const BASE = [
  n("K-FUN-0001", "FUN-CON"),
  n("K-FUN-0002", "FUN-CON", ["K-FUN-0001"]),
  n("S-FUN-0003", "FUN-GRA", ["K-FUN-0002", "K-FUN-0001"]), // K1→S3 是传递边（K1→K2→S3）
  n("M-FUN-0004", "FUN-DET", ["S-FUN-0003", "二元一次方程组", "K-EQU-0901"]),
  n("MD-FUN-0005", "FUN-MOD", ["M-FUN-0004"]),
  n("P-FUN-0006", "FUN-GRA", ["S-FUN-0003"]),
  n("C-FUN-RNG", "FUN-MOD", ["MD-FUN-0005"]),
];
const ids = (r: ReturnType<typeof build>) => r.nodes.map((x) => x.id);
const issues = (r: ReturnType<typeof build>, old: string) => r.worklist.filter((w) => w.oldId === old).map((w) => w.issue);

test("节点：领域、按知识簇的主题、机械 ID；类型映射 K/S/M(MD)；C 与缺错因的 P 不导入", () => {
  const r = build(BASE, OPTS);
  assert.deepEqual(ids(r), [
    "MATH.FUNC", "MATH.FUNC.CON", "MATH.FUNC.DET", "MATH.FUNC.GRA", "MATH.FUNC.MOD",
    "MATH.FUNC.CON.K_FUN_0001", "MATH.FUNC.CON.K_FUN_0002", "MATH.FUNC.DET.M_FUN_0004", "MATH.FUNC.MOD.MD_FUN_0005",
    "MATH.FUNC.GRA.S_FUN_0003",
  ]);
  const kind = Object.fromEntries(r.nodes.map((x) => [x.id, x.kind]));
  assert.equal(kind["MATH.FUNC.CON.K_FUN_0001"], "kp");
  assert.equal(kind["MATH.FUNC.GRA.S_FUN_0003"], "ability_kp");
  assert.equal(kind["MATH.FUNC.MOD.MD_FUN_0005"], "method");
  assert.equal(kind["MATH.FUNC"], null);
  assert.match(issues(r, "C-FUN-RNG")[0]!, /能力视图候选/);
  assert.match(issues(r, "P-FUN-0006")[0]!, /缺错因大类/);
});

test("前置边：只连本专题内已导入的节点；传递边丢弃；自然语言、专题外、未导入的前置进工作单", () => {
  const r = build(BASE, OPTS);
  assert.deepEqual(r.edges.map((e) => e.join(" -> ")), [
    "MATH.FUNC.CON.K_FUN_0001 -> MATH.FUNC.CON.K_FUN_0002",
    "MATH.FUNC.CON.K_FUN_0002 -> MATH.FUNC.GRA.S_FUN_0003",
    "MATH.FUNC.DET.M_FUN_0004 -> MATH.FUNC.MOD.MD_FUN_0005",
    "MATH.FUNC.GRA.S_FUN_0003 -> MATH.FUNC.DET.M_FUN_0004",
  ]);
  assert.deepEqual(r.droppedTransitive.map((e) => e.join(" -> ")), ["MATH.FUNC.CON.K_FUN_0001 -> MATH.FUNC.GRA.S_FUN_0003"]);
  const m4 = issues(r, "M-FUN-0004");
  assert.ok(m4.some((i) => i.includes("自然语言前置“二元一次方程组”")));
  assert.ok(m4.some((i) => i.includes("K-EQU-0901 在本专题之外")));
  assert.equal(r.sql.includes("K-EQU-0901"), false, "专题外前置不进 SQL");
});

test("缺边界：知识点与能力型知识点缺包含 / 不包含 / 题例 / 认知层次；方法不要求", () => {
  const r = build(BASE, OPTS);
  assert.ok(issues(r, "K-FUN-0001").includes("缺包含、不包含、典型题例、认知层次"));
  assert.equal(issues(r, "M-FUN-0004").some((i) => i.startsWith("缺包含")), false);
});

test("环：导入失败，由教研决定断哪条边", () => {
  const cyc = [n("K-FUN-0001", "FUN-CON", ["K-FUN-0002"]), n("K-FUN-0002", "FUN-CON", ["K-FUN-0001"])];
  assert.throws(() => build(cyc, OPTS), (e: unknown) => e instanceof ImportError && /成环/.test(e.message));
});

test("覆盖文件：语义化命名、边界、易错点错因、类型映射、补充前置", () => {
  const ov: Overrides = {
    kind_map: { C: "method" },
    nodes: {
      "K-FUN-0001": { id_segment: "QUAD_RELATION", includes: ["（合成）"], excludes: ["（合成）"], examples: ["（合成）"],
                      cognitive_levels: ["understand"] },
      "P-FUN-0006": { error_type: "knowledge", includes: ["（合成）识别特征"] },
      "MD-FUN-0005": { extra_prerequisites: ["P-FUN-0006"] },
    },
  };
  const r = build(BASE, OPTS, ov);
  assert.ok(ids(r).includes("MATH.FUNC.CON.QUAD_RELATION"));
  assert.ok(ids(r).includes("MATH.FUNC.GRA.P_FUN_0006"));
  assert.ok(ids(r).includes("MATH.FUNC.MOD.C_FUN_RNG"));
  assert.equal(issues(r, "K-FUN-0001").some((i) => i.startsWith("缺")), false);
  assert.ok(r.edges.some(([a, b]) => a === "MATH.FUNC.GRA.P_FUN_0006" && b === "MATH.FUNC.MOD.MD_FUN_0005"), "补充前置连成边");
  const cyclic = { ...ov, nodes: { ...ov.nodes, "K-FUN-0002": { extra_prerequisites: ["C-FUN-RNG"] } } };
  assert.throws(() => build(BASE, OPTS, cyclic), /成环/, "补充前置造成的环同样拒绝");
  assert.match(r.sql, /'knowledge'::platform.cross_subject_error/);
  assert.match(r.sql, /\('[0-9a-f-]+', 'aya', 'K-FUN-0001', 'MATH\.FUNC\.CON\.QUAD_RELATION', 'rename'\)/, "旧编码登记进迁移表");
});

test("覆盖文件取值不合法：拒绝", () => {
  const bad: Overrides[] = [
    { nodes: { "K-FUN-0001": { id_segment: "quad-relation" } } },
    { nodes: { "K-FUN-0001": { cognitive_levels: ["remember"] } } },
    { nodes: { "P-FUN-0006": { error_type: "carelessness" } } },
    { kind_map: { P: "concept" as never } },
    { nodes: { "K-FUN-0002": { extra_prerequisites: ["K-EQU-0901"] } } }, // 专题外
    { nodes: { "K-FUN-0002": { extra_prerequisites: ["二次式"] } } },
  ];
  for (const o of bad) assert.throws(() => build(BASE, OPTS, o), ImportError, JSON.stringify(o));
});

test("确定性：同样输入逐字节同样输出；输入顺序不影响结果", () => {
  const a = build(BASE, OPTS);
  const b = build([...BASE].reverse(), OPTS);
  assert.equal(a.sql, b.sql);
  assert.equal(a.worklistMd, b.worklistMd);
  assert.equal(a.mapVersionId, build(BASE, OPTS).mapVersionId);
  assert.notEqual(a.mapVersionId, build(BASE, { ...OPTS, version: "0.2.0" }).mapVersionId);
});

test("SQL 转义：名称里的单引号不会截断语句", () => {
  const r = build([{ ...n("K-FUN-0001", "FUN-CON"), name: "（合成）a'); DROP TABLE x; --" }], OPTS);
  assert.ok(r.sql.includes("'（合成）a''); DROP TABLE x; --'"));
});
