// “能做对吗”探针（冲刺 2-L）的单测与静态检查：
//   - 文案过禁语表 A–E 类（packages/personas/forbidden/，只读，经 tests/forbidden-lexicon.ts；词表本身 reviewed: false，待人设负责人核对），
//     另查一遍不打分、不评价、不提历史的用词；
//   - 构建开关（构建期常量，4-H H2）：只有 STARBUDDY_DEV_PAGES=1 且 STARBUDDY_ENV 为 dev / test 时，page.dev.tsx 才算页面；
//   - 开发专用页面的文件名都是 page.dev.tsx，演示页的 server action 与客户端只被演示页引用。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { devPagesEnabled, distDirFor, E2E_DIST_DIR, pageExtensionsFor } from '../next.config.ts';
import { DEMO_COPY, PREDICTION_OPTIONS, PROBE_COPY } from '../src/probe/copy.ts';
import { CLASSES, loadForbidden, ruleLineCount } from './forbidden-lexicon.ts';

const WEB = new URL('..', import.meta.url).pathname;

const COPY: string[] = [
  PROBE_COPY.question, PROBE_COPY.skip, ...PREDICTION_OPTIONS.map((o) => o.label),
  DEMO_COPY.title, DEMO_COPY.note, DEMO_COPY.itemHeading, DEMO_COPY.itemPlaceholder('SYN-ZX-ITEM-01'),
  DEMO_COPY.end, DEMO_COPY.ended, DEMO_COPY.notSaved,
];

test('禁语表能读出来，且确实能命中（防止解析失败后“什么都没查”）', () => {
  const rules = loadForbidden();
  const lines = CLASSES.reduce((n, c) => n + ruleLineCount(c), 0);
  assert.ok(rules.length >= 30, `只读出 ${rules.length} 条`);
  assert.equal(rules.length, lines, '每一条禁语都要解析出来');
  assert.deepEqual([...new Set(rules.map((r) => r.cls))], [...CLASSES]);
  const hit = (s: string) => rules.filter((r) => r.rx.test(s)).map((r) => r.id);
  assert.ok(hit('合成：你总是这样').includes('E-12'));
  assert.ok(hit('合成：你应该先读题').includes('A-01'));
});

test('探针与演示页文案不命中禁语表 A–E 类', () => {
  const rules = loadForbidden();
  for (const s of COPY) {
    const hits = rules.filter((r) => r.rx.test(s)).map((r) => r.id);
    assert.deepEqual(hits, [], s);
  }
});

test('探针只问预测：不打分、不评价、不提历史或别人', () => {
  assert.equal(PROBE_COPY.question, '这道题你觉得能做对吗？');
  assert.deepEqual(PREDICTION_OPTIONS.map((o) => [o.code, o.label]), [['yes', '能'], ['no', '不能'], ['unsure', '不确定']]);
  const banned = /得分|分数|打分|正确率|对了|错了|答对|答错|上次|以前|历史|记录|同学|别人|大家|棒|厉害|聪明|加油|自信|紧张|焦虑|心情/;
  for (const s of COPY) assert.ok(!banned.test(s), s);
});

test('开发专用页面只在开发、测试构建里是页面', () => {
  for (const env of [{}, { STARBUDDY_DEV_PAGES: '1' }, { STARBUDDY_ENV: 'test' }, { STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'production' },
    { STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'staging' }, { STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'pilot' },
    { STARBUDDY_DEV_PAGES: 'true', STARBUDDY_ENV: 'dev' }]) {
    assert.equal(devPagesEnabled(env), false, JSON.stringify(env));
    assert.ok(!pageExtensionsFor(env).includes('dev.tsx'), JSON.stringify(env));
  }
  for (const env of [{ STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'dev' }, { STARBUDDY_DEV_PAGES: '1', STARBUDDY_ENV: 'test' }]) {
    assert.ok(pageExtensionsFor(env).includes('dev.tsx'), JSON.stringify(env));
  }
});

test('端到端测试构建输出到 .next-e2e：只认这一个取值，其余一律是正式的 .next；两份 tsconfig 各管各的路由类型', () => {
  for (const v of [undefined, '', '.next', '../.next', '.next-e2e/', 'out', '/tmp/x']) {
    assert.equal(distDirFor({ STARBUDDY_NEXT_DIST_DIR: v }), '.next', String(v));
  }
  assert.equal(distDirFor({ STARBUDDY_NEXT_DIST_DIR: E2E_DIST_DIR }), '.next-e2e');
  // tsconfig.json 只含 .next 的路由类型、tsconfig.e2e.json 只含 .next-e2e 的：两边不会重复声明同一批路由类型
  const read = (name: string) => JSON.parse(readFileSync(join(WEB, name), 'utf8')) as { include: string[]; exclude?: string[] };
  const main = read('tsconfig.json');
  const e2e = read('tsconfig.e2e.json');
  assert.ok(main.include.every((p) => !p.startsWith('.next-e2e')), 'tsconfig.json 不含 .next-e2e');
  assert.ok(main.include.includes('.next/types/**/*.ts'));
  assert.ok(e2e.include.every((p) => !p.startsWith('.next/')), 'tsconfig.e2e.json 不含 .next');
  assert.ok(e2e.include.includes('.next-e2e/types/**/*.ts'));
  assert.ok(readFileSync(join(WEB, '.gitignore'), 'utf8').split('\n').includes('.next-e2e/'), '.next-e2e 不进仓库');
});

test('探针演示页是 page.dev.tsx；它的 action 与客户端只被演示页引用', () => {
  const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
  const src = files(join(WEB, 'src')).map((p) => relative(WEB, p));
  const demoDir = 'src/app/dev/probe/';
  assert.deepEqual(src.filter((p) => p.startsWith(demoDir) && /\/page\.[a-z.]+$/.test(p)), ['src/app/dev/probe/[studentId]/page.dev.tsx']);
  for (const p of src.filter((f) => !f.startsWith(demoDir))) {
    const text = readFileSync(join(WEB, p), 'utf8');
    assert.ok(!/dev\/probe\/actions|ProbeDemo/.test(text), `${p} 不能引用演示页的 action 或客户端`);
  }
});
