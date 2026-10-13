// KGE-08：命令行——退出码、--out 守卫（不存在或空目录、拒绝符号链接与文件、不得在仓库或 --aya 之下）、写盘（wx + 改名、不覆盖）、
// 逐字节一致。全部合成数据；临时目录都在系统临时目录里。
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { DEFAULT_CONFIG } from "../src/config.ts";
import { parseFixture } from "../src/input.ts";
import { ArgError, main, REPO_ROOT, resolveOut, writeOutputs } from "../src/main.ts";
import { analyze, renderWorklist, stableJson } from "../src/report.ts";
import { FIXTURE_PATH, FIXTURE_RAW, TOOL } from "./helpers.ts";

const quiet = (args: string[]): number => {
  const log = console.log, err = console.error;
  console.log = () => undefined;
  console.error = () => undefined;
  try {
    return main(args);
  } finally {
    console.log = log;
    console.error = err;
  }
};

function withTmp(fn: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "kge-dedupe-"));
  try {
    fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("逐字节一致：同一输入两次运行产物相同，且与内存渲染一致；没有残留临时文件", () => {
  withTmp((dir) => {
    const a = join(dir, "a"), b = join(dir, "b");
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", a]), 0);
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", b]), 0);
    for (const f of ["dedupe-report.json", "worklist.md"]) assert.ok(readFileSync(join(a, f)).equals(readFileSync(join(b, f))), f);
    const r = analyze(parseFixture(FIXTURE_RAW), DEFAULT_CONFIG);
    assert.equal(readFileSync(join(a, "dedupe-report.json"), "utf8"), stableJson(r));
    assert.equal(readFileSync(join(a, "worklist.md"), "utf8"), renderWorklist(r));
    assert.deepEqual(readdirSync(a).sort(), ["dedupe-report.json", "worklist.md"]);
  });
});

test("--out 可以是已存在的空目录", () => {
  withTmp((dir) => {
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", dir]), 0);
    assert.ok(existsSync(join(dir, "worklist.md")));
  });
});

test("--out 守卫：非空目录、文件、符号链接一律退 2，且不写任何东西", () => {
  withTmp((dir) => {
    const full = join(dir, "full");
    mkdirSync(full);
    writeFileSync(join(full, "keep.txt"), "（合成）");
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", full]), 2);
    assert.deepEqual(readdirSync(full), ["keep.txt"]);
    const file = join(dir, "file");
    writeFileSync(file, "（合成）");
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", file]), 2);
    const target = join(dir, "target"), link = join(dir, "link");
    mkdirSync(target);
    symlinkSync(target, link);
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", link]), 2);
    assert.deepEqual(readdirSync(target), []);
    assert.throws(() => resolveOut(link, []), /符号链接/);
    assert.throws(() => resolveOut(file, []), /不是目录/);
    assert.throws(() => resolveOut(full, []), /空目录/);
  });
});

test("--out 守卫：真实路径在仓库或 --aya 之下退 2（经符号链接绕进去也不行），且不创建目录", () => {
  const inRepo = join(TOOL, "kge-dedupe-out-should-not-exist");
  assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", inRepo]), 2);
  assert.ok(!existsSync(inRepo));
  assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", REPO_ROOT]), 2);
  withTmp((dir) => {
    const viaLink = join(dir, "repo-link");
    symlinkSync(TOOL, viaLink);
    assert.equal(quiet(["--fixture", FIXTURE_PATH, "--out", join(viaLink, "x", "y")]), 2);
    assert.ok(!existsSync(join(TOOL, "x")));
    const aya = join(dir, "aya");
    mkdirSync(aya);
    assert.equal(quiet(["--aya", aya, "--out", join(aya, "out")]), 2);
    assert.ok(!existsSync(join(aya, "out")));
    assert.throws(() => resolveOut(join(aya, "sub"), [REPO_ROOT, aya]), ArgError);
    assert.equal(resolveOut(join(dir, "fresh", "deeper"), [REPO_ROOT, aya]), join(dir, "fresh", "deeper"));
  });
});

test("写盘：正式文件已存在不覆盖；临时文件已存在（wx）不覆盖", () => {
  withTmp((dir) => {
    writeFileSync(join(dir, "a.json"), "（合成）旧");
    assert.throws(() => writeOutputs(dir, [["a.json", "新"]]), ArgError);
    assert.equal(readFileSync(join(dir, "a.json"), "utf8"), "（合成）旧");
    writeFileSync(join(dir, ".b.json.tmp"), "（合成）旧");
    assert.throws(() => writeOutputs(dir, [["b.json", "新"]]), /EEXIST/);
    assert.ok(!existsSync(join(dir, "b.json")));
    writeOutputs(dir, [["c.json", "（合成）新"]]);
    assert.equal(readFileSync(join(dir, "c.json"), "utf8"), "（合成）新");
    assert.ok(!existsSync(join(dir, ".c.json.tmp")));
  });
});

test("命令行：参数不对退 2，输入被守卫拒绝退 1", () => {
  withTmp((dir) => {
    const cases: [string[], number][] = [
      [["--out", join(dir, "o1")], 2],
      [["--fixture", FIXTURE_PATH], 2],
      [["--fixture", FIXTURE_PATH, "--aya", TOOL, "--out", join(dir, "o2")], 2],
      [["--bogus"], 2],
      [["--aya", join(dir, "missing"), "--out", join(dir, "o3")], 2],
      [["--fixture", `${TOOL}package.json`, "--out", join(dir, "o4")], 1],
    ];
    for (const [args, want] of cases) assert.equal(quiet(args), want, args.join(" "));
    assert.ok(!existsSync(join(dir, "o4", "dedupe-report.json")));
  });
});
