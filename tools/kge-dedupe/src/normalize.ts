// 题目文本规范化（KGE-08 范围 1）：全半角、标点、空白统一；LaTeX 只去无义包装；可选遮蔽数字与字母变量得到“模板文本”。
// 不做语义改写：\frac{a}{b} 与 a/b 不强行等同。

/** NFKC 之后仍不统一的中文标点 → 一个固定写法（NFKC 已把全角 ，：；！？（）＝＋ 与全角数字字母折成半角） */
const PUNCT: ReadonlyMap<string, string> = new Map([
  ["。", "."], ["、", ","], ["“", '"'], ["”", '"'], ["‘", "'"], ["’", "'"], ["「", '"'], ["」", '"'], ["『", '"'], ["』", '"'],
  ["【", "["], ["】", "]"], ["〔", "("], ["〕", ")"], ["《", "<"], ["》", ">"], ["〈", "<"], ["〉", ">"],
  ["—", "-"], ["–", "-"], ["―", "-"], ["－", "-"], ["～", "~"], ["〜", "~"], ["・", "·"], ["•", "·"],
]);

/** LaTeX 的无义包装：\left \right \big…、\displaystyle、\dfrac/\tfrac → \frac、间距命令、数学定界符 $ \( \) \[ \] */
export function stripLatexWrappers(s: string): string {
  return s
    .replace(/\\(?:left|right|bigg|Bigg|big|Big)(?![A-Za-z])/g, "")
    .replace(/\\(?:displaystyle|textstyle)(?![A-Za-z])/g, "")
    .replace(/\\[dt]frac(?![A-Za-z])/g, "\\frac")
    .replace(/\\(?:qquad|quad)(?![A-Za-z])|\\[,;:! ]/g, " ")
    .replace(/\\[()[\]]/g, " ")
    .replace(/\$/g, " ");
}

/** 规范化但保留单个空格（遮蔽要看词边界）；canonicalText / templateText 再去掉空格 */
export function normalizeSpaced(s: string): string {
  let t = s.normalize("NFKC");
  t = Array.from(t, (ch) => PUNCT.get(ch) ?? ch).join("");
  t = stripLatexWrappers(t);
  return t.replace(/\s+/g, " ").trim();
}

/** 规范文本：完全重复与近似判定都用它 */
export function canonicalText(s: string): string {
  return normalizeSpaced(s).replace(/ /g, "");
}

export interface MaskOptions { numbers: boolean; letters: boolean }

/** 模板文本：数字遮成 #，单个拉丁字母（前后不是字母、前面不是反斜杠）遮成 @，再去空白 */
export function templateText(s: string, mask: MaskOptions): string {
  let t = normalizeSpaced(s);
  if (mask.numbers) t = t.replace(/\d+(?:\.\d+)?/g, "#");
  if (mask.letters) t = t.replace(/(?<![A-Za-z\\])[A-Za-z](?![A-Za-z])/g, "@");
  return t.replace(/ /g, "");
}
