// KGE-00 盘点入口（静态模式）。
// 用法：node src/main.ts --aya <阿鸦 legacy 根> --wenli <文砺 legacy 根> --out <输出目录> [--db-output <库名>=<文件> ...]
// 只读 legacy，不联网、不连库；运行前后核对 legacy 的 git 状态没有变化（spec 验收最后一条）。
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, realpathSync, writeFileSync, existsSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { gitMeta } from "./legacy.ts";
import type { DbOutputRow, Inventory } from "./model.ts";
import { longCjkRuns, renderConclusionsMd, renderInventoryMd, renderJson } from "./report.ts";
import { allowedOutputKeys } from "./sqlcheck.ts";

const TOOL_DIR = new URL("..", import.meta.url).pathname;

/** 隔离子进程的启动参数：只读 legacy 与本工具目录，不给写文件与起子进程的权限（Node 22 的权限模型不管网络） */
export function isolatedArgs(ayaRoot: string, wenliRoot: string, script: string, args: string[] = []): string[] {
  // Node 22 的目录授权写成 <目录>/*，每个目录一个 --allow-fs-read
  const readable = [ayaRoot, wenliRoot, TOOL_DIR].map((p) => `${realpathSync(p).replace(/\/$/, "")}/*`);
  return [
    "--no-warnings",
    "--permission",
    ...readable.map((p) => `--allow-fs-read=${p}`),
    "--allow-worker", // module.register 的解析钩子运行在 worker 线程里
    script,
    ...args,
  ];
}

/**
 * 在隔离子进程里 import legacy 并提取数据（src/extract.ts）：只读权限、不能写文件与起子进程、环境变量清空。
 * 返回提取结果；子进程失败时抛错。
 */
export function extractIsolated(ayaRoot: string, wenliRoot: string): Pick<Inventory, "subjects" | "answers" | "blockers" | "conclusions"> {
  const out = execFileSync(
    process.execPath,
    isolatedArgs(ayaRoot, wenliRoot, join(TOOL_DIR, "src/extract.ts"), [realpathSync(ayaRoot), realpathSync(wenliRoot)]),
    { env: {}, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "inherit"] },
  );
  return JSON.parse(out);
}

export const TOOL_VERSION = "0.1.0";

const OUTPUT_KEYS = allowedOutputKeys();

/** 库模式的值只能是计数、absent、列名清单，或值全为计数的小 JSON 对象（安全审查：不让任何行内容回灌进报告） */
function valueOk(key: string, value: string): boolean {
  if (value === "absent" || value === "column absent") return true;
  if (key.startsWith("columns:")) return /^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$/.test(value);
  if (/^\d+$/.test(value)) return !key.startsWith("group:");
  if (!key.startsWith("group:")) return false;
  try {
    const obj = JSON.parse(value) as unknown;
    if (obj === null || typeof obj !== "object" || Array.isArray(obj)) return false;
    const entries = Object.entries(obj);
    return entries.length <= 50 && entries.every(([k, n]) => k.length <= 32 && Number.isInteger(n));
  } catch {
    return false;
  }
}

/** 解析库模式输出（psql -At -F '|'）：每行 section|key|value。键不在白名单或值不合规的行拒绝并计数。 */
export function parseDbOutput(database: string, text: string): { rows: DbOutputRow[]; rejected: number } {
  const rows: DbOutputRow[] = [];
  let rejected = 0;
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (t === "" || !t.includes("|")) continue;
    const [section, key, ...rest] = t.split("|");
    const k = `${section}:${key}`;
    const value = rest.join("|");
    if (OUTPUT_KEYS.has(k) && valueOk(k, value)) rows.push({ database, key: k, value });
    else rejected++;
  }
  return { rows, rejected };
}

export async function buildInventory(ayaRoot: string, wenliRoot: string, dbFiles: [string, string][], rootLabel: (p: string) => string): Promise<Inventory> {
  const extracted = extractIsolated(ayaRoot, wenliRoot);
  const parsed = dbFiles.map(([name, file]) => parseDbOutput(name, readFileSync(file, "utf8")));
  const db = parsed.flatMap((p) => p.rows);
  const rejected = parsed.reduce((a, p) => a + p.rejected, 0);
  return {
    meta: {
      tool: "tools/kge-inventory",
      toolVersion: TOOL_VERSION,
      legacy: [
        { name: "aya", root: rootLabel(ayaRoot), ...gitMeta(ayaRoot) },
        { name: "wenli", root: rootLabel(wenliRoot), ...gitMeta(wenliRoot) },
      ],
    },
    ...extracted,
    db: { provided: db.length > 0, rows: db, rejected },
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
  // 仓库外的 legacy 只写目录名，不把本机绝对路径写进报告（安全审查）
  const label = (p: string) => (p.startsWith(here) ? p.slice(here.length) : `（仓库外）${basename(p)}`);
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
