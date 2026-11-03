// PLT-12（specs/present-item-contract）前端：受限富文本题面的白名单（src/lib/rich-item.ts）、公式的纯文本排版（KaTeX 的等价替代）、
// 组件级渲染（src/shell/RichItem.tsx，react-dom/server；同 tests/star-v1-render.test.ts 的加载方式）与渲染结果的泄露核对：
// 没有 HTML 注入（文字一律转义）、没有答案类字段名、没有内部编码样式。全部是合成数据（规则 8）。
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';
import { pickView, type DiagnosisView } from '../src/lib/diagnosis.ts';
import { ANSWER_FIELD_NAMES, LATEX_COMMANDS, latexToText, pickRich } from '../src/lib/rich-item.ts';
import { pickNext } from '../src/practice/view.ts';

register('./tsx-loader.mjs', import.meta.url);
const { renderToStaticMarkup } = await import('react-dom/server');
const { createElement } = await import('react');
const { RichParagraphs, RichRuns } = await import('../src/shell/RichItem.tsx');

const STEM = [
  { type: 'paragraph', runs: [{ type: 'text', text: '（合成题面）解方程 ' }, { type: 'math', latex: 'x^2-2x+1=0' }, { type: 'text', text: '。' }] },
  { type: 'paragraph', runs: [{ type: 'image', image_ref: 'objstore://synthetic/aya/fig-01.png', alt: '（合成）示意图' }] },
];
const OPTIONS = [{ key: 'A', content: [{ type: 'math', latex: 'x_1=x_2=1' }] }, { key: 'B', content: [{ type: 'text', text: '（合成）无解' }] }];

test('白名单：合格的 rich 原样重建；多余字段、HTML、危险公式、公网图片、选项键对不上都拒绝', () => {
  assert.deepEqual(pickRich({ stem: STEM, options: OPTIONS }, ['A', 'B']), { stem: STEM, options: OPTIONS });
  const bad: unknown[] = [
    { stem: STEM, answer_key: ['A'] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '<script>alert(1)</script>' }] }] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'math', latex: '\\href{https://x.invalid}{a}' }] }] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'image', image_ref: 'https://x.invalid/a.png' }] }] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '（合成）', is_correct: true }] }] },
  ];
  for (const b of bad) assert.equal(pickRich(b, []), null, JSON.stringify(b));
  assert.equal(pickRich({ stem: STEM, options: OPTIONS }, ['A', 'C']), null);
});

const para = (runs: unknown[]) => ({ stem: [{ type: 'paragraph', runs }] });

test('白名单（审查建议 4、9、12）：双向控制符、零宽、C1 控制字符、认不出的公式命令、总字数超过 8000 都整体拒绝', () => {
  for (const ch of ['\u202e', '\u2066', '\u200b', '\u2028', '\ufeff', '\u0085']) {
    assert.equal(pickRich(para([{ type: 'text', text: `（合成）a${ch}b` }]), []), null, JSON.stringify(ch));
    assert.equal(pickRich(para([{ type: 'image', image_ref: 'objstore://a/b.png', alt: `a${ch}b` }]), []), null, JSON.stringify(ch));
  }
  assert.equal(pickRich(para([{ type: 'math', latex: '\\unknown{x}' }]), []), null);
  assert.equal(pickRich(para([{ type: 'math', latex: '\\frac{\\mystery}{2}' }]), []), null);
  const full = Array.from({ length: 4 }, () => ({ type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(2000) }] }));
  assert.ok(pickRich({ stem: full }, []));                                                   // 正好 8000
  assert.equal(pickRich({ stem: [...full, { type: 'paragraph', runs: [{ type: 'math', latex: 'x' }] }] }, []), null);
  const opts = [{ key: 'A', content: [{ type: 'text', text: '甲' }] }, { key: 'B', content: [{ type: 'text', text: '乙' }] }];
  assert.equal(pickRich({ stem: [...full.slice(0, 3), { type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(1999) }] }], options: opts }, ['A', 'B']), null);
});

test('认得的公式命令与 BFF src/item-rich.ts 同一份（两条路径对“认不出就退回纯文字”口径一致）', async () => {
  const bff = await import('../../../services/bff/src/item-rich.ts');
  assert.deepEqual([...bff.LATEX_COMMANDS].sort(), [...LATEX_COMMANDS].sort());
  for (const name of LATEX_COMMANDS) assert.notEqual(latexToText(`\\${name}{x}{y}`), null, name);
});

test('公式的纯文本排版：常见命令换成符号、上下标换成上下标字符、分式与根式可读；认不出的命令不渲染（null）', () => {
  const cases: [string, string | null][] = [
    ['x^2-2x+1=0', 'x²-2x+1=0'],
    ['x_1=x_2=1', 'x₁=x₂=1'],
    ['\\frac{1}{2}a', '(1)/(2)a'],
    ['\\sqrt{2}\\times 3', '√(2)×3'],
    ['\\sqrt[3]{8}', '³√(8)'],
    ['a\\le b\\ne c', 'a≤b≠c'],
    ['\\angle ABC=90^{\\circ}', '∠ABC=90^(°)'],
    ['\\left(x+1\\right)^{2}', '(x+1)²'],
    ['y=ax^{n+1}', 'y=axⁿ⁺¹'],
    ['a_{ij}', 'a_(ij)'],
    ['\\unknown{x}', null],
    ['x^{\\mystery}', null],
    ['\\sqrt[\\nope]{2}', null],
    ['a\\\\b', 'a\\b'],
    ['x<3', 'x<3'],
  ];
  for (const [src, want] of cases) assert.equal(latexToText(src), want, src);
});

