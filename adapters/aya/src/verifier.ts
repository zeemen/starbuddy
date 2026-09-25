// 验证器 v0：aya-symbolic-ts@0.1.0（spec 映射规则 grade 表；aya adapter-design §2；decisions #10）。
// 在 legacy gradeAnswer 之上加两件事：
//   1. 边界情况不下对错结论，返回 needs_review 与契约 VerifierReasonCode；
//   2. 给出采样点数与一致点数，作为置信度与 verifier_trace 证据。
// legacy 判为正确的作答，这里一定判为正确（测试逐题核对），唯一的例外是含三角或多字母函数的作答：
// legacy 会把 sin 当成三个变量，可能误判等价，这里改为 needs_review。
import { gradeAnswer, normalizeFill } from "./core/grading.ts";
import { evalConstant, parseExpr, sampleCompare, type SampleComparison } from "./core/symbolic.ts";
import type { PackItem } from "./content.ts";

export const VERIFIER_ID = "aya-symbolic-ts";
export const VERIFIER_VERSION = "0.1.0";
export const VERIFIER_REF = `${VERIFIER_ID}@${VERIFIER_VERSION}`;

export type ReasonCode =
  | "trig_or_multiletter_function"
  | "equation_or_inequality_solving"
  | "proof_or_multi_answer"
  | "parse_failure"
  | "sampling_insufficient"
  | "no_reference_answer";

export interface Verification {
  verdict: "correct" | "incorrect" | "needs_review";
  /** 0–1。key 比对或有确定反例 = 1；采样判等价 = 一致点占比；边界情况 ≤ 0.5 */
  confidence: number;
  sampled: number;
  agreeing: number;
  reasonCodes: ReasonCode[];
  /** 命中的预标注陷阱码，只在 incorrect 时非空 */
  trapsHit: string[];
}

/** 最少一致点数：少于这个数不下“等价”结论（legacy areEquivalent 的保守策略） */
export const MIN_AGREEING = 4;
export const BOUNDARY_MAX_CONFIDENCE = 0.5;

const FUNCTION_RE = /(sin|cos|tan|cot|sec|csc|arc|log|lg|ln|exp)/i;
const RELATION_RE = /[=<>≤≥≠]/;
const MULTI_RE = /[,，;；]|±/;

const needsReview = (codes: ReasonCode[], cmp?: SampleComparison): Verification => ({
  verdict: "needs_review",
  confidence: cmp ? Math.min(BOUNDARY_MAX_CONFIDENCE, cmp.agreeing / cmp.sampled) : 0,
  sampled: cmp?.sampled ?? 0,
  agreeing: cmp?.agreeing ?? 0,
  reasonCodes: codes,
  trapsHit: [],
});

/** 单选、多选：选项 key 集合比对（legacy gradeAnswer），确定性，置信度 1。 */
export function verifyChoice(item: PackItem, given: string[]): Verification {
  const r = gradeAnswer(item, given);
  return {
    verdict: r.correct ? "correct" : "incorrect",
    confidence: 1,
    sampled: 0,
    agreeing: 0,
    reasonCodes: [],
    trapsHit: r.correct ? [] : [...new Set(r.trapsHit)],
  };
}

/** 去掉开头的“x=”之类赋值前缀后，是否还含等号或不等号（legacy normalizeExpr 只去一处前缀） */
function hasRelation(s: string): boolean {
  return RELATION_RE.test(s.trim().replace(/\s+/g, "").replace(/^[a-z]=/i, ""));
}

function splitParts(s: string): string[] {
  return s.replace(/[，；;]/g, ",").split(",").map((p) => p.trim()).filter(Boolean);
}

/** 多答案且全部是常量：按数值多重集比对，确定性。否则返回 null（交给边界处理）。 */
function compareNumericMultiset(raw: string, refs: string[]): boolean | null {
  const mine = splitParts(raw).map(evalConstant);
  if (mine.length === 0 || mine.some((v) => v === null)) return null;
  const sortedMine = (mine as number[]).sort((a, b) => a - b);
  let allNumeric = true;
  for (const ref of refs) {
    const theirs = splitParts(ref).map(evalConstant);
    if (theirs.some((v) => v === null)) {
      allNumeric = false;
      continue;
    }
    const sortedTheirs = (theirs as number[]).sort((a, b) => a - b);
    if (
      sortedTheirs.length === sortedMine.length &&
      sortedTheirs.every((v, i) => Math.abs(v - sortedMine[i]!) <= 1e-6 * Math.max(1, Math.abs(v)))
    ) {
      return true;
    }
  }
  return allNumeric ? false : null;
}

