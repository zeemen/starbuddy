// 语文切片导入器入口（KGE-07 第一步，specs/kge07-chinese-slice/spec.md 范围 1）。只读输入、只生成文件，不连库、不联网、不调用任何模型。
// 用法（在 tools/kge-import 下）：
//   node src/chinese/main.ts map --domain <领域.yaml> [--domain ...] --version 0.0.1 [--standard-year 2022] --out <目录>
//        → import.sql（草稿地图版本种子）、import-report.json、snapshot.json（覆盖报表 tools/kge-coverage --subject wenli 的输入）
// 退出码：0 成功；1 校验失败；2 参数不对。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { buildMap, parseDomain } from "./map.ts";
import { renderMapSeed, stableJson } from "./seed.ts";

class ArgError extends Error {}

const need = (v: string | undefined, name: string): string => {
  if (v === undefined || v === "") throw new ArgError(`缺 --${name}`);
  return v;
};

export function main(argv = process.argv.slice(2)): number {
  const [cmd, ...rest] = argv;
  let values: { domain?: string[]; version?: string; "standard-year"?: string; out?: string };
  try {
    ({ values } = parseArgs({
      args: rest,
      options: { domain: { type: "string", multiple: true }, version: { type: "string" }, "standard-year": { type: "string" }, out: { type: "string" } },
    }));
  } catch (e) {
    console.error(`kge-chinese: ${(e as Error).message}`);
    return 2;
  }
  try {
    if (cmd !== "map") throw new ArgError(`不认识的子命令 ${String(cmd)}（只有 map）`);
    const files = values.domain;
    if (!files?.length) throw new ArgError("缺 --domain");
    const version = need(values.version, "version");
    const standardYear = values["standard-year"] === undefined ? undefined : Number(values["standard-year"]);
    const out = resolve(need(values.out, "out"));
    const map = buildMap(files.map((f) => parseDomain(readFileSync(f, "utf8"), basename(f))));
    const r = renderMapSeed(map, { version, standardYear });
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, "import.sql"), r.sql);
    writeFileSync(join(out, "import-report.json"), stableJson(r.report));
    writeFileSync(join(out, "snapshot.json"), stableJson(r.snapshot));
    const rep = r.report as Record<string, number>;
    console.log(`kge-chinese map: ${String(rep.ability_kps)} 个能力点、${String(rep.materials)} 个素材、${String(rep.methods)} 个方法；`
      + `层次递进 ${String(rep.progression_edges)}、显式前置 ${String(rep.explicit_prerequisites)}、关联 ${String(rep.related_edges)}、`
      + `易混 ${String(rep.confusable_edges)}、用方法 ${String(rep.uses_method_edges)}；投影 ${String(rep.projections)} 条`
      + `${map.synthetic ? "（合成样例）" : ""} → ${out}`);
    return 0;
  } catch (e) {
    if (e instanceof ArgError) {
      console.error(`kge-chinese: ${e.message}`);
      return 2;
    }
    if (e instanceof Error) {
      console.error(`kge-chinese: ${e.message}`);
      return 1;
    }
    throw e;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main();
}
