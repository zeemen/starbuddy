// 统一壳 H4 的单测与静态检查（spec 第 8、11、12、18 节与验收“文案自查”“信任界面预留”“点点入口与家长端”）：
//   - 文案全部在 src/shell/copy.ts（与 src/shell/denials.ts），逐条过禁语扫描（personas.guard，platform 口径）、禁用推断词表
//     （docs/design/sie/sie_lexicon.py）、比较 / 评价 / 营销词表，并且不出现白名单外维度的名称；
//   - 壳页面与组件（.tsx）里没有中文文案（只从文案文件取），关于学生的句子只在 TaskList 里以 data-claim-id 渲染；
//   - 信任界面开关默认关、staging 以上一律关；预留位组件关时返回 null、不发请求；
//   - 科目清单：数学 live、道法与语文 coming、英语 hidden，每个可见学科有自己的目录与入口组件；不用 iframe；
//   - 家长端页面不引用点点入口与角色资产；学生壳有点点入口。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { ALL_SHELL_COPY, CONFIDENCE_BAND, STAR_DISPLAY_V1, TRUST_SLOT } from '../src/shell/copy.ts';
import { DENIAL_COPY } from '../src/shell/denials.ts';
import { TRUST_FEATURES, trustEnabled, trustFeatures } from '../src/shell/features.ts';
import { SUBJECT_MODULES, subjectModule, visibleSubjects } from '../src/subjects/registry.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
// 星图 v1 的状态词（STAR_DISPLAY_V1）不在全量文案表里（只能出现在 data-claim-id 内），这里另列，照样过禁语与词表扫描
const ALL = [...ALL_SHELL_COPY, ...Object.values(DENIAL_COPY), ...Object.values(STAR_DISPLAY_V1)];

test('壳文案过禁语扫描（personas.guard，platform 口径）', () => {
  const r = spawnSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json'], {
    cwd: ROOT, input: JSON.stringify(ALL), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const out = JSON.parse(r.stdout) as { results: { source: string; hits: unknown[] }[] };
  assert.equal(out.results.length, ALL.length);
  assert.deepEqual(out.results.filter((x) => x.hits.length > 0), []);
});

