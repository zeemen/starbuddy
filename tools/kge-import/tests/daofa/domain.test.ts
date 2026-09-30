// 冲刺 4-K 道法观点地图：YAML 子集、模板、校验器、种子生成的单测（全部合成数据）。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { NODE_ID } from "../../src/daofa/common.ts";
import { buildMap, parseDomain } from "../../src/daofa/domain.ts";
import { mapVersionIdOf, renderMapSeed } from "../../src/daofa/seed.ts";
import { parseYamlDoc, YamlDocError } from "../../src/daofa/yaml-doc.ts";

const TOOL = fileURLToPath(new URL("../../", import.meta.url));
const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const LAW = readFileSync(`${TOOL}samples/daofa/law.yaml`, "utf8");
const PACK_NODES = readFileSync(`${TOOL}samples/daofa/pack-nodes.yaml`, "utf8");

/** 最小的合成领域文件；body 替换 big_concepts 之后的部分 */
const mini = (body: string, head = "synthetic: true\nnotice: 合成、非教材内容：单测\n") =>
  `format: kge-daofa-domain/1\n${head}subject: zhixing\ndomain:\n  id: MORAL.LAW\n  name: （合成）法律\n${body}`;
const BC = `big_concepts:
  - id: MORAL.LAW.BC1
    name: （合成）大概念
    viewpoints:
      - id: MORAL.LAW.BC1.V1
        name: （合成）观点一
      - id: MORAL.LAW.BC1.V2
        name: （合成）观点二
      - id: MORAL.LAW.BC1.V3
        name: （合成）观点三
`;

test("YAML 子集：映射、块状列表、列表项映射、流式列表、注释；不支持的写法报错", () => {
  const doc = parseYamlDoc("a: 1\nb:\n  - x\n  - 'y z'\nc:\n  - k: 1\n    m: [p, \"q, r\", 2]\n  - k: 2\nd: 2026-09-01 # 注释\n");
  assert.deepEqual(doc, { a: 1, b: ["x", "y z"], c: [{ k: 1, m: ["p", "q, r", 2] }, { k: 2 }], d: "2026-09-01" });
  for (const bad of ["a: [1, [2]]", "- a", "a:\n   b: 1", "a: 1\na: 2", "a:\tb", "a: {b: 1}", "a: &x 1", "a: b: c", "a: [1,\n  2]"]) {
    assert.throws(() => parseYamlDoc(bad), YamlDocError, bad);
  }
});

