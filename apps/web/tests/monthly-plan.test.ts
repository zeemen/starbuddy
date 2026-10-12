// 月度计划（specs/family-goals-monthly-plan/step3-spec.md，PLT-14 第 3 步）的前端单测与静态检查：
//   - 开关 STARBUDDY_MONTHLY_PLAN：默认关；只在 dev / test 可开；关时 BFF 客户端不发任何请求，页面与入口在读会话之前先过开关；
//   - BFF 返回值按白名单挑字段（关于学生的句子没有合法 claim_id 不留；学生端挑法不认识注意事项、回顾与分差）；
//   - 文案全部是 {{TODO_COPY:monthly_plan.*}} 占位，过禁语扫描、禁用推断词表与“名次 / 概率 / 承诺”词表；
//   - 学生端只引用学习计划一段：学生页、学习计划组件与学生入口不引用家长端的文案与挑法、诉求模块，不含注意事项、回顾、分差、诉求的字段名；
//   - 页面与组件里没有中文文案（只从 copy 文件取）；计划里的先后不编号（无序列表）。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { monthlyPlanEnabled } from '../src/monthly-plan/features.ts';
import { MONTHLY_PLAN } from '../src/monthly-plan/guardian-copy.ts';
import { pickGuardianPlan } from '../src/monthly-plan/guardian-view.ts';
import { MY_PLAN, PLAN_SECTION } from '../src/monthly-plan/student-copy.ts';
import { pickMyPlan, pickStudentPlan } from '../src/monthly-plan/student-view.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const S = '6f540000-0000-4000-8000-000000000c01';
const C1 = '6f540000-0000-4000-8000-0000000000e1';
const CE = '6f540000-0000-4000-8000-0000000000e4';
const COPY: string[] = [...Object.values(MONTHLY_PLAN), ...Object.values(MY_PLAN), ...Object.values(PLAN_SECTION)];
const STUDENT_FILES = ['src/app/s/[studentId]/plan/page.tsx', 'src/monthly-plan/StudentPlanSection.tsx', 'src/monthly-plan/MyPlanLink.tsx',
                       'src/monthly-plan/student-view.ts', 'src/monthly-plan/student-copy.ts', 'src/app/s/[studentId]/today/page.tsx'];

test('开关：默认关；只认 monthly_plan；只在 dev / test 可开', () => {
  assert.equal(monthlyPlanEnabled({}), false);
  assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: 'test' }), false);
  assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_FAMILY_GOALS: 'family_goals' }), false);
  assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_MONTHLY_PLAN: 'monthly_plan' }), true);
  assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: 'dev', STARBUDDY_MONTHLY_PLAN: ' monthly_plan ' }), true);
  assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: 'test', STARBUDDY_MONTHLY_PLAN: 'plan' }), false);
  for (const env of ['staging', 'pilot', 'production', '']) {
    assert.equal(monthlyPlanEnabled({ STARBUDDY_ENV: env, STARBUDDY_MONTHLY_PLAN: 'monthly_plan' }), false, env);
  }
});

test('开关关时 BFF 客户端不发任何请求（按 404 返回）；打开后只请求 BFF 的两条月度计划路由；只有 call 一处 fetch、先过开关、只读不计量', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], f: process.env['STARBUDDY_MONTHLY_PLAN'], fetch: globalThis.fetch };
  const calls: string[] = [];
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push(`${init.method} ${String(url)}`);
    return new Response(JSON.stringify({ student_id: S, month: '2026-10', plan: null }), { status: 200 });
  }) as typeof fetch;
  try {
    const api = await import('../src/monthly-plan/api.ts');
    const sess = { token: 'f'.repeat(64), csrf: 'c'.repeat(64) } as never;
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_MONTHLY_PLAN'];
    assert.deepEqual(await api.fetchGuardianPlan(sess, S), { ok: false, status: 404 });
    assert.deepEqual(await api.fetchMyPlan(sess, S), { ok: false, status: 404 });
    assert.equal(calls.length, 0);
    process.env['STARBUDDY_MONTHLY_PLAN'] = 'monthly_plan';
    assert.equal((await api.fetchMyPlan(sess, 'not-a-uuid')).ok, false);
    assert.equal(calls.length, 0);
    assert.equal((await api.fetchGuardianPlan(sess, S)).ok, true);
    assert.equal((await api.fetchMyPlan(sess, S)).ok, true);
    assert.deepEqual(calls, [`GET http://127.0.0.1:8787/bff/v0/students/${S}/monthly-plan`, `GET http://127.0.0.1:8787/bff/v0/students/${S}/my-plan`]);
    const src = code(read('src/monthly-plan/api.ts'));
    assert.equal(src.match(/fetch\(/g)?.length, 1);
    assert.match(src, /async function call\([^)]*\)[^{]*\{\n\s+if \(!monthlyPlanEnabled\(\)\) return OFF;/);
    assert.ok(!/idempotency|usage_id|meter|method: 'POST'|method: 'PUT'/.test(src));
    // 页面在读会话之前过开关；入口同样先过开关
    for (const p of ['src/app/family/[studentId]/monthly-plan/page.tsx', 'src/app/s/[studentId]/plan/page.tsx']) {
      assert.match(code(read(p)), /\{\n\s+if \(!monthlyPlanEnabled\(\)\) notFound\(\);\n\s+const \{ studentId \} = await params;/, p);
    }
    assert.match(code(read('src/monthly-plan/PlanLinks.tsx')), /if \(!monthlyPlanEnabled\(\)\) return null;/);
    assert.match(code(read('src/monthly-plan/MyPlanLink.tsx')), /if \(!monthlyPlanEnabled\(\)\) return null;/);
  } finally {
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.f === undefined) delete process.env['STARBUDDY_MONTHLY_PLAN']; else process.env['STARBUDDY_MONTHLY_PLAN'] = saved.f;
    globalThis.fetch = saved.fetch;
  }
});

