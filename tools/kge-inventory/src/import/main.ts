// KGE-01 骨架导入入口（specs/kge-01-skeleton-import/spec.md）。
// 用法：node src/import/main.ts --aya <阿鸦 legacy 根> [--topic fun] [--version 0.1.0] [--overrides 覆盖.json] --out <输出目录>
// 只读 legacy（隔离子进程加载）；不连库。输出 import.sql、worklist.md、import-report.json。
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { isolatedArgs } from "../main.ts";
import { build, checkLegacy, ImportError, type LegacyNode, type Overrides } from "./build.ts";

const HERE = fileURLToPath(new URL(".", import.meta.url));

export function loadTopic(aya: string, topic: string): LegacyNode[] {
  const root = realpathSync(aya);
  const out = execFileSync(process.execPath, isolatedArgs(root, root, join(HERE, "extract-topic.ts"), [root, topic]),
    { env: {}, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "inherit"] });
  const parsed: unknown = JSON.parse(out);
  checkLegacy(parsed); // 子进程与 legacy 代码同进程，输出按不可信数据核对（安全审查建议 2）
  return parsed;
}

export async function main(argv = process.argv.slice(2)): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      aya: { type: "string" },
      topic: { type: "string", default: "fun" },
      version: { type: "string", default: "0.1.0" },
      overrides: { type: "string" },
      out: { type: "string" },
    },
  });
  if (!values.aya || !values.out || !/^[a-z]{2,8}$/.test(values.topic!)) {
    console.error("kge-import: 需要 --aya、--out，专题代码为 2–8 个小写字母");
    return 2;
  }
  try {
    const overrides = values.overrides ? (JSON.parse(readFileSync(values.overrides, "utf8")) as Overrides) : {};
    const r = build(loadTopic(values.aya, values.topic!), { topic: values.topic!, version: values.version! }, overrides);
    const out = resolve(values.out);
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, "import.sql"), r.sql);
    writeFileSync(join(out, "worklist.md"), r.worklistMd);
    writeFileSync(join(out, "import-report.json"), `${JSON.stringify(r.report, null, 2)}\n`);
    console.log(`kge-import: ${String(r.report.imported_nodes)} 个节点、${r.edges.length} 条前置边、工作项 ${r.worklist.length} 条 → ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof ImportError) {
      console.error(`kge-import: ${e.message}`);
      return 1;
    }
    throw e;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main();
}