const render = (el: unknown) => renderToStaticMarkup(el as Parameters<typeof renderToStaticMarkup>[0]);

test('组件渲染：段落与片段都是文本节点（尖括号转义），公式排版后显示，图片只显示说明；没有答案类字段名与内部编码', () => {
  const html = render(createElement(RichParagraphs, { stem: STEM as never }));
  assert.equal(html, '<p><span>（合成题面）解方程 </span><span class="item-math">x²-2x+1=0</span><span>。</span></p>'
    + '<p><span class="item-figure">［（合成）示意图］</span></p>');
  const opt = render(createElement(RichRuns, { runs: OPTIONS[0]!.content as never }));
  assert.equal(opt, '<span class="item-math">x₁=x₂=1</span>');
  // 即使有人绕过白名单把尖括号塞进来，渲染也只是文本（React 转义），不会变成标签
  const raw = render(createElement(RichRuns, { runs: [{ type: 'text', text: '<img src=x onerror=alert(1)>' }, { type: 'math', latex: '<b>' }] as never }));
  assert.ok(!raw.includes('<img') && !raw.includes('<b>') && raw.includes('&lt;img'), raw);
  for (const out of [html, opt]) {
    for (const f of ANSWER_FIELD_NAMES) assert.ok(!out.includes(f), f);
    assert.ok(!/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/.test(out), `出现 snake_case 编码：${out}`);
    assert.ok(!/objstore:/.test(out), '图片引用（对象存储地址）不进页面');
  }
});

test('诊断视图与再练取题：开关关闭（没有 rich）时与之前相同；有 rich 时重建后保留；rich 不合格时两条路径都丢掉 rich、照常出纯文字题面', () => {
  const plain = { format: 'single', stem: '（合成题面）', options: [{ key: 'A', text: '甲' }, { key: 'B', text: '乙' }] };
  const view = (item: unknown) => ({
    session_id: '5d000000-0000-4000-8000-0000000007f1', status: 'active', elapsed_sec: 0, time_budget_sec: 2100, item_count: 0,
    item_limit: 20, process_tasks: false, step: { seq: 1, kind: 'adaptive', probe_recorded: false, step_elapsed_sec: 0, item },
  }) as unknown as DiagnosisView;
  assert.equal(JSON.stringify(pickView(view(plain)).step?.item), JSON.stringify(plain));
  assert.deepEqual(pickView(view({ ...plain, rich: { stem: STEM, options: OPTIONS } })).step?.item.rich, { stem: STEM, options: OPTIONS });
  assert.equal(JSON.stringify(pickView(view({ ...plain, rich: { stem: STEM, options: OPTIONS, analysis: '（合成）' } })).step?.item), JSON.stringify(plain));
  const entry = '5d000000-0000-4000-8000-0000000007f2';
  const ready = (item: unknown) => ({ status: 'ready', entry_id: entry, round: 1, ticket: 'sbp1.synthetic', item });
  const fill = { format: 'fill', stem: '（合成题面）' };
  assert.deepEqual(pickNext(ready(fill), entry), { kind: 'ready', ready: { entry_id: entry, round: 1, ticket: 'sbp1.synthetic', item: fill } });
  const withRich = pickNext(ready({ ...fill, rich: { stem: STEM } }), entry);
  assert.ok(withRich && withRich.kind === 'ready');
  assert.deepEqual(withRich.ready.item.rich, { stem: STEM });
  for (const bad of [{ stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '<b>x</b>' }] }] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'math', latex: '\\unknown{x}' }] }] }]) {
    assert.deepEqual(pickNext(ready({ ...fill, rich: bad }), entry), { kind: 'ready', ready: { entry_id: entry, round: 1, ticket: 'sbp1.synthetic', item: fill } });
    assert.equal(JSON.stringify(pickView(view({ ...fill, rich: bad })).step?.item), JSON.stringify(fill));
  }
  // 选项键与契约 PresentedOption.key 同一规则（[A-Za-z0-9]{1,8}）
  const keyed = { format: 'single', stem: '（合成题面）', options: [{ key: 'a1', text: '甲' }, { key: '2', text: '乙' }] };
  const r = pickNext(ready(keyed), entry);
  assert.ok(r && r.kind === 'ready' && r.ready.item.options?.map((o) => o.key).join() === 'a1,2');
  assert.equal(pickNext(ready({ ...keyed, options: [{ key: 'A 正确', text: '甲' }, { key: 'B', text: '乙' }] }), entry), null);
});