const studentPlan = () => ({
  priority_nodes: [{ claim_id: C1, subject: 'aya', text: '{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜MATH.SYN.A｜apply', position: 1 },
                   { claim_id: 'x', subject: 'aya', text: '没有合法判断 ID 的句子' }, { claim_id: C1, subject: 'echo', text: 'x' }],
  weekly_rhythm: { sessions_per_week: 5, minutes_per_session: 30, text: '{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30', focus_factor: 1.5 },
  retests: { count: 2, dates: ['2026-10-01', 'bad'], by_subject: [], text: '{{TODO_COPY:monthly_plan.student.retests}}｜2' },
  mini_reviews: [{ subject: 'aya', date: '2026-10-24', text: '{{TODO_COPY:monthly_plan.student.mini_review}}｜aya｜2026-10-24' }],
});

test('学习计划挑字段：没有合法 claim_id 的句子、不认识的学科与字段一律丢掉；学生端只认学习计划', () => {
  const p = pickStudentPlan(studentPlan())!;
  assert.deepEqual(p, {
    priority: [{ claimId: C1, subject: 'aya', text: '{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜MATH.SYN.A｜apply' }],
    rhythm: { sessions: 5, minutes: 30, text: '{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30' },
    retests: { count: 2, dates: ['2026-10-01'], text: '{{TODO_COPY:monthly_plan.student.retests}}｜2' },
    miniReviews: [{ subject: 'aya', date: '2026-10-24', text: '{{TODO_COPY:monthly_plan.student.mini_review}}｜aya｜2026-10-24' }],
  });
  const my = pickMyPlan({ student_id: S, month: '2026-10', plan: { generated_at: '2026-10-12T02:00:00Z', student_plan: studentPlan(),
                                                                     guardian_notes: [{ text: 'x' }], review: { score_gaps: [] } } })!;
  assert.deepEqual(Object.keys(my).sort(), ['month', 'plan']);
  assert.deepEqual(Object.keys(my.plan!).sort(), ['miniReviews', 'priority', 'retests', 'rhythm']);
  assert.deepEqual(pickMyPlan({ student_id: S, month: '2026-10', plan: null }), { month: '2026-10', plan: null });
  assert.equal(pickMyPlan({ month: 'x', plan: null }), null);
  assert.equal(pickMyPlan(null), null);
});

test('家长端挑字段：注意事项、上月回顾、分差区间（只有区间）；没有权益 → entitled false、plan null', () => {
  const v = pickGuardianPlan({
    student_id: S, month: '2026-10', entitled: true,
    plan: {
      generated_at: '2026-10-11T18:00:00Z', student_plan: studentPlan(),
      guardian_notes: [{ text: '{{TODO_COPY:monthly_plan.guardian.weekly_rhythm_support}}｜5｜30' }, { text: '' }],
      review: {
        last_month: { month: '2026-09', planned_nodes: 3, text: '{{TODO_COPY:monthly_plan.review.last_month}}｜3', lit: [{ claim_id: 'bad', subject: 'aya', text: 'x' }] },
        score_gaps: [
          { claim_id: CE, subject: 'aya', exam_type: 'midterm', exam_date: '2026-10-09', gap_band: { reached: false, low: 10, high: 15 }, text: 't', raw_score: 92.5 },
          { claim_id: CE, subject: 'aya', exam_type: 'midterm', exam_date: '2026-10-09', gap_band: { reached: false, low: 10, high: 12 }, text: 't' },
          { claim_id: CE, subject: 'wenli', exam_type: 'final', exam_date: '2026-07-01', gap_band: { reached: true }, text: 't' },
        ],
      },
    },
  })!;
  assert.equal(v.entitled, true);
  assert.equal(v.plan!.generatedDay, '2026-10-12', '生成日期按上海时区');
  assert.deepEqual(v.plan!.notes, ['{{TODO_COPY:monthly_plan.guardian.weekly_rhythm_support}}｜5｜30']);
  assert.deepEqual(v.plan!.lastMonth, { month: '2026-09', plannedNodes: 3, text: '{{TODO_COPY:monthly_plan.review.last_month}}｜3', lit: [] });
  assert.deepEqual(v.plan!.gaps, [
    { claimId: CE, subject: 'aya', text: 't', examType: 'midterm', examDate: '2026-10-09', band: { reached: false, low: 10, high: 15 } },
    { claimId: CE, subject: 'wenli', text: 't', examType: 'final', examDate: '2026-07-01', band: { reached: true } },
  ]);
  assert.ok(!JSON.stringify(v).includes('92.5'), '成绩不进页面');
  assert.deepEqual(pickGuardianPlan({ student_id: S, month: '2026-10', entitled: false, plan: null }), { month: '2026-10', entitled: false, plan: null });
  assert.equal(pickGuardianPlan({ month: '2026-10' }), null);
});

