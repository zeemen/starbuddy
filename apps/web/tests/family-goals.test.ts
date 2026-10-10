// 家长诉求（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14）的前端单测与静态检查：
//   - 开关 STARBUDDY_FAMILY_GOALS：默认关；只在 dev / test 可开；关时 BFF 客户端不发任何请求，页面与 server action 在读会话之前先过开关；
//   - 表单 → 请求体的格式检查；BFF 返回值按白名单挑字段；
//   - 文案全部是 {{TODO_COPY:family_goals.*}} 占位，过禁语扫描、禁用推断词表、比较 / 评价 / 营销与名次 / 概率 / 承诺用词；
//   - 学生端不出现诉求：学生端的页面与组件（src/app/s、src/app/student、src/shell、src/subjects、src/star、src/today、src/practice、
//     src/retest、src/probe、src/diagnosis）不引用诉求模块、不含诉求的字段名与路由（DOM 里自然也不会有；e2e 另在运行时核对）。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { FAMILY_GOALS, FAMILY_GOALS_RESULT } from '../src/family-goals/copy.ts';
import { familyGoalsEnabled } from '../src/family-goals/features.ts';
import { goalBodyFromForm } from '../src/family-goals/form.ts';
import { pickGoalsView } from '../src/family-goals/view.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const S = '6f530000-0000-4000-8000-000000000c09';
const SCHOOL = '6f530000-0000-4000-8000-0000000005c1';
const COPY: string[] = [...Object.values(FAMILY_GOALS).map((v) => (typeof v === 'function' ? (v as (...a: unknown[]) => string)(3, '2026-10-01') : v)),
                        ...Object.values(FAMILY_GOALS_RESULT)];

test('开关：默认关；只认 family_goals；只在 dev / test 可开', () => {
  assert.equal(familyGoalsEnabled({}), false);
  assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_RETEST_UI: 'retest' }), false);
  assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_FAMILY_GOALS: 'family_goals' }), true);
  assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_FAMILY_GOALS: ' family_goals ' }), true);
  assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_FAMILY_GOALS: 'goals' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(familyGoalsEnabled({ STARBUDDY_ENV: env, STARBUDDY_FAMILY_GOALS: 'family_goals' }), false, env);
  }
});

