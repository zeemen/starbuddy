// W13 T11（specs/tech-debt-batch3 待人确认 #4）：内容登记脚本放在 tools/content-register/，适配器目录（测试与只读的内容包除外）
// 不出现写平台表的代码（CLAUDE.md 规则 1）。三科登记脚本各自只读本科适配器的内容包加载代码。
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const ROOT = new URL("../../../", import.meta.url).pathname;
const SUBJECTS = ["aya", "wenli", "zhixing"] as const;
const SKIP = new Set(["node_modules", "tests", "content", "conformance", "fixtures", ".next"]);

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    if (SKIP.has(n)) return [];
    const p = join(d, n);
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|mts|mjs|js)$/.test(n) ? [p] : [];
  });

test("适配器目录里（测试、内容包、一致性夹具除外）没有生成或执行 platform.* SQL 的代码", () => {
  for (const s of SUBJECTS) {
    assert.ok(!existsSync(join(ROOT, `adapters/${s}/scripts/register-content.ts`)), `${s}：登记脚本应已迁到 tools/content-register/`);
    for (const f of walk(join(ROOT, `adapters/${s}`))) {
      assert.doesNotMatch(readFileSync(f, "utf8"), /\b(INSERT INTO|UPDATE|DELETE FROM)\s+platform\./i, f);
    }
  }
});

test("三科登记脚本各在 tools/content-register/，只引用本科适配器的内容包加载代码", () => {
  for (const s of SUBJECTS) {
    const src = readFileSync(join(ROOT, `tools/content-register/${s}.ts`), "utf8");
    const imports = [...src.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]!);
    assert.deepEqual(imports.filter((i) => !i.startsWith("node:")), [`../../adapters/${s}/src/content.ts`], s);
    assert.match(src, /INSERT INTO platform\.item/, `${s}：登记 SQL 仍在这里生成`);
  }
});