test("节点 ID 正则与迁移 0011、docs/design/kge/id-and-segments.md 一致", () => {
  const sql = readFileSync(`${REPO}db/migrations/0011_kge__knowledge_map.up.sql`, "utf8");
  const doc = readFileSync(`${REPO}docs/design/kge/id-and-segments.md`, "utf8");
  assert.equal(NODE_ID.source, /CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)/.exec(sql)![1]);
  assert.equal(NODE_ID.source, /<!-- node-id-regex:start -->\s*`([^`]+)`/.exec(doc)![1]);
  assert.match(sql, /WHEN 'zhixing' THEN 'MORAL'/);
});

test("合成样例：法律领域 2 个大概念、6 个观点、关联边；模板本身能过单文件校验", () => {
  const law = parseDomain(LAW, "law.yaml");
  assert.equal(law.synthetic, true);
  assert.equal(law.bigConcepts.length, 2);
  assert.equal(law.bigConcepts.flatMap((b) => b.viewpoints).length, 6);
  assert.ok(law.relations.filter((r) => r.type === "related").length >= 3);
  assert.ok(law.bigConcepts.flatMap((b) => b.viewpoints).every((v) => v.name.includes("合成") && v.projections.length > 0));
  const tpl = parseDomain(readFileSync(`${TOOL}templates/daofa-domain.yaml`, "utf8"), "daofa-domain.yaml");
  assert.equal(tpl.synthetic, false);
  assert.equal(tpl.bigConcepts[0]!.viewpoints[0]!.projections[0]!.edition, "bubian-2024");
});

test("校验器拒绝：缺大概念、ID 不合规、带原句字段、观点间硬凑前置、非 related 的边", () => {
  const rejects = (yaml: string, re: RegExp, why: string) => assert.throws(() => buildMap([parseDomain(yaml)]), re, why);
  // 缺大概念：观点直接挂在领域下 / 观点 ID 与所在大概念不一致 / 没有 big_concepts
  rejects(mini(`viewpoints:\n  - id: MORAL.LAW.BC1.V1\n    name: x\n${BC}`), /不认识的键“viewpoints”/, "顶层直接写观点");
  rejects(mini(BC.replace("MORAL.LAW.BC1.V2", "MORAL.LAW.BC9.V2")), /必须挂在所在大概念/, "观点 ID 前缀与大概念不一致");
  rejects(mini("big_concepts:\n"), /缺“big_concepts”/, "没有大概念");
  rejects(mini("big_concepts: []\n"), /big_concepts 必须是 1–64 个/, "大概念为空列表");
  // ID 不合规
  rejects(mini(BC.replace("MORAL.LAW.BC1.V1", "moral.law.bc1.v1")), /节点 ID 不符合规范/, "小写");
  rejects(mini(BC.replace("MORAL.LAW.BC1.V1", "MATH.LAW.BC1.V1")), /必须以 MORAL\. 开头/, "前缀不是 MORAL");
  rejects(mini(BC.replace("MORAL.LAW.BC1.V1", "MORAL.LAW.BC1.V1.X")), /应为 4 段/, "观点段数不对");
  rejects(mini(BC.replace("MORAL.LAW.BC1.V1", "MORAL.LAW.BC1.V-1")), /节点 ID 不符合规范/, "连字符");
  rejects(mini(BC).replace("id: MORAL.LAW\n", "id: MORAL\n"), /节点 ID 不符合规范/, "领域只有一段");
  // 带原句字段：任何层级
  rejects(mini(BC.replace("        name: （合成）观点一\n", "        name: （合成）观点一\n        quote_text: 占位\n")), /原句或原文字段/, "观点上的 quote_text");
  rejects(mini(`${BC}      - id: MORAL.LAW.BC1.V4\n        name: x\n        projections:\n          - edition: syn-ed-2024\n            edition_year: 2024\n            volume: a\n            chapter: b\n            page_from: 1\n            page_to: 1\n            原句: 占位\n`),
    /原句或原文字段/, "投影上的原句");
  rejects(mini(BC, "synthetic: true\nnotice: 合成、非教材内容\noriginal_text: 占位\n"), /原句或原文字段/, "顶层的 original_text");
  rejects(mini(BC.replace("        name: （合成）观点一\n", "        name: （合成）观点一\n        phrases: [a]\n")), /原句或原文字段/, "legacy 的 phrases");
  // 观点间硬凑前置：没有显式标注的前置边
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1.V2\n    type: prerequisite\n`), /不硬凑前置边/, "未显式标注的前置");
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1.V2\n    type: prerequisite\n    explicit_prerequisite: true\n`), /必须写 basis/, "显式前置缺依据");
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1.V2\n    type: confusable\n`), /只用 related/, "易混边");
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1.V2\n  - from: MORAL.LAW.BC1.V2\n    to: MORAL.LAW.BC1.V1\n`), /关联无方向/, "反向重复的关联边");
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1\n`), /应为 4 段/, "边的一端是大概念");
  rejects(mini(`${BC}relations:\n  - from: MORAL.LAW.BC1.V1\n    to: MORAL.LAW.BC1.V9\n`), /两端都必须是本地图里的观点/, "边的一端不存在");
  const pre = (a: string, b: string) => `  - from: MORAL.LAW.BC1.${a}\n    to: MORAL.LAW.BC1.${b}\n    type: prerequisite\n    explicit_prerequisite: true\n    basis: （合成）依据\n`;
  rejects(mini(`${BC}relations:\n${pre("V1", "V2")}${pre("V2", "V1")}`), /成环/, "显式前置成环");
  rejects(mini(`${BC}relations:\n${pre("V1", "V2")}${pre("V2", "V3")}${pre("V1", "V3")}`), /传递边/, "显式前置的传递边");
  // 其他
  rejects(mini(BC, "synthetic: true\nnotice: 只是个样例\n"), /合成、非教材内容/, "合成样例没写明");
  rejects(mini(BC).replace("subject: zhixing", "subject: history"), /subject 必须是 zhixing/, "历史学科");
  rejects(mini(BC.replace("        name: （合成）观点一\n", "        name: （合成）观点一\n        cognitive_levels: [remember]\n")), /cognitive_levels/, "认知层次取值");
  rejects(mini(BC.replace("（合成）观点一", "（合成）张三同学的观点")), /个人信息/, "个人信息");
  assert.throws(() => buildMap([parseDomain(mini(BC)), parseDomain(mini(BC.replace(/LAW/g, "ECON")).replace("id: MORAL.LAW\n", "id: MORAL.ECON\n")
    .replace("synthetic: true", "synthetic: false"))]), /合成样例与真实内容/, "合成与真实混在一个版本");
  // 显式标注的前置边可以收
  const ok = buildMap([parseDomain(mini(`${BC}relations:\n${pre("V1", "V2")}  - from: MORAL.LAW.BC1.V2\n    to: MORAL.LAW.BC1.V3\n`))]);
  assert.deepEqual(ok.edges.map((e) => `${e.type}:${e.from}>${e.to}`), ["prerequisite:MORAL.LAW.BC1.V1>MORAL.LAW.BC1.V2", "related:MORAL.LAW.BC1.V2>MORAL.LAW.BC1.V3"]);
});

