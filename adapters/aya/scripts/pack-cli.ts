// 生成内容包的共同收尾（build-content-pack.ts 与 rebuild-content-pack.ts 共用）：
//   读准入决定文件（可选）→ 按决定重建准入与来源 → C9 词表检查提示原文 → 规范化序列化 → 写文件。
//
// 参数（也可用环境变量）：
//   --decisions <文件>    准入决定文件（AYA_ADMISSION_DECISIONS）；缺省时若 content/admission-decisions.json 存在就用它，否则不套用
//   --no-decisions        明确不套用任何决定（全部题目回到 source_unverified）
//   --c9 strict|flag      C9 模式（AYA_C9_MODE），默认 strict：提示命中禁语就失败、不写文件；flag 放行并把命中的题记在 hint_scan.flagged_items
//   --report <文件>       把 C9 报告（题号、等级、规则编号，不含原文）写成 JSON
//   --out <文件>          输出路径，默认 content/pack.json
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import type { ContentPack } from "../src/content.ts";
import { indexPack } from "../src/content.ts";
import { applyAdmissionDecisions, parseAdmissionDecisions, serializePack } from "../src/pack/admission.ts";
import { enforceHintScan, scanHints, type HintScanMode } from "../src/pack/hint-scan.ts";

const HERE = new URL(".", import.meta.url).pathname;
export const DEFAULT_OUT = resolve(HERE, "../content/pack.json");
export const DEFAULT_DECISIONS = resolve(HERE, "../content/admission-decisions.json");

export interface PackCliOptions {
  decisions: string | null;
  c9: HintScanMode;
  report: string | null;
  out: string;
  pack: string;
}

export function packCliOptions(argv: string[] = process.argv.slice(2)): PackCliOptions {
  const { values } = parseArgs({
    args: argv,
    options: {
      decisions: { type: "string" },
      "no-decisions": { type: "boolean", default: false },
      c9: { type: "string" },
      report: { type: "string" },
      out: { type: "string" },
      pack: { type: "string" },
    },
    strict: true,
  });
  const c9 = values.c9 ?? process.env.AYA_C9_MODE ?? "strict";
  if (c9 !== "strict" && c9 !== "flag") throw new Error("--c9 只能是 strict 或 flag");
  const explicit = values.decisions ?? process.env.AYA_ADMISSION_DECISIONS;
  const decisions = values["no-decisions"] ? null : explicit ? resolve(explicit) : existsSync(DEFAULT_DECISIONS) ? DEFAULT_DECISIONS : null;
  return {
    decisions,
    c9,
    report: values.report ? resolve(values.report) : null,
    out: values.out ? resolve(values.out) : DEFAULT_OUT,
    pack: values.pack ? resolve(values.pack) : DEFAULT_OUT,
  };
}

/** 套用决定、做 C9、校验、写文件；返回写出的内容包 */
export function finishPack(base: ContentPack, opts: PackCliOptions, label: string): ContentPack {
  const ids = new Set(base.items.map((i) => i.id));
  const file = opts.decisions ? parseAdmissionDecisions(JSON.parse(readFileSync(opts.decisions, "utf8")), base.curriculum, ids) : null;
  const rebuilt = applyAdmissionDecisions(base, file);
  const report = scanHints(rebuilt, opts.c9);
  if (opts.report) writeFileSync(opts.report, JSON.stringify(report, null, 1) + "\n");
  const pack: ContentPack = { ...rebuilt, hint_scan: enforceHintScan(report) };
  indexPack(pack); // 写出前按加载时同样的规则校验一遍
  writeFileSync(opts.out, serializePack(pack));
  const counts = pack.items.reduce<Record<string, number>>((m, i) => ((m[i.admission] = (m[i.admission] ?? 0) + 1), m), {});
  console.log(
    `${label}: ${pack.items.length} 道题 → ${opts.out}（${pack.pack_version}；准入 ${JSON.stringify(counts)}；` +
      `决定文件 ${file ? `${file.export_ref}，${file.decisions.length} 条` : "无"}）`,
  );
  console.log(
    `${label}: C9 词表检查（${report.mode === "strict" ? "严格" : "非严格：只标记"}，persona ${report.persona}，词表 ${report.lexicon_version}）` +
      `扫描 ${report.texts} 条提示，命中 ${report.hits.length} 处、${report.flagged_items.length} 道题` +
      (report.flagged_items.length ? `：${report.flagged_items.join("、")}` : ""),
  );
  return pack;
}

export function runCli(main: () => Promise<void> | void, label: string): void {
  Promise.resolve()
    .then(main)
    .catch((e: unknown) => {
      console.error(`${label}: ${e instanceof Error ? e.message : String(e)}`);
      process.exit(1);
    });
}
