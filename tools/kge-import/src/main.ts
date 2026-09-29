// KGE-02 题库导入与打标入口（specs/kge-02-atoms-tagging/spec.md 设计要点 1、2、6）。
// 用法：
//   node src/main.ts --aya <阿鸦 legacy 根> --map <地图版本种子目录> [--map <另一个专题的种子目录> ...] [--out <输出目录>]
//                    [--config config/aya-chapter-segments.yaml] [--pack <内容包 pack.json> | --no-pack]
//                    [--vector] [--model] [--top-k 5] [--rules-only-a]
// 只读 legacy（隔离子进程加载）与仓库里的种子；不连库、不联网、不调用任何真实模型。
// --vector / --model 只接假实现（不是生产模型，见 src/tagging/vector.ts、model.ts），用于把流水线打通。
// 输出 items.sql、worklist.md、tagging-report.json，默认写到 db/seeds/kge/aya-items-<地图版本号>/。
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { buildItems } from "./items/build.ts";
import { loadBank } from "./legacy-items.ts";
import { loadMapInput } from "./map-version.ts";
import { parseSegmentConfig } from "./segments.ts";
import { FakeTagModel } from "./tagging/model.ts";
import { makePipeline } from "./tagging/pipeline.ts";
import { buildRuleContext } from "./tagging/rules.ts";
import { FakeNgramEmbedder } from "./tagging/vector.ts";

const TOOL = fileURLToPath(new URL("..", import.meta.url));
const REPO = resolve(TOOL, "../..");

/** 内容包里登记过的题目 ID（只读 items[].id；内容包是本仓库文件，不是 legacy） */
export function loadPackItemIds(path: string): Set<string> {
  const pack = JSON.parse(readFileSync(path, "utf8")) as { items?: { id?: unknown }[] };
  if (!Array.isArray(pack.items)) throw new Error(`${path} 没有 items 数组`);
  return new Set(pack.items.map((i) => i.id).filter((x): x is string => typeof x === "string"));
}

export async function main(argv = process.argv.slice(2)): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      aya: { type: "string" },
      map: { type: "string", multiple: true },
      out: { type: "string" },
      config: { type: "string", default: join(TOOL, "config/aya-chapter-segments.yaml") },
      pack: { type: "string", default: join(REPO, "adapters/aya/content/pack.json") },
      "no-pack": { type: "boolean", default: false },
      vector: { type: "boolean", default: false },
      model: { type: "boolean", default: false },
      "top-k": { type: "string", default: "5" },
      "rules-only-a": { type: "boolean", default: false },
    },
  });
  if (!values.aya || !values.map?.length) {
    console.error("kge-import: 需要 --aya 与至少一个 --map（KGE-01 生成的地图版本种子目录）");
    return 2;
  }
  const map = loadMapInput(values.map.map((m) => resolve(m)));
  const bank = loadBank(values.aya);
  const segments = parseSegmentConfig(readFileSync(values.config!, "utf8"));
  const registered = values["no-pack"] ? null : loadPackItemIds(values.pack!);
  const pipeline = makePipeline(map, buildRuleContext(map, bank.topics), {
    embedder: values.vector ? new FakeNgramEmbedder() : undefined,
    model: values.model ? new FakeTagModel() : undefined,
    topK: Number(values["top-k"]),
    rulesOnlyA: values["rules-only-a"],
  });
  const r = buildItems({ bank, map, segments, registeredItems: registered, pipeline });
  const out = resolve(values.out ?? join(REPO, `db/seeds/kge/aya-items-${map.version}`));
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "items.sql"), r.sql);
  writeFileSync(join(out, "worklist.md"), r.worklistMd);
  writeFileSync(join(out, "tagging-report.json"), r.reportJson);
  const c = r.report.counts;
  console.log(`kge-import: ${c.items_total} 题，已导入主题里 ${c.items_in_scope} 题（A ${c.grade.A} / B ${c.grade.B} / 无处可标 ${c.untaggable}），`
    + `item_tag ${c.item_tag}、候选 ${c.candidate_rows} 行、题组 ${c.families}；六段推不出 ${c.six_segments_missing} 题 → ${out}`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    process.exitCode = await main();
  } catch (e) {
    console.error(`kge-import: ${e instanceof Error ? e.message : String(e)}`);
    process.exitCode = 1;
  }
}