test('文案全部是 {{TODO_COPY:monthly_plan.*}} 占位；没有评价、名次、概率、承诺、营销用词，也不出现“偏科”标签', () => {
  assert.ok(COPY.length >= 35);
  for (const s of COPY) assert.match(s, /^\{\{TODO_COPY:monthly_plan\.(page|my|plan)\.[a-z0-9_]+\}\}/, s);
  const banned = /别人|同学都|排名|名次|最差|最好|落后|领先|聪明|笨|天赋|性格|焦虑|紧张|懒|购买|优惠|加油|棒|学霸|学渣|偏科|概率|录取|够得着|保证|提分|承诺|一定能|第\s*\d+/;
  for (const s of COPY) assert.ok(!banned.test(s), s);
});

test('文案过禁语扫描（personas.guard，platform 口径）、禁用推断词表与“名次 / 概率 / 承诺”词表（sie_lexicon）', () => {
  const g = spawnSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json'], {
    cwd: ROOT, input: JSON.stringify(COPY), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.equal(g.status, 0, g.stdout + g.stderr);
  assert.deepEqual((JSON.parse(g.stdout) as { results: { hits: unknown[] }[] }).results.filter((x) => x.hits.length > 0), []);
  const script = `import json, sys\nfrom pathlib import Path\nsys.path.insert(0, 'docs/design/sie')\nimport sie_lexicon\n`
    + `lexes = [sie_lexicon.load(), sie_lexicon.load(Path('docs/design/sie/no-promise.v0.draft.yaml'))]\n`
    + `xs = json.load(sys.stdin)\nprint(json.dumps([[x, [h.category for lex in lexes for h in lex.find(x)]] for x in xs], ensure_ascii=False))`;
  const r = spawnSync('python3', ['-c', script], { cwd: ROOT, input: JSON.stringify([...COPY, '（合成）这孩子天赋一般', '（合成）本月可以提高 15 分']), encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout) as [string, string[]][];
  assert.ok(out.at(-1)![1].length > 0 && out.at(-2)![1].length > 0, '探针应命中');
  assert.deepEqual(out.slice(0, -2).filter(([, hits]) => hits.length > 0), []);
});

test('学生端只引用学习计划一段：不引用家长端文案与挑法、诉求模块；不含注意事项、回顾、分差、诉求的字段名与路由', () => {
  for (const p of STUDENT_FILES) {
    const t = code(read(p));
    for (const s of ['guardian-copy', 'guardian-view', 'PlanLinks', 'family-goals', 'MONTHLY_PLAN', 'pickGuardianPlan', 'fetchGuardianPlan',
                     'guardian_notes', 'score_gaps', 'gap_band', 'last_month', 'entitled', 'focus_subjects', 'target_scores', 'focus_factor',
                     '}/monthly-plan']) {
      assert.ok(!t.includes(s), `${p} 含 ${s}`);
    }
  }
  // 家长端的文案与挑法只被家长端页面与家长入口引用
  for (const mod of ['monthly-plan/guardian-copy', 'monthly-plan/guardian-view', 'monthly-plan/PlanLinks']) {
    const users = ['src/app/family/[studentId]/monthly-plan/page.tsx', 'src/app/family/[studentId]/page.tsx', ...STUDENT_FILES]
      .filter((p) => code(read(p)).includes(mod) || code(read(p)).includes(mod.split('/')[1]!));
    assert.ok(users.every((p) => p.startsWith('src/app/family/')), `${mod} 被 ${users.join(',')} 引用`);
  }
  // 页面与组件里没有中文文案（只从 copy 文件取）；优先知识点是无序列表（计划里的先后不编号）
  for (const p of ['src/app/family/[studentId]/monthly-plan/page.tsx', 'src/app/s/[studentId]/plan/page.tsx', 'src/monthly-plan/StudentPlanSection.tsx',
                   'src/monthly-plan/PlanLinks.tsx', 'src/monthly-plan/MyPlanLink.tsx']) {
    assert.deepEqual(code(read(p)).match(/[一-鿿]+/g) ?? [], [], p);
    assert.ok(!/<ol\b/.test(read(p)), `${p} 不用有序列表`);
  }
});
