// 语文覆盖报表单测（KGE-07 第一步，specs/kge07-chinese-slice 范围 2）。夹具 tests/fixtures/chinese-snapshot.json 全部合成。手算：
//   有效的知识节点层节点 10 个（OLD 已停用：连同它的投影 P4、指向它的弱边都不计），码点序 H1 H2 K1 K2 K3 M1 M2 M3 X1 MB1
//   K1：understand；递进 0/1；用方法 1     K2：understand、apply；递进 1/0（K2→OLD 不计）；强前置 0/1
//   K3：无层次；强前置 1/0；关联 1          M1：投影 1/1/0；关联 2（K3、MB1）；易混 1     M2：投影 0/1/0；易混 1
//   M3：无投影     X1：考情拓展（不计入必修）     H1：被 K1 使用     H2：没有被使用     MB1：投影 1/0/0；关联 1
//   主题 CHN.SYN_A.T1：能力点 3、素材 4、方法 2，understand 2、apply 1；T2 空、T3 只有停用节点；CHN.SYN_B.T1：素材 1
//   合计：投影 4（已核对 2、未核对 2）；递进 1、强前置 1、关联 2、易混 1、用方法 1；必修 9 个，有已核对投影的 2 个（22.2%）
//   缺口（CHN，只看必修）：T2、T3 没有知识节点；H2 没被使用；K3 缺认知层次、不在递进上；M2 出处都未核对；M3 没有出处（共 7 条）
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { checkChineseSnapshot, ChineseCoverageError, computeChineseCoverage, toChineseCsv, toChineseMarkdown } from "../src/chinese.ts";
import { main } from "../src/main.ts";

const FIX = fileURLToPath(new URL("./fixtures/chinese-snapshot.json", import.meta.url));
const load = () => checkChineseSnapshot(JSON.parse(readFileSync(FIX, "utf8")));

const quiet = <T>(f: () => T): T => {
  const [log, err] = [console.log, console.error];
  console.log = () => {};
  console.error = () => {};
  try {
    return f();
  } finally {
    console.log = log;
    console.error = err;
  }
};

test("语文：节点矩阵、主题矩阵、合计与手算一致", () => {
  const r = computeChineseCoverage(load());
  assert.deepEqual(r.rows.map((x) => [x.id.slice(4), x.kind, Object.values(x.levels).join(""), `${x.verified}/${x.unverified}/${x.disputed}`,
    `${x.progressionIn}/${x.progressionOut}`, `${x.prerequisiteIn}/${x.prerequisiteOut}`, x.related, x.confusable, x.usesMethod]), [
    ["SYN_A.T1.H1", "method", "0000", "0/0/0", "0/0", "0/0", 0, 0, 1],
    ["SYN_A.T1.H2", "method", "0000", "0/0/0", "0/0", "0/0", 0, 0, 0],
    ["SYN_A.T1.K1", "ability_kp", "0100", "0/0/0", "0/1", "0/0", 0, 0, 1],
    ["SYN_A.T1.K2", "ability_kp", "0110", "0/0/0", "1/0", "0/1", 0, 0, 0],
    ["SYN_A.T1.K3", "ability_kp", "0000", "0/0/0", "0/0", "1/0", 1, 0, 0],
    ["SYN_A.T1.M1", "material", "0000", "1/1/0", "0/0", "0/0", 2, 1, 0],
    ["SYN_A.T1.M2", "material", "0000", "0/1/0", "0/0", "0/0", 0, 1, 0],
    ["SYN_A.T1.M3", "material", "0000", "0/0/0", "0/0", "0/0", 0, 0, 0],
    ["SYN_A.T1.X1", "material", "0000", "0/0/0", "0/0", "0/0", 0, 0, 0],
    ["SYN_B.T1.MB1", "material", "0000", "1/0/0", "0/0", "0/0", 1, 0, 0],
  ]);
  assert.deepEqual(r.topics.map((t) => [t.id, t.abilityKps, t.materials, t.methods, Object.values(t.levels).join("")]), [
    ["CHN.SYN_A.T1", 3, 4, 2, "0210"], ["CHN.SYN_A.T2", 0, 0, 0, "0000"], ["CHN.SYN_A.T3", 0, 0, 0, "0000"], ["CHN.SYN_B.T1", 0, 1, 0, "0000"],
  ]);
  assert.deepEqual(r.totals, { abilityKps: 3, materials: 5, methods: 2, topics: 4, projections: 4, verified: 2, unverified: 2, disputed: 0,
    progression: 1, strongPrerequisite: 1, related: 2, confusable: 1, usesMethod: 1, required: 9, requiredVerified: 2 });
  assert.deepEqual(r.nonRequired, [{ id: "CHN.SYN_A.T1.X1", scope: "exam_ext" }]);
  assert.deepEqual(r.gaps, [
    { id: "CHN.SYN_A.T2", issue: "主题下没有知识节点" },
    { id: "CHN.SYN_A.T3", issue: "主题下没有知识节点" },
    { id: "CHN.SYN_A.T1.H2", issue: "方法没有被任何能力点使用" },
    { id: "CHN.SYN_A.T1.K3", issue: "能力点缺认知层次" },
    { id: "CHN.SYN_A.T1.K3", issue: "能力点不在任何层次递进上" },
    { id: "CHN.SYN_A.T1.M2", issue: "素材的教材出处都未核对" },
    { id: "CHN.SYN_A.T1.M3", issue: "素材没有教材出处（册-课-页）" },
  ]);
  assert.deepEqual(computeChineseCoverage(load(), "CHN.SYN_B").gaps, []);
  assert.deepEqual(computeChineseCoverage(load(), "CHN.SYN_Z").gaps, [{ id: "CHN.SYN_Z", issue: "地图里没有这个领域" }]);
});

