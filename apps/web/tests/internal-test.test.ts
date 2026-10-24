// 内部成员测试运营（specs/launch-lt06-internal-test-ops 范围第 3、4 条）的前端单测与静态检查：
//   - 开关 STARBUDDY_INTERNAL_TEST：默认关；只认 internal_test；dev / test / staging 可开，pilot、生产一律关；
//   - 分类、字数上限、路由模板登记表与 BFF（services/bff/src/http/internal-test-routes.ts）逐字一致；实际地址 → 路由模板；
//   - 表单 → 请求体的格式检查；BFF 客户端：开关关时不发请求，开时只请求三条路由，退出只发 { confirm: true }；
//   - 页面、server action、入口组件在读会话之前先过开关；退出要勾选确认（二次确认）；
//   - 文案：占位、隐私提示原句、过禁语扫描与禁用推断词表；反馈文本不进日志（本目录没有 console 调用）。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { EXIT, EXIT_RESULT, FEEDBACK, FEEDBACK_CATEGORY_LABEL, FEEDBACK_RESULT } from '../src/internal-test/copy.ts';
import { internalTestEnabled } from '../src/internal-test/features.ts';
import { feedbackFromForm } from '../src/internal-test/form.ts';
import { FEEDBACK_CATEGORIES, FEEDBACK_MAX_CHARS, FEEDBACK_ROUTES, routeTemplateOf } from '../src/internal-test/routes.ts';
import { feedbackQuery } from '../src/lib/review.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const S = '6f530000-0000-4000-8000-000000000c61';
const call = (v: unknown): string => (typeof v === 'function' ? (v as (...a: string[]) => string)('/s/[studentId]/today') : String(v));
const COPY: string[] = [
  ...Object.values(FEEDBACK).map(call), ...Object.values(FEEDBACK_RESULT), ...Object.values(FEEDBACK_CATEGORY_LABEL),
  ...Object.values(EXIT).flatMap((v) => (Array.isArray(v) ? v : [call(v)])), ...Object.values(EXIT_RESULT),
];

test('开关：默认关；只认 internal_test；dev / test / staging 可开，pilot、生产与没设一律关', () => {
  assert.equal(internalTestEnabled({}), false);
  assert.equal(internalTestEnabled({ STARBUDDY_ENV: 'test' }), false);
  for (const env of ['dev', 'test', 'staging']) {
    assert.equal(internalTestEnabled({ STARBUDDY_ENV: env, STARBUDDY_INTERNAL_TEST: 'internal_test' }), true, env);
    assert.equal(internalTestEnabled({ STARBUDDY_ENV: env, STARBUDDY_INTERNAL_TEST: ' internal_test ' }), true, env);
  }
  for (const raw of ['internal-test', 'retest', 'internal_test,retest', '1']) {
    assert.equal(internalTestEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_INTERNAL_TEST: raw }), false, raw);
  }
  for (const env of ['pilot', 'production', '']) {
    assert.equal(internalTestEnabled({ STARBUDDY_ENV: env, STARBUDDY_INTERNAL_TEST: 'internal_test' }), false, env);
  }
});

test('分类、字数上限、路由模板登记表与 BFF 逐字一致', () => {
  const bff = readFileSync(join(ROOT, 'services/bff/src/http/internal-test-routes.ts'), 'utf8');
  const arr = (name: string): string[] => {
    const m = new RegExp(`export const ${name}[^=]*= \\[([\\s\\S]*?)\\](?: as const)?;`).exec(bff);
    assert.ok(m, name);
    return [...m[1]!.matchAll(/'([^']*)'/g)].map((x) => x[1]!);
  };
  assert.deepEqual(arr('FEEDBACK_CATEGORIES'), [...FEEDBACK_CATEGORIES]);
  assert.deepEqual(arr('FEEDBACK_ROUTES'), [...FEEDBACK_ROUTES]);
  assert.match(bff, new RegExp(`export const FEEDBACK_MAX_CHARS = ${FEEDBACK_MAX_CHARS};`));
  // 与迁移 0059 的 CHECK 同一套分类
  const mig = readFileSync(join(ROOT, 'db/migrations/0059_platform__internal_test.up.sql'), 'utf8');
  assert.ok(mig.includes(`category IN (${FEEDBACK_CATEGORIES.map((c) => `'${c}'`).join(', ')})`));
  // 每个模板都对应 src/app 下的一个页面（模板不是任意字符串）
  for (const r of FEEDBACK_ROUTES) {
    const p = join(WEB, 'src/app', r, 'page.tsx');
    const dir = join(WEB, 'src/app', r);
    assert.ok(statSync(dir).isDirectory() && (statSync(p, { throwIfNoEntry: false })?.isFile() || r.endsWith('/why')), r);
  }
});

