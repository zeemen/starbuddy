// teach v0（specs/adapter-aya-methods-sprint4/spec.md 设计要点第 4 节；待人确认 #1 默认）：确定性，不调模型，不写任何话术。
// - hint：提示阶梯 aya-hint-ladder@0.1.0，只取内容包里该题该级的提示原文（legacy hintPath，索引即等级）；
// - lesson：知识页面直出 aya-page-lesson@0.1.0，只切分编排层放进请求的已发布页面 body_md，原文不改、不加阿鸦话术。
// 两种模式都不读、不存、不回显 conversation、recent_evidence、persona（只参与请求体指纹）；不做身份披露与转介
// （由平台人设层负责）；不输出 verified_statements；不判对错，不写关于学生的判断（评审 sie-#3）；不产生证据。
import type { LoadedPack, PackItem } from "../content.ts";
import { ProblemError } from "../problem.ts";
import { localId } from "./shared.ts";

export const HINT_ALGORITHM = "aya-hint-ladder@0.1.0";
export const PAGE_LESSON_ALGORITHM = "aya-page-lesson@0.1.0";
/** 作答提交之后最多给到第 5 级；第 6 级（变式题）交给 generate_variation（规格书 §3.1） */
export const MAX_HINT_LEVEL = 5;
/**
 * 作答提交之前最多给到第几级（配置项 max_hint_level_before_submit，默认 3）。legacy 的 4–5 级是“参考结论”或完整示范，
 * 338 道填空题里 273 道含标准答案（decisions #4：答案不下发到客户端）。
 * v1.0 候选版的 TeachRequest 没有表示“作答已提交”的字段，所以目前一律按“未提交”处理：4 级及以上都不给
 * （放开方式需契约支持，lessons 待人确认）。配置只能往下调，不能超过 3。
 */
export const DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT = 3;
/** 0–3 级提示不得出现标准答案原文（spec：防止提前露答案） */
export const ANSWER_GUARD_MAX_LEVEL = 3;
/**
 * 不给提示原文、只指回题目时的原因编码。契约 TeachResult 与信封都不允许附加字段，ReviewReason 里也没有对应的值，
 * 所以这个编码目前**写不进响应**，只在适配器内部用于测试与说明（lessons 待人确认：需契约给出位置）。
 */
export const HINT_WITHHELD_REASONS = ["hint_reveals_answer"] as const;
export type HintWithheldReason = (typeof HINT_WITHHELD_REASONS)[number];

export interface TeachConfig {
  max_hint_level_before_submit: number;
}
export const DEFAULT_TEACH_CONFIG: TeachConfig = { max_hint_level_before_submit: DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT };

/** 配置只能在 0–3 之间：超过 3 就等于在作答提交前放出含答案的示范级 */
export function checkTeachConfig(c: TeachConfig): TeachConfig {
  const m = c.max_hint_level_before_submit;
  if (!Number.isInteger(m) || m < 0 || m > DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT) {
    throw new Error(`max_hint_level_before_submit 必须是 0–${DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT} 的整数`);
  }
  return c;
}
/** 契约 TeachSegment.text 的上限 */
export const SEGMENT_MAX_CHARS = 4000;

export type TeachSegment = { type: "text"; text: string } | { type: "item_ref"; item_ref: string };
export interface TeachOutcome {
  segments: TeachSegment[];
  hintLevelUsed: number;
  /** 不给提示原文、只指回题目时的原因（写不进响应，见 HINT_WITHHELD_REASONS） */
  withheld?: HintWithheldReason;
  /** 提示引用的题（lesson 没有） */
  item?: PackItem;
}

const compact = (s: string) => s.replace(/[\s$]/g, "");
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * 提示里是否出现了标准答案原文（比较在进程内完成，答案不出进程）。去掉空白与 `$` 后比较：
 * - 两个字符以上的答案按子串查；
 * - 单个字符的答案按“独立记号”查（前后不是字母、数字、小数点、上下标、反斜杠），避免 `x^2` 里的 2 这类误判。
 */
