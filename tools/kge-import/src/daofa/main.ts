// 道法切片导入器入口（冲刺 4-K，specs/kge-daofa-slice-sprint4/spec.md）。只读输入、只生成文件，不连库、不联网、不调用任何模型。
// 用法（在 tools/kge-import 下）：
//   node src/daofa/main.ts map --domain <领域.yaml> [--domain ...] --version 0.0.1 [--standard-year 2022] --out <目录>
//        → import.sql（草稿地图版本种子）、import-report.json、snapshot.json（覆盖报表的输入，只含地图部分）
//   node src/daofa/main.ts rubric --pack <pack.json> --map <目录> --out <目录>          → scoring-points.sql
//   node src/daofa/main.ts rubric-check --pack <pack.json> --export <原子层导出.json>    → 一致退出 0，不一致退出 1
//   node src/daofa/main.ts affairs --entries <时政.yaml> --source-domains <白名单.txt> --map <目录> --out <目录> [--max-months 12]
//        → current-affairs.sql（一律 pending）
//   node src/daofa/main.ts affairs-expiring --entries ... --source-domains ... --map <目录> --today YYYY-MM-DD [--days 30] [--out 报表.md]
//   node src/daofa/main.ts snapshot --map <目录> [--pack <pack.json>] [--entries ... --source-domains ...] --out <snapshot.json>
//   node src/daofa/main.ts page-sample (--map <目录> | --snapshot <snapshot.json>) [--seed ...] [--ratio 0.2] [--min 20] --out <核验单.csv>
//   node src/daofa/main.ts page-backfill (--map <目录> | --snapshot <snapshot.json>) --checklist <核验单.csv> --backfill <回填.csv>
//        --approver <工号> --approved-on YYYY-MM-DD [--threshold 0.95] --out <目录>
//        → page-backfill.sql 与 page-decision.json；有版次低于阈值时退出 1（该版次不会被标 verified）
// 退出码：0 成功；1 校验失败或判定不通过；2 参数不对。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import type { ContentPack } from "../../../../adapters/zhixing/src/content.ts";
import { expiryReport, parseAffairs, parseSourceDomains, renderAffairsSql, renderExpiry, snapshotAffairs, DEFAULT_WARN_DAYS } from "./affairs.ts";
import { DaofaError } from "./common.ts";
import { buildMap, parseDomain } from "./domain.ts";
import {
  DEFAULT_MIN, DEFAULT_RATIO, DEFAULT_SEED, DEFAULT_THRESHOLD, gateEditions, parseBackfill, parseCsv, renderBackfillSql, renderChecklist, samplePages,
  CHECKLIST_COLUMNS, type SampleRow,
} from "./page-check.ts";
import { atomsFromPack, diffRubric, parseExport, renderScoringSql, snapshotPoints } from "./rubric.ts";
import { renderMapSeed } from "./seed.ts";
import { sortSnapshot, stableJson, SNAPSHOT_FORMAT, type DaofaSnapshot } from "./snapshot.ts";

class ArgError extends Error {}

interface MapDir { report: { map_version_id: string; version: string; synthetic: boolean }; snapshot: DaofaSnapshot }

function loadMapDir(dir: string): MapDir {
  const report = JSON.parse(readFileSync(join(dir, "import-report.json"), "utf8")) as MapDir["report"];
  const snapshot = loadSnapshot(join(dir, "snapshot.json"));
  if (report.map_version_id !== snapshot.map_version_id || report.version !== snapshot.version) {
    throw new DaofaError(`${dir}：import-report.json 与 snapshot.json 的地图版本不一致`);
  }
  return { report, snapshot };
}

function loadSnapshot(path: string): DaofaSnapshot {
  const s = JSON.parse(readFileSync(path, "utf8")) as DaofaSnapshot;
  if (s.format !== SNAPSHOT_FORMAT || s.subject !== "zhixing") throw new DaofaError(`${path}：不是道法快照（format 应为 ${SNAPSHOT_FORMAT}）`);
  return s;
}

const viewpoints = (s: DaofaSnapshot) => new Set(s.nodes.filter((n) => n.kind === "viewpoint" && n.status === "active").map((n) => n.id));

const need = (v: string | undefined, name: string): string => {
  if (v === undefined || v === "") throw new ArgError(`缺 --${name}`);
  return v;
};

function affairsOf(values: Record<string, unknown>, map: MapDir) {
  const entries = need(values.entries as string | undefined, "entries");
  const maxMonths = values["max-months"] === undefined ? undefined : Number(values["max-months"]);
  const f = parseAffairs(readFileSync(entries, "utf8"), {
    viewpointIds: viewpoints(map.snapshot),
    sourceDomains: parseSourceDomains(readFileSync(need(values["source-domains"] as string | undefined, "source-domains"), "utf8")),
    maxMonths,
  }, basename(entries));
  if (f.synthetic !== map.report.synthetic) throw new DaofaError("合成样例与真实内容不能混用（时政条目与地图版本的 synthetic 不一致）");
  return f;
}