test('实际地址 → 路由模板：编号换成占位，深层编号截掉，不在登记表里的不附', () => {
  assert.equal(routeTemplateOf(`/s/${S}/today`, S), '/s/[studentId]/today');
  assert.equal(routeTemplateOf(`/s/${S}/learn/math`, S), '/s/[studentId]/learn/[subject]');
  assert.equal(routeTemplateOf(`/s/${S}/learn/math/practice/${S}`, S), '/s/[studentId]/learn/[subject]/practice');
  assert.equal(routeTemplateOf(`/s/${S}/why/6f530000-0000-4000-8000-0000000000aa`, S), '/s/[studentId]/why');
  assert.equal(routeTemplateOf(`/s/${S}/feedback`, S), null);
  assert.equal(routeTemplateOf('/s/6f530000-0000-4000-8000-0000000000bb/today', S), null);  // 别人的编号不换
  assert.equal(routeTemplateOf('/student?x=1', S), '/student');
  assert.equal(routeTemplateOf('', S), null);
});

const form = (pairs: [string, string][]): FormData => {
  const f = new FormData();
  for (const [k, v] of pairs) f.append(k, v);
  return f;
};

test('表单 → 请求体：合法输入；页面类型只在勾选时附上', () => {
  assert.deepEqual(feedbackFromForm(form([['category', 'slow'], ['body', '合成反馈\r\n第二行']])),
                   { category: 'slow', body: '合成反馈\n第二行', route_template: null });
  assert.deepEqual(feedbackFromForm(form([['category', 'other'], ['body', '合成'], ['route_template', '/s/[studentId]/today']])),
                   { category: 'other', body: '合成', route_template: null });
  assert.deepEqual(feedbackFromForm(form([['category', 'other'], ['body', '合成'], ['route_template', '/s/[studentId]/today'], ['attach_route', 'on']])),
                   { category: 'other', body: '合成', route_template: '/s/[studentId]/today' });
  assert.ok(feedbackFromForm(form([['category', 'login'], ['body', '字'.repeat(FEEDBACK_MAX_CHARS)]])));
});

test('表单 → 请求体：明显不合格的不发请求', () => {
  const zw = String.fromCodePoint(0x200b);
  const bad: [string, string][][] = [
    [['body', '合成']], [['category', 'bug'], ['body', '合成']], [['category', 'slow']], [['category', 'slow'], ['body', '   ']],
    [['category', 'slow'], ['body', '字'.repeat(FEEDBACK_MAX_CHARS + 1)]], [['category', 'slow'], ['body', `合成${zw}`]],
    [['category', 'slow'], ['body', `合成${String.fromCodePoint(7)}`]],
    [['category', 'slow'], ['body', '合成'], ['route_template', '/s/x/today'], ['attach_route', 'on']],
  ];
  for (const b of bad) assert.equal(feedbackFromForm(form(b)), null, JSON.stringify(b));
});