export function hintRevealsAnswer(hint: string, answers: string[]): boolean {
  const h = compact(hint);
  return answers.some((a) => {
    const c = compact(a);
    if (c.length === 0) return false;
    if (c.length >= 2) return h.includes(c);
    return new RegExp(`(?<![A-Za-z0-9.^_\\\\])${escapeRe(c)}(?![A-Za-z0-9.^_])`).test(h);
  });
}

/**
 * 实际输出的等级：min(allowed, 封顶, 最高一级)。封顶 = 作答提交前的上限（默认 3）；v1.0 没有“已提交”的信号，
 * 所以不存在提交后的分支。返回 -1 表示这一级含标准答案原文：不降级找别的等级，而是整条不给，只指回题目。
 */
export function hintLevelFor(item: PackItem, allowed: number, maxBeforeSubmit = DEFAULT_MAX_HINT_LEVEL_BEFORE_SUBMIT): number {
  const level = Math.min(allowed, maxBeforeSubmit, MAX_HINT_LEVEL, item.hintPath.length - 1);
  // 选择题的答案是选项 key，提示原文里的 A/B/C/D 不构成泄露
  if (item.format === "fill" && level <= ANSWER_GUARD_MAX_LEVEL && hintRevealsAnswer(item.hintPath[level]!, item.answer)) return -1;
  return level;
}

export function teachHint(
  content: LoadedPack,
  req: { skill_node: string; item_ref?: string; allowed_hint_level: number },
  config: TeachConfig = DEFAULT_TEACH_CONFIG,
): TeachOutcome {
  if (req.item_ref === undefined) throw new ProblemError(422, "INVALID_REQUEST", "hint mode needs item_ref");
  const item = content.itemById.get(localId(req.item_ref));
  if (!item) throw new ProblemError(404, "ITEM_NOT_FOUND", "item_ref is not in the content pack");
  if (!item.mainNodes.includes(localId(req.skill_node))) {
    throw new ProblemError(422, "INVALID_REQUEST", "skill_node is not a main node of item_ref");
  }
  const level = hintLevelFor(item, req.allowed_hint_level, config.max_hint_level_before_submit);
  // 这一级含答案：不返回原文，只指回题目本身（74 道题，清单见 lessons.md，待教研改题）
  if (level < 0) {
    return { segments: [{ type: "item_ref", item_ref: `aya:${item.id}` }], hintLevelUsed: 0, item, withheld: "hint_reveals_answer" };
  }
  // 不越级：只返回这一级的那一条，不附带更高等级（规格书 §2.3、§5.2 F 类“越级提示”）
  return { segments: [{ type: "text", text: item.hintPath[level]! }], hintLevelUsed: level, item };
}

/** 按代码点切成不超过 max 的若干段（不拆开代理对） */
function chunk(text: string, max: number): string[] {
  const cps = Array.from(text);
  if (cps.length <= max) return [text];
  const out: string[] = [];
  for (let i = 0; i < cps.length; i += max) out.push(cps.slice(i, i + max).join(""));
  return out;
}

/** 已发布页面按空行切段：段内原文不改，段间空行去掉；超长段按 4000 字再切 */
export function splitPage(bodyMd: string): string[] {
  return bodyMd
    .split(/\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*/)
    .filter((p) => p.trim() !== "")
    .flatMap((p) => chunk(p, SEGMENT_MAX_CHARS));
}

export function teachLesson(content: LoadedPack, req: { skill_node: string; knowledge_page?: { body_md: string } }): TeachOutcome {
  // v0 不自己生成讲解：没有页面就是不支持的模式
  if (!req.knowledge_page) throw new ProblemError(422, "MODE_NOT_SUPPORTED", "lesson mode needs a published knowledge_page in the request");
  if (!content.nodeById.has(localId(req.skill_node))) throw new ProblemError(422, "INVALID_REQUEST", "skill_node is not in the content pack");
  const parts = splitPage(req.knowledge_page.body_md);
  if (parts.length === 0) throw new ProblemError(422, "INVALID_REQUEST", "knowledge_page body is empty");
  return { segments: parts.map((text) => ({ type: "text", text })), hintLevelUsed: 0 };
}
