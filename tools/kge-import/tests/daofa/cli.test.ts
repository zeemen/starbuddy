// 冲刺 4-K 道法导入器命令行：产物齐全且两次逐字节相同；参数错误退出 2；校验失败、判定不通过、内容包与原子层不一致退出 1。
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { main } from "../../src/daofa/main.ts";
import { atomsFromPack, EXPORT_FORMAT } from "../../src/daofa/rubric.ts";

const TOOL = fileURLToPath(new URL("../../", import.meta.url));
const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const S = `${TOOL}samples/daofa/`;
const PACK = `${REPO}adapters/zhixing/content/pack.json`;

function quiet<T>(f: () => T): T {
  const [log, err] = [console.log, console.error];
  console.log = () => {};
  console.error = () => {};
  try {
    return f();
  } finally {
    console.log = log;
    console.error = err;
  }
}

test("命令行：map / rubric / affairs / snapshot / page-sample 产物齐全、确定性；退出码", () => {
  const dir = mkdtempSync(join(tmpdir(), "kge-daofa-cli-"));
  try {
    const map = (out: string) => quiet(() => main(["map", "--domain", `${S}law.yaml`, "--domain", `${S}pack-nodes.yaml`, "--version", "0.0.1", "--out", out]));
    assert.equal(map(join(dir, "a")), 0);
    assert.equal(map(join(dir, "b")), 0);
    for (const f of ["import.sql", "import-report.json", "snapshot.json"]) {
      assert.equal(readFileSync(join(dir, "a", f), "utf8"), readFileSync(join(dir, "b", f), "utf8"), `${f} 两次逐字节相同`);
    }
    const m = join(dir, "a");
    assert.equal(quiet(() => main(["rubric", "--pack", PACK, "--map", m, "--out", m])), 0);
    const aff = ["--entries", `${S}current-affairs.yaml`, "--source-domains", `${S}source-domains.txt`, "--map", m];
    assert.equal(quiet(() => main(["affairs", ...aff, "--out", m])), 0);
    assert.equal(quiet(() => main(["affairs-expiring", ...aff, "--today", "2026-09-30", "--out", join(dir, "exp.md")])), 0);
    assert.match(readFileSync(join(dir, "exp.md"), "utf8"), /已过期 1 条，即将到期 1 条/);
    assert.equal(quiet(() => main(["snapshot", "--map", m, "--pack", PACK, ...aff.slice(0, 4), "--out", join(dir, "snap.json")])), 0);
    const snap = JSON.parse(readFileSync(join(dir, "snap.json"), "utf8")) as { scoring_points: unknown[]; current_affairs: unknown[] };
    assert.equal(snap.scoring_points.length, 10);
    assert.equal(snap.current_affairs.length, 3);
    assert.equal(quiet(() => main(["page-sample", "--map", m, "--out", join(dir, "c.csv")])), 0);

    // 参数错误 → 2；校验失败 → 1
    assert.equal(quiet(() => main(["map", "--version", "0.0.1", "--out", dir])), 2);
    assert.equal(quiet(() => main(["nope"])), 2);
    assert.equal(quiet(() => main(["map", "--bogus"])), 2);
    writeFileSync(join(dir, "bad.yaml"), readFileSync(`${S}law.yaml`, "utf8").replace("    legacy_code: syn1\n", "    legacy_code: syn1\n    quote_text: 占位\n"));
    assert.equal(quiet(() => main(["map", "--domain", join(dir, "bad.yaml"), "--version", "0.0.1", "--out", join(dir, "x")])), 1);
    // 一致性检查：一致 0，不一致 1
    const atoms = atomsFromPack(JSON.parse(readFileSync(PACK, "utf8")));
    writeFileSync(join(dir, "exp.json"), JSON.stringify({ format: EXPORT_FORMAT, subject: "zhixing", points: atoms }));
    assert.equal(quiet(() => main(["rubric-check", "--pack", PACK, "--export", join(dir, "exp.json")])), 0);
    atoms[3]!.score += 1;
    writeFileSync(join(dir, "exp2.json"), JSON.stringify({ format: EXPORT_FORMAT, subject: "zhixing", points: atoms }));
    assert.equal(quiet(() => main(["rubric-check", "--pack", PACK, "--export", join(dir, "exp2.json")])), 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
