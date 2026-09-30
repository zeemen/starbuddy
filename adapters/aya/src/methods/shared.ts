// 四个新方法共用的小工具。都是纯函数：不读系统时钟、不读写任何外部存储（spec 设计要点第 0 节）。
import type { PackItem } from "../content.ts";
import { ProblemError } from "../problem.ts";

/** 阿鸦题型 → 考情题型编码（spec 待人确认 #7 默认；考情库词表定下来之前请求里不带 question_type_codes 时不过滤） */
export const QUESTION_TYPE_CODE: Record<PackItem["format"], string> = {
  single: "single_choice",
  fill: "fill_blank",
  multi: "multi_choice",
};

/** exam_config.question_type_codes 非空时，只留题型在列表里的题 */
export function formatAllowed(item: PackItem, codes: string[] | undefined): boolean {
  return !codes || codes.length === 0 || codes.includes(QUESTION_TYPE_CODE[item.format]);
}

/** `aya:K-QF-0301` → `K-QF-0301`；前缀不是 aya 时报 SUBJECT_MISMATCH */
export function localId(ref: string): string {
  const i = ref.indexOf(":");
  if (i < 0 || ref.slice(0, i) !== "aya") throw new ProblemError(400, "SUBJECT_MISMATCH", "reference is not an aya reference");
  return ref.slice(i + 1);
}

export const isAyaRef = (ref: string) => ref.startsWith("aya:");

/**
 * 带时区的时刻 → 毫秒（契约 Timestamp 已约束格式）。按时刻比较，不按字符串（评审 N10）：
 * `2026-10-12T08:00:00+08:00` 早于 `2026-10-12T01:00:00Z`。
 */
export function instant(ts: string): number {
  const t = Date.parse(ts);
  if (Number.isNaN(t)) throw new ProblemError(400, "INVALID_REQUEST", "timestamp cannot be parsed");
  return t;
}
