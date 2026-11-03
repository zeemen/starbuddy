// KGE-02 覆盖矩阵报表入口（specs/kge-02-atoms-tagging/spec.md 设计要点 4）。
// 用法：node src/main.ts --map <db/seeds/kge/aya-<主题>-<版本>> [--map <同一地图版本的另一专题> ...] --tagging <tagging-report.json>
//        [--scope all|item_tag] [--out report]
//        道法（冲刺 4-K）：node src/main.ts --subject zhixing --snapshot <kge-daofa-snapshot/1 JSON> [--focus-domain MORAL.LAW] [--out report]
//        → <out>/coverage-zhixing-<版本>.md 与 .csv（快照由 tools/kge-import 的 daofa snapshot 离线组装，或 sql/daofa-snapshot.sql 在演练库导出）
//        语文（KGE-07）：node src/main.ts --subject wenli --snapshot <kge-chinese-snapshot/1 JSON> [--focus-domain CHN.SYN_READ] [--out report]
//        → <out>/coverage-wenli-<版本>.md 与 .csv（快照由 tools/kge-import 的 chinese map 离线生成，或 sql/chinese-snapshot.sql 在演练库导出）
// 只读输入、不连库；输出 <out>/coverage-<版本>.md 与 <out>/coverage-<版本>.csv。退出码：0 生成成功（门槛未达标也是 0，只标注）；
// 1 输入不一致或格式不对；2 参数不对。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { computeCoverage, CoverageError, type Scope } from "./coverage.ts";
import {
  checkChineseSnapshot, CHINESE_FOCUS, ChineseCoverageError, computeChineseCoverage, DEFAULT_CHINESE_FOCUS, toChineseCsv, toChineseMarkdown,
} from "./chinese.ts";
import { checkSnapshot, computeDaofaCoverage, DaofaCoverageError, DEFAULT_FOCUS_DOMAIN, toDaofaCsv, toDaofaMarkdown } from "./daofa.ts";
import { loadMapDir, MapError, mergeMaps } from "./map.ts";
import { toCsv, toMarkdown } from "./render.ts";
import { loadTaggingReport, TaggingError } from "./tagging.ts";

export function main(argv = process.argv.slice(2)): number {
  let values;
  try {
    ({ values } = parseArgs({
      args: argv,
      options: {
        map: { type: "string", multiple: true }, tagging: { type: "string" },
        scope: { type: "string", default: "all" }, out: { type: "string", default: "report" },
        subject: { type: "string", default: "aya" }, snapshot: { type: "string" }, "focus-domain": { type: "string" },
      },
    }));
  } catch (e) {
    console.error(`kge-coverage: ${(e as Error).message}`);
    return 2;
  }
  if (values.subject === "zhixing") return daofaMain(values.snapshot, values["focus-domain"] ?? DEFAULT_FOCUS_DOMAIN, values.out!);
  if (values.subject === "wenli") return chineseMain(values.snapshot, values["focus-domain"] ?? DEFAULT_CHINESE_FOCUS, values.out!);
  if (values.subject !== "aya") {
    console.error("kge-coverage: --subject 只能是 aya（默认）、zhixing 或 wenli");
    return 2;
  }
  if (!values.map?.length || !values.tagging || !["all", "item_tag"].includes(values.scope!)) {
    console.error("kge-coverage: 需要 --map（地图导入产物目录，可多个）与 --tagging（打标报告 JSON）；--scope 只能是 all 或 item_tag");
    return 2;
  }
  try {
    const r = computeCoverage(mergeMaps(values.map.map(loadMapDir)), loadTaggingReport(values.tagging), values.scope as Scope);
    if (!/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(r.version)) throw new MapError(`地图版本号不能用作文件名：${r.version}`);
    const out = resolve(values.out!);
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, `coverage-${r.version}.md`), toMarkdown(r));
    writeFileSync(join(out, `coverage-${r.version}.csv`), toCsv(r));
    console.log(`kge-coverage: ${String(r.itemCount)} 道题、${String(r.rows.length)} 个节点，覆盖 ${String(r.coveredCount)} 个` +
      `（${r.meetsThreshold ? "达标" : "未达标，已标红"}）→ ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof MapError || e instanceof TaggingError || e instanceof CoverageError || e instanceof SyntaxError) {
      console.error(`kge-coverage: ${e.message}`);
      return 1;
    }
    throw e;
  }
}

function daofaMain(snapshot: string | undefined, focus: string, outDir: string): number {
  if (!snapshot || !/^MORAL\.[A-Z0-9][A-Z0-9_]{0,31}$/.test(focus)) {
    console.error("kge-coverage: 道法需要 --snapshot（kge-daofa-snapshot/1 JSON）；--focus-domain 是道法领域 ID（如 MORAL.LAW）");
    return 2;
  }
  try {
    const r = computeDaofaCoverage(checkSnapshot(JSON.parse(readFileSync(snapshot, "utf8"))), focus);
    if (!/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(r.version)) throw new DaofaCoverageError(`地图版本号不能用作文件名：${r.version}`);
    const out = resolve(outDir);
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, `coverage-zhixing-${r.version}.md`), toDaofaMarkdown(r));
    writeFileSync(join(out, `coverage-zhixing-${r.version}.csv`), toDaofaCsv(r));
    console.log(`kge-coverage: 道法 ${String(r.totals.viewpoints)} 个观点，投影已核对 ${String(r.totals.verified)} / ${String(r.totals.projections)}，`
      + `采分点 ${String(r.totals.scoringPoints)}，${focus} 缺口 ${String(r.gaps.length)} 条 → ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof DaofaCoverageError || e instanceof SyntaxError || (e as NodeJS.ErrnoException).code === "ENOENT") {
      console.error(`kge-coverage: ${(e as Error).message}`);
      return 1;
    }
    throw e;
  }
}

function chineseMain(snapshot: string | undefined, focus: string, outDir: string): number {
  if (!snapshot || !CHINESE_FOCUS.test(focus)) {
    console.error("kge-coverage: 语文需要 --snapshot（kge-chinese-snapshot/1 JSON）；--focus-domain 是 CHN（整张地图，默认）或语文领域 ID（如 CHN.SYN_READ）");
    return 2;
  }
  try {
    const r = computeChineseCoverage(checkChineseSnapshot(JSON.parse(readFileSync(snapshot, "utf8"))), focus);
    if (!/^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$/.test(r.version)) throw new ChineseCoverageError(`地图版本号不能用作文件名：${r.version}`);
    const out = resolve(outDir);
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, `coverage-wenli-${r.version}.md`), toChineseMarkdown(r));
    writeFileSync(join(out, `coverage-wenli-${r.version}.csv`), toChineseCsv(r));
    console.log(`kge-coverage: 语文 ${String(r.totals.abilityKps)} 个能力点、${String(r.totals.materials)} 个素材、${String(r.totals.methods)} 个方法，`
      + `投影已核对 ${String(r.totals.verified)} / ${String(r.totals.projections)}，${focus} 缺口 ${String(r.gaps.length)} 条 → ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof ChineseCoverageError || e instanceof SyntaxError || (e as NodeJS.ErrnoException).code === "ENOENT") {
      console.error(`kge-coverage: ${(e as Error).message}`);
      return 1;
    }
    throw e;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main();
}
