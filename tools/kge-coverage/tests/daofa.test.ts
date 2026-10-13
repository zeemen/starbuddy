// 道法覆盖报表单测（冲刺 4-K，spec 设计要点 6）。夹具 tests/fixtures/daofa-snapshot.json 全部合成。手算：
//   有效观点 3 个（V3 已停用，连同它的投影 P5、采分点 SYN-I3/p1 都不计）：W1、V1、V2（码点序 MORAL.ECO… < MORAL.LAW…）
//   W1：apply、synthesize；投影 P4 已核对 → 1/0/0；采分点 SYN-I1/p2、SYN-I2/p1 → 2，题目 2；时政 c3（pending，未下架）→ 1
//   V1：know、understand；P1 已核对、P2 未核对 → 1/1/0；采分点 SYN-I1/p1、p2 → 2，题目 1；时政 c1（c2 已下架不计）→ 1
//   V2：无层次；P3 有争议 → 0/0/1；采分点 0、题目 0、时政 0
//   层次合计：know 1、understand 1、apply 1、synthesize 1；投影合计 2/1/1（共 4）；采分点 3、题目 2、时政 2；关联 2、显式前置 1
//   MORAL.LAW 缺口：BC2 没有观点；V2 缺认知层次、没有已核对的教材投影、没有采分点、没有题目（共 5 条）
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { checkSnapshot, computeDaofaCoverage, DaofaCoverageError, toDaofaCsv, toDaofaMarkdown } from "../src/daofa.ts";
import { main } from "../src/main.ts";

const FIX = fileURLToPath(new URL("./fixtures/daofa-snapshot.json", import.meta.url));
const load = () => checkSnapshot(JSON.parse(readFileSync(FIX, "utf8")));

test("道法：观点 × 认知层次、投影已核对 / 未核对 / 有争议、采分点、题目、时政与手算一致", () => {
  const r = computeDaofaCoverage(load());
  assert.deepEqual(r.rows.map((x) => [x.id, Object.values(x.levels).join(""), `${x.verified}/${x.unverified}/${x.disputed}`, x.scoringPoints, x.items, x.affairs]), [
    ["MORAL.ECO.T1.W1", "0011", "1/0/0", 2, 2, 1],
    ["MORAL.LAW.BC1.V1", "1100", "1/1/0", 2, 1, 1],
    ["MORAL.LAW.BC1.V2", "0000", "0/0/1", 0, 0, 0],
  ]);
  assert.deepEqual(r.levelTotals, { know: 1, understand: 1, apply: 1, synthesize: 1 });
  assert.deepEqual(r.totals, { viewpoints: 3, bigConcepts: 3, projections: 4, verified: 2, unverified: 1, disputed: 1, scoringPoints: 3, items: 2,
    affairs: 2, relatedEdges: 2, prerequisiteEdges: 1 });
  assert.deepEqual(r.gaps, [
    { id: "MORAL.LAW.BC2", issue: "大概念下没有观点（待观点下钻）" },
    { id: "MORAL.LAW.BC1.V2", issue: "缺认知层次" },
    { id: "MORAL.LAW.BC1.V2", issue: "没有已核对的教材投影" },
    { id: "MORAL.LAW.BC1.V2", issue: "没有采分点" },
    { id: "MORAL.LAW.BC1.V2", issue: "没有题目" },
  ]);
  assert.deepEqual(computeDaofaCoverage(load(), "MORAL.ECO").gaps, []);
  assert.deepEqual(computeDaofaCoverage(load(), "MORAL.HIST").gaps, [{ id: "MORAL.HIST", issue: "地图里没有这个领域" }]);
});

test("道法：csv 与 md 确定性输出，单列读本已排除，只含汇总与 ID", () => {
  const r = computeDaofaCoverage(load());
  assert.equal(toDaofaCsv(r), [
    "node_id,big_concept,know,understand,apply,synthesize,projections_verified,projections_unverified,projections_disputed,scoring_points,items,current_affairs",
    "MORAL.ECO.T1.W1,MORAL.ECO.T1,0,0,1,1,1,0,0,2,2,1",
    "MORAL.LAW.BC1.V1,MORAL.LAW.BC1,1,1,0,0,1,1,0,2,1,1",
    "MORAL.LAW.BC1.V2,MORAL.LAW.BC1,0,0,0,0,0,0,1,0,0,0",
    "",
  ].join("\n"));
  const md = toDaofaMarkdown(r);
  assert.equal(md, toDaofaMarkdown(computeDaofaCoverage(load())));
  assert.match(md, /读本内容：已排除/);
  assert.match(md, /教材投影 4 条：已核对 2（50\.0%），未核对 1，有争议 1/);
  assert.match(md, /\| 合计 \| \| 1 \| 1 \| 1 \| 1 \| 2 \/ 1 \/ 1 \| 3 \| 2 \| 2 \|/);
  assert.match(md, /## 3\. MORAL\.LAW 领域缺口清单/);
  assert.ok(!md.includes("（合成）观点甲一"), "报表只含 ID，不含观点名称");
});

test("道法：快照按不可信输入核对；命令行参数与退出码", () => {
  const raw = JSON.parse(readFileSync(FIX, "utf8"));
  assert.throws(() => checkSnapshot({ ...raw, format: "x" }), DaofaCoverageError);
  assert.throws(() => checkSnapshot({ ...raw, subject: "aya" }), /学科/);
  assert.throws(() => checkSnapshot({ ...raw, nodes: [...raw.nodes, { ...raw.nodes[0], id: "MATH.A.B.C" }] }), /节点 ID 不合法/);
  assert.throws(() => checkSnapshot({ ...raw, nodes: [...raw.nodes, raw.nodes[0]] }), /重复/);
  assert.throws(() => checkSnapshot({ ...raw, projections: [{ ...raw.projections[0], node_id: "MORAL.X.Y.Z" }] }), /不存在的节点/);
  assert.throws(() => checkSnapshot({ ...raw, projections: [{ ...raw.projections[0], mapping_state: "ok" }] }), /核对状态/);
  const out = mkdtempSync(join(tmpdir(), "kge-cov-daofa-"));
  const [log, err] = [console.log, console.error];
  console.log = () => {};
  console.error = () => {};
  try {
    assert.equal(main(["--subject", "zhixing", "--snapshot", FIX, "--out", out]), 0);
    assert.equal(readFileSync(join(out, "coverage-zhixing-9.9.9.csv"), "utf8"), toDaofaCsv(computeDaofaCoverage(load())));
    assert.equal(main(["--subject", "zhixing", "--out", out]), 2);
    assert.equal(main(["--subject", "zhixing", "--snapshot", FIX, "--focus-domain", "LAW", "--out", out]), 2);
    assert.equal(main(["--subject", "nosuch", "--snapshot", FIX]), 2);
    // KGE-07 起 wenli 是语文覆盖报表：喂道法快照按格式不对退出 1
    assert.equal(main(["--subject", "wenli", "--snapshot", FIX, "--out", out]), 1);
    assert.equal(main(["--subject", "zhixing", "--snapshot", join(out, "missing.json"), "--out", out]), 1);
  } finally {
    console.log = log;
    console.error = err;
    rmSync(out, { recursive: true, force: true });
  }
});