test('开关关时 BFF 客户端不发任何请求；打开后只请求三条路由；退出只发 { confirm: true }；只有一处 fetch、先过开关、不计量', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], f: process.env['STARBUDDY_INTERNAL_TEST'], fetch: globalThis.fetch };
  const calls: { url: string; method: string; body: string | undefined }[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url: String(url), method: String(init.method), body: init.body as string | undefined });
    return new Response(JSON.stringify({ status: 'recorded', exited: false }), { status: 200 });
  }) as typeof fetch;
  try {
    const api = await import('../src/internal-test/api.ts');
    const sess = { token: 'f'.repeat(64), csrf: 'c'.repeat(64) } as never;
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_INTERNAL_TEST'];
    assert.deepEqual(await api.fetchInternalTestStatus(sess, S), { ok: false, status: 404, code: 'NOT_FOUND' });
    assert.equal(calls.length, 0);
    process.env['STARBUDDY_INTERNAL_TEST'] = 'internal_test';
    assert.equal((await api.fetchInternalTestStatus(sess, 'not-a-uuid')).ok, false);
    assert.equal(calls.length, 0);
    assert.equal((await api.fetchInternalTestStatus(sess, S)).ok, true);
    assert.deepEqual(calls.map((c) => [c.method, c.url]), [['GET', `http://127.0.0.1:8787/bff/v0/students/${S}/internal-test`]]);
    // 写请求要读浏览器请求头（next/headers，单测里解析不了），只静态核对：路径与请求体
    const src = code(read('src/internal-test/api.ts'));
    assert.match(src, /call<\{ status: 'recorded' \}>\(s, student, 'POST', '\/feedback', input\)/);
    assert.match(src, /call<[^>]+>\(s, student, 'POST', '\/internal-test\/exit', \{ confirm: true \}\)/);
    assert.equal(src.match(/'POST'/g)?.length, 3);  // 类型里一处 + 两个写调用
    assert.equal(src.match(/fetch\(/g)?.length, 1);
    assert.match(src, /\): Promise<InternalTestApi<T>> \{\n\s+if \(!internalTestEnabled\(\)\) return OFF;/);
    assert.ok(!/idempotency|usage_id|meter/.test(src));
  } finally {
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.f === undefined) delete process.env['STARBUDDY_INTERNAL_TEST']; else process.env['STARBUDDY_INTERNAL_TEST'] = saved.f;
    globalThis.fetch = saved.fetch;
  }
});

