// 受限富文本题面（PLT-12，specs/present-item-contract）：学习服务在 LEARNING_PRESENT_ITEM_SOURCE=adapter 时给题面多带一个 rich，
// 内容来自插件契约 1.1.0 候选版 present_item（段落里只有文字、行内公式 LaTeX、图片引用三种片段），学习服务已过两道白名单。
// BFF 转给前端之前再按白名单重建一次（第三道）：只认这几个字段与取值形状，任何多余字段、HTML 标签、控制字符（含不可见与双向控制字符）、
// 危险或前端认不出的 LaTeX 命令、非对象存储的图片地址、总字数超过 8000 都整体拒绝（返回 null）。诊断与再练两条路径一样：不合格就丢掉
// rich，题目照常以纯文字题面出（审查建议 10）。开关关闭时没有 rich，转给前端的内容不变。
// 字符约束与契约 RichTextRun / RichMathRun / RichImageRun 同一写法（tests/item-rich.test.ts 用契约原文核对）。

export type RichInline =
  | { type: 'text'; text: string }
  | { type: 'math'; latex: string }
  | { type: 'image'; image_ref: string; alt?: string };
export interface RichParagraph { type: 'paragraph'; runs: RichInline[] }
export interface RichItem { stem: RichParagraph[]; options?: { key: string; content: RichInline[] }[] }

// 文字与图片说明另排除 C1 控制字符、零宽与方向标记、行/段分隔符、双向文本控制符与 BOM（与契约同一写法，审查建议 4）
export const TEXT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,2000}$/u;
export const ALT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,200}$/u;
export const LATEX_RE =
  /^(?!.*\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|def|gdef|edef|xdef|let|newcommand|renewcommand|providecommand|newenvironment|input|include|write|immediate|catcode|csname)(?![A-Za-z]))[\x20-\x7E]{1,1000}$/u;
export const IMAGE_REF_RE = /^objstore:\/\/[a-z0-9][a-z0-9/_.\-]{0,255}$/u;
const KEY_RE = /^[A-Za-z0-9]{1,8}$/;
/** 题面总字数上限（题干与选项全部 text、latex、alt 按码点合计；契约 PresentedItem 说明） */
export const MAX_TOTAL_CHARS = 8000;
/**
 * 前端能排版的 LaTeX 字母命令（apps/web src/lib/rich-item.ts 的 LATEX_COMMANDS，同一份；apps/web tests/rich-item.test.ts 对拍）。
 * 公式里有不在表里的命令时 rich 整体不合格（审查建议 9、10：认不出的命令不猜，退回纯文字题面）。
 */
export const LATEX_COMMANDS: readonly string[] = [
  'Delta', 'Leftrightarrow', 'Rightarrow', 'alpha', 'angle', 'approx', 'bar', 'because', 'beta', 'cap', 'cdot', 'cdots', 'circ', 'cong',
  'cup', 'degree', 'delta', 'dfrac', 'displaystyle', 'div', 'frac', 'gamma', 'ge', 'geq', 'hat', 'in', 'infty', 'lambda', 'ldots', 'le',
  'left', 'leftarrow', 'leq', 'mathbf', 'mathrm', 'mp', 'mu', 'ne', 'neq', 'notin', 'omega', 'operatorname', 'overline', 'parallel',
  'perp', 'pi', 'pm', 'qquad', 'quad', 'right', 'rightarrow', 'sigma', 'sim', 'sqrt', 'subset', 'text', 'textrm', 'tfrac', 'therefore',
  'theta', 'times', 'to', 'triangle', 'underline', 'vec',
];
const KNOWN_COMMANDS = new Set(LATEX_COMMANDS);

/** 公式里的字母命令都认得（按 TeX 的记号切分：`\\` 后跟一个非字母字符是转义，不是命令） */
export function latexCommandsKnown(latex: string): boolean {
  for (const m of latex.matchAll(/\\([A-Za-z]+|[^A-Za-z])/gsu)) {
    const name = m[1]!;
    if (/^[A-Za-z]+$/u.test(name) && !KNOWN_COMMANDS.has(name)) return false;
  }
  return true;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const only = (o: Record<string, unknown>, keys: readonly string[]) => Object.keys(o).every((k) => keys.includes(k));

function run(v: unknown): RichInline | null {
  if (!isRecord(v)) return null;
  if (v['type'] === 'text' && only(v, ['type', 'text']) && typeof v['text'] === 'string' && TEXT_RE.test(v['text'])) {
    return { type: 'text', text: v['text'] };
  }
  if (v['type'] === 'math' && only(v, ['type', 'latex']) && typeof v['latex'] === 'string' && LATEX_RE.test(v['latex'])
      && latexCommandsKnown(v['latex'])) {
    return { type: 'math', latex: v['latex'] };
  }
  if (v['type'] === 'image' && only(v, ['type', 'image_ref', 'alt']) && typeof v['image_ref'] === 'string' && IMAGE_REF_RE.test(v['image_ref'])) {
    const alt = v['alt'];
    if (alt !== undefined && (typeof alt !== 'string' || !ALT_RE.test(alt))) return null;
    return { type: 'image', image_ref: v['image_ref'], ...(alt !== undefined ? { alt } : {}) };
  }
  return null;
}

function runs(v: unknown, max: number): RichInline[] | null {
  if (!Array.isArray(v) || v.length === 0 || v.length > max) return null;
  const out = v.map(run);
  return out.every((r): r is RichInline => r !== null) ? out : null;
}

/**
 * 白名单重建 rich。optionKeys：这道题纯文字选项的键（rich 的选项必须一一对应）；填空题传空数组。
 * 合格返回重建后的对象；不合格返回 null。
 */
export function pickRich(v: unknown, optionKeys: readonly string[]): RichItem | null {
  if (!isRecord(v) || !only(v, ['stem', 'options']) || !Array.isArray(v['stem']) || v['stem'].length === 0 || v['stem'].length > 20) return null;
  const stem: RichParagraph[] = [];
  for (const p of v['stem']) {
    if (!isRecord(p) || !only(p, ['type', 'runs']) || p['type'] !== 'paragraph') return null;
    const rs = runs(p['runs'], 64);
    if (!rs) return null;
    stem.push({ type: 'paragraph', runs: rs });
  }
  const opts = v['options'];
  if (opts === undefined) return optionKeys.length === 0 && totalChars(stem, []) <= MAX_TOTAL_CHARS ? { stem } : null;
  if (!Array.isArray(opts) || opts.length !== optionKeys.length || opts.length > 10) return null;
  const options: { key: string; content: RichInline[] }[] = [];
  for (const [i, o] of opts.entries()) {
    if (!isRecord(o) || !only(o, ['key', 'content']) || typeof o['key'] !== 'string' || !KEY_RE.test(o['key']) || o['key'] !== optionKeys[i]) return null;
    const content = runs(o['content'], 32);
    if (!content) return null;
    options.push({ key: o['key'], content });
  }
  return totalChars(stem, options) <= MAX_TOTAL_CHARS ? { stem, options } : null;
}

/** 题面总字数：题干与选项全部片段的 text、latex、alt（按码点） */
function totalChars(stem: RichParagraph[], options: { content: RichInline[] }[]): number {
  let n = 0;
  for (const r of [...stem.flatMap((p) => p.runs), ...options.flatMap((o) => o.content)]) {
    n += [...(r.type === 'text' ? r.text : r.type === 'math' ? r.latex : (r.alt ?? ''))].length;
  }
  return n;
}