function snapshotRows(values: Record<string, unknown>): { snapshot: DaofaSnapshot; map: MapDir | null } {
  if (values.snapshot) return { snapshot: loadSnapshot(values.snapshot as string), map: null };
  const map = loadMapDir(need(values.map as string | undefined, "map 或 --snapshot"));
  return { snapshot: map.snapshot, map };
}

function readChecklist(path: string): SampleRow[] {
  const rows = parseCsv(readFileSync(path, "utf8"));
  const head = rows.shift();
  if (!head || head.join(",") !== CHECKLIST_COLUMNS.join(",")) throw new DaofaError(`核验单表头应为 ${CHECKLIST_COLUMNS.join(",")}`);
  return rows.map((c) => ({
    sample_id: c[0]!, projection_id: c[1]!, edition: c[2]!, edition_year: Number(c[3]), volume: c[4]!, chapter: c[5]!,
    page_from: Number(c[6]), page_to: Number(c[7]), node_id: c[8]!,
  }));
}

export function main(argv = process.argv.slice(2)): number {
  const [cmd, ...rest] = argv;
  let values: Record<string, unknown>;
  try {
    ({ values } = parseArgs({
      args: rest,
      options: {
        domain: { type: "string", multiple: true }, version: { type: "string" }, "standard-year": { type: "string" }, out: { type: "string" },
        pack: { type: "string" }, map: { type: "string" }, export: { type: "string" }, entries: { type: "string" },
        "source-domains": { type: "string" }, "max-months": { type: "string" }, today: { type: "string" }, days: { type: "string" },
        snapshot: { type: "string" }, seed: { type: "string" }, ratio: { type: "string" }, min: { type: "string" },
        checklist: { type: "string" }, backfill: { type: "string" }, approver: { type: "string" }, "approved-on": { type: "string" },
        threshold: { type: "string" },
      },
    }));
  } catch (e) {
    console.error(`kge-daofa: ${(e as Error).message}`);
    return 2;
  }
  try {
    switch (cmd) {
      case "map": {
        const files = values.domain as string[] | undefined;
        if (!files?.length) throw new ArgError("缺 --domain");
        const map = buildMap(files.map((f) => parseDomain(readFileSync(f, "utf8"), basename(f))));
        const r = renderMapSeed(map, {
          version: need(values.version as string | undefined, "version"),
          standardYear: values["standard-year"] === undefined ? undefined : Number(values["standard-year"]),
        });
        const out = resolve(need(values.out as string | undefined, "out"));
        mkdirSync(out, { recursive: true });
        writeFileSync(join(out, "import.sql"), r.sql);
        writeFileSync(join(out, "import-report.json"), stableJson(r.report));
        writeFileSync(join(out, "snapshot.json"), stableJson(r.snapshot));
        console.log(`kge-daofa map: ${String(r.report.domains)} 个领域、${String(r.report.big_concepts)} 个大概念、${String(r.report.viewpoints)} 个观点、`
          + `关联边 ${String(r.report.related_edges)} 条、投影 ${String(r.report.projections)} 条${map.synthetic ? "（合成样例）" : ""} → ${out}`);
        return 0;
      }
      case "rubric": {
        const map = loadMapDir(need(values.map as string | undefined, "map"));
        const pack = JSON.parse(readFileSync(need(values.pack as string | undefined, "pack"), "utf8")) as ContentPack;
        if (pack.synthetic !== map.report.synthetic) throw new DaofaError("合成内容包与真实地图版本不能混用（synthetic 不一致）");
        const atoms = atomsFromPack(pack);
        const sql = renderScoringSql(atoms, { mapVersionId: map.report.map_version_id, viewpointIds: viewpoints(map.snapshot), synthetic: pack.synthetic,
          source: `${pack.pack_name}@${pack.pack_version}` });
        const out = resolve(need(values.out as string | undefined, "out"));
        mkdirSync(out, { recursive: true });
        writeFileSync(join(out, "scoring-points.sql"), sql);
        console.log(`kge-daofa rubric: ${atoms.length} 个采分点（${new Set(atoms.map((a) => a.item_id)).size} 题）→ ${out}/scoring-points.sql`);
        return 0;
      }
      case "rubric-check": {
        const pack = JSON.parse(readFileSync(need(values.pack as string | undefined, "pack"), "utf8")) as ContentPack;
        const exp = parseExport(readFileSync(need(values.export as string | undefined, "export"), "utf8"));
        const atoms = atomsFromPack(pack);
        const diffs = diffRubric(atoms, exp.points);
        if (diffs.length > 0) {
          console.error(`zhixing-rubric-check: 内容包与原子层（kge.scoring_point）不一致，${diffs.length} 处：`);
          for (const d of diffs.slice(0, 50)) console.error(`  ${d.key}：${d.field} ${d.detail}`);
          return 1;
        }
        const versions = [...new Set(atoms.map((a) => a.rubric_version))].join("、");
        console.log(`zhixing-rubric-check: 一致（${versions}：${atoms.length} 个采分点逐字段相同）`);
        return 0;
      }
      case "affairs": {
        const map = loadMapDir(need(values.map as string | undefined, "map"));
        const f = affairsOf(values, map);
        const out = resolve(need(values.out as string | undefined, "out"));
        mkdirSync(out, { recursive: true });
        writeFileSync(join(out, "current-affairs.sql"), renderAffairsSql(f, { mapVersionId: map.report.map_version_id, version: map.report.version,
          source: basename(values.entries as string) }));
        console.log(`kge-daofa affairs: ${f.entries.length} 条时政（一律 pending）→ ${out}/current-affairs.sql`);
        return 0;
      }
      case "affairs-expiring": {
        const map = loadMapDir(need(values.map as string | undefined, "map"));
        const f = affairsOf(values, map);
        const today = need(values.today as string | undefined, "today");
        const days = values.days === undefined ? DEFAULT_WARN_DAYS : Number(values.days);
        const md = renderExpiry(expiryReport(f, map.report.version, today, days), today, days);
        if (values.out) writeFileSync(values.out as string, md);
        else process.stdout.write(md);
        return 0;
      }
      case "snapshot": {
        const map = loadMapDir(need(values.map as string | undefined, "map"));
        const s: DaofaSnapshot = { ...map.snapshot };
        if (values.pack) s.scoring_points = snapshotPoints(atomsFromPack(JSON.parse(readFileSync(values.pack as string, "utf8")) as ContentPack));
        if (values.entries) s.current_affairs = snapshotAffairs(affairsOf(values, map), map.report.version);
        writeFileSync(need(values.out as string | undefined, "out"), stableJson(sortSnapshot(s)));
        return 0;
      }
      case "page-sample": {
        const { snapshot } = snapshotRows(values);
        const rows = samplePages(snapshot.projections, {
          seed: (values.seed as string | undefined) ?? DEFAULT_SEED,
          ratio: values.ratio === undefined ? DEFAULT_RATIO : Number(values.ratio),
          min: values.min === undefined ? DEFAULT_MIN : Number(values.min),
        });
        writeFileSync(need(values.out as string | undefined, "out"), renderChecklist(rows));
        console.log(`kge-daofa page-sample: ${snapshot.projections.length} 条投影抽 ${rows.length} 条 → ${String(values.out)}`);
        return 0;
      }
      case "page-backfill": {
        const { snapshot } = snapshotRows(values);
        const checklist = readChecklist(need(values.checklist as string | undefined, "checklist"));
        const backfill = parseBackfill(readFileSync(need(values.backfill as string | undefined, "backfill"), "utf8"), checklist);
        const threshold = values.threshold === undefined ? DEFAULT_THRESHOLD : Number(values.threshold);
        const decisions = gateEditions(snapshot.projections, backfill, threshold);
        const sql = renderBackfillSql({ mapVersionId: snapshot.map_version_id, projections: snapshot.projections, backfill, decisions,
          approver: need(values.approver as string | undefined, "approver"), approvedOn: need(values["approved-on"] as string | undefined, "approved-on") });
        const out = resolve(need(values.out as string | undefined, "out"));
        mkdirSync(out, { recursive: true });
        writeFileSync(join(out, "page-backfill.sql"), sql);
        writeFileSync(join(out, "page-decision.json"), stableJson({ format: "kge-daofa-page-decision/1", map_version_id: snapshot.map_version_id, decisions }));
        const blocked = decisions.filter((d) => !d.can_verify);
        for (const d of decisions) {
          console.log(`kge-daofa page-backfill: ${d.edition} 通过 ${d.passed}/${d.sampled}（${(d.pass_rate * 100).toFixed(1)}%）→ ${d.can_verify ? "可标 verified" : "不能标 verified"}`);
        }
        return blocked.length > 0 ? 1 : 0;
      }
      default:
        throw new ArgError(`不认识的子命令 ${String(cmd)}（map、rubric、rubric-check、affairs、affairs-expiring、snapshot、page-sample、page-backfill）`);
    }
  } catch (e) {
    if (e instanceof ArgError) {
      console.error(`kge-daofa: ${e.message}`);
      return 2;
    }
    if (e instanceof DaofaError || e instanceof SyntaxError || (e as NodeJS.ErrnoException).code === "ENOENT" || e instanceof Error) {
      console.error(`kge-daofa: ${(e as Error).message}`);
      return 1;
    }
    throw e;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main();
}
