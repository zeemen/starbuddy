// PLT-12 审查建议 16：前端受限富文本白名单（src/lib/rich-item.ts）的变异测试。apps/web 没有单独的变异脚本，这里自带：
// 把 rich-item.ts 复制到临时目录，每个变异改一处源码，动态加载改过的副本，跑同一组核对（与 tests/rich-item.test.ts 的断言同一口径），
// 断言每个变异至少有一项核对失败；不变异的对照组必须全部通过（证明失败来自变异，不是核对本身写错）。全部是合成数据（规则 8）。
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

type Mod = typeof import('../src/lib/rich-item.ts');
const SOURCE = readFileSync(new URL('../src/lib/rich-item.ts', import.meta.url), 'utf8');

const STEM = [{ type: 'paragraph', runs: [{ type: 'text', text: '（合成题面）解方程 ' }, { type: 'math', latex: 'x^2-2x+1=0' }] }];
const OPTIONS = [{ key: 'A', content: [{ type: 'text', text: '甲' }] }, { key: 'B', content: [{ type: 'text', text: '乙' }] }];
const para = (runs: unknown[]) => ({ stem: [{ type: 'paragraph', runs }] });
const cp = (n: number) => String.fromCodePoint(n);
const full = (n: number) => ({ stem: [...Array.from({ length: Math.floor(n / 2000) }, () => ({ type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(2000) }] })),
  ...(n % 2000 ? [{ type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(n % 2000) }] }] : [])] });

/** 核对项：名字 → 是否成立。对照组全部成立；每个变异至少一项不成立 */
function checks(m: Mod): Record<string, boolean> {
  return {
    合格的原样重建: JSON.stringify(m.pickRich({ stem: STEM, options: OPTIONS }, ['A', 'B'])) === JSON.stringify({ stem: STEM, options: OPTIONS }),
    多余字段拒绝: m.pickRich({ stem: STEM, answer_key: ['A'] }, []) === null,
    片段多余字段拒绝: m.pickRich(para([{ type: 'text', text: '（合成）', is_correct: true }]), []) === null,
    HTML拒绝: m.pickRich(para([{ type: 'text', text: '<b>x</b>' }]), []) === null,
    双向控制符拒绝: m.pickRich(para([{ type: 'text', text: `a${cp(0x202e)}b` }]), []) === null,
    零宽字符拒绝: m.pickRich(para([{ type: 'text', text: `a${cp(0x200b)}b` }]), []) === null,
    图片说明不可见字符拒绝: m.pickRich(para([{ type: 'image', image_ref: 'objstore://a/b.png', alt: `a${cp(0x2066)}b` }]), []) === null,
    危险公式拒绝: m.pickRich(para([{ type: 'math', latex: '\\href{https://x.invalid}{a}' }]), []) === null,
    // 危险命令即使哪天被加进可排版命令表，LATEX_RE 这道禁用表也要拦住（两道各自成立）
    危险命令禁用表: ['\\href{x}{y}', '\\url{x}', '\\def\\a{1}', '\\input{f}'].every((s) => !m.LATEX_RE.test(s)),
    认不出的命令拒绝: m.pickRich(para([{ type: 'math', latex: '\\unknown{x}' }]), []) === null && m.latexToText('\\unknown{x}') === null,
    公网图片拒绝: m.pickRich(para([{ type: 'image', image_ref: 'https://x.invalid/a.png' }]), []) === null,
    选项键对不上拒绝: m.pickRich({ stem: STEM, options: OPTIONS }, ['A', 'C']) === null,
    正好8000字可以: m.pickRich(full(8000), []) !== null,
    超过8000字拒绝: m.pickRich(full(8001), []) === null,
    公式排版: m.latexToText('\\frac{1}{2}') === '(1)/(2)' && m.latexToText('x^2') === 'x²',
    答案类字段名在表里: ['answer_key', 'is_correct', 'correct_options', 'scoring_points', 'rubric_version'].every((f) => m.ANSWER_FIELD_NAMES.includes(f)),
  };
}

const MUTATIONS: [name: string, from: string, to: string][] = [
  ['rich 顶层放过多余字段', "if (!isRecord(v) || !only(v, ['stem', 'options'])", 'if (!isRecord(v)'],
  ['片段放过多余字段', "v['type'] === 'text' && only(v, ['type', 'text'])", "v['type'] === 'text'"],
  ['文字允许双向控制符与零宽字符', '\\u007F-\\u009F\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]{1,2000}$/u;', '\\u007F]{1,2000}$/u;'],
  ['图片说明允许不可见字符', '\\u007F-\\u009F\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E\\u2066-\\u2069\\uFEFF]{1,200}$/u;', '\\u007F]{1,200}$/u;'],
  ['公式允许 \\href', '(?:href|url|', '(?:url|'],
  ['认不出的命令只留命令名', '        return null;                                                // 认不出的命令：不渲染', '        out += name;'],
  ['白名单不核对公式能不能排版', "\n      && latexToText(v['latex']) !== null)", ')'],
  ['图片允许公网地址', 'const IMAGE_REF_RE = /^objstore:\\/\\/', 'const IMAGE_REF_RE = /^(?:objstore|https):\\/\\/'],
  ['选项键不核对', " || o['key'] !== optionKeys[i]) return null;", ') return null;'],
  ['不限总字数', 'return optionKeys.length === 0 && totalChars(stem, []) <= MAX_TOTAL_CHARS ? { stem } : null;', 'return optionKeys.length === 0 ? { stem } : null;'],
  ['总字数上限放宽', 'export const MAX_TOTAL_CHARS = 8000;', 'export const MAX_TOTAL_CHARS = 9000;'],
  ['答案类字段名表漏掉 is_correct', "'answer_key', 'correct_answer', 'correct_options', 'is_correct',", "'answer_key', 'correct_answer', 'correct_options',"],
];

async function load(dir: string, name: string, source: string): Promise<Mod> {
  const file = join(dir, `${name}.ts`);
  writeFileSync(file, source, 'utf8');
  return (await import(pathToFileURL(file).href)) as Mod;
}

test('变异测试：rich-item.ts 的每条白名单与排版规则改坏后，至少一项核对失败；对照组全部通过', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'starbuddy-web-richmut-'));
  try {
    const base = checks(await load(dir, 'baseline', SOURCE));
    assert.deepEqual(Object.entries(base).filter(([, ok]) => !ok).map(([k]) => k), [], '对照组（不变异）就有核对失败');
    const missed: string[] = [];
    for (const [i, [name, from, to]] of MUTATIONS.entries()) {
      assert.equal(SOURCE.split(from).length - 1, 1, `变异本身写错（原文没有恰好出现一次）：${name}`);
      const got = checks(await load(dir, `m${i}`, SOURCE.replace(from, to)));
      if (Object.values(got).every(Boolean)) missed.push(name);
    }
    assert.deepEqual(missed, [], `漏检的变异：${missed.join('、')}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
