// 受限富文本题面（PLT-12，specs/present-item-contract）：学习服务在 LEARNING_PRESENT_ITEM_SOURCE=adapter 时题面多带 rich
// （插件契约 1.1.0 候选版 present_item：段落里只有文字、行内公式 LaTeX、图片引用三种片段），BFF 已按白名单重建。
// 前端再取一次白名单（pickRich），渲染只走 React 文本节点（src/shell/RichItem.tsx，不用 dangerouslySetInnerHTML），没有 HTML 注入的路径。
// 公式：仓库里没有 KaTeX（引入要另走依赖许可审批），这里用等价的纯文本排版 latexToText：常见命令换成 Unicode 符号，上下标换成上下标字符，
// \frac 写成 (a)/(b)，\sqrt 写成 √(x)。认不出的命令（不在 LATEX_COMMANDS 里）不猜：latexToText 返回 null，pickRich 把整道题的
// rich 当作不合格，退回纯文字题面照常出题（与 BFF 诊断、再练两条路径同一口径；BFF 用同一份命令表，PLT-12 审查建议 9、10）。
// 图片：对象存储引用浏览器取不到（还没有取图接口），只显示图片说明。

export type RichInline =
  | { type: 'text'; text: string }
  | { type: 'math'; latex: string }
  | { type: 'image'; image_ref: string; alt?: string };
export interface RichParagraph { type: 'paragraph'; runs: RichInline[] }
export interface RichItem { stem: RichParagraph[]; options?: { key: string; content: RichInline[] }[] }

/**
 * 答案类字段名：题面里永远不该出现（运行时 DOM 泄露核对 e2e/dom-leak.ts 与组件渲染测试都用这一份）。
 * 这是共享库 starbuddy_contract.present.ANSWER_FIELD_NAMES 的**刻意子集**：DOM 泄露核对是在整页渲染结果（HTML 文本与属性）里
 * 按子串找这些名字，所以只收不会在正常页面文字、类名、路由里出现的长名字；answer、hint、score、points、analysis 这类短词在页面上
 * 本来就有（例如作答框的 name、提示按钮、“得分”），按子串找只会误报。短名字由上游三道白名单（适配器投影、契约 schema 与共享库、
 * 学习服务与 BFF 的逐字段重建）按字段拦住，到不了浏览器。
 */
export const ANSWER_FIELD_NAMES: readonly string[] = [
  'answer_key', 'correct_answer', 'correct_options', 'is_correct', 'standard_answer', 'reference_answer', 'worked_solution',
  'scoring_point', 'scoring_points', 'rubric_version', 'hint_path', 'wrong_answer_traps', 'distractor_reason',
];

// 文字与图片说明另排除 C1 控制字符、零宽与方向标记、行/段分隔符、双向文本控制符与 BOM（与契约同一写法，审查建议 4）
const TEXT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,2000}$/u;
const ALT_RE = /^[^<>\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u2028\u2029\u202A-\u202E\u2066-\u2069\uFEFF]{1,200}$/u;
/** 题面总字数上限（题干与选项全部 text、latex、alt 按码点合计；契约 PresentedItem 说明） */
export const MAX_TOTAL_CHARS = 8000;
export const LATEX_RE =
  /^(?!.*\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|def|gdef|edef|xdef|let|newcommand|renewcommand|providecommand|newenvironment|input|include|write|immediate|catcode|csname)(?![A-Za-z]))[\x20-\x7E]{1,1000}$/u;
const IMAGE_REF_RE = /^objstore:\/\/[a-z0-9][a-z0-9/_.\-]{0,255}$/u;
const KEY_RE = /^[A-Za-z0-9]{1,8}$/;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const only = (o: Record<string, unknown>, keys: readonly string[]) => Object.keys(o).every((k) => keys.includes(k));

