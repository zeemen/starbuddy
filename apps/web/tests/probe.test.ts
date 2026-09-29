// “能做对吗”探针（冲刺 2-L）的单测与静态检查：
//   - 文案过点点禁语表（evals/persona/diandian/lexicon/forbidden.yaml，只读；词表本身 reviewed: false，待人设负责人核对），
//     另查一遍不打分、不评价、不提历史的用词；
//   - 构建开关：只有 STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV 为 dev / test 时，page.dev.tsx 才算页面；
//   - 开发专用页面的文件名都是 page.dev.tsx，演示页的 server action 与客户端只被演示页引用。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { devPagesEnabled, pageExtensionsFor } from '../next.config.ts';
import { DEMO_COPY, PREDICTION_OPTIONS, PROBE_COPY } from '../src/probe/copy.ts';

const WEB = new URL('..', import.meta.url).pathname;
const LEXICON = join(WEB, '..', '..', 'evals', 'persona', 'diandian', 'lexicon', 'forbidden.yaml');

/**
 * 读禁语表的 forbidden 段。apps/web 不引入 YAML 解析依赖：词表每条是一行流式映射
 * `- { id: A-01, …, class: A, regex: "…", source: … }`，这里只取 id、class 与双引号里的 regex（按 JSON 字符串转义还原）。
 */
function loadForbidden(): { id: string; cls: string; rx: RegExp }[] {
  const text = readFileSync(LEXICON, 'utf8');
  const section = text.slice(text.indexOf('\nforbidden:'), text.indexOf('\npatterns:'));
  const out: { id: string; cls: string; rx: RegExp }[] = [];
  for (const line of section.split('\n')) {
    const m = /^\s*- \{ id: ([A-Z]-\d+),.*?\bclass: ([A-Z]),.*?\bregex: "((?:[^"\\]|\\.)*)"/.exec(line);
    if (m) out.push({ id: m[1]!, cls: m[2]!, rx: new RegExp(JSON.parse(`"${m[3]!}"`) as string) });
  }
  return out;
}

const COPY: string[] = [
  PROBE_COPY.question, PROBE_COPY.skip, ...PREDICTION_OPTIONS.map((o) => o.label),
  DEMO_COPY.title, DEMO_COPY.note, DEMO_COPY.itemHeading, DEMO_COPY.itemPlaceholder('SYN-ZX-ITEM-01'),
  DEMO_COPY.end, DEMO_COPY.ended, DEMO_COPY.notSaved,
];

test('禁语表能读出来，且确实能命中（防止解析失败后“什么都没查”）', () => {
  const rules = loadForbidden();
  const lines = readFileSync(LEXICON, 'utf8').split('\n').filter((l) => /^\s*- \{ id: [A-Z]-\d+,/.test(l)).length;
  assert.ok(rules.length >= 30, `只读出 ${rules.length} 条`);
  assert.equal(rules.length, lines, '每一条禁语都要解析出来');
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
  for (const env of [{}, { STARBUDDY_DEV_AUTH: '1' }, { STARBUDDY_ENV: 'test' }, { STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'production' },
    { STARBUDDY_DEV_AUTH: 'true', STARBUDDY_ENV: 'dev' }]) {
    assert.equal(devPagesEnabled(env), false, JSON.stringify(env));
    assert.ok(!pageExtensionsFor(env).includes('dev.tsx'), JSON.stringify(env));
  }
  for (const env of [{ STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'dev' }, { STARBUDDY_DEV_AUTH: '1', STARBUDDY_ENV: 'test' }]) {
    assert.ok(pageExtensionsFor(env).includes('dev.tsx'), JSON.stringify(env));
  }
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
