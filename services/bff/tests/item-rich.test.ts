// PLT-12（specs/present-item-contract）BFF 侧：受限富文本题面（rich）的白名单重建，诊断当前题与再练取题转给前端之前的第三道白名单。
// 全部是合成数据（规则 8）。不连库。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { publicView } from '../src/diagnosis/service.ts';
import type { SessionView } from '../src/diagnosis/learning-client.ts';
import { ALT_RE, IMAGE_REF_RE, LATEX_COMMANDS, LATEX_RE, latexCommandsKnown, pickRich, TEXT_RE } from '../src/item-rich.ts';
import { readReady } from '../src/practice/service.ts';

const STEM = [{ type: 'paragraph', runs: [{ type: 'text', text: '（合成题面）计算 ' }, { type: 'math', latex: '3\\times 4' }] },
              { type: 'paragraph', runs: [{ type: 'image', image_ref: 'objstore://synthetic/aya/fig-01.png', alt: '（合成）示意图' }] }];
const OPTIONS = ['A', 'B'].map((key) => ({ key, content: [{ type: 'text', text: `（合成选项）${key}` }] }));
const clone = <T>(v: T): T => structuredClone(v);

test('合格的 rich 原样重建（键与取值都在白名单里）', () => {
  assert.deepEqual(pickRich({ stem: STEM }, []), { stem: STEM });
  assert.deepEqual(pickRich({ stem: STEM, options: OPTIONS }, ['A', 'B']), { stem: STEM, options: OPTIONS });
});

test('多余字段、答案类字段、HTML、危险公式、公网图片、选项键对不上：一律拒绝', () => {
  const bad: [string, unknown, string[]][] = [
    ['顶层夹带答案', { stem: STEM, answer: ['A'] }, []],
    ['段落夹带解析', { stem: [{ ...clone(STEM[0]), analysis: '（合成）' }] }, []],
    ['片段夹带答案', { stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '（合成）', answer: '12' }] }] }, []],
    ['选项夹带 is_correct', { stem: STEM, options: [{ ...OPTIONS[0], is_correct: true }, OPTIONS[1]] }, ['A', 'B']],
    ['文字含 HTML', { stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '<img src=x onerror=alert(1)>' }] }] }, []],
    ['片段类型是 html', { stem: [{ type: 'paragraph', runs: [{ type: 'html', html: '<b>x</b>' }] }] }, []],
    ['公式含 \\href', { stem: [{ type: 'paragraph', runs: [{ type: 'math', latex: '\\href{https://x.invalid}{a}' }] }] }, []],
    ['公式含中文', { stem: [{ type: 'paragraph', runs: [{ type: 'math', latex: '解：x=1' }] }] }, []],
    ['图片是公网地址', { stem: [{ type: 'paragraph', runs: [{ type: 'image', image_ref: 'https://x.invalid/a.png' }] }] }, []],
    ['图片说明含 HTML', { stem: [{ type: 'paragraph', runs: [{ type: 'image', image_ref: 'objstore://a/b.png', alt: '<b>x</b>' }] }] }, []],
    ['空题干', { stem: [] }, []],
    ['选项键对不上', { stem: STEM, options: OPTIONS }, ['A', 'C']],
    ['填空题带选项', { stem: STEM, options: OPTIONS }, []],
    ['选择题缺富文本选项', { stem: STEM }, ['A', 'B']],
  ];
  for (const [name, v, keys] of bad) assert.equal(pickRich(v, keys), null, name);
});

test('正则与契约 v1.1 原文一致（契约改了这里要一起改）', () => {
  const text = readFileSync(new URL('../../../packages/contracts/openapi/plugin-api.v1.1.yaml', import.meta.url), 'utf8');
  const pattern = (schema: string, prop: string): string => {
    const block = text.slice(text.indexOf(`    ${schema}:\n`));
    const m = new RegExp(`\\n        ${prop}:\\n(?:.*\\n){0,4}?\\s+pattern: '([^']+)'`).exec(block);
    assert.ok(m, `${schema}.${prop}`);
    return m[1]!;
  };
  const unslash = (re: RegExp) => re.source.replaceAll('\\/', '/');
  assert.equal(unslash(TEXT_RE), pattern('RichTextRun', 'text'));
  assert.equal(unslash(ALT_RE), pattern('RichImageRun', 'alt'));
  assert.equal(unslash(LATEX_RE), pattern('RichMathRun', 'latex'));
  assert.equal(unslash(IMAGE_REF_RE), pattern('RichImageRun', 'image_ref'));
});

const view = (item: Record<string, unknown>): SessionView => ({
  session_id: '5d000000-0000-4000-8000-0000000007d1', status: 'active', state: 'presented', usage_id: '5d000000-0000-4000-8000-0000000007d2',
  elapsed_sec: 0, time_budget_sec: 2100, item_count: 0, item_limit: 20, process_tasks: false,
  step: { seq: 1, kind: 'adaptive', subject: 'aya', item_id: 'Q-SYN-1', item, probe_recorded: false, step_elapsed_sec: 0 },
} as unknown as SessionView);