test("语文：csv 与 md 确定性输出，不计入必修的单列，只含汇总与 ID", () => {
  const r = computeChineseCoverage(load());
  const csv = toChineseCsv(r).split("\n");
  assert.equal(csv[0], "node_id,topic,kind,scope,know,understand,apply,synthesize,projections_verified,projections_unverified,projections_disputed,"
    + "progression_in,progression_out,prerequisite_in,prerequisite_out,related,confusable,uses_method");
  assert.equal(csv[4], "CHN.SYN_A.T1.K2,CHN.SYN_A.T1,ability_kp,required,0,1,1,0,0,0,0,1,0,0,1,0,0,0");
  assert.equal(csv[9], "CHN.SYN_A.T1.X1,CHN.SYN_A.T1,material,exam_ext,0,0,0,0,0,0,0,0,0,0,0,0,0,0");
  assert.equal(csv.length, 12);
  const md = toChineseMarkdown(r);
  assert.equal(md, toChineseMarkdown(computeChineseCoverage(load())));
  assert.match(md, /不计入必修覆盖：CHN\.SYN_A\.T1\.X1（考情拓展）/);
  assert.match(md, /必修节点 9 个，有已核对教材投影的 2 个（22\.2%）/);
  assert.match(md, /层次递进边（prerequisite \+ weak）1 条，显式强前置 1 条/);
  assert.match(md, /\| 合计 \| 3 \| 5 \| 2 \| 0 \| 2 \| 1 \| 0 \|/);
  assert.match(md, /## 4\. CHN 缺口清单/);
  assert.ok(!md.includes("（合成）素材一"), "报表只含 ID，不含节点名称");
});

test("语文：快照按不可信输入核对；命令行参数与退出码", () => {
  const raw = JSON.parse(readFileSync(FIX, "utf8"));
  const bad = (patch: Record<string, unknown>, re: RegExp) => assert.throws(() => checkChineseSnapshot({ ...raw, ...patch }), re);
  assert.throws(() => checkChineseSnapshot({ ...raw, format: "kge-daofa-snapshot/1" }), ChineseCoverageError);
  bad({ subject: "zhixing" }, /学科/);
  bad({ nodes: [...raw.nodes, { ...raw.nodes[0], id: "MORAL.A.B.C" }] }, /节点 ID 不合法/);
  bad({ nodes: [...raw.nodes, raw.nodes[0]] }, /重复/);
  bad({ nodes: [{ ...raw.nodes[0], scope: "core" }, ...raw.nodes.slice(1)] }, /范围属性/);
  bad({ nodes: [{ ...raw.nodes[0], cognitive_levels: ["remember"] }, ...raw.nodes.slice(1)] }, /认知层次/);
  bad({ edges: [{ ...raw.edges[0], to_id: "CHN.X.Y.Z" }] }, /不存在的节点/);
  bad({ edges: [{ ...raw.edges[0], strength: "medium" }] }, /强弱/);
  bad({ edges: [{ ...raw.edges[0], type: "related", strength: "weak" }] }, /弱边只能是 prerequisite/);
  bad({ edges: [{ ...raw.edges[0], type: "confusable", strength: "weak" }] }, /弱边只能是 prerequisite/);
  bad({ projections: [{ ...raw.projections[0], node_id: "CHN.X.Y.Z" }] }, /不存在的节点/);
  bad({ projections: [{ ...raw.projections[0], mapping_state: "ok" }] }, /核对状态/);
  const out = mkdtempSync(join(tmpdir(), "kge-cov-chn-"));
  try {
    quiet(() => {
      assert.equal(main(["--subject", "wenli", "--snapshot", FIX, "--out", out]), 0);
      assert.equal(readFileSync(join(out, "coverage-wenli-9.9.9.csv"), "utf8"), toChineseCsv(computeChineseCoverage(load())));
      assert.equal(readFileSync(join(out, "coverage-wenli-9.9.9.md"), "utf8"), toChineseMarkdown(computeChineseCoverage(load())));
      assert.equal(main(["--subject", "wenli", "--out", out]), 2);
      assert.equal(main(["--subject", "wenli", "--snapshot", FIX, "--focus-domain", "MORAL.LAW", "--out", out]), 2);
      assert.equal(main(["--subject", "wenli", "--snapshot", FIX, "--focus-domain", "CHN.SYN_B", "--out", join(out, "b")]), 0);
      assert.equal(main(["--subject", "wenli", "--snapshot", join(out, "missing.json"), "--out", out]), 1);
      assert.equal(main(["--subject", "zhixing", "--snapshot", FIX, "--out", out]), 1, "道法分支不收语文快照");
    });
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("库上快照导出只读：chinese-snapshot.sql 以 BEGIN READ ONLY 开事务、以 ROLLBACK 结束，不含写语句", () => {
  const sql = readFileSync(fileURLToPath(new URL("../sql/chinese-snapshot.sql", import.meta.url)), "utf8");
  const code = sql.split("\n").filter((l) => !l.startsWith("--") && l.trim() !== "");
  assert.ok(code.includes("BEGIN READ ONLY;"), "要有 BEGIN READ ONLY");
  assert.equal(code[code.length - 1], "ROLLBACK;", "最后一条语句是 ROLLBACK");
  assert.ok(!code.some((l) => /\bCOMMIT\b/i.test(l)), "不提交");
  assert.ok(!code.some((l) => /\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE|GRANT)\b/i.test(l)), "不含写语句");
});

// 回归：KGE-07 改动 main.ts 前，用同一批夹具生成的数学、道法报表的 sha256（逐字节不变）
const BEFORE: Record<string, string> = {
  "aya-all/coverage-9.9.9-syn.csv": "536afeff1aa3b962c12b20f9569d19374517a46237583855dc1d3f93e4dbbb73",
  "aya-all/coverage-9.9.9-syn.md": "6d1173ed623df06a3bdbb004a5db36d34c26cf41d61ff5606a79a83b7692ed4f",
  "aya-tag/coverage-9.9.9-syn.csv": "536afeff1aa3b962c12b20f9569d19374517a46237583855dc1d3f93e4dbbb73",
  "aya-tag/coverage-9.9.9-syn.md": "9931741dcdf15cab6712fe5f25fa5766778ca03fd74bb8e210bcb7c23316d811",
  "zx/coverage-zhixing-9.9.9.csv": "90146bfe6347f905fd88b09d0cb0f085979beb20624d0e160793ee14d165cf5d",
  "zx/coverage-zhixing-9.9.9.md": "6851f634444cbab4a3f4a702065b12e7ed8c7ac217457801f1c53e4393d51977",
};

test("回归：数学（两种范围）与道法报表与 KGE-07 之前逐字节相同", () => {
  const fx = (p: string) => fileURLToPath(new URL(`./fixtures/${p}`, import.meta.url));
  const out = mkdtempSync(join(tmpdir(), "kge-cov-reg-"));
  try {
    quiet(() => {
      assert.equal(main(["--map", fx("map"), "--tagging", fx("tagging-report.json"), "--out", join(out, "aya-all")]), 0);
      assert.equal(main(["--map", fx("map"), "--tagging", fx("tagging-report.json"), "--scope", "item_tag", "--out", join(out, "aya-tag")]), 0);
      assert.equal(main(["--subject", "zhixing", "--snapshot", fx("daofa-snapshot.json"), "--out", join(out, "zx")]), 0);
    });
    for (const [rel, hash] of Object.entries(BEFORE)) {
      assert.equal(createHash("sha256").update(readFileSync(join(out, rel))).digest("hex"), hash, rel);
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});