test('页面、server action、入口在读会话之前先过开关；退出要勾选确认；审核台导航只在开关打开时出现', () => {
  for (const p of ['src/app/s/[studentId]/feedback/page.tsx', 'src/app/family/[studentId]/feedback/page.tsx', 'src/app/family/[studentId]/exit/page.tsx']) {
    assert.match(code(read(p)), /\{\n\s+if \(!internalTestEnabled\(\)\) notFound\(\);\n\s+const \{ studentId \} = await params;/, p);
  }
  for (const p of ['src/app/review/feedback/page.tsx', 'src/app/review/internal-test/page.tsx']) {
    assert.match(code(read(p)), /\{\n\s+if \(!internalTestEnabled\(\)\) notFound\(\);\n\s+const staff = await currentSession\(\);/, p);
  }
  const actions = code(read('src/internal-test/actions.ts'));
  assert.equal(actions.match(/\{\n\s+if \(!internalTestEnabled\(\)\) return \{ code: 'NOT_FOUND' \};\n\s+const s = await actionSession/g)?.length, 2);
  assert.match(actions, /if \(f\.get\('confirm'\) !== 'on'\) return \{ code: 'CONFIRM_MISSING' \};\n\s+const r = await postInternalTestExit/);
  assert.match(code(read('src/app/review/actions.ts')),
               /feedbackStatusAction\([^)]*\)[^{]*\{\n\s+if \(!internalTestEnabled\(\)\) return \{ code: 'NOT_FOUND' \};/);
  const links = code(read('src/internal-test/links.tsx'));
  assert.equal(links.match(/if \(!internalTestEnabled\(\)\) return (null|false);/g)?.length, 3);
  assert.match(code(read('src/app/review/layout.tsx')), /\{internalTest \? <a href="\/review\/feedback">/);
  // 学生壳与家长孩子页只经开关守着的组件渲染入口
  assert.match(code(read('src/shell/StudentShell.tsx')), /<StudentFeedbackLink studentId=\{studentId\} path=\{path\} \/>/);
  const child = code(read('src/app/family/[studentId]/page.tsx'));
  assert.match(child, /<GuardianInternalTestLinks studentId=\{studentId\} exited=\{exited\} \/>/);
  assert.ok(!child.includes('/exit`') && !child.includes('/feedback`'));  // 链接只在开关守着的组件里
  // 退出页：勾选框必填，提交后才调 BFF
  assert.match(code(read('src/app/family/[studentId]/exit/page.tsx')), /<input type="checkbox" name="confirm" required \/>/);
});

test('反馈表单：固定分类、500 字上限、隐私提示原句；来源不由前端提交', () => {
  const f = code(read('src/internal-test/FeedbackForm.tsx'));
  assert.match(f, /FEEDBACK_CATEGORIES\.map/);
  assert.match(f, /maxLength=\{FEEDBACK_MAX_CHARS\}/);
  assert.match(f, /aria-describedby="feedback-privacy"/);
  assert.equal(FEEDBACK.privacyHint, '不要填写姓名、电话等个人信息');
  assert.ok(!/name="source"/.test(f));
});

test('反馈文本不进日志：内部测试模块与页面没有 console 调用，也不引用日志与指标模块', () => {
  const ours = [...files(join(WEB, 'src/internal-test')), join(WEB, 'src/app/s/[studentId]/feedback/page.tsx'),
                join(WEB, 'src/app/family/[studentId]/feedback/page.tsx'), join(WEB, 'src/app/family/[studentId]/exit/page.tsx'),
                join(WEB, 'src/app/review/feedback/page.tsx'), join(WEB, 'src/app/review/internal-test/page.tsx')];
  for (const p of ours) {
    const t = code(readFileSync(p, 'utf8'));
    assert.ok(!/console\.|\/events|metrics|logger/.test(t), relative(WEB, p));
  }
});

test('审核台列表查询串：只拼经过校验的值，游标两个参数一起出现', () => {
  assert.equal(feedbackQuery({}), 'limit=50');
  assert.equal(feedbackQuery({ status: 'new' }), 'limit=50&status=new');
  assert.equal(feedbackQuery({ before: '2031-03-01T09:00:00+00:00' }), 'limit=50');
  assert.equal(feedbackQuery({ before: '2031-03-01T09:00:00+00:00', before_id: S }),
               `limit=50&before=2031-03-01T09%3A00%3A00%2B00%3A00&before_id=${S}`);
});

test('文案：除隐私提示原句外全部是 {{TODO_COPY:internal_test.*}} 占位；结果码齐全；不含同意书正文', () => {
  assert.ok(COPY.length >= 35);
  for (const s of COPY) {
    if (s === FEEDBACK.privacyHint) continue;
    assert.match(s, /^\{\{TODO_COPY:internal_test\.[a-z0-9_]+\}\}/, s);
  }
  for (const k of ['recorded', 'INVALID', 'TOO_MANY_REQUESTS', 'TEST_EXITED', 'CSRF', 'NOT_FOUND', 'UNAVAILABLE', 'INTERNAL']) assert.ok(FEEDBACK_RESULT[k], k);
  for (const k of ['exited', 'CONFIRM_MISSING', 'CONFIRMATION_REQUIRED', 'TOO_MANY_REQUESTS', 'FORBIDDEN', 'CSRF', 'NOT_FOUND', 'INTERNAL']) assert.ok(EXIT_RESULT[k], k);
  for (const c of FEEDBACK_CATEGORIES) assert.ok(FEEDBACK_CATEGORY_LABEL[c], c);
  const banned = /别人|同学都|排名|名次|聪明|笨|天赋|性格|焦虑|紧张|懒|购买|升级|会员|优惠|学霸|学渣|保证|承诺|一定能|你是|你扮演|本人同意|特此/;
  for (const s of COPY) assert.ok(!banned.test(s), s);
});

test('文案过禁语扫描（personas.guard，platform 口径）与禁用推断词表（sie_lexicon）', () => {
  const g = spawnSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json'], {
    cwd: ROOT, input: JSON.stringify(COPY), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.equal(g.status, 0, g.stdout + g.stderr);
  assert.deepEqual((JSON.parse(g.stdout) as { results: { hits: unknown[] }[] }).results.filter((x) => x.hits.length > 0), []);
  const script = `import json, sys\nsys.path.insert(0, 'docs/design/sie')\nimport sie_lexicon\nlex = sie_lexicon.load()\n`
    + `xs = json.load(sys.stdin)\nprint(json.dumps([[x, [h.category for h in lex.find(x)]] for x in xs], ensure_ascii=False))`;
  const r = spawnSync('python3', ['-c', script], { cwd: ROOT, input: JSON.stringify([...COPY, '（合成）这孩子天赋一般']), encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout) as [string, string[]][];
  assert.ok(out.at(-1)![1].length > 0, '探针应命中');
  assert.deepEqual(out.slice(0, -1).filter(([, hits]) => hits.length > 0), []);
});