test("种子：两次生成逐字节相同、与文件顺序无关；投影不带原句、未核对；合成样例带挡板", () => {
  const build = (order: string[]) => renderMapSeed(buildMap(order.map((t, i) => parseDomain(t, i === 0 ? "a.yaml" : "b.yaml"))), { version: "0.0.1" });
  const a = build([LAW, PACK_NODES]), b = build([LAW, PACK_NODES]), c = build([PACK_NODES, LAW]);
  assert.equal(a.sql, b.sql);
  assert.deepEqual(a.snapshot, c.snapshot, "节点、边、投影按码点排序，与文件顺序无关");
  assert.equal(a.sql.split("\n").slice(3).join("\n"), c.sql.split("\n").slice(3).join("\n"), "只有注释里的文件名顺序不同");
  assert.equal(a.mapVersionId, mapVersionIdOf("0.0.1"));
  assert.match(a.sql, /^SET LOCAL ROLE starbuddy_kge;/m);
  assert.match(a.sql, /starbuddy\.allow_synthetic/);
  assert.match(a.sql, /INSERT INTO kge\.map_version \(id, subject, standard_year, version\) VALUES \('[0-9a-f-]{36}', 'zhixing', 2022, '0\.0\.1'\);/);
  const projLines = a.sql.split("\n").filter((l) => l.includes("'syn-ed-2024'"));
  assert.equal(projLines.length, 16);
  assert.ok(projLines.every((l) => /, NULL, 'unverified'\)[,;]$/.test(l)), "quote_text 为 NULL、mapping_state 为 unverified");
  assert.doesNotMatch(a.sql, /INSERT INTO kge\.knowledge_edge[^;]*'(confusable|part_of|uses_method)'/);
  assert.deepEqual(a.report.expected_orphans, []);
  assert.deepEqual(a.report.expected_missing_boundary, []);
  assert.equal(a.report.student_reader, "excluded");
  assert.equal(a.snapshot.nodes.filter((n) => n.kind === "viewpoint").length, 16);
  // 真实内容（synthetic: false）不带挡板
  const real = renderMapSeed(buildMap([parseDomain(mini(BC, "synthetic: false\nnotice: 教研填写\n"))]), { version: "1.0.0" });
  assert.doesNotMatch(real.sql, /allow_synthetic/);
  assert.deepEqual(real.report.expected_orphans, ["MORAL.LAW.BC1.V1", "MORAL.LAW.BC1.V2", "MORAL.LAW.BC1.V3"]);
  assert.throws(() => renderMapSeed(buildMap([parseDomain(LAW)]), { version: "1.0" }), /主\.次\.修订/);
});
