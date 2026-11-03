// KGE-07 语文切片导入器：模板、校验器、种子与快照的单测（全部合成数据）。
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { NODE_ID } from "../../src/daofa/common.ts";
import { main } from "../../src/chinese/main.ts";
import { buildMap, ChineseError, parseDomain, PREFIX } from "../../src/chinese/map.ts";
import { mapVersionIdOf, renderMapSeed, SNAPSHOT_FORMAT } from "../../src/chinese/seed.ts";

const TOOL = fileURLToPath(new URL("../../", import.meta.url));
const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const READING = readFileSync(`${TOOL}samples/chinese/reading.yaml`, "utf8");
const ACC = readFileSync(`${TOOL}samples/chinese/accumulation.yaml`, "utf8");
const TEMPLATE = readFileSync(`${TOOL}templates/chinese-domain.yaml`, "utf8");

// ---------- 最小的合成领域文件 ----------
const HEAD = "format: kge-chinese-domain/1\nsynthetic: true\nnotice: 合成、非教材内容：单测\nsubject: wenli\ndomain:\n  id: CHN.SYN_T\n  name: （合成）领域\n";
const VOCAB = "ladders:\n  - code: A\n    name: （合成）能力甲\n    rungs: [L1, L2, L3]\n  - code: B\n    name: （合成）能力乙\n    rungs: [L1, L2]\n"
  + "text_types:\n  - code: T\n    name: （合成）文本\n  - code: T2\n    name: （合成）文本二\nquestion_types:\n  - code: Q\n    name: （合成）题型\n  - code: Q2\n    name: （合成）题型二\n";
const ak = (id: string, ability: string, rung: string, q = "Q", extra = "", t = "T") => `      - id: CHN.SYN_T.TP.${id}\n        kind: ability_kp\n`
  + `        name: （合成）${id}\n        ability: ${ability}\n        rung: ${rung}\n        text_type: ${t}\n        question_type: ${q}\n${extra}`;
const mat = (id: string, extra = "") => `      - id: CHN.SYN_T.TP.${id}\n        kind: material\n        name: （合成）${id}\n${extra}`;
const meth = (id: string) => `      - id: CHN.SYN_T.TP.${id}\n        kind: method\n        name: （合成）${id}\n`;
const topics = (nodes: string) => `topics:\n  - id: CHN.SYN_T.TP\n    name: （合成）主题\n    nodes:\n${nodes}`;
const rel = (a: string, b: string, type: string, extra = "") => `  - from: CHN.SYN_T.TP.${a}\n    to: CHN.SYN_T.TP.${b}\n    type: ${type}\n${extra}`;
const EXP = "    explicit_prerequisite: true\n    basis: （合成）依据\n";
const NODES = ak("A1", "A", "L1") + ak("A2", "A", "L2") + ak("A3", "A", "L3") + ak("B1", "B", "L1") + mat("M1") + mat("M2") + meth("H1");
const BASE = rel("A1", "A2", "progression") + rel("A2", "A3", "progression") + rel("B1", "H1", "uses_method") + rel("M1", "M2", "confusable")
  + rel("M1", "A1", "related");
const doc = (relations: string, nodes = NODES, head = HEAD, vocab = VOCAB) => `${head}${vocab}${topics(nodes)}relations:\n${relations}`;
const build = (y: string) => buildMap([parseDomain(y, "t.yaml")]);
const rejects = (y: string, re: RegExp, why: string) => assert.throws(() => build(y), (e: unknown) => e instanceof ChineseError && re.test(e.message), why);

