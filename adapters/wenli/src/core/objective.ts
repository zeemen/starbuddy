// 客观题确定性判分（纯函数，spec 设计要点 3）。
//
// 移植自 legacy（只读）：
//   legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/utils/normalize.ts（normalizeText、stripParenNote、matchFill、
//   matchChoice、matchMulti），调用方 apps/student/app/api/quiz/grade/route.ts（填空走规范化匹配、选择走选项比对）。
// 相对 legacy 的改动：
//   - 选择题的作答是契约 AttemptInput.response.choice（字符串数组），标准答案是选项字母数组：两边都归一成字母后按集合完全相等判定
//     （合并了 legacy matchChoice 的“字母或下标”写法与 matchMulti 的“不多选、不少选、不选错”）；
//   - 没有移植 reflowMastery / reflowAdvice（掌握度回流与建议文案：掌握度由平台计算，规则 2；文案不进结果，规则 3）。
import type { ChoiceItem, FillItem } from "../content.ts";

/** 中英文标点与空白（legacy PUNCT_RE 原样） */
const PUNCT_RE = /[\s　，。、；：？！“”‘’"'（）()《》〈〉【】\[\]{}—…·.,;:?!]/g;

export function normalizeText(s: string): string {
  return s.replace(PUNCT_RE, "").toLowerCase();
}

/** 剥离答案中的括号说明（中英文括号），如「朗润（重读）」→「朗润」 */
function stripParenNote(s: string): string {
  return s.replace(/[（(][^）)]*[）)]/g, "");
}

/**
 * 填空判分：规范化后比对。答案可用竖线 ｜ / | 列多个可接受项（命中任一即对），并自动剥离括号说明。
 * 只用竖线分隔——默写等含句内逗号的单答案不受影响。（legacy matchFill 原样）
 */
export function matchFill(input: string, answer: string): boolean {
  const inp = normalizeText(input);
  const candidates = stripParenNote(answer)
    .split(/[｜|]/)
    .map(normalizeText)
    .filter(Boolean);
  if (candidates.length === 0) return inp === normalizeText(answer);
  return candidates.includes(inp);
}

/** 选项归一：字母（大小写均可）→ 大写字母；一位数字按下标（0 = A，legacy matchChoice 的 toIdx）；其他 → null */
export function normalizeChoice(v: string): string | null {
  const t = v.trim();
  if (/^[A-Za-z]$/.test(t)) return t.toUpperCase();
  if (/^\d$/.test(t)) return String.fromCharCode(65 + Number(t));
  return null;
}

/** 选择判分：两边归一后排序比较，集合完全相等才对（不多选、不少选、不选错；重复选项视为不等） */
export function matchChoices(input: readonly string[], answer: readonly string[]): boolean {
  const got = input.map(normalizeChoice);
  if (got.length === 0 || got.some((x) => x === null)) return false;
  const a = [...(got as string[])].sort().join(",");
  const b = [...answer].map((x) => x.toUpperCase()).sort().join(",");
  return a === b;
}

export type ObjectiveResponse = { choice: string[] } | { text: string };

/** 一道客观题的对错（作答形态与题型不符时由调用方先拒绝） */
export function gradeObjective(item: ChoiceItem | FillItem, response: ObjectiveResponse): boolean {
  if (item.type === "choice") return "choice" in response && matchChoices(response.choice, item.answer);
  return "text" in response && matchFill(response.text, item.answer);
}
