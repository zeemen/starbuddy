// 今日计划跨科编排前端（PLT-11，specs/today-plan-orchestration-plt11 范围第 3 条）的单测与静态检查：
//   - 开关 STARBUDDY_TODAY_ORCH：默认关；只在 dev / test 可开；staging、pilot、生产一律关；
//   - “去做”：复测条目 → 复测页、再练条目 → 再练页（对应开关打开、学科有入口时），计划任务与引用为空 → 科目页；格式不对的引用不拼进地址；
//   - 文案：全部是 {{TODO_COPY:today.*}} 占位并已并入壳文案（禁语、禁用推断词表、比较营销词表的扫描在 tests/shell.test.ts）；
//   - 今日页只在开关打开、学生本人、BFF 返回了 ordered 时渲染跨科列表；渲染不出现掌握、置信度、证据、来源与原因编码；
//     关于学生的句子仍只在 TaskList 里以 data-claim-id 渲染（家长端页面不引用跨科列表）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { ALL_SHELL_COPY, ALL_TODAY_ORCH_COPY, TODAY_ORCH, TODAY_ORCH_KIND } from '../src/shell/copy.ts';
import { todayOrchEnabled } from '../src/shell/today-orch.ts';
import { goHref } from '../src/today/orch.ts';

const WEB = new URL('..', import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

const S = '00000000-0000-4000-8000-00000000a001';
const E = '00000000-0000-4000-8000-00000000e001';
const ON = { STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'today_orch', STARBUDDY_RETEST_UI: 'retest', STARBUDDY_PRACTICE: 'practice' };

test('开关：默认关；只在 dev / test 可开；staging、pilot、生产一律关；与再练、复测开关互不影响', () => {
  assert.equal(todayOrchEnabled({}), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_RETEST_UI: 'retest', STARBUDDY_PRACTICE: 'practice' }), false);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'today_orch' }), true);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_TODAY_ORCH: ' today_orch ' }), true);
  assert.equal(todayOrchEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_TODAY_ORCH: 'retest' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(todayOrchEnabled({ STARBUDDY_ENV: env, STARBUDDY_TODAY_ORCH: 'today_orch' }), false, env);
  }
});

test('“去做”：复测 → 复测页，再练 → 再练页；计划任务、引用为空、对应开关关、学科没有入口 → 科目页；格式不对不拼地址', () => {
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: E }, ON), `/s/${S}/learn/math/retest/${E}`);
  assert.equal(goHref(S, { source: 'practice', subject: 'politics', entry_id: E }, ON), `/s/${S}/learn/politics/practice/${E}`);
  assert.equal(goHref(S, { source: 'plan', subject: 'math', entry_id: null }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'plan', subject: 'math', entry_id: E }, ON), `/s/${S}/learn/math`, '计划任务不链到条目');
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: null }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: 'chinese', entry_id: E }, ON), `/s/${S}/learn/chinese`, '语文没有复测入口');
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: E }, { ...ON, STARBUDDY_RETEST_UI: '' }), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'practice', subject: 'math', entry_id: E }, { ...ON, STARBUDDY_PRACTICE: '' }), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: 'math', entry_id: '../../x' }, ON), `/s/${S}/learn/math`);
  assert.equal(goHref(S, { source: 'retest', subject: '../admin', entry_id: E }, ON), null);
  assert.equal(goHref('not-a-student', { source: 'plan', subject: 'math', entry_id: null }, ON), null);
});

test('文案：全部是 {{TODO_COPY:today.*}} 占位，已并入壳文案；类别只给占位键、不附提示', () => {
  for (const s of ALL_TODAY_ORCH_COPY) {
    assert.match(s, /^\{\{TODO_COPY:today\.[a-z_.]+\}\}/, s);
    assert.ok(ALL_SHELL_COPY.includes(s), s);
  }
  for (const s of Object.values(TODAY_ORCH_KIND)) assert.match(s, /^\{\{TODO_COPY:today\.orch\.kind\.[a-z_]+\}\}$/, s);
  assert.deepEqual(Object.keys(TODAY_ORCH_KIND).sort(), ['diagnose', 'lesson', 'mock_exam', 'practice', 'retest', 'review', 'verify']);
  assert.ok(!/掌握|置信|证据|薄弱|粗心/.test(Object.values(TODAY_ORCH).join('')));
});

test('今日页：只在开关打开、学生本人、BFF 返回了 ordered 时渲染跨科列表；理由仍只在 TaskList 以 data-claim-id 渲染', () => {
  const page = code(read('src/app/s/[studentId]/today/page.tsx'));
  assert.match(page, /todayOrchEnabled\(\) && ps\.me\.account_kind === 'student' && ordered !== undefined/);
  assert.match(page, /import \{ OrderedTaskList \} from '\.\.\/\.\.\/\.\.\/\.\.\/today\/OrderedTaskList\.tsx'/);
  const ordered = code(read('src/today/OrderedTaskList.tsx'));
  assert.match(ordered, /<Reason r=\{t\.rationale\} audience="student"/);
  assert.match(ordered, /import \{ Reason \} from '\.\.\/shell\/TaskList\.tsx'/);
  // 跨科列表不渲染内部字段：来源、条目引用只进链接地址，理由编码、掌握、置信度、证据不出现；也不自己渲染判断句
  for (const k of ['t.source}', 't.entry_id}', 'rationale_code', 'p_mastery', 'confidence', 'evidence', 'priority', 'node_id', 'data-claim-id']) {
    assert.ok(!ordered.includes(k), k);
  }
  assert.deepEqual(ordered.match(/[一-鿿]+/g) ?? [], [], '组件里没有中文文案');
  // 家长端与壳目录不引用跨科列表与“去做”地址（壳目录不链再练、复测页，tests/practice.test.ts）
  for (const f of src.filter((x) => /^src\/(app\/family|shell|subjects)\//.test(x.path))) {
    assert.ok(!/OrderedTaskList|today\/orch/.test(code(f.text)), f.path);
  }
  assert.ok(src.filter((x) => /OrderedTaskList/.test(code(x.text))).map((x) => x.path).sort().join(',')
    === 'src/app/s/[studentId]/today/page.tsx,src/today/OrderedTaskList.tsx');
});
