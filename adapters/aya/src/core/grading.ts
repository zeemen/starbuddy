// 复制自 legacy/aya/lib/grading.ts（ayaheshui 提交 0ed2fe4）。legacy 只读，本文件是副本。
// 改动：import 路径改为带 .ts 后缀、类型改从 ./types.ts 引入（Node 直接运行 TS 需要）；逻辑未改。
import type { Question } from "./types.ts";
import { areEquivalent, isAlgebraic } from "./symbolic.ts";

/** 归一化填空答案：去空格/中文标点/单位字，分数 -0.5 与 -1/2 等价，多值忽略顺序 */
export function normalizeFill(raw: string): string {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/，/g, ",")
    .replace(/[；;]/g, ",")
    .replace(/\s+/g, "")
    .replace(/[米人元件个%％]/g, (m) => (m === "%" || m === "％" ? "%" : ""))
    .replace(/^x=|^k=|^m=|^r=/g, "");
  const parts = cleaned
    .split(",")
    .filter(Boolean)
    .map(normalizeNumberToken)
    .sort();
  return parts.join(",");
}

function normalizeNumberToken(tok: string): string {
  // 分数 → 小数（能整除时），保持 -1/2 与 -0.5 一致
  const frac = tok.match(/^(-?)(\d+)\/(\d+)$/);
  if (frac) {
    const val = (frac[1] === "-" ? -1 : 1) * (Number(frac[2]) / Number(frac[3]));
    return trimNumber(val);
  }
  const pct = tok.match(/^(-?[\d.]+)%$/);
  if (pct) return trimNumber(Number(pct[1]) / 100);
  const num = Number(tok);
  if (!Number.isNaN(num)) return trimNumber(num);
  return tok;
}

function trimNumber(n: number): string {
  return Number(n.toFixed(6)).toString();
}

export interface GradeResult {
  correct: boolean;
  trapsHit: string[];
}

/** 判定作答并识别触发的错误陷阱 */
export function gradeAnswer(q: Question, given: string[]): GradeResult {
  if (q.format === "fill") {
    const raw = given[0] ?? "";
    const norm = normalizeFill(raw);
    let ok = q.answer.some((a) => normalizeFill(a) === norm);
    // 符号验证回退（11.2）：数值归一不匹配时，若为代数表达式则判等价
    // （如 2x-2 ≡ 2(x-1)、(x-1)^2 ≡ x^2-2x+1）。单表达式（无逗号）才走此路径。
    if (!ok && !raw.includes(",") && isAlgebraic(raw)) {
      ok = q.answer.some((a) => !a.includes(",") && isAlgebraic(a) && areEquivalent(raw, a));
    }
    if (ok) return { correct: true, trapsHit: [] };
    const traps: string[] = [];
    for (const wt of q.wrongAnswerTraps ?? []) {
      if (wt.pattern.some((p) => normalizeFill(p) === norm)) traps.push(...wt.traps);
    }
    return { correct: false, trapsHit: traps };
  }
  // single / multi：answer 为正确 key 集合
  const givenSet = new Set(given);
  const answerSet = new Set(q.answer);
  const correct = givenSet.size === answerSet.size && [...givenSet].every((k) => answerSet.has(k));
  if (correct) return { correct: true, trapsHit: [] };
  const traps: string[] = [];
  for (const opt of q.options ?? []) {
    if (givenSet.has(opt.key) && !answerSet.has(opt.key) && opt.traps) traps.push(...opt.traps);
  }
  return { correct: false, trapsHit: traps };
}
