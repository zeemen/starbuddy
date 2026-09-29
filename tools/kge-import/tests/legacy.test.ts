// 需要本机 legacy/aya 只读副本的检查（CI 没有 legacy 时打印“跳过”）：
//   - 重新生成与仓库里的 db/seeds/kge/aya-items-<版本>/ 逐字节相同（种子过期即失败）；
//   - 同一输入两次运行逐字节相同；
//   - 自然语言引用正好是 KGE-00 统计的 97 处；六段推不出的正好是 28 道压轴专项题。
// 另有不读 legacy 的不可信输入核对（checkBank）。
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { checkBank, LegacyDataError } from "../src/legacy-items.ts";
import { main } from "../src/main.ts";
import { synthBank } from "./fixtures.ts";

const REPO = fileURLToPath(new URL("../../../", import.meta.url));
const AYA = process.env.KGE_IMPORT_AYA ?? join(REPO, "legacy/aya");
const SEED = join(REPO, "db/seeds/kge/aya-items-0.1.0");
const MAP = join(REPO, "db/seeds/kge/aya-fun-0.1.0");
const FILES = ["items.sql", "worklist.md", "tagging-report.json"];

test("legacy 输出按不可信数据核对", () => {
  const ok = synthBank();
  assert.equal(checkBank(ok).questions.length, ok.questions.length);
  const bad = (f: (b: ReturnType<typeof synthBank>) => void) => { const b = synthBank(); f(b); return b; };
  assert.throws(() => checkBank(bad((b) => { b.questions[0]!.id = "Q 1;DROP"; })), LegacyDataError);
  assert.throws(() => checkBank(bad((b) => { b.questions[0]!.topic = "zzz"; })), LegacyDataError);
  assert.throws(() => checkBank(bad((b) => { b.questions[0]!.mainNodes = [1 as unknown as string]; })), LegacyDataError);
  assert.throws(() => checkBank(bad((b) => { b.topics[0]!.name = "a\u0000"; })), LegacyDataError);
  assert.throws(() => checkBank({ topics: [], questions: [], sprintCategories: [] }), LegacyDataError);
});

test("真实 legacy：重新生成与仓库种子逐字节相同，两次运行相同", { skip: existsSync(join(AYA, "content/topics.ts")) ? false : "跳过（没有 legacy/aya 只读副本）" }, async () => {
  const dirs = [mkdtempSync(join(tmpdir(), "kge-items-")), mkdtempSync(join(tmpdir(), "kge-items-"))];
  try {
    for (const d of dirs) assert.equal(await main(["--aya", AYA, "--map", MAP, "--out", d]), 0);
    for (const f of FILES) {
      const a = readFileSync(join(dirs[0]!, f));
      assert.ok(a.equals(readFileSync(join(dirs[1]!, f))), `${f} 两次运行不同`);
      assert.ok(existsSync(join(SEED, f)), `仓库里缺种子 ${f}（运行 README 里的生成命令）`);
      assert.ok(a.equals(readFileSync(join(SEED, f))), `${f} 与仓库种子不同（种子过期，重新生成）`);
    }
    const rep = JSON.parse(readFileSync(join(dirs[0]!, "tagging-report.json"), "utf8"));
    assert.equal(rep.text_refs.total, 97, "KGE-00：97 处自然语言引用");
    assert.equal(rep.counts.items_total, 721);
    assert.equal(rep.counts.six_segments_missing, 28);
    assert.equal(rep.counts.six_segments_derived, 693);
  } finally {
    for (const d of dirs) rmSync(d, { recursive: true, force: true });
  }
});
