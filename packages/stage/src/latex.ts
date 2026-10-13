// 公式的纯文本排版（不引入 KaTeX；spec 范围 1）。与 apps/web src/lib/rich-item.ts 的 latexToText 同一口径的独立实现：
// 常见命令换成 Unicode 符号，上下标换成上下标字符（换不了的写成 ^(…) / _(…)），\frac 写成 (a)/(b)，\sqrt 写成 √(x)。
// 认不出的字母命令一律不猜：返回 null，校验器把整份 DSL 判为不合格。packages/stage 不引用 apps/web 的代码（ADR 07 决定 7：
// 依赖方向只能是 apps/web → packages/stage），tests/latex.test.ts 用同一组用例核对两边输出一致。

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

/** 认得的字母命令 */
export const LATEX_COMMANDS: readonly string[] = ['dfrac', 'frac', 'sqrt', 'tfrac', ...Object.keys(SYMBOL)].sort();

/**
 * LaTeX 原文的字符与禁用命令：只允许可打印 ASCII；\href、\url、\def、\input 等能引外部资源或改写宏的命令即使将来进了命令表也拒绝
 * （与 apps/web 的 LATEX_RE 同一份禁用表）。
 */
export const LATEX_RE =
  /^(?!.*\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|def|gdef|edef|xdef|let|newcommand|renewcommand|providecommand|newenvironment|input|include|write|immediate|catcode|csname)(?![A-Za-z]))[\x20-\x7E]{1,200}$/u;

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

/** LaTeX → 纯文本排版。有认不出的命令时返回 null */
export function latexToText(s: string): string | null {
  let out = '';
  let i = 0;
  while (i < s.length) {
    const c = s[i]!;
    if (c === '\\') {
      const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i));
      const name = m ? m[1]! : '';
      i += m ? m[0].length : 1;
      if (/^[A-Za-z]+$/.test(name)) while (s[i] === ' ') i++;
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
      } else if (Object.hasOwn(SYMBOL, name)) {
        out += SYMBOL[name];
      } else if (/^[A-Za-z]+$/.test(name)) {
        return null;
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
