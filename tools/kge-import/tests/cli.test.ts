// 命令行：启用假层（--vector / --model）时必须显式给 --out，且不能写进仓库 db/seeds/；产物标注“含假实现，不得执行”。
// 不读 legacy：输出目录核对在加载任何输入之前，--aya / --map 给不存在的路径也能测到。
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { buildItems } from "../src/items/build.ts";
import { checkFakeOut, main } from "../src/main.ts";
import { FakeTagModel } from "../src/tagging/model.ts";
import { makePipeline } from "../src/tagging/pipeline.ts";
import { buildRuleContext } from "../src/tagging/rules.ts";
import { FakeNgramEmbedder } from "../src/tagging/vector.ts";
import { synthBank, synthMap, synthSegments } from "./fixtures.ts";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));

async function quiet<T>(f: () => Promise<T>): Promise<{ value: T; err: string }> {
  const orig = console.error;
  let err = "";
  console.error = (...a: unknown[]) => { err += `${a.join(" ")}\n`; };
  try {
    return { value: await f(), err };
  } finally {
    console.error = orig;
  }
}

test("假层没有 --out、--out 在 db/seeds/ 之下（含符号链接绕进去）：报错退出 2，不写任何文件", async () => {
  const none = ["--aya", "/nonexistent/aya", "--map", "/nonexistent/map"];
  for (const flags of [["--vector"], ["--model"], ["--vector", "--model"]]) {
    const r = await quiet(() => main([...none, ...flags]));
    assert.equal(r.value, 2, flags.join(" "));
    assert.match(r.err, /必须用 --out 显式指定/);
    for (const out of [join(REPO, "db/seeds/kge/aya-items-0.1.0"), join(REPO, "db/seeds"), join(REPO, "db/seeds/kge/new-dir/x")]) {
      const s = await quiet(() => main([...none, ...flags, "--out", out]));
      assert.equal(s.value, 2, out);
      assert.match(s.err, /拒绝把含假实现的产物写进种子目录/);
    }
  }
  const tmp = mkdtempSync(join(tmpdir(), "kge-cli-"));
  try {
    const link = join(tmp, "seeds-link");
    symlinkSync(join(REPO, "db/seeds"), link);
    assert.match(checkFakeOut(join(link, "kge/fake"), ["--vector"]) ?? "", /拒绝/, "符号链接指向种子目录也拒绝");
    assert.equal(checkFakeOut(join(tmp, "out"), ["--vector"]), null, "仓库 db/seeds/ 之外可以");
    assert.equal(checkFakeOut(join(REPO, "db/seeds-other"), ["--model"]), null, "只挡 db/seeds/ 本身，不挡同名前缀");
    mkdirSync(join(tmp, "repo/db/seeds"), { recursive: true });
    assert.match(checkFakeOut(join(tmp, "repo/db/seeds/x"), ["--model"], join(tmp, "repo")) ?? "", /拒绝/);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
  assert.equal(checkFakeOut(undefined, []), null, "只跑规则层时不限制（默认写种子目录）");
});

test("含假实现的产物：SQL 头部、报告、工作单都有醒目标注；SQL 在事务里第一句就报错；只跑规则层没有标注", async () => {
  const map = synthMap();
  const bank = synthBank();
  const build = async (opts: Parameters<typeof makePipeline>[2]) => buildItems({
    bank, map, segments: synthSegments(), registeredItems: new Set(bank.questions.map((x) => x.id)),
    pipeline: await makePipeline(map, buildRuleContext(map, bank.topics), opts) });
  for (const [opts, layers, name] of [
    [{ embedder: new FakeNgramEmbedder() }, ["vector"], "向量层"],
    [{ model: new FakeTagModel() }, ["model"], "模型层"],
    [{ embedder: new FakeNgramEmbedder(), model: new FakeTagModel() }, ["vector", "model"], "向量层、模型层"],
  ] as const) {
    const r = await build(opts);
    assert.ok(r.report.warning?.startsWith(`含假实现，不得执行：${name}用的是假实现`), r.report.warning);
    assert.deepEqual(r.report.run.fake_layers, layers);
    const lines = r.sql.split("\n");
    assert.ok(lines[1]!.startsWith("-- !!! 含假实现，不得执行"), "SQL 第二行就是标注");
    const begin = lines.indexOf("BEGIN;");
    assert.match(lines[begin + 1]!, /^DO \$guard\$ BEGIN RAISE EXCEPTION '%', 'items\.sql 含假实现，不得执行：.*'; END \$guard\$;$/);
    assert.ok(r.reportJson.startsWith(`{\n  "warning": "含假实现，不得执行`), "报告第一个字段就是标注");
    assert.ok(r.worklistMd.includes(`> **${r.report.warning}**`));
  }
  const clean = await build({});
  assert.equal(clean.report.warning, undefined);
  assert.deepEqual(clean.report.run.fake_layers, []);
  for (const out of [clean.sql, clean.reportJson, clean.worklistMd]) assert.equal(out.includes("含假实现"), false);
  assert.equal(clean.sql.includes("RAISE EXCEPTION"), false);
});
