// C9 词表检查：内容包里全部提示阶梯原文逐条过禁语扫描（specs/release-gate-sprint4 “2026-09-30 复核”；阿鸦 lessons #5）。
//
// 词表不自建：复用冲刺 3-F 收拢后的单一真相源 packages/personas（禁语 A–F，F 取数学口径，按登记表里阿鸦的适用类，
// 与 decisions #7 一致），调用方式与 apps/web 文案检查相同——`python -m personas.guard scan --persona aya --stdin-json`。
// 只读调用，不改词表；guard 不回显原文，报告里也只有题号、等级与规则编号。
// 失败即关闭（fail closed）：解释器不可用、guard 报错、结果条数对不上，一律抛错，不当成“没有命中”。
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import type { ContentPack } from "../content.ts";

export const HINT_SCAN_FORMAT = "aya-hint-scan/1";
export const HINT_SCAN_PERSONA = "aya";
export type HintScanMode = "strict" | "flag";

export interface HintText {
  item_id: string;
  level: number;
  text: string;
}

export interface GuardHit {
  class: string;
  rule_id: string;
}

/** guard scan 的输出（只取用到的字段） */
export interface GuardScanOutput {
  lexicon_version: string;
  guard_version: string;
  persona_id: string;
  results: { source: string; hits: GuardHit[] }[];
}

export type GuardRunner = (texts: string[]) => GuardScanOutput;

export interface HintScanHit {
  item_id: string;
  level: number;
  rule_id: string;
  class: string;
}

/** 构建报告（打印或写文件；不进内容包） */
export interface HintScanReport {
  format: typeof HINT_SCAN_FORMAT;
  check: "C9";
  persona: string;
  lexicon_version: string;
  guard_version: string;
  mode: HintScanMode;
  texts: number;
  hits: HintScanHit[];
  flagged_items: string[];
}

/** 内容包里的记录：用了哪版词表、哪种模式、哪些题被标记 */
export interface HintScanRecord {
  check: "C9";
  persona: string;
  lexicon_version: string;
  guard_version: string;
  mode: HintScanMode;
  texts: number;
  flagged_items: string[];
}

/** 全部提示原文，按题号、等级排序 */
export function hintTexts(pack: Pick<ContentPack, "items">): HintText[] {
  return [...pack.items]
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .flatMap((it) => it.hintPath.map((text, level) => ({ item_id: it.id, level, text })));
}

const REPO_ROOT = resolve(new URL(".", import.meta.url).pathname, "../../../..");

/** 默认的扫描器：本仓 packages/personas 的 guard 命令行（PYTHON 环境变量可指定解释器，默认 python3） */
export function guardRunner(opts: { python?: string; repoRoot?: string } = {}): GuardRunner {
  const python = opts.python ?? process.env.PYTHON ?? "python3";
  const root = opts.repoRoot ?? REPO_ROOT;
  return (texts) => {
    const r = spawnSync(python, ["-m", "personas.guard", "scan", "--persona", HINT_SCAN_PERSONA, "--stdin-json", "--exit-zero"], {
      cwd: root,
      input: JSON.stringify(texts),
      encoding: "utf8",
      maxBuffer: 256 * 1024 * 1024,
      env: { ...process.env, PYTHONPATH: resolve(root, "packages") },
    });
    if (r.error) throw new Error(`C9 词表检查无法运行（${python}）：${r.error.message}`);
    if (r.status !== 0) throw new Error(`C9 词表检查失败（guard 退出码 ${r.status}）：${(r.stderr ?? "").trim().slice(0, 500)}`);
    return JSON.parse(r.stdout) as GuardScanOutput;
  };
}

/** 扫描全部提示原文，把命中映射回（题号, 等级）。结果与输入对不上时抛错 */
export function scanHints(pack: Pick<ContentPack, "items">, mode: HintScanMode, runner: GuardRunner = guardRunner()): HintScanReport {
  const entries = hintTexts(pack);
  const out = runner(entries.map((e) => e.text));
  if (out.persona_id !== HINT_SCAN_PERSONA) throw new Error(`C9 词表检查的角色不是 ${HINT_SCAN_PERSONA}`);
  if (!Array.isArray(out.results) || out.results.length !== entries.length) throw new Error("C9 词表检查的结果条数与提示条数不一致");
  if (typeof out.lexicon_version !== "string" || typeof out.guard_version !== "string") throw new Error("C9 词表检查没有给出词表版本");
  const hits: HintScanHit[] = [];
  out.results.forEach((r, i) => {
    if (r.source !== `stdin[${i}]`) throw new Error("C9 词表检查的结果顺序与输入不一致");
    const e = entries[i]!;
    for (const h of r.hits) hits.push({ item_id: e.item_id, level: e.level, rule_id: h.rule_id, class: h.class });
  });
  hits.sort((a, b) => (a.item_id < b.item_id ? -1 : a.item_id > b.item_id ? 1 : a.level - b.level || (a.rule_id < b.rule_id ? -1 : a.rule_id > b.rule_id ? 1 : 0)));
  return {
    format: HINT_SCAN_FORMAT,
    check: "C9",
    persona: HINT_SCAN_PERSONA,
    lexicon_version: out.lexicon_version,
    guard_version: out.guard_version,
    mode,
    texts: entries.length,
    hits,
    flagged_items: [...new Set(hits.map((h) => h.item_id))],
  };
}

/** 严格模式有命中就失败（列出题号与等级）；非严格模式放行，命中的题记在内容包的 hint_scan.flagged_items 里 */
export function enforceHintScan(report: HintScanReport): HintScanRecord {
  if (report.mode === "strict" && report.hits.length > 0) {
    const list = report.hits.slice(0, 50).map((h) => `${h.item_id}[${h.level}] ${h.rule_id}`);
    throw new Error(`C9 词表检查（严格模式）：${report.flagged_items.length} 道题的提示命中禁语：\n  ${list.join("\n  ")}`);
  }
  return {
    check: "C9",
    persona: report.persona,
    lexicon_version: report.lexicon_version,
    guard_version: report.guard_version,
    mode: report.mode,
    texts: report.texts,
    flagged_items: report.flagged_items,
  };
}
