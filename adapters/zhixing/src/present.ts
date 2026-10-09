// present_item（契约 1.1.0 候选版，PLT-12：specs/present-item-contract/spec.md）：道法主观题的题面。
// 只读、确定性：不调模型、不判分、不产生证据、不读时钟。题面按白名单投影：只取材料（material_text，按换行分段）与设问
// （prompt_question），都作纯文字片段；采分点、关键词族、给分规则、常见失分、Rubric 版本、满分与标题一律不碰。
// 作答格式：文字，长度上限与 grade 收的作答上限一致（MAX_ANSWER_CHARS）。
import type { LoadedPack, PackItem } from "./content.ts";

export const PRESENT_ALGORITHM = "zhixing-present@0.1.0";

/**
 * 与契约 RichTextRun.text 同一写法（不含尖括号、控制字符（含 C1）、零宽与方向标记、行/段分隔符、双向文本控制符与 BOM，
 * 1–2000 个字符）；内容包加载时用它核对材料与设问
 */
export const TEXT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,2000}$/u;
/** 题面总字数上限（契约 PresentedItem 说明：题干与选项全部文字合计） */
export const MAX_TOTAL_CHARS = 8000;

export interface PresentedItem {
  item_ref: string;
  item_version: string;
  item_type: "short_answer";
  content_state: "complete";
  stem: { type: "paragraph"; runs: { type: "text"; text: string }[] }[];
  answer_format: { kind: "text"; max_length: number };
  license_state: "original" | "unverified";
}

/** 材料按换行分段（去掉空段与首尾空白），设问单独一段 */
export function stemParagraphs(item: PackItem): string[] {
  return [...item.material_text.split(/\r?\n+/u), item.prompt_question].map((s) => s.trim()).filter((s) => s.length > 0);
}

/** 内容包加载时的核对：每一段都必须是契约允许的纯文字（问题列表，空 = 合格） */
export function presentationProblems(item: PackItem): string[] {
  const paras = stemParagraphs(item);
  if (paras.length === 0 || paras.length > 20) return [`题目 ${item.id} 的题面段数不在 1–20`];
  if (paras.some((p) => !TEXT_RE.test(p))) return [`题目 ${item.id} 的材料或设问含尖括号、控制字符、不可见字符或单段过长`];
  const total = paras.reduce((n, p) => n + [...p].length, 0);
  return total > MAX_TOTAL_CHARS ? [`题目 ${item.id} 的题面超过 ${MAX_TOTAL_CHARS} 字`] : [];
}

export function presentItem(content: LoadedPack, item: PackItem, maxAnswerChars: number): PresentedItem {
  return {
    item_ref: `zhixing:${item.id}`,
    item_version: `${content.pack.pack_name}@${content.pack.pack_version}`,
    item_type: "short_answer",
    content_state: "complete",
    stem: stemParagraphs(item).map((text) => ({ type: "paragraph", runs: [{ type: "text", text }] })),
    answer_format: { kind: "text", max_length: maxAnswerChars },
    // 合成内容包（教研自编的虚构材料）→ original；真实题目的版权状态随内容包来源登记再定（PLT-12 不做版权核对）
    license_state: content.pack.synthetic && item.synthetic ? "original" : "unverified",
  };
}
