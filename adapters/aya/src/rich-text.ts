// 受限富文本（契约 1.1.0 候选版 RichText / RichInline，PLT-12 present_item）：段落里只有文字、行内公式（LaTeX）、图片引用三种片段。
// 内容包加载时用 richTextProblems 检查（不合格拒绝启动），出门前用 projectRuns / projectParagraphs 按白名单重建（多余字段一律丢掉）。
// 字符约束与契约同一写法：文字不含尖括号与控制字符；公式只许可打印 ASCII，且不许链接、图片、宏定义、文件读写与 HTML 类命令；
// 图片只给对象存储引用。契约改了这里要一起改（tests/present.test.ts 用契约原文核对这几条正则）。

export interface RichTextRun {
  type: "text";
  text: string;
}
export interface RichMathRun {
  type: "math";
  latex: string;
}
export interface RichImageRun {
  type: "image";
  image_ref: string;
  alt?: string;
}
export type RichInline = RichTextRun | RichMathRun | RichImageRun;
export interface RichParagraph {
  type: "paragraph";
  runs: RichInline[];
}

// 文字与图片说明另排除 C1 控制字符、零宽与方向标记、行/段分隔符、双向文本控制符与 BOM（审查建议 4）
export const TEXT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,2000}$/u;
export const ALT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,200}$/u;
export const LATEX_RE =
  /^(?!.*\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|def|gdef|edef|xdef|let|newcommand|renewcommand|providecommand|newenvironment|input|include|write|immediate|catcode|csname)(?![A-Za-z]))[\x20-\x7E]{1,1000}$/u;
export const IMAGE_REF_RE = /^objstore:\/\/[a-z0-9][a-z0-9/_.\-]{0,255}$/u;
export const MAX_PARAGRAPHS = 20;
export const MAX_RUNS = 64;
export const MAX_OPTION_RUNS = 32;
/** 题面总字数上限（题干与选项全部 text、latex、alt 按码点合计；契约 PresentedItem 说明） */
export const MAX_TOTAL_CHARS = 8000;

/** 一组片段的字数（text、latex、alt 按 Unicode 码点） */
export function runsChars(runs: readonly RichInline[]): number {
  let n = 0;
  for (const r of runs) {
    const s = r.type === "text" ? r.text : r.type === "math" ? r.latex : (r.alt ?? "");
    n += [...s].length;
  }
  return n;
}

/** 片段里的纯文字（文字原样、公式写源码、图片写说明），用于答案词扫描 */
export function runsPlain(runs: readonly RichInline[]): string {
  return runs.map((r) => (r.type === "text" ? r.text : r.type === "math" ? r.latex : (r.alt ?? ""))).join(" ");
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const onlyKeys = (o: Record<string, unknown>, keys: readonly string[]) => Object.keys(o).every((k) => keys.includes(k));

/** 一个片段的问题（空数组 = 合格） */
export function runProblems(run: unknown, where: string): string[] {
  if (!isObject(run)) return [`${where} 不是对象`];
  switch (run.type) {
    case "text":
      if (!onlyKeys(run, ["type", "text"])) return [`${where} 有多余字段`];
      return typeof run.text === "string" && TEXT_RE.test(run.text) ? [] : [`${where} 的文字含尖括号、控制字符或过长`];
    case "math":
      if (!onlyKeys(run, ["type", "latex"])) return [`${where} 有多余字段`];
      return typeof run.latex === "string" && LATEX_RE.test(run.latex) ? [] : [`${where} 的公式不在允许的 LaTeX 范围内`];
    case "image": {
      if (!onlyKeys(run, ["type", "image_ref", "alt"])) return [`${where} 有多余字段`];
      const out: string[] = [];
      if (typeof run.image_ref !== "string" || !IMAGE_REF_RE.test(run.image_ref)) out.push(`${where} 的图片引用不是对象存储引用`);
      if (run.alt !== undefined && (typeof run.alt !== "string" || !ALT_RE.test(run.alt))) out.push(`${where} 的图片说明不是纯文本`);
      return out;
    }
    default:
      return [`${where} 的片段类型未知`];
  }
}

export function runsProblems(runs: unknown, where: string, max: number): string[] {
  if (!Array.isArray(runs) || runs.length === 0 || runs.length > max) return [`${where} 必须有 1–${max} 个片段`];
  return runs.flatMap((r, i) => runProblems(r, `${where}[${i}]`));
}

export function richTextProblems(stem: unknown, where: string): string[] {
  if (!Array.isArray(stem) || stem.length === 0 || stem.length > MAX_PARAGRAPHS) return [`${where} 必须有 1–${MAX_PARAGRAPHS} 段`];
  return stem.flatMap((p, i) => {
    if (!isObject(p) || p.type !== "paragraph" || !onlyKeys(p, ["type", "runs"])) return [`${where}[${i}] 不是段落`];
    return runsProblems(p.runs, `${where}[${i}].runs`, MAX_RUNS);
  });
}

/** 白名单重建一个片段：只取契约列出的字段（内容包已检查过，这里不再报错，只丢多余字段） */
export function projectRun(run: RichInline): RichInline {
  if (run.type === "text") return { type: "text", text: run.text };
  if (run.type === "math") return { type: "math", latex: run.latex };
  return { type: "image", image_ref: run.image_ref, ...(run.alt !== undefined ? { alt: run.alt } : {}) };
}

export const projectRuns = (runs: readonly RichInline[]): RichInline[] => runs.map(projectRun);

export const projectParagraphs = (stem: readonly RichParagraph[]): RichParagraph[] =>
  stem.map((p) => ({ type: "paragraph", runs: projectRuns(p.runs) }));