/** 填空题。 */
export function verifyFill(item: PackItem, raw: string): Verification {
  const refs = item.answer.filter((a) => a.trim() !== "");
  if (refs.length === 0) return needsReview(["no_reference_answer"]);
  if (raw.trim() === "") {
    return { verdict: "incorrect", confidence: 1, sampled: 0, agreeing: 0, reasonCodes: [], trapsHit: [] };
  }

  // 1. 与某个标准答案归一后相同：确定判对（legacy normalizeFill）
  const norm = normalizeFill(raw);
  if (refs.some((a) => normalizeFill(a) === norm)) {
    return { verdict: "correct", confidence: 1, sampled: 0, agreeing: 0, reasonCodes: [], trapsHit: [] };
  }
  const legacy = gradeAnswer(item, [raw]);
  const incorrect = (): Verification => ({
    verdict: "incorrect",
    confidence: 1,
    sampled: 0,
    agreeing: 0,
    reasonCodes: [],
    trapsHit: [...new Set(legacy.trapsHit)],
  });
  // 2. 命中预标注的典型错误答案：legacy 按归一化精确匹配，是确定的错
  if (!legacy.correct && legacy.trapsHit.length > 0) return incorrect();

  // 3. 采样判等价不可靠的情况：先于 legacy 的符号路径检查。
  //    legacy 把 sin 解析成 s·i·n 三个变量，sin(2x) 与 2sin(x) 会被误判为等价。
  const codes: ReasonCode[] = [];
  if ([raw, ...refs].some((s) => FUNCTION_RE.test(s))) codes.push("trig_or_multiletter_function");
  if ([raw, ...refs].some(hasRelation)) codes.push("equation_or_inequality_solving");
  if (codes.length) return needsReview(codes);

  // 4. legacy 走符号等价判对：报告一致点最多的那个标准答案的采样结果
  if (legacy.correct) {
    const cmp = bestComparison(raw, refs);
    if (cmp && cmp.agreeing >= MIN_AGREEING) {
      return { verdict: "correct", confidence: cmp.agreeing / cmp.sampled, sampled: cmp.sampled, agreeing: cmp.agreeing, reasonCodes: [], trapsHit: [] };
    }
  }

  if ([raw, ...refs].some((s) => MULTI_RE.test(s))) {
    const numeric = compareNumericMultiset(raw, refs);
    if (numeric === true) return { verdict: "correct", confidence: 1, sampled: 0, agreeing: 0, reasonCodes: [], trapsHit: [] };
    if (numeric === false) return incorrect();
    return needsReview(["proof_or_multi_answer"]);
  }

  try {
    parseExpr(raw);
  } catch {
    return needsReview(["parse_failure"]);
  }
  const comparisons = refs.flatMap((ref) => {
    try {
      return [sampleCompare(raw, ref)];
    } catch {
      return [];
    }
  });
  if (comparisons.length === 0) return needsReview(["parse_failure"]);
  const equal = comparisons
    .filter((c) => c.disagreeing === 0 && c.agreeing >= MIN_AGREEING)
    .sort((a, b) => b.agreeing - a.agreeing)[0];
  if (equal) {
    return { verdict: "correct", confidence: equal.agreeing / equal.sampled, sampled: equal.sampled, agreeing: equal.agreeing, reasonCodes: [], trapsHit: [] };
  }
  // 每个标准答案都至少有一个有限的反例点：确定不等价
  if (comparisons.every((c) => c.disagreeing > 0)) {
    const c = comparisons[0]!;
    return { ...incorrect(), sampled: c.sampled, agreeing: c.agreeing };
  }
  const best = comparisons.filter((c) => c.disagreeing === 0).sort((a, b) => b.agreeing - a.agreeing)[0]!;
  return needsReview(["sampling_insufficient"], best);
}

function bestComparison(raw: string, refs: string[]): SampleComparison | null {
  let best: SampleComparison | null = null;
  for (const ref of refs) {
    try {
      const c = sampleCompare(raw, ref);
      if (c.disagreeing === 0 && (!best || c.agreeing > best.agreeing)) best = c;
    } catch {
      /* 该写法无法解析，跳过 */
    }
  }
  return best;
}
