// 做题等待时的动作级过程状态（specs/process-status）的单测与静态检查：
//   - 阶段只有前端真实经历的三个（preparing / submitting / next），没有“核对答案”“判分”之类的服务端内部阶段；
//   - 阈值：3 秒起显示已等待秒数、15 秒起加“还在处理”；
//   - 文案全是 {{TODO_COPY:status.*}} 占位并已并入壳文案（禁语、禁用推断词表扫描在 tests/shell.test.ts）；不评价、不预计时间；
//   - 组件里没有中文、不渲染服务端文字、秒数在 aria-hidden 里、不用内联样式；动画在 reduce-motion 时停；
//   - 诊断、再练、复测三处接入：等待时状态行换成组件，原来的“正在提交”“正在准备题目”文案已删除。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { ALL_SHELL_COPY, PRACTICE, PROCESS_STATUS, RETEST } from '../src/shell/copy.ts';
import { DIAGNOSIS_COPY } from '../src/diagnosis/copy.ts';
import { PROCESS_STAGES, STILL_AFTER_SEC, WAITED_AFTER_SEC, waitView } from '../src/shell/process-status.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

test('阶段只有前端真实经历的三个；阈值 3 秒显示秒数、15 秒加“还在处理”', () => {
  assert.deepEqual([...PROCESS_STAGES], ['preparing', 'submitting', 'next']);
  assert.equal(WAITED_AFTER_SEC, 3);
  assert.equal(STILL_AFTER_SEC, 15);
  assert.deepEqual(waitView(0), { seconds: null, still: false });
  assert.deepEqual(waitView(2.9), { seconds: null, still: false });
  assert.deepEqual(waitView(3), { seconds: 3, still: false });
  assert.deepEqual(waitView(14), { seconds: 14, still: false });
  assert.deepEqual(waitView(15), { seconds: 15, still: true });
  for (const bad of [-5, Number.NaN, Number.POSITIVE_INFINITY]) assert.deepEqual(waitView(bad), { seconds: null, still: false }, String(bad));
});

test('文案：每个阶段都有，全是 {{TODO_COPY:status.*}} 占位，已并入壳文案；不说服务端内部步骤、不评价、不预计时间', () => {
  for (const s of PROCESS_STAGES) assert.ok(PROCESS_STATUS[s], s);
  for (const [k, v] of Object.entries(PROCESS_STATUS)) {
    assert.match(v, new RegExp(`^\\{\\{TODO_COPY:status\\.${k}\\}\\}`), k);
    assert.ok(ALL_SHELL_COPY.includes(v), k);
    assert.ok(!/核对|判分|批改|对错|正确|错误|分数|掌握|模型|思考|分钟后|马上|很快|太慢|网络/.test(v), v);
  }
  // 原来的静态等待文案已删除（换成组件）
  assert.ok(!('loading' in PRACTICE) && !('submitting' in PRACTICE));
  assert.ok(!('loading' in RETEST) && !('submitting' in RETEST));
  assert.ok(!('starting' in DIAGNOSIS_COPY) && !('submitting' in DIAGNOSIS_COPY));
});

test('组件：没有中文、不渲染服务端文字、秒数在 aria-hidden 里、阶段变化重新计时、不用内联样式；reduce-motion 时不转', () => {
  const c = code(read('src/shell/ProcessStatus.tsx'));
  assert.ok(!/[一-鿿]/.test(c), '组件里没有中文文案');
  assert.match(c, /export function ProcessStatus\(\{ stage \}: \{ stage: ProcessStage \}\)/, '只收阶段编码，不收任何文字');
  assert.match(c, /<span className="process-mark" aria-hidden="true" \/>/);
  assert.match(c, /<span className="process-waited" aria-hidden="true" data-testid="process-waited">/);
  assert.match(c, /\}, \[stage\]\);/, '阶段变化时重新计时');
  assert.match(c, /return \(\) => clearInterval\(id\);/);
  assert.ok(!/style=|dangerouslySetInnerHTML|role=/.test(c), '不用内联样式、不插 HTML；播报由宿主的 role="status" 行负责');
  const css = read('src/app/globals.css');
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{\n\s+\.process-mark \{ animation: none; \}/);
});

test('三处接入：等待时宿主的 role="status" 行换成组件；诊断作答成功后读下一题显示 next', () => {
  for (const [p, id] of [['src/practice/PracticeItem.tsx', 'practice-status'], ['src/retest/RetestItem.tsx', 'retest-status']] as const) {
    const t = code(read(p));
    assert.match(t, new RegExp(`<p role="status" data-testid="${id}">\\{busy \\? <ProcessStatus stage="submitting" /> : `), p);
    assert.match(t, /\{phase === 'loading' \? <h2 id=\{ids\.heading\}><ProcessStatus stage="preparing" \/><\/h2> : null\}/, p);
    assert.ok(!/C\.submitting|C\.loading/.test(t), p);
  }
  const d = code(read('src/diagnosis/Diagnosis.tsx'));
  assert.match(d, /<p role="status">\{pending \? <ProcessStatus stage=\{pending\} \/> : message\}<\/p>/);
  assert.match(d, /\{phase === 'loading' \? <p><ProcessStatus stage="preparing" \/><\/p> : null\}/);
  const submit = d.slice(d.indexOf('const submit = async'), d.indexOf('const pause = async'));
  const order = ["setPending('submitting');", 'await api.answer(', "setPending('next');", 'await load(session);', 'setPending(null);'].map((x) => submit.indexOf(x));
  assert.ok(order.every((x, i) => x > 0 && (i === 0 || x > order[i - 1]!)), `诊断提交的阶段顺序 ${order.join(',')}`);
  assert.ok(!/C\.submitting|C\.starting/.test(d));
});
