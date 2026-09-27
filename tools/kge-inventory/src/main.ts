// KGE-00 盘点入口（静态模式）。
// 用法：node src/main.ts --aya <阿鸦 legacy 根> --wenli <文砺 legacy 根> --out <输出目录> [--db-output <库名>=<文件> ...]
// 只读 legacy，不联网、不连库；运行前后核对 legacy 的 git 状态没有变化（spec 验收最后一条）。
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { parseArgs } from "node:util";
import { inventoryConclusions } from "./conclusions.ts";
import { gitMeta, registerLegacyHooks } from "./legacy.ts";
import type { DbOutputRow, Inventory } from "./model.ts";
import { longCjkRuns, renderConclusionsMd, renderInventoryMd, renderJson } from "./report.ts";
import { inventoryChinese } from "./subjects/chinese.ts";
import { inventoryMath } from "./subjects/math.ts";
import { inventoryMoral } from "./subjects/moral.ts";

export const TOOL_VERSION = "0.1.0";

/** 解析库模式输出（psql -At -F '|'）：每行 section|key|value */
export function parseDbOutput(database: string, text: string): DbOutputRow[] {
  const rows: DbOutputRow[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (t === "" || !t.includes("|")) continue;
    const [section, key, ...rest] = t.split("|");
    rows.push({ database, key: `${section}:${key}`, value: rest.join("|") });
  }
  return rows;
}

export async function buildInventory(ayaRoot: string, wenliRoot: string, dbFiles: [string, string][], rootLabel: (p: string) => string): Promise<Inventory> {
  registerLegacyHooks(ayaRoot, [ayaRoot, wenliRoot]);
  const math = await inventoryMath(ayaRoot);
  const chinese = await inventoryChinese(wenliRoot);
  const moral = await inventoryMoral(wenliRoot);
  const conclusions = inventoryConclusions(ayaRoot, wenliRoot);
  const db = dbFiles.flatMap(([name, file]) => parseDbOutput(name, readFileSync(file, "utf8")));
  return {
    meta: {
      tool: "tools/kge-inventory",
      toolVersion: TOOL_VERSION,
      legacy: [
        { name: "aya", root: rootLabel(ayaRoot), ...gitMeta(ayaRoot) },
        { name: "wenli", root: rootLabel(wenliRoot), ...gitMeta(wenliRoot) },
      ],
    },
    subjects: [math.report, chinese.report, moral.report],
    answers: [...math.answers, ...chinese.answers, ...moral.answers],
    blockers: [...math.blockers, ...chinese.blockers, ...moral.blockers],
    conclusions,
    db: { provided: db.length > 0, rows: db },
  };
}

async function main(): Promise<number> {
  const here = new URL("../../..", import.meta.url).pathname;
  const { values } = parseArgs({
    options: {
      aya: { type: "string", default: join(here, "legacy/aya") },
      wenli: { type: "string", default: join(here, "legacy/wenli") },
      out: { type: "string" },
      "db-output": { type: "string", multiple: true, default: [] },
    },
  });
  if (!values.out) {
    console.error("kge-inventory: 缺少 --out <输出目录>");
    return 2;
  }
  const aya = resolve(values.aya!);
  const wenli = resolve(values.wenli!);
  for (const [name, p] of [["阿鸦", aya], ["文砺", wenli]] as const) {
    if (!existsSync(p)) {
      console.error(`kge-inventory: 找不到${name} legacy 目录 ${p}（legacy 是只读副本，本机没有时无法生成报告）`);
      return 2;
    }
  }
  const dbFiles: [string, string][] = [];
  for (const spec of values["db-output"] ?? []) {
    const i = spec.indexOf("=");
    if (i <= 0) {
      console.error(`kge-inventory: --db-output 应为 <库名>=<文件>，收到 ${spec}`);
      return 2;
    }
    dbFiles.push([spec.slice(0, i), resolve(spec.slice(i + 1))]);
  }

  const before = [gitMeta(aya), gitMeta(wenli)];
  const label = (p: string) => (p.startsWith(here) ? p.slice(here.length) : p);
  const inv = await buildInventory(aya, wenli, dbFiles, label);
  const after = [gitMeta(aya), gitMeta(wenli)];
  if (JSON.stringify(before) !== JSON.stringify(after)) {
    console.error("kge-inventory: legacy 的 git 状态在运行前后不一致，拒绝输出");
    return 1;
  }

  const files: [string, string][] = [
    ["asset-inventory.md", renderInventoryMd(inv)],
    ["asset-inventory.json", renderJson(inv)],
    ["conclusion-fields.md", renderConclusionsMd(inv)],
  ];
  for (const [name, text] of files) {
    const runs = longCjkRuns(text);
    if (runs.length > 0) {
      console.error(`kge-inventory: ${name} 里有 40 字以上的连续汉字（疑似原文片段），拒绝输出：${runs.slice(0, 3).join("；")}`);
      return 1;
    }
  }
  const out = resolve(values.out);
  mkdirSync(out, { recursive: true });
  for (const [name, text] of files) writeFileSync(join(out, name), text);
  const errs = inv.subjects.flatMap((s) => s.loadErrors);
  console.log(`kge-inventory: 已生成 ${files.map((f) => f[0]).join("、")} → ${out}${errs.length ? `（加载问题 ${errs.length} 条，见报告）` : ""}`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main();
}