function run(v: unknown): RichInline | null {
  if (!isRecord(v)) return null;
  if (v['type'] === 'text' && only(v, ['type', 'text']) && typeof v['text'] === 'string' && TEXT_RE.test(v['text'])) return { type: 'text', text: v['text'] };
  if (v['type'] === 'math' && only(v, ['type', 'latex']) && typeof v['latex'] === 'string' && LATEX_RE.test(v['latex'])
      && latexToText(v['latex']) !== null) return { type: 'math', latex: v['latex'] };
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

/** 白名单重建（同 BFF src/item-rich.ts）。optionKeys：纯文字选项的键，富文本选项必须一一对应；不合格返回 null */
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
  if (!Array.isArray(opts) || opts.length !== optionKeys.length) return null;
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

// ---------- 公式的纯文本排版（KaTeX 的等价替代，见文件头） ----------
const SYMBOL: Record<string, string> = {
  times: '×', div: '÷', cdot: '·', pm: '±', mp: '∓', le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠', approx: '≈',
  infty: '∞', pi: 'π', alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', theta: 'θ', lambda: 'λ', mu: 'μ', sigma: 'σ', omega: 'ω',
  Delta: 'Δ', angle: '∠', triangle: '△', perp: '⊥', parallel: '∥', circ: '°', degree: '°', because: '∵', therefore: '∴',
  in: '∈', notin: '∉', cup: '∪', cap: '∩', subset: '⊂', sim: '∼', cong: '≅', to: '→', rightarrow: '→', Rightarrow: '⇒',
  leftarrow: '←', Leftrightarrow: '⇔', cdots: '⋯', ldots: '…', quad: ' ', qquad: '  ', left: '', right: '', displaystyle: '',
  text: '', mathrm: '', textrm: '', mathbf: '', operatorname: '', overline: '', underline: '', vec: '', bar: '', hat: '',
};
const SUP: Record<string, string> = { 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', n: 'ⁿ', i: 'ⁱ' };
const SUB: Record<string, string> = { 0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄', 5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎' };

/** 读一个参数：{...}（配对花括号）或单个字符 / 命令。返回 [参数原文, 下一个位置] */
function arg(s: string, i: number): [string, number] {
  while (s[i] === ' ') i++;
  if (s[i] === '{') {
    let depth = 0;
    for (let j = i; j < s.length; j++) {
      if (s[j] === '{') depth++;
      else if (s[j] === '}' && --depth === 0) return [s.slice(i + 1, j), j + 1];
    }
    return [s.slice(i + 1), s.length];
  }
  if (s[i] === '\\') {
    const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i));
    return m ? [m[0], i + m[0].length] : ['', i + 1];
  }
  return [s[i] ?? '', i + 1];
}

function script(body: string, table: Record<string, string>, mark: string): string | null {
  const t = latexToText(body);
  if (t === null) return null;
  const mapped = [...t].map((c) => table[c]);
  return mapped.every((c) => c !== undefined) ? mapped.join('') : `${mark}(${t})`;
}

/**
 * 认得的 LaTeX 命令（字母命令）。不在表里的命令一律不渲染：latexToText 返回 null，整道题的 rich 不合格。
 * BFF src/item-rich.ts 的 LATEX_COMMANDS 必须与这里同一份（tests/rich-item.test.ts 对拍）。
 */
export const LATEX_COMMANDS: readonly string[] = ['dfrac', 'frac', 'sqrt', 'tfrac', ...Object.keys(SYMBOL)].sort();

/** LaTeX → 纯文本排版。有认不出的命令时返回 null（不猜，也不只留命令名） */
export function latexToText(s: string): string | null {
  let out = '';
  let i = 0;
  while (i < s.length) {
    const c = s[i]!;
    if (c === '\\') {
      const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i));
      const name = m ? m[1]! : '';
      i += m ? m[0].length : 1;
      if (/^[A-Za-z]+$/.test(name)) while (s[i] === ' ') i++;   // 命令名后的空格只是分隔符（同 TeX）
      if (name === 'frac' || name === 'dfrac' || name === 'tfrac') {
        const [a, j] = arg(s, i);
        const [b, k] = arg(s, j);
        const ta = latexToText(a);
        const tb = latexToText(b);
        if (ta === null || tb === null) return null;
        out += `(${ta})/(${tb})`;
        i = k;
      } else if (name === 'sqrt') {
        let index = '';
        if (s[i] === '[') {
          const end = s.indexOf(']', i);
          index = end > i ? s.slice(i + 1, end) : '';
          i = end > i ? end + 1 : i + 1;
        }
        const [a, j] = arg(s, i);
        const ti = index ? script(index, SUP, '^') : '';
        const ta = latexToText(a);
        if (ti === null || ta === null) return null;
        out += `${ti}√(${ta})`;
        i = j;
      } else if (name in SYMBOL) {
        out += SYMBOL[name];
      } else if (/^[A-Za-z]+$/.test(name)) {
        return null;                                                // 认不出的命令：不渲染
      } else {
        out += name === ',' || name === ';' || name === ' ' ? ' ' : name;
      }
    } else if (c === '^' || c === '_') {
      const [a, j] = arg(s, i + 1);
      const t = script(a, c === '^' ? SUP : SUB, c);
      if (t === null) return null;
      out += t;
      i = j;
    } else if (c === '{' || c === '}') {
      i++;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}
