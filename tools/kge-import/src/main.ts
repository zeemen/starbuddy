// KGE-02 题库导入与打标入口（specs/kge-02-atoms-tagging/spec.md 设计要点 1、2、6）。
// 用法：
//   node src/main.ts --aya <阿鸦 legacy 根> --map <地图版本种子目录> [--map <另一个专题的种子目录> ...] [--out <输出目录>]
//                    [--config config/aya-chapter-segments.yaml] [--pack <内容包 pack.json> | --no-pack]
//                    [--vector] [--model] [--top-k 5] [--rules-only-a]
// 只读 legacy（隔离子进程加载）与仓库里的种子；不连库、不联网、不调用任何真实模型。
// --vector / --model 只接假实现（不是生产模型，见 src/tagging/vector.ts、model.ts），用于把流水线打通；
// 真实路由的桩（RoutedEmbedder、RoutedTagModel）调用即抛错，这里不接。
// 启用任何假层时：必须显式给 --out，且 --out 不能在仓库的 db/seeds/ 之下（拒绝把假实现产物写进种子目录）；产物标注“含假实现，不得执行”。
// 输出 items.sql、worklist.md、tagging-report.json，默认（只跑规则层时）写到 db/seeds/kge/aya-items-<地图版本号>/。
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
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

/** 路径的真实位置：从最近的已存在祖先目录取 realpath，再拼上还不存在的部分（防止借符号链接绕进种子目录） */
function realish(p: string): string {
  let head = resolve(p);
  const tail: string[] = [];
  while (!existsSync(head)) {
    const parent = dirname(head);
    if (parent === head) break;
    tail.unshift(basename(head));
    head = parent;
  }
  return join(existsSync(head) ? realpathSync(head) : head, ...tail);
}

const isInside = (child: string, parent: string): boolean => {
  const rel = relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
};

/** 假实现产物的输出目录核对：必须显式给出，且不能在仓库 db/seeds/ 之下；返回错误信息，没问题返回 null */
export function checkFakeOut(out: string | undefined, fakeFlags: string[], repo = REPO): string | null {
  if (fakeFlags.length === 0) return null;
  const flags = fakeFlags.join(" / ");
  if (out === undefined || out.trim() === "") {
    return `${flags} 只接假实现，产物含假实现、不得执行：必须用 --out 显式指定仓库 db/seeds/ 之外的输出目录`;
  }
  const seeds = resolve(repo, "db/seeds");
  const target = resolve(out);
  const seedsReal = existsSync(seeds) ? realpathSync(seeds) : seeds;
  if (isInside(target, seeds) || isInside(realish(target), seedsReal)) {
    return `${flags} 只接假实现，拒绝把含假实现的产物写进种子目录 db/seeds/（--out ${out}）`;
  }
  return null;
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
  const fakeFlags = [...(values.vector ? ["--vector"] : []), ...(values.model ? ["--model"] : [])];
  const outError = checkFakeOut(values.out, fakeFlags);
  if (outError) {
    console.error(`kge-import: ${outError}`);
    return 2;
  }
  const map = loadMapInput(values.map.map((m) => resolve(m)));
  const bank = loadBank(values.aya);
  const segments = parseSegmentConfig(readFileSync(values.config!, "utf8"));
  const registered = values["no-pack"] ? null : loadPackItemIds(values.pack!);
  const pipeline = await makePipeline(map, buildRuleContext(map, bank.topics), {
    embedder: values.vector ? new FakeNgramEmbedder() : undefined,
    model: values.model ? new FakeTagModel() : undefined,
    topK: Number(values["top-k"]),
    rulesOnlyA: values["rules-only-a"],
  });
  const r = await buildItems({ bank, map, segments, registeredItems: registered, pipeline });
  const out = resolve(values.out ?? join(REPO, `db/seeds/kge/aya-items-${map.version}`));
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "items.sql"), r.sql);
  writeFileSync(join(out, "worklist.md"), r.worklistMd);
  writeFileSync(join(out, "tagging-report.json"), r.reportJson);
  const c = r.report.counts;
  console.log(`kge-import: ${c.items_total} 题，已导入主题里 ${c.items_in_scope} 题（A ${c.grade.A} / B ${c.grade.B} / 无处可标 ${c.untaggable}），`
    + `item_tag ${c.item_tag}、候选 ${c.candidate_rows} 行、题组 ${c.families}；六段推不出 ${c.six_segments_missing} 题 → ${out}`);
  if (r.report.warning) console.error(`kge-import: 注意：${r.report.warning}`);
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