test('壳文案不命中禁用推断词表（sie_lexicon），词表确实能命中', () => {
  const script = `import json, sys\nsys.path.insert(0, 'docs/design/sie')\nimport sie_lexicon\nlex = sie_lexicon.load()\n`
    + `xs = json.load(sys.stdin)\nprint(json.dumps([[x, [h.category for h in lex.find(x)]] for x in xs], ensure_ascii=False))`;
  const r = spawnSync('python3', ['-c', script], { cwd: ROOT, input: JSON.stringify([...ALL, '（合成）这孩子天赋一般']), encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout) as [string, string[]][];
  assert.ok(out.at(-1)![1].length > 0, '探针应命中');
  assert.deepEqual(out.slice(0, -1).filter(([, hits]) => hits.length > 0), []);
});

test('壳文案不含比较、评价、营销类用词，不出现白名单外维度的名称', () => {
  const banned = /别人|同学都|排名|最差|最好|落后|领先|聪明|笨|天赋|性格|焦虑|紧张|懒|购买|升级|会员|优惠|加油|棒|学霸|学渣|学习策略|自主学习|表达应用|学习持续性/;
  for (const s of ALL) assert.ok(!banned.test(s), s);
});

test('人写人审的正文是明显占位：置信度说明、同意正文、信任界面入口都带 {{TODO_COPY}}', () => {
  for (const s of [...Object.values(CONFIDENCE_BAND), ...Object.values(TRUST_SLOT)]) assert.ok(s.startsWith('{{TODO_COPY}}'), s);
});

test('壳页面与组件里没有中文文案：文字只从文案文件取；关于学生的句子只在 TaskList 以 data-claim-id 渲染', () => {
  const shellTsx = src.filter((f) => /^src\/(shell|subjects|trust|app\/s|app\/family|app\/student)\/.*\.tsx$/.test(f.path));
  assert.ok(shellTsx.length >= 10);
  for (const f of shellTsx) {
    const cjk = code(f.text).match(/[一-鿿]+/g) ?? [];
    assert.deepEqual(cjk, [], f.path);
  }
  const claimRenderers = src.filter((f) => /data-claim-id/.test(code(f.text))).map((f) => f.path);
  // 冲刺 5-H 起另有“为什么”页、考试页（对账卡）与周报页渲染判断句（tests/trust.test.ts 逐个核对）；
  // H5 星图 v1（开关 starmap_v1，默认关）的星位与星数说明（tests/star-v1.test.ts 核对）
  assert.deepEqual(claimRenderers.sort(), ['src/shell/TaskList.tsx', 'src/star/StarMapV1.tsx', 'src/trust/ExamPages.tsx', 'src/trust/WeeklyPage.tsx',
                                          'src/trust/WhyPage.tsx']);
  assert.match(readFileSync(join(WEB, 'src/shell/TaskList.tsx'), 'utf8'), /data-claim-id=\{r\.claim_id\}>\{r\.text\}/);
});

test('信任界面开关：默认全关；只在 dev / test 可开；staging、pilot、生产一律关', () => {
  assert.deepEqual([...trustFeatures({})], []);
  assert.deepEqual([...trustFeatures({ STARBUDDY_ENV: 'test' })], []);
  assert.deepEqual([...trustFeatures({ STARBUDDY_ENV: 'test', STARBUDDY_TRUST_UI: 'why, dispute,bogus' })], ['why', 'dispute']);
  for (const env of ['staging', 'pilot', 'production']) {
    assert.deepEqual([...trustFeatures({ STARBUDDY_ENV: env, STARBUDDY_TRUST_UI: TRUST_FEATURES.join(',') })], [], env);
  }
  assert.equal(trustEnabled('reconcile', {}), false);
  assert.deepEqual([...TRUST_FEATURES].sort(), ['data_rights', 'dispute', 'reconcile', 'weekly', 'why']);
});

test('预留位组件：关时返回 null；不发任何请求；只经开关判断（冲刺 5-H 的真实入口与请求见 tests/trust.test.ts）', () => {
  const slot = readFileSync(join(WEB, 'src/shell/TrustSlot.tsx'), 'utf8');
  assert.match(slot, /if \(!trustEnabled\(feature\)\) return null;/);
  assert.ok(!/fetch|shell-api|bff|use client|action/.test(code(slot)));
  for (const f of src.filter((x) => /\.(tsx?)$/.test(x.path))) {
    assert.ok(!/insight\.(dispute|reconcile|export|erase)|\/(dispute|reconcile|export|erase)\b/.test(code(f.text)), f.path);
  }
});

test('科目清单：数学 live，道法与语文 coming，英语 hidden；可见学科各有目录与入口组件；不用 iframe', () => {
  assert.deepEqual(SUBJECT_MODULES.map((m) => [m.code, m.status]),
                   [['math', 'live'], ['politics', 'coming'], ['chinese', 'coming'], ['english', 'hidden']]);
  assert.deepEqual(visibleSubjects().map((m) => m.code), ['math', 'politics', 'chinese']);
  assert.equal(subjectModule('english'), null);
  assert.equal(subjectModule('history'), null);
  for (const m of visibleSubjects()) assert.ok(existsSync(join(WEB, `src/subjects/${m.code}/Entry.tsx`)), m.code);
  assert.ok(!existsSync(join(WEB, 'src/subjects/english')));
  for (const f of src) assert.ok(!/<iframe|legacy\//.test(code(f.text)), f.path);
});

test('家长端没有点点入口、没有角色形象；学生壳有点点入口（占位，系统口吻）', () => {
  const family = src.filter((f) => f.path.startsWith('src/app/family/'));
  assert.ok(family.length >= 4);
  for (const f of family) {
    assert.ok(!/DIANDIAN|diandian|StudentShell|personas|avatar|\.png|\.svg|character/i.test(code(f.text)), f.path);
  }
  const shell = readFileSync(join(WEB, 'src/shell/StudentShell.tsx'), 'utf8');
  assert.match(shell, /DIANDIAN\.entry/);
  assert.ok(!/<img|\.png|\.svg/.test(shell));
});

test('壳不写浏览器存储；学生页布局只在学生本人的会话下才取额度与提醒', () => {
  for (const f of src.filter((x) => /src\/(shell|subjects|app\/s)\//.test(x.path))) {
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(code(f.text)), f.path);
  }
  const layout = readFileSync(join(WEB, 'src/app/s/[studentId]/layout.tsx'), 'utf8');
  assert.match(layout, /ps\.me\.account_kind !== 'student' \|\| ps\.me\.student_id !== studentId\) return <>\{children\}<\/>;/);
});

test('计量缺省值不写死在前端：默认宵禁、每天自练次数由 BFF 下发，文案参数化（审查第 3 条）', () => {
  for (const f of src.filter((x) => /^src\/(shell|subjects|app\/s|app\/family|lib\/shell-api)/.test(x.path))) {
    assert.ok(!/22:00|06:00|\b5 次/.test(code(f.text)), f.path);
  }
  const time = readFileSync(join(WEB, 'src/app/family/[studentId]/time/page.tsx'), 'utf8');
  assert.match(time, /TIME_GUARD\.curfewHint\(g\.default_curfew\.start, g\.default_curfew\.end\)/);
  assert.match(readFileSync(join(WEB, 'src/shell/StatusBar.tsx'), 'utf8'), /BALANCE\.trialAfter\(b\.practice_daily_limit\)/);
});

test('家长孩子页：只有 404 走“没有找到”，其他错误各区块显示“暂时无法获取”；开通按钮不按 open 隐藏（审查第 4、5 条）', () => {
  const page = code(readFileSync(join(WEB, 'src/app/family/[studentId]/page.tsx'), 'utf8'));
  assert.match(page, /if \(!r\.ok && r\.status === 404\) notFound\(\);/);
  assert.ok(!/if \(!r\.ok\) notFound\(\);/.test(page));
  for (const block of ['balance', 'enrollments', 'today']) assert.match(page, new RegExp(`data-unavailable="${block}"`));
  assert.ok(!/s\.open \?/.test(page), 'open 只决定“暂未开放”标签');
});
