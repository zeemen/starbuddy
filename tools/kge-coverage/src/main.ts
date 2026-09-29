// KGE-02 覆盖矩阵报表入口（specs/kge-02-atoms-tagging/spec.md 设计要点 4）。
// 用法：node src/main.ts --map <db/seeds/kge/aya-<主题>-<版本>> [--map <同一地图版本的另一专题> ...] --tagging <tagging-report.json>
//        [--scope all|item_tag] [--out report]
// 只读输入、不连库；输出 <out>/coverage-<版本>.md 与 <out>/coverage-<版本>.csv。退出码：0 生成成功（门槛未达标也是 0，只标注）；
// 1 输入不一致或格式不对；2 参数不对。
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { computeCoverage, CoverageError, type Scope } from "./coverage.ts";
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
      },
    }));
  } catch (e) {
    console.error(`kge-coverage: ${(e as Error).message}`);
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

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main();
}