test('诊断当前题：开关关闭（没有 rich）时转给前端的题面与之前相同；有 rich 时重建后转；不合格就丢掉 rich，只留纯文字', () => {
  const plain = { format: 'single', stem: '（合成题面）', options: [{ key: 'A', text: '甲' }, { key: 'B', text: '乙' }] };
  const off = publicView(view(plain)) as { step: { item: unknown } };
  assert.equal(JSON.stringify(off.step.item), JSON.stringify(plain));
  const on = publicView(view({ ...plain, rich: { stem: STEM, options: OPTIONS } })) as { step: { item: Record<string, unknown> } };
  assert.deepEqual(on.step.item['rich'], { stem: STEM, options: OPTIONS });
  const leaky = publicView(view({ ...plain, rich: { stem: STEM, options: OPTIONS, answer: ['A'] } })) as { step: { item: unknown } };
  assert.equal(JSON.stringify(leaky.step.item), JSON.stringify(plain));
  // 学习服务视图里别的多余字段（例如 answer）本来就不转
  const extra = publicView(view({ ...plain, answer: ['A'] })) as { step: { item: unknown } };
  assert.equal(JSON.stringify(extra.step.item), JSON.stringify(plain));
});

test('再练取题：有 rich 时重建后转；rich 不合格就丢掉 rich、照常出纯文字题面（与诊断同一口径，审查建议 10）', () => {
  const base = { entry_id: '5d000000-0000-4000-8000-0000000007e1', round: 1, ticket: 'opaque', expires_at: '2026-10-09T10:00:00Z' };
  const fill = { format: 'fill', stem: '（合成题面）' };
  assert.deepEqual(readReady({ ...base, item: fill }, base.entry_id)?.item, fill);
  assert.deepEqual(readReady({ ...base, item: { ...fill, rich: { stem: STEM } } }, base.entry_id)?.item, { ...fill, rich: { stem: STEM } });
  for (const bad of [{ stem: STEM, explanation: '（合成）' }, { stem: [{ type: 'paragraph', runs: [{ type: 'text', text: '<script>' }] }] },
    { stem: [{ type: 'paragraph', runs: [{ type: 'math', latex: '\\unknown{x}' }] }] }]) {
    const r = readReady({ ...base, item: { ...fill, rich: bad } }, base.entry_id);
    assert.equal(JSON.stringify(r?.item), JSON.stringify(fill));
  }
  // 选项键与契约 PresentedOption.key 同一规则（[A-Za-z0-9]{1,8}）
  const keyed = { format: 'single', stem: '（合成题面）', options: [{ key: 'a1', text: '甲' }, { key: '2', text: '乙' }] };
  assert.deepEqual(readReady({ ...base, item: keyed }, base.entry_id)?.item, keyed);
  assert.equal(readReady({ ...base, item: { ...keyed, options: [{ key: 'A 正确', text: '甲' }, { key: 'B', text: '乙' }] } }, base.entry_id), null);
});

const para = (runs: unknown[]) => ({ stem: [{ type: 'paragraph', runs }] });

test('不可见与双向控制字符、认不出的公式命令、总字数超过 8000：整体拒绝（审查建议 4、9、12）', () => {
  for (const ch of ['\u202e', '\u202a', '\u2066', '\u2069', '\u200b', '\u200f', '\u2028', '\u2029', '\ufeff', '\u0080', '\u009f']) {
    assert.equal(pickRich(para([{ type: 'text', text: `（合成）a${ch}b` }]), []), null, JSON.stringify(ch));
    assert.equal(pickRich(para([{ type: 'image', image_ref: 'objstore://a/b.png', alt: `a${ch}b` }]), []), null, JSON.stringify(ch));
  }
  for (const latex of ['\\unknown{x}', 'x^{\\mystery}', '\\frac{1}{\\nope}']) assert.equal(pickRich(para([{ type: 'math', latex }]), []), null, latex);
  for (const latex of ['\\frac{1}{2}', '\\left(x\\right)^2', 'a\\\\b', '\\{x\\}', '90^{\\circ}']) assert.ok(pickRich(para([{ type: 'math', latex }]), []), latex);
  assert.ok(LATEX_COMMANDS.every((c) => latexCommandsKnown(`\\${c}`)));
  const full = Array.from({ length: 4 }, () => ({ type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(2000) }] }));
  assert.ok(pickRich({ stem: full }, []));                                                    // 正好 8000
  assert.equal(pickRich({ stem: [...full, { type: 'paragraph', runs: [{ type: 'math', latex: 'x' }] }] }, []), null);
  const opts = [{ key: 'A', content: [{ type: 'text', text: '甲' }] }, { key: 'B', content: [{ type: 'text', text: '乙' }] }];
  assert.equal(pickRich({ stem: [...full.slice(0, 3), { type: 'paragraph', runs: [{ type: 'text', text: '合'.repeat(1999) }] }], options: opts }, ['A', 'B']), null);
});