test("节点 ID 正则与迁移 0011、ID 文档一致；语文前缀是 CHN（kge.subject_prefix('wenli')）", () => {
  const sql = readFileSync(`${REPO}db/migrations/0011_kge__knowledge_map.up.sql`, "utf8");
  const docText = readFileSync(`${REPO}docs/design/kge/id-and-segments.md`, "utf8");
  assert.equal(NODE_ID.source, /CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)/.exec(sql)![1]);
  assert.equal(NODE_ID.source, /<!-- node-id-regex:start -->\s*`([^`]+)`/.exec(docText)![1]);
  assert.match(sql, new RegExp(`WHEN 'wenli' THEN '${PREFIX}'`));
  // 边类型与强弱只用迁移里已有的取值
  assert.match(sql, /type\s+text NOT NULL CHECK \(type IN \('prerequisite', 'part_of', 'related', 'confusable', 'uses_method', 'has_pitfall', 'aligned_to'\)\)/);
  assert.match(sql, /strength\s+text NOT NULL DEFAULT 'strong' CHECK \(strength IN \('strong', 'weak'\)\)/);
  assert.match(sql, /kind\s+text CHECK \(kind IN \('kp', 'ability_kp', 'viewpoint', 'material', 'method', 'pitfall', 'exam_point'\)\)/);
});

test("合成样例：两个领域、能力点 9、素材 7、方法 2，自由文本都带合成标记；模板（synthetic: false）在确认前一律拒绝", () => {
  const r = parseDomain(READING, "reading.yaml"), a = parseDomain(ACC, "accumulation.yaml");
  assert.equal(r.synthetic && a.synthetic, true);
  const map = buildMap([r, a]);
  const kinds = (k: string) => map.nodes.filter((n) => n.kind === k).length;
  assert.deepEqual([kinds("ability_kp"), kinds("material"), kinds("method")], [9, 7, 2]);
  assert.ok(map.nodes.every((n) => n.name.includes("合成") && n.id.split(".")[1]!.startsWith("SYN")));
  assert.ok(map.nodes.every((n) => [...n.includes, ...n.excludes, ...n.examples].every((x) => x.includes("合成"))));
  assert.ok(map.projections.every((p) => p.volume.includes("合成") && p.chapter.includes("合成")));
  assert.throws(() => parseDomain(TEMPLATE, "chinese-domain.yaml"), /真实内容（synthetic: false）在法务与语文教研确认前一律不收/);
  // 基础合成文件可过
  assert.equal(build(doc(BASE)).edges.length, 5);
});

test("边：层次递进落成 prerequisite + weak，显式前置 prerequisite + strong，关联、易混、用方法同名；素材不进前置图", () => {
  const map = buildMap([parseDomain(READING), parseDomain(ACC)]);
  const pre = map.edges.filter((e) => e.type === "prerequisite");
  assert.deepEqual(pre.map((e) => `${e.relation}:${e.strength}:${e.from}>${e.to}`), [
    "prerequisite:strong:CHN.SYN_READ.SYN_CLASSICAL.WORD_INFER_L1>CHN.SYN_READ.SYN_CLASSICAL.TRANSLATE_L1",
    "progression:weak:CHN.SYN_READ.SYN_NARR.INFER_CHAR_L1>CHN.SYN_READ.SYN_NARR.INFER_CHAR_L2",
    "progression:weak:CHN.SYN_READ.SYN_NARR.INFER_CHAR_L2>CHN.SYN_READ.SYN_NARR.INFER_CHAR_L3",
    "progression:weak:CHN.SYN_READ.SYN_NARR.LOCATE_DETAIL_L1>CHN.SYN_READ.SYN_NARR.LOCATE_DETAIL_L2",
  ]);
  const kindOf = new Map(map.nodes.map((n) => [n.id, n.kind]));
  assert.ok(pre.every((e) => kindOf.get(e.from) === "ability_kp" && kindOf.get(e.to) === "ability_kp"));
  assert.ok(map.edges.filter((e) => e.type !== "prerequisite").every((e) => e.strength === "strong"));
  assert.deepEqual([...new Set(map.edges.map((e) => e.type))].sort(), ["confusable", "prerequisite", "related", "uses_method"]);
  assert.match(pre[1]!.evidenceRef, /^chinese:progression:SYN_INFER:L1->L2$/);
  assert.match(pre[0]!.evidenceRef, /^explicit:/);
});

test("校验器拒绝：素材或方法进前置图、未显式标注的前置、非相邻层次递进、环、传递冗余边、孤点", () => {
  rejects(doc(BASE + rel("M1", "A3", "prerequisite", EXP)), /素材节点不进前置依赖图/, "素材做显式前置的起点");
  rejects(doc(BASE + rel("A3", "M2", "prerequisite", EXP)), /素材节点不进前置依赖图/, "素材做显式前置的终点");
  rejects(doc(BASE + rel("M1", "M2", "progression")), /素材节点不进前置依赖图/, "素材之间写层次递进");
  rejects(doc(BASE + rel("B1", "H1", "progression")), /方法节点不进前置依赖图/, "方法进层次递进");
  rejects(doc(BASE + rel("A1", "B1", "prerequisite")), /显式标注/, "未显式标注的前置");
  rejects(doc(BASE + rel("A1", "B1", "prerequisite", "    explicit_prerequisite: true\n")), /必须写 basis/, "显式前置缺依据");
  rejects(doc(BASE + rel("A1", "B1", "related", "    basis: x\n")), /basis 只用于显式前置边/, "关联边带依据");
  rejects(doc(rel("A1", "A3", "progression") + BASE.slice(BASE.indexOf("  - from: CHN.SYN_T.TP.A2"))), /相邻两级/, "跳级的层次递进");
  rejects(doc(BASE + rel("A3", "B1", "progression")), /相邻两级/, "跨能力的层次递进");
  rejects(doc(BASE + rel("B1", "A2", "progression")), /相邻两级/, "跨能力的层次递进（级数上恰好相邻）");
  rejects(doc(BASE + rel("A1", "X2", "progression"), NODES + ak("X2", "A", "L2", "Q", "", "T2")), /文本类型与题型必须相同/, "跨文本类型的层次递进");
  rejects(doc(BASE + rel("A1", "Y2", "progression"), NODES + ak("Y2", "A", "L2", "Q2")), /文本类型与题型必须相同/, "跨题型的层次递进");
  rejects(doc(BASE.replace(rel("A1", "A2", "progression"), rel("A2", "A1", "progression"))), /相邻两级/, "由高到低的层次递进");
  rejects(doc(BASE + rel("A3", "A1", "prerequisite", EXP)), /成环/, "强弱前置合在一起成环");
  rejects(doc(BASE + rel("A1", "A3", "prerequisite", EXP)), /传递冗余边/, "显式前置与层次递进链构成传递边");
  rejects(doc(BASE + rel("A1", "A2", "prerequisite", EXP)), /只能写一条/, "同一对能力点既是层次递进又是显式前置");
  rejects(doc(BASE, NODES + ak("A9", "B", "L2")), /孤点.*CHN\.SYN_T\.TP\.A9/, "没有边也没有投影的能力点");
  rejects(doc(BASE, NODES + mat("M9")), /孤点.*CHN\.SYN_T\.TP\.M9/, "没有边也没有投影的素材");
});

test("校验器拒绝：关联与易混的写法、uses_method 方向、边类型与端点", () => {
  rejects(doc(BASE + rel("M2", "M1", "confusable")), /重复/, "反向重复的易混边");
  rejects(doc(BASE + rel("M2", "M1", "related")), /重复/, "同一对节点既关联又易混");
  rejects(doc(BASE + rel("M2", "A3", "confusable")), /只连同类/, "素材与能力点之间的易混");
  rejects(doc(BASE + rel("H1", "B1", "uses_method")), /uses_method 的起点必须是能力点/, "uses_method 方向反了");
  rejects(doc(BASE + rel("A1", "M1", "uses_method")), /uses_method 的起点必须是能力点/, "uses_method 终点不是方法");
  rejects(doc(BASE + rel("M1", "H1", "uses_method")), /uses_method 的起点必须是能力点/, "uses_method 起点是素材");
  rejects(doc(BASE + rel("A1", "B1", "part_of")), /边类型只能取/, "不收 part_of");
  rejects(doc(BASE + rel("A1", "B1", "has_pitfall")), /边类型只能取/, "不收 has_pitfall");
  rejects(doc(BASE + "  - from: CHN.SYN_T.TP.A1\n    to: CHN.SYN_T.TP\n    type: related\n"), /应为 4 段/, "边的一端是主题");
  rejects(doc(BASE + rel("A1", "Z9", "related")), /两端都必须是本地图里的知识节点层节点/, "边的一端不存在");
  rejects(doc(BASE + rel("A1", "A1", "related")), /自己连自己/, "自环");
});

test("校验器拒绝：节点类型、能力点四要素、认知层次、ID 规范、原文与注释字段、合成标记、个人信息", () => {
  rejects(doc(BASE, NODES.replace("kind: material", "kind: kp")), /kind 只能取 ability_kp \/ material \/ method/, "语文不收知识点 kp");
  rejects(doc(BASE, NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        cognitive_levels: [know]\n")), /不带认知层次/, "素材带认知层次");
  rejects(doc(BASE, NODES.replace("        name: （合成）H1\n", "        name: （合成）H1\n        cognitive_levels: [apply]\n")), /不带认知层次/, "方法带认知层次");
  rejects(doc(BASE, NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        rung: L1\n")), /只用于能力点/, "素材写能力阶梯");
  rejects(doc(BASE, NODES.replace("        question_type: Q\n", "")), /能力点必须写 question_type/, "能力点缺题型");
  rejects(doc(BASE, NODES.replace("ability: B", "ability: C")), /能力 C 没有在 ladders 里定义/, "未定义的能力阶梯");
  rejects(doc(BASE, NODES.replace("ability: B\n        rung: L1", "ability: B\n        rung: L3")), /没有 L3 这一级/, "阶梯里没有这一级");
  rejects(doc(BASE, NODES.replace("text_type: T", "text_type: X")), /文本类型 X/, "未定义的文本类型");
  rejects(doc(BASE, NODES + ak("A9", "A", "L1")), /相同/, "能力 × 级 × 文本类型 × 题型重复");
  rejects(doc(BASE, NODES.replace("ability: A\n        rung: L1\n        text_type: T\n        question_type: Q", "ability: A\n        rung: L1\n        text_type: T\n        question_type: Q\n        cognitive_levels: [remember]")),
    /cognitive_levels 只能取/, "认知层次取值");
  rejects(doc(BASE).replace("CHN.SYN_T.TP.A1\n        kind", "chn.syn_t.tp.a1\n        kind"), /节点 ID 不符合规范/, "小写 ID");
  rejects(doc(BASE).replace("CHN.SYN_T.TP.A1\n        kind", "MATH.SYN_T.TP.A1\n        kind"), /必须以 CHN\. 开头/, "前缀不是 CHN");
  rejects(doc(BASE).replace("CHN.SYN_T.TP.A1\n        kind", "CHN.SYN_T.TP.A1.X\n        kind"), /应为 4 段/, "节点段数不对");
  rejects(doc(BASE).replace("CHN.SYN_T.TP.A1\n        kind", "CHN.SYN_T.TQ.A1\n        kind"), /必须挂在所在主题/, "节点不在所在主题下");
  rejects(doc(BASE, NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        quote_text: 占位\n")), /原文或注释字段/, "素材上的 quote_text");
  rejects(doc(BASE, NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        原文: 占位\n")), /原文或注释字段/, "素材上的原文");
  rejects(doc(BASE, NODES.replace("        name: （合成）M2\n", "        name: （合成）M2\n        annotation: 占位\n")), /原文或注释字段/, "素材上的教材注释");
  rejects(doc(BASE, NODES.replace("        name: （合成）M2\n", "        name: （合成）M2\n        注释: [占位]\n")), /原文或注释字段/, "中文键名的注释");
  rejects(doc(BASE, NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        projections:\n          - edition: syn-tb-2024\n"
    + "            edition_year: 2024\n            volume: a\n            chapter: b\n            page_from: 1\n            page_to: 1\n            excerpt: 占位\n")),
  /原文或注释字段/, "投影上的原文摘录");
  rejects(doc(BASE, NODES, `${HEAD}full_text: 占位\n`), /原文或注释字段/, "顶层的全文");
  rejects(doc(BASE, NODES.replace("name: （合成）M1", "name: 某篇目")), /必须带“合成”字样/, "合成样例里出现不带合成标记的名称");
  // 合成样例的每个自由文本字段都要带合成标记
  const freeText = (field: string, value: string) => NODES.replace("        name: （合成）M1\n", `        name: （合成）M1\n        ${field}:\n          - ${value}\n`);
  for (const f of ["includes", "excludes", "examples"]) {
    rejects(doc(BASE, freeText(f, "某义项")), /自由文本必须带“合成”字样/, `${f} 不带合成标记`);
  }
  const proj = (volume: string, chapter: string, printing = "") => NODES.replace("        name: （合成）M1\n", "        name: （合成）M1\n        projections:\n"
    + `          - edition: syn-tb-2024\n            edition_year: 2024\n${printing}            volume: ${volume}\n            chapter: ${chapter}\n            page_from: 1\n            page_to: 1\n`);
  assert.equal(build(doc(BASE, proj("（合成）册", "（合成）课"))).projections.length, 1, "带合成标记的投影可过");
  rejects(doc(BASE, proj("七年级上册", "（合成）课")), /volume：合成样例的自由文本/, "册不带合成标记");
  rejects(doc(BASE, proj("（合成）册", "第一课")), /chapter：合成样例的自由文本/, "课不带合成标记");
  rejects(doc(BASE, proj("（合成）册", "（合成）课", "            printing: 第1次印刷\n")), /printing：合成样例的自由文本/, "印次不带合成标记");
  rejects(doc(BASE + rel("A1", "B1", "prerequisite", "    explicit_prerequisite: true\n    basis: 教研依据\n")), /basis：合成样例的自由文本/, "依据不带合成标记");
  rejects(doc(BASE, NODES, HEAD.replace("synthetic: true", "synthetic: false").replace("合成、非教材内容：单测", "教研填写")),
    /真实内容（synthetic: false）在法务与语文教研确认前一律不收/, "真实内容在确认前一律拒绝");
  rejects(doc(BASE, NODES, HEAD.replace(/CHN\.SYN_T/g, "CHN.T")).replace(/CHN\.SYN_T/g, "CHN.T"), /第二段必须以 SYN 开头/, "合成样例的领域 ID");
  rejects(doc(BASE, NODES, HEAD.replace("合成、非教材内容：单测", "只是个样例")), /合成、非教材内容/, "合成样例没写明");
  rejects(doc(BASE, NODES, HEAD.replace("subject: wenli", "subject: zhixing")), /subject 必须是 wenli/, "学科不对");
  rejects(doc(BASE, NODES.replace("name: （合成）M1", "name: （合成）李华同学的积累")), /个人信息/, "个人信息");
  rejects(doc(BASE, NODES, HEAD, VOCAB.replace("rungs: [L1, L2]", "rungs: [L1]")), /2–8 级/, "能力阶梯只有一级");
});

test("跨文件：合成与真实不能混、领域不能重复、编码表定义必须一致、跨文件的边可以连", () => {
  const other = (head: string, vocab: string) => `${head}${vocab}topics:\n  - id: CHN.SYN_U.TP\n    name: （合成）主题\n    nodes:\n`
    + "      - id: CHN.SYN_U.TP.N1\n        kind: material\n        name: （合成）N1\n"
    + "relations:\n  - from: CHN.SYN_U.TP.N1\n    to: CHN.SYN_T.TP.M1\n    type: related\n";
  const uHead = HEAD.replace(/CHN\.SYN_T/g, "CHN.SYN_U");
  const ok = buildMap([parseDomain(doc(BASE)), parseDomain(other(uHead, ""))]);
  assert.ok(ok.edges.some((e) => e.from === "CHN.SYN_U.TP.N1" && e.to === "CHN.SYN_T.TP.M1"));
  assert.throws(() => buildMap([parseDomain(doc(BASE)), parseDomain(other(uHead, VOCAB.replace("（合成）能力甲", "（合成）能力丙")))]), /定义不一致/);
  assert.throws(() => buildMap([parseDomain(doc(BASE)), parseDomain(doc(BASE))]), /在两个文件里出现/);
  const real = other(uHead.replace("synthetic: true", "synthetic: false").replace("合成、非教材内容：单测", "教研填写"), "");
  assert.throws(() => parseDomain(real), /真实内容（synthetic: false）在法务与语文教研确认前一律不收/, "真实文件在解析时就被拒绝");
  // 防御：即便解析放开，合成与真实也不能进同一个版本
  assert.throws(() => buildMap([parseDomain(doc(BASE)), { ...parseDomain(other(uHead, "")), synthetic: false }]), /合成样例与真实内容/);
});

test("种子：两次生成逐字节相同、与文件顺序无关；挡板、角色、弱边、投影不带原文；报告口径", () => {
  const seed = (order: string[]) => renderMapSeed(buildMap(order.map((t) => parseDomain(t, t === READING ? "reading.yaml" : "accumulation.yaml"))), { version: "0.0.1" });
  const a = seed([READING, ACC]), b = seed([READING, ACC]), c = seed([ACC, READING]);
  assert.equal(a.sql, b.sql);
  assert.deepEqual(a.snapshot, c.snapshot, "节点、边、投影按码点排序，与文件顺序无关");
  assert.equal(a.sql.split("\n").slice(1).join("\n"), c.sql.split("\n").slice(1).join("\n"), "只有首行注释里的文件名顺序不同");
  assert.equal(a.mapVersionId, mapVersionIdOf("0.0.1"));
  assert.match(a.sql, /^SET LOCAL ROLE starbuddy_kge;/m);
  assert.match(a.sql, /starbuddy\.allow_synthetic/);
  assert.match(a.sql, /INSERT INTO kge\.map_version \(id, subject, standard_year, version\) VALUES \('[0-9a-f-]{36}', 'wenli', 2022, '0\.0\.1'\);/);
  assert.equal((a.sql.match(/, 'prerequisite', 'weak', 'chinese:progression:/g) ?? []).length, 3);
  assert.equal((a.sql.match(/, 'prerequisite', 'strong', 'explicit:/g) ?? []).length, 1);
  const projLines = a.sql.split("\n").filter((l) => l.includes("'syn-tb-2024'"));
  assert.equal(projLines.length, 7);
  assert.ok(projLines.every((l) => /, NULL, 'unverified'\)[,;]$/.test(l)), "quote_text 为 NULL、mapping_state 为 unverified");
  for (const l of a.sql.split("\n").filter((x) => x.includes("'prerequisite'"))) assert.doesNotMatch(l, /SYN_ACC/, "素材不进前置图");
  assert.equal(a.snapshot.format, SNAPSHOT_FORMAT);
  assert.equal(a.snapshot.edges.filter((e) => e.strength === "weak").length, 3);
  assert.deepEqual(a.report.expected_missing_boundary, []);
  assert.deepEqual(a.report.non_required_scope, ["CHN.SYN_ACC.SYN_LOCAL.LOCAL_A"]);
  assert.equal(a.report.synthetic, true);
  // 种子生成器本身对非合成地图不加挡板（真实内容放开后才会走到；现在解析阶段就拒绝）
  const real = renderMapSeed({ ...buildMap([parseDomain(READING), parseDomain(ACC)]), synthetic: false }, { version: "1.0.0" });
  assert.doesNotMatch(real.sql, /allow_synthetic/);
  assert.throws(() => renderMapSeed(buildMap([parseDomain(READING), parseDomain(ACC)]), { version: "1.0" }), /主\.次\.修订/);
  assert.throws(() => renderMapSeed(buildMap([parseDomain(READING), parseDomain(ACC)]), { version: "1.0.0", standardYear: 1999 }), /课标年份/);
});

test("命令行：map 生成三个文件、两次逐字节相同；参数不对退出 2，校验失败退出 1", () => {
  const out = mkdtempSync(join(tmpdir(), "kge-chn-"));
  const [log, err] = [console.log, console.error];
  console.log = () => {};
  console.error = () => {};
  try {
    const args = (o: string) => ["map", "--domain", `${TOOL}samples/chinese/reading.yaml`, "--domain", `${TOOL}samples/chinese/accumulation.yaml`,
      "--version", "0.0.1", "--out", join(out, o)];
    assert.equal(main(args("a")), 0);
    assert.equal(main(args("b")), 0);
    for (const f of ["import.sql", "import-report.json", "snapshot.json"]) {
      assert.equal(readFileSync(join(out, "a", f), "utf8"), readFileSync(join(out, "b", f), "utf8"), f);
    }
    assert.equal(main(["map", "--version", "0.0.1", "--out", out]), 2);
    assert.equal(main(["map", "--domain", `${TOOL}samples/chinese/reading.yaml`, "--out", out]), 2);
    assert.equal(main(["bogus"]), 2);
    assert.equal(main(["map", "--bogus"]), 2);
    // 只给阅读领域：跨文件的边指向积累领域的素材，校验失败
    assert.equal(main(["map", "--domain", `${TOOL}samples/chinese/reading.yaml`, "--version", "0.0.1", "--out", join(out, "c")]), 1);
    assert.equal(main(["map", "--domain", join(out, "missing.yaml"), "--version", "0.0.1", "--out", join(out, "d")]), 1);
  } finally {
    console.log = log;
    console.error = err;
    rmSync(out, { recursive: true, force: true });
  }
});
