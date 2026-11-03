// KGE-08 第一步入口（specs/kge08-paper-dedupe/spec.md 范围 2）。离线、只读输入、只生成文件；不连库、不联网、不调用模型。
// 用法（在 tools/kge-dedupe 下）：
//   node src/main.ts --fixture <合成样例.json> --out <目录>
//   node src/main.ts --aya <legacy 阿鸦根目录> --out <目录>   （只读，复用 tools/kge-import 的隔离加载器 loadBank）
//   → dedupe-report.json（不含题干）、worklist.md（教研勾选）
// --out 必须不存在或是空目录、不能是符号链接，解析真实路径后不得落在仓库根或 --aya 根之下（阿鸦运行结果不进仓库，spec“不做”）；
// 每个文件先以独占方式（wx）写临时文件，再改名为正式文件，已有文件一律不覆盖。
// 退出码：0 成功；1 输入或校验失败；2 参数不对（含 --out 不合要求）。
import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { loadBank } from "../../kge-import/src/legacy-items.ts";
import { DEFAULT_CONFIG } from "./config.ts";
import { fromLegacy, InputError, MAX_FIXTURE_BYTES, parseFixture } from "./input.ts";
import { analyze, renderWorklist, stableJson } from "./report.ts";

export class ArgError extends Error {}

export const REPO_ROOT = realpathSync(fileURLToPath(new URL("../../../", import.meta.url)));

const lstatOrNull = (p: string) => {
  try {
    return lstatSync(p);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw e;
  }
};

/** 校验 --out 并返回它的真实路径（此时还不创建目录） */
export function resolveOut(out: string, forbiddenRoots: readonly string[]): string {
  const abs = resolve(out);
  const st = lstatOrNull(abs);
  if (st !== null) {
    if (st.isSymbolicLink()) throw new ArgError("--out 不能是符号链接");
    if (!st.isDirectory()) throw new ArgError("--out 已存在且不是目录");
    if (readdirSync(abs).length > 0) throw new ArgError("--out 必须不存在或是空目录");
  }
  let probe = abs;
  const rest: string[] = [];
  while (!existsSync(probe)) {
    rest.unshift(basename(probe));
    probe = dirname(probe);
  }
  const real = join(realpathSync(probe), ...rest);
  for (const root of forbiddenRoots) {
    if (real === root || real.startsWith(`${root}${sep}`)) throw new ArgError("--out 不得在仓库或 --aya 目录之下（运行结果不进仓库）");
  }
  return real;
}

/** 逐个文件：正式文件已存在就拒绝；先以 wx 写临时文件，再改名 */
export function writeOutputs(dir: string, files: readonly [string, string][]): void {
  for (const [name, data] of files) {
    const dst = join(dir, name), tmp = join(dir, `.${name}.tmp`);
    if (lstatOrNull(dst) !== null) throw new ArgError(`${name} 已存在，不覆盖`);
    writeFileSync(tmp, data, { flag: "wx" });
    renameSync(tmp, dst);
  }
}

export function main(argv = process.argv.slice(2)): number {
  let values: { fixture?: string; aya?: string; out?: string };
  try {
    ({ values } = parseArgs({ args: argv, options: { fixture: { type: "string" }, aya: { type: "string" }, out: { type: "string" } } }));
  } catch {
    console.error("kge-dedupe: 参数不对（只认 --fixture、--aya、--out）");
    return 2;
  }
  try {
    if ((values.fixture === undefined) === (values.aya === undefined)) throw new ArgError("--fixture 与 --aya 必须且只能给一个");
    if (values.out === undefined || values.out === "") throw new ArgError("缺 --out");
    let ayaRoot: string | null = null;
    if (values.aya !== undefined) {
      if (!existsSync(values.aya)) throw new ArgError("--aya 目录不存在");
      ayaRoot = realpathSync(values.aya);
    }
    const out = resolveOut(values.out, ayaRoot === null ? [REPO_ROOT] : [REPO_ROOT, ayaRoot]);
    let input;
    if (values.fixture !== undefined) {
      if (statSync(values.fixture).size > MAX_FIXTURE_BYTES) throw new InputError(`样例超过 ${String(MAX_FIXTURE_BYTES)} 字节`);
      input = parseFixture(readFileSync(values.fixture, "utf8"));
    } else {
      input = fromLegacy(loadBank(ayaRoot!));
    }
    const report = analyze(input, DEFAULT_CONFIG);
    mkdirSync(out, { recursive: true });
    writeOutputs(out, [["dedupe-report.json", stableJson(report)], ["worklist.md", renderWorklist(report)]]);
    const c = report.counts;
    console.log(`kge-dedupe: ${String(c.items)} 题（规范文本 ${String(c.unique_texts)} 种）；完全重复 ${String(c.exact_groups)} 组、近似 ${String(c.near_pairs)} 对、`
      + `族候选 ${String(c.family_candidates)} 个、待看 ${String(c.review_templates)} 个、旧题组冲突 ${String(c.legacy_family_conflicts)} 题；`
      + `试卷 ${String(c.papers)} 份，同卷 ${String(c.paper_pairs_same)} 对、相似卷 ${String(c.paper_pairs_similar)} 对${input.synthetic ? "（合成样例）" : ""} → ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof ArgError) {
      console.error(`kge-dedupe: ${e.message}`);
      return 2;
    }
    if (e instanceof Error) {
      console.error(`kge-dedupe: ${e.message}`);
      return 1;
    }
    throw e;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main();
}