test('开关关时 BFF 客户端不发任何请求（按 404 返回）；打开后只请求 BFF 的诉求路由；只有 call 一处 fetch、先过开关、不计量', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], f: process.env['STARBUDDY_FAMILY_GOALS'], fetch: globalThis.fetch };
  const calls: string[] = [];
  globalThis.fetch = (async (url: string) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ can_set_school: false, enrolled_subjects: [], school_options: [], current: null, history: [] }), { status: 200 });
  }) as typeof fetch;
  try {
    const api = await import('../src/family-goals/api.ts');
    const sess = { token: 'f'.repeat(64), csrf: 'c'.repeat(64) } as never;
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_FAMILY_GOALS'];
    assert.deepEqual(await api.fetchFamilyGoals(sess, S), { ok: false, status: 404, code: 'NOT_FOUND' });
    assert.equal(calls.length, 0);
    process.env['STARBUDDY_FAMILY_GOALS'] = 'family_goals';
    assert.equal((await api.fetchFamilyGoals(sess, 'not-a-uuid')).ok, false);
    assert.equal(calls.length, 0);
    assert.equal((await api.fetchFamilyGoals(sess, S)).ok, true);
    assert.deepEqual(calls, [`http://127.0.0.1:8787/bff/v0/students/${S}/family-goals`]);
    const src = code(read('src/family-goals/api.ts'));
    assert.equal(src.match(/fetch\(/g)?.length, 1);
    assert.match(src, /async function call<T>\([^)]*\)[^{]*\{\n\s+if \(!familyGoalsEnabled\(\)\) return OFF;/);
    assert.ok(!/idempotency|usage_id|meter/.test(src));
    // 页面在读会话之前过开关；server action 同样先过开关
    assert.match(code(read('src/app/family/[studentId]/goals/page.tsx')), /\{\n\s+if \(!familyGoalsEnabled\(\)\) notFound\(\);\n\s+const \{ studentId \} = await params;/);
    assert.match(code(read('src/family-goals/actions.ts')), /\{\n\s+if \(!familyGoalsEnabled\(\)\) return \{ code: 'NOT_FOUND' \};\n\s+const s = await actionSession/);
    assert.match(code(read('src/family-goals/GoalsLink.tsx')), /if \(!familyGoalsEnabled\(\)\) return null;/);
  } finally {
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.f === undefined) delete process.env['STARBUDDY_FAMILY_GOALS']; else process.env['STARBUDDY_FAMILY_GOALS'] = saved.f;
    globalThis.fetch = saved.fetch;
  }
});

const form = (pairs: [string, string][]): FormData => {
  const f = new FormData();
  for (const [k, v] of pairs) f.append(k, v);
  return f;
};

test('表单 → 请求体：合法输入', () => {
  assert.deepEqual(goalBodyFromForm(form([])), { focus_subjects: [], target_scores: [], school_id: null, note: null });
  assert.deepEqual(goalBodyFromForm(form([['focus', 'aya'], ['focus', 'wenli'], ['full_aya', '120'], ['target_aya', '105.5'],
                                          ['full_wenli', ''], ['target_wenli', ''], ['school', SCHOOL], ['note', '  合成说明  ']])),
                   { focus_subjects: ['aya', 'wenli'], target_scores: [{ subject: 'aya', full_score: 120, target_score: 105.5 }],
                     school_id: SCHOOL, note: '合成说明' });
});

test('表单 → 请求体：明显不合格的不发请求', () => {
  const bad: [string, string][][] = [
    [['focus', 'aya'], ['focus', 'wenli'], ['focus', 'zhixing']], [['focus', 'aya'], ['focus', 'aya']], [['focus', 'math']],
    [['full_aya', '120']], [['target_aya', '100']], [['full_aya', '120'], ['target_aya', '121']], [['full_aya', '0'], ['target_aya', '0']],
    [['full_aya', '301'], ['target_aya', '1']], [['full_aya', '100'], ['target_aya', '95.25']], [['full_aya', '1e2'], ['target_aya', '90']],
    [['full_aya', '-1'], ['target_aya', '0']], [['school', 'x']], [['note', '说'.repeat(201)]],
  ];
  for (const b of bad) assert.equal(goalBodyFromForm(form(b)), null, JSON.stringify(b));
});

test('BFF 返回值按白名单挑字段：不认识的字段、不合格的学科与学校一律丢掉；历史只留版本与时间', () => {
  const v = pickGoalsView({
    grade: 9, can_set_school: true, enrolled_subjects: ['aya', 'math', 'wenli'], extra: 'x',
    school_options: [{ id: SCHOOL, public_name: '合成高中' }, { id: 'x', public_name: 'y' }],
    current: { version: 2, focus_subjects: ['aya'], target_scores: [{ subject: 'aya', full_score: 120, target_score: 100 }, { subject: 'x' }],
               school: { id: SCHOOL, public_name: '合成高中' }, note: '合成', source_type: 'guardian', created_at: '2026-10-01T00:00:00Z', rank: 1 },
    history: [{ version: 1, focus_subjects: [], target_scores: [], school_id: null, note: '旧的说明', created_at: '2026-09-01T00:00:00Z',
                superseded_at: '2026-10-01T00:00:00Z' }],
  })!;
  assert.deepEqual(v, {
    canSetSchool: true, enrolled: ['aya', 'wenli'], schools: [{ id: SCHOOL, name: '合成高中' }],
    current: { version: 2, focus: ['aya'], targets: [{ subject: 'aya', full_score: 120, target_score: 100 }], schoolId: SCHOOL, note: '合成' },
    history: [{ version: 1, createdAt: '2026-09-01T00:00:00Z' }],
  });
  assert.equal(pickGoalsView(null), null);
  assert.equal(pickGoalsView([]), null);
});

test('文案全部是 {{TODO_COPY:family_goals.*}} 占位；结果码齐全；没有评价、名次、概率、承诺、营销用词，也不出现“偏科”标签', () => {
  assert.ok(COPY.length >= 30);
  for (const s of COPY) assert.match(s, /^\{\{TODO_COPY:family_goals\.[a-z0-9_.]+\}\}/, s);
  for (const k of ['saved', 'INVALID', 'TEXT_NOT_ACCEPTED', 'SCHOOL_NOT_OPEN_FOR_GRADE', 'GOAL_GRADE_NOT_SUPPORTED', 'TOO_MANY', 'TOO_MANY_REQUESTS', 'CONFLICT',
                   'CSRF', 'NOT_FOUND', 'UNAVAILABLE', 'INTERNAL']) {
    assert.ok(FAMILY_GOALS_RESULT[k], k);
  }
  const banned = /别人|同学都|排名|名次|最差|最好|落后|领先|聪明|笨|天赋|性格|焦虑|紧张|懒|购买|升级|会员|优惠|加油|棒|学霸|学渣|偏科|概率|录取|够得着|保证|提分|承诺|一定能/;
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

test('学生端不出现诉求：学生端页面与组件不引用诉求模块，不含诉求的字段名、路由与文案键', () => {
  const studentDirs = ['src/app/s', 'src/app/student', 'src/shell', 'src/subjects', 'src/star', 'src/today', 'src/practice', 'src/retest',
                       'src/probe', 'src/diagnosis'];
  const studentFiles = studentDirs.flatMap((d) => files(join(WEB, d))).map((p) => ({ path: relative(WEB, p), text: code(readFileSync(p, 'utf8')) }));
  assert.ok(studentFiles.length >= 30);
  for (const f of studentFiles) {
    for (const s of ['family-goals', 'family_goals', 'FAMILY_GOALS', 'familyGoals', 'focus_subjects', 'target_scores', 'school_options',
                     'guardian_goal', 'GoalsLink']) {
      assert.ok(!f.text.includes(s), `${f.path} 含 ${s}`);
    }
  }
  // 诉求模块只被家长端引用：家长孩子页（入口）与“我的诉求”页
  const users = files(join(WEB, 'src')).map((p) => relative(WEB, p))
    .filter((p) => !p.startsWith('src/family-goals/') && /family-goals\//.test(code(readFileSync(join(WEB, p), 'utf8'))));
  assert.deepEqual(users.sort(), ['src/app/family/[studentId]/goals/page.tsx', 'src/app/family/[studentId]/page.tsx']);
  // 页面与组件里没有中文文案（只从 ./copy.ts 取）
  for (const p of ['src/app/family/[studentId]/goals/page.tsx', 'src/family-goals/GoalsLink.tsx']) {
    assert.deepEqual(code(read(p)).match(/[一-鿿]+/g) ?? [], [], p);
  }
});
