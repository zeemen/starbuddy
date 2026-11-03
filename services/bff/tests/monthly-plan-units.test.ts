// 月度计划（PLT-14 第 3 步，specs/family-goals-monthly-plan/step3-spec.md）的 BFF 纯函数单测（不连库）：
//   - 开关只在 dev / test、只认 monthly_plan；
//   - 模板库按固定写法解析（与洞察服务同一份文件），未签字只收占位、只在 dev / test；写法不对、文案命中词表一律拒绝加载；
//   - 渲染：占位渲染为“占位｜变量值…”，签字文案按 {变量名} 填空；不渲染计划里的先后（position）与任何来自诉求的字段；
//     成品句子过两张词表，命中的那一条不出、只记条数；关于学生的句子所引判断的模板须在对外白名单里；
//   - “名次 / 概率 / 承诺”词表在 BFF 端读得到、自检通过，与禁用推断词表一起过；
//   - 洞察服务生成接口客户端：caller = bff 带 actor；只有 404 CONTENT_VERSION_NOT_FOUND 算“不可用（没有权益）”，其余都是 unavailable。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  checkLock, cleanWith, exportLibrary, httpMonthlyPlanClient, MAX_TEXT, newTally, NOTE_FACTS, parseMonthlyPlanFeature, parseTemplates, renderCopy,
  renderGuardianPlan, renderStudentPlan, renderStudentView, TEMPLATE_LOCK_PATH, TEMPLATE_PATH, type RenderCtx, type Tally, type TemplateLibrary,
} from '../src/family/monthly-plan.ts';
import { createHash } from 'node:crypto';
import { checkNoPromise, forbiddenHits, lexicon, noPromiseLexicon, parseLexicon } from '../src/trust/lexicon.ts';

const FILE = readFileSync(TEMPLATE_PATH, 'utf8');
const CLEAN = cleanWith(lexicon(), noPromiseLexicon());
const LIB = parseTemplates(FILE, 'test', CLEAN);
const C1 = '9a540000-0000-4000-8000-0000000000c1';
const C2 = '9a540000-0000-4000-8000-0000000000c2';
const C3 = '9a540000-0000-4000-8000-0000000000c3';
const CX = '9a540000-0000-4000-8000-0000000000c9';
const EXTERNAL = new Set(['syn.math.node_mastery@1', 'syn.math.node_transfer@1', 'syn.exam.reconcile@1']);
const ctx = (over: Partial<RenderCtx> = {}): RenderCtx => ({ lib: LIB, clean: CLEAN, external: EXTERNAL, ...over });

test('开关：只在 dev / test、只认 monthly_plan；缺省关', () => {
  assert.equal(parseMonthlyPlanFeature('test', undefined), false);
  assert.equal(parseMonthlyPlanFeature('test', ' '), false);
  assert.equal(parseMonthlyPlanFeature('test', 'monthly_plan'), true);
  assert.equal(parseMonthlyPlanFeature('dev', ' monthly_plan '), true);
  assert.throws(() => parseMonthlyPlanFeature('test', 'family_goals'), /STARBUDDY_MONTHLY_PLAN/);
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseMonthlyPlanFeature(env, 'monthly_plan'), /STARBUDDY_MONTHLY_PLAN/);
});

test('模板库：与洞察服务同一份文件，解析出全部固定模板与注意事项模板；未签字的占位只在 dev / test 可读', () => {
  assert.equal(LIB.version, 'syn-tpl-0.1.0');
  assert.equal(LIB.signed, false);
  assert.match(LIB.fingerprint, /^[0-9a-f]{64}$/);
  assert.deepEqual([...LIB.byKey.keys()].sort(), [
    'monthly_plan.guardian.exam_month_routine', 'monthly_plan.guardian.mini_review_dates', 'monthly_plan.guardian.quiet_time_for_retests',
    'monthly_plan.guardian.weekly_rhythm_support', 'monthly_plan.review.last_month', 'monthly_plan.review.lit', 'monthly_plan.review.score_gap',
    'monthly_plan.student.mini_review', 'monthly_plan.student.priority_node', 'monthly_plan.student.retests', 'monthly_plan.student.weekly_rhythm',
  ]);
  assert.deepEqual(LIB.byKey.get('monthly_plan.student.priority_node'),
                   { copy: '{{TODO_COPY:monthly_plan.student.priority_node}}', vars: ['subject', 'node_id', 'cognitive_level'] });
  // 计划里的先后（position）不是模板变量：不渲染为名次
  for (const t of LIB.byKey.values()) assert.ok(!t.vars.includes('position'));
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseTemplates(FILE, env, CLEAN), /只允许 dev \/ test/);
});

test('模板库：写法不对、占位键不一致、未签字却有文字、文案命中词表、缺模板、多出不认识的行，一律拒绝加载', () => {
  const bad = [
    FILE.replace('format: sie-monthly-plan-templates/0.1', 'format: other'),
    FILE.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', '"{{TODO_COPY:monthly_plan.student.weekly_rhythm}}"'),
    FILE.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', '"本月复测 {count} 次"'),
    FILE.replace(/ {2}mini_review:\n {4}copy: "[^"]+"\n {4}vars: \[subject, date\]\n/, ''),
    FILE.replace('vars: [count]', 'vars: count'),
    FILE.replace('template_version: syn-tpl-0.1.0', 'template_version: syn-tpl-0.1.0\nunexpected line'),
    FILE.replace('  - key: exam_month_routine', '  - key: quiet_time_for_retests'),
  ];
  for (const [i, t] of bad.entries()) assert.notEqual(t, FILE, `变体 ${i} 没有改到`);
  for (const [i, t] of bad.entries()) assert.throws(() => parseTemplates(t, 'test', CLEAN), Error, `变体 ${i}`);
  // 签字的文案可以是文字，但同样要过两张词表
  const signed = FILE.replace('status: draft', 'status: signed').replace('synthetic: true', 'synthetic: false')
    .replace('signed_by: ""', 'signed_by: "syn-reviewer"').replace('signed_at: ""', 'signed_at: "2026-10-12"');
  const ok = parseTemplates(signed.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', '"本月复测 {count} 次"'), 'staging', CLEAN);
  assert.equal(ok.signed, true);
  assert.equal(renderCopy(ok, 'monthly_plan.student.retests', { count: '3' }), '本月复测 3 次');
  assert.throws(() => parseTemplates(signed.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', '"坚持复测 {count} 次保证提分"'), 'staging', CLEAN), /词表/);
  assert.throws(() => parseTemplates(signed.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', '"孩子有点懒惰，本月复测 {count} 次"'), 'staging', CLEAN), /词表/);
  // 签字文案里的 {变量} 集合必须恰好等于声明的 vars（与洞察服务同一规则）
  for (const copy of ['"本月复测几次"', '"本月复测 {count} 次，每周 {days} 天"', '"本月复测 {count} 次 }"', '"本月复测 {Count} 次"']) {
    assert.throws(() => parseTemplates(signed.replace('"{{TODO_COPY:monthly_plan.student.retests}}"', copy), 'staging', CLEAN), /vars/, copy);
  }
  // 注意事项的变量只能是计划事实（与洞察服务 monthly.NOTE_FACTS 同一份白名单）
  assert.throws(() => parseTemplates(FILE.replace('vars: [exam_count]', 'vars: [exam_count, subject]'), 'test', CLEAN), /计划事实/);
});

test('注意事项变量白名单与洞察服务 monthly.NOTE_FACTS 一致', () => {
  const py = readFileSync(new URL('../../insight/insight/monthly.py', import.meta.url), 'utf8');
  const m = /^NOTE_FACTS = \(([^)]*)\)/m.exec(py)!;
  assert.ok(m, 'monthly.py 里找不到 NOTE_FACTS');
  assert.deepEqual([...m[1]!.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]), [...NOTE_FACTS]);
});

test('指纹 = 模板库原文字节的 SHA-256；加载时与钉死文件核对：内容变了而版本号与钉死不变即拒绝', () => {
  const bytes = readFileSync(TEMPLATE_PATH);
  const lockText = readFileSync(TEMPLATE_LOCK_PATH, 'utf8');
  assert.equal(LIB.fingerprint, createHash('sha256').update(bytes).digest('hex'));
  assert.equal(checkLock(parseTemplates(bytes, 'test', CLEAN), lockText).version, 'syn-tpl-0.1.0');
  const changed = FILE.replace('retest_count_min: 1', 'retest_count_min: 2');  // BFF 不用 when，但内容变了指纹就变
  assert.notEqual(changed, FILE);
  assert.throws(() => checkLock(parseTemplates(changed, 'test', CLEAN), lockText), /钉死/);
  const bumped = FILE.replace('template_version: syn-tpl-0.1.0', 'template_version: syn-tpl-0.1.1');
  assert.throws(() => checkLock(parseTemplates(bumped, 'test', CLEAN), lockText), /钉死/, '只改版本号、钉死文件没跟着改');
  const lock = JSON.parse(lockText) as Record<string, unknown>;
  assert.throws(() => checkLock(LIB, JSON.stringify({ ...lock, extra: 1 })), /钉死/);
  assert.throws(() => checkLock(LIB, JSON.stringify({ template_version: lock['template_version'] })), /钉死/);
});

test('跨语言一致性：BFF 解析出的键、vars、文案、版本、指纹与洞察服务（Python）导出的夹具一致', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../insight/tests/fixtures/monthly_plan_templates.expected.json', import.meta.url), 'utf8'));
  assert.deepEqual(exportLibrary(LIB), fixture);
});

test('渲染：占位渲染为“占位｜变量值…”（按模板声明的顺序）；签字文案填空不能留下花括号；缺变量不出', () => {
  assert.equal(renderCopy(LIB, 'monthly_plan.student.weekly_rhythm', { sessions_per_week: '5', minutes_per_session: '30' }),
               '{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30');
  assert.equal(renderCopy(LIB, 'monthly_plan.student.weekly_rhythm', { sessions_per_week: '5' }), null);
  assert.equal(renderCopy(LIB, 'monthly_plan.student.no_such', {}), null);
});

const studentPlan = () => ({
  priority_nodes: [
    { position: 1, subject: 'aya', node_id: 'MATH.SYN.MP.ALPHA', cognitive_level: 'apply', claim_id: C1, template_key: 'syn.math.node_mastery',
      template_version: 1, copy_key: 'monthly_plan.student.priority_node' },
    { position: 2, subject: 'aya', node_id: 'MATH.SYN.MP.BETA', cognitive_level: 'apply', claim_id: C2, template_key: 'syn.other.not_whitelisted',
      template_version: 1, copy_key: 'monthly_plan.student.priority_node' },
    { position: 3, subject: 'aya', node_id: 'math.bad', cognitive_level: 'apply', claim_id: C3, template_key: 'syn.math.node_mastery',
      template_version: 1, copy_key: 'monthly_plan.student.priority_node' },
  ],
  weekly_rhythm: { sessions_per_week: 5, minutes_per_session: 30, copy_key: 'monthly_plan.student.weekly_rhythm' },
  retests: { count: 2, dates: ['2026-10-01', '2026-10-12'], by_subject: [{ subject: 'aya', count: 2 }], copy_key: 'monthly_plan.student.retests' },
  mini_reviews: [{ subject: 'aya', date: '2026-10-24', copy_key: 'monthly_plan.student.mini_review' }],
});

const guardianView = () => ({
  month: '2026-10-01', generated_at: '2026-10-12T02:00:00.123456+00:00', config_version: 'syn-0.1.0_syn-0.1.0', template_version: 'syn-tpl-0.1.0',
  student_plan: studentPlan(),
  guardian_notes: [
    { key: 'quiet_time_for_retests', copy_key: 'monthly_plan.guardian.quiet_time_for_retests', vars: { retest_count: 2, sessions_per_week: 5, minutes_per_session: 30 } },
    { key: 'weekly_rhythm_support', copy_key: 'monthly_plan.guardian.weekly_rhythm_support', vars: { sessions_per_week: 5, minutes_per_session: 30 } },
  ],
  review: {
    last_month: { month: '2026-09-01', planned_nodes: 3, copy_key: 'monthly_plan.review.last_month',
                  lit: [{ subject: 'aya', node_id: 'MATH.SYN.MP.GAMMA', claim_id: CX, template_key: 'syn.math.node_transfer', template_version: 1,
                          copy_key: 'monthly_plan.review.lit' }] },
    score_gaps: [
      { subject: 'aya', exam_type: 'midterm', exam_date: '2026-10-09', gap_band: { reached: false, low: 10, high: 15 }, claim_id: CX,
        template_key: 'syn.exam.reconcile', template_version: 1, copy_key: 'monthly_plan.review.score_gap' },
      { subject: 'wenli', exam_type: 'final', exam_date: '2026-07-01', gap_band: { reached: true }, claim_id: CX,
        template_key: 'syn.exam.reconcile', template_version: 1, copy_key: 'monthly_plan.review.score_gap' },
    ],
  },
});

test('学习计划渲染：每条优先知识点带 claim_id；不出 position、层次编码之外的字段；白名单外的判断模板与形状不对的条目不出、只记条数', () => {
  const t: Tally = newTally();
  const out = renderStudentPlan(studentPlan(), ctx(), t)!;
  assert.deepEqual(out.priority_nodes, [{ claim_id: C1, subject: 'aya', text: '{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜MATH.SYN.MP.ALPHA｜apply' }]);
  assert.equal(t.dropped, 2);
  assert.deepEqual(out.weekly_rhythm, { sessions_per_week: 5, minutes_per_session: 30, text: '{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30' });
  assert.deepEqual(out.retests, { count: 2, dates: ['2026-10-01', '2026-10-12'], by_subject: [{ subject: 'aya', count: 2 }],
                                  text: '{{TODO_COPY:monthly_plan.student.retests}}｜2' });
  assert.deepEqual(out.mini_reviews, [{ subject: 'aya', date: '2026-10-24', text: '{{TODO_COPY:monthly_plan.student.mini_review}}｜aya｜2026-10-24' }]);
  const json = JSON.stringify(out, (k, v) => (k === 'text' ? undefined : v));
  for (const k of ['position', 'template_key', 'template_version', 'copy_key', 'focus_factor', 'priority', 'gap', 'weight', 'goal_id', 'node_id', 'cognitive_level']) {
    assert.ok(!json.includes(`"${k}"`), k);
  }
  assert.equal(renderStudentPlan({ ...studentPlan(), priority_nodes: 'x' }, ctx(), newTally()), null, '整段形状不对：不可用');
  assert.equal(renderStudentPlan(null, ctx(), newTally()), null);
});

test('家长视图渲染：注意事项、上月回顾、分差区间（不出成绩与目标分）；每条关于学生的句子带 claim_id', () => {
  const t: Tally = newTally();
  const out = renderGuardianPlan(guardianView(), ctx(), t)!;
  assert.equal(t.dropped, 2);
  assert.deepEqual(out.guardian_notes.map((n) => n.text), [
    '{{TODO_COPY:monthly_plan.guardian.quiet_time_for_retests}}｜2｜5｜30', '{{TODO_COPY:monthly_plan.guardian.weekly_rhythm_support}}｜5｜30']);
  assert.deepEqual(out.review.last_month, { month: '2026-09', planned_nodes: 3, text: '{{TODO_COPY:monthly_plan.review.last_month}}｜3',
                                            lit: [{ claim_id: CX, subject: 'aya', text: '{{TODO_COPY:monthly_plan.review.lit}}｜aya｜MATH.SYN.MP.GAMMA' }] });
  assert.deepEqual(out.review.score_gaps, [
    { claim_id: CX, subject: 'aya', exam_type: 'midterm', exam_date: '2026-10-09', gap_band: { reached: false, low: 10, high: 15 },
      text: '{{TODO_COPY:monthly_plan.review.score_gap}}｜aya｜10–15' },
    { claim_id: CX, subject: 'wenli', exam_type: 'final', exam_date: '2026-07-01', gap_band: { reached: true },
      text: '{{TODO_COPY:monthly_plan.review.score_gap}}｜wenli｜≤0' },
  ]);
  assert.equal(out.generated_at, '2026-10-12T02:00:00.123456+00:00');
  // 区间不合 5 分一档、带了精确值的分差不出
  const v = guardianView();
  v.review.score_gaps = [{ ...v.review.score_gaps[0]!, gap_band: { reached: false, low: 10, high: 12 } },
                         { ...v.review.score_gaps[0]!, gap_band: { reached: false, low: 10, high: 15, gap: 12 } as never }];
  const t2: Tally = newTally();
  assert.deepEqual(renderGuardianPlan(v, ctx(), t2)!.review.score_gaps, []);
  assert.equal(t2.dropped, 4);
  // 没有上月快照
  const v3 = guardianView();
  (v3.review as Record<string, unknown>)['last_month'] = null;
  assert.equal(renderGuardianPlan(v3, ctx(), newTally())!.review.last_month, null);
  assert.equal(renderGuardianPlan({ ...guardianView(), generated_at: 'yesterday' }, ctx(), newTally()), null);
  assert.equal(renderGuardianPlan({ ...guardianView(), guardian_notes: [{}, {}, {}, {}] }, ctx(), newTally()), null);
});

test('成品句子过两张词表：命中的那一条不出、只记条数；其余照常', () => {
  const noPromise = noPromiseLexicon();
  const t: Tally = newTally();
  // 让“分差”的成品句子命中（模拟签字文案或变量值带出承诺类表述）：只丢那两条
  const clean = (text: string) => CLEAN(text) && !text.includes('score_gap');
  const out = renderGuardianPlan(guardianView(), ctx({ clean }), t)!;
  assert.deepEqual(out.review.score_gaps, []);
  assert.equal(out.guardian_notes.length, 2);
  assert.equal(t.dropped, 4);
  assert.equal(t.lexicon_hits, 2, '命中词表的只有两条分差；另两条是白名单外与形状不对');
  // 两张表真的都在查：禁用推断与名次 / 概率 / 承诺的样例都被挡
  assert.equal(CLEAN('孩子有点懒惰'), false);
  assert.equal(CLEAN('本月可以提高 15 分'), false);
  assert.equal(CLEAN('期中有望进入年级前 50 名'), false);
  assert.equal(CLEAN('考上目标学校的概率是 60%'), false);
  assert.equal(CLEAN('{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30'), true);
  assert.ok(forbiddenHits('这次排名上升了', noPromise).includes('rank'));
});

test('判断句与注意事项的 copy_key 必须对得上所在位置：优先知识点不能借别的模板、注意事项只能用 guardian 段的模板', () => {
  const sp = studentPlan();
  sp.priority_nodes[0] = { ...sp.priority_nodes[0]!, copy_key: 'monthly_plan.review.lit' };
  const t = newTally();
  assert.deepEqual(renderStudentPlan(sp, ctx(), t)!.priority_nodes, []);
  assert.equal(t.dropped, 3);
  const v = guardianView();
  v.guardian_notes = [{ key: 'weekly_rhythm', copy_key: 'monthly_plan.student.weekly_rhythm', vars: { sessions_per_week: 5, minutes_per_session: 30 } },
                      { key: 'weekly_rhythm_support', copy_key: 'monthly_plan.review.last_month', vars: { planned_nodes: 3 } } as never];
  const t2 = newTally();
  assert.deepEqual(renderGuardianPlan(v, ctx(), t2)!.guardian_notes, [], '注意事项借学习计划或回顾的模板：不出');
  const s = guardianView();
  s.review.score_gaps = [{ ...s.review.score_gaps[0]!, copy_key: 'monthly_plan.review.lit' }];
  assert.deepEqual(renderGuardianPlan(s, ctx(), newTally())!.review.score_gaps, []);
});

test('学生视图：generated_at 用完整时间戳校验，不合格整份不可用', () => {
  const ok = renderStudentView({ month: '2026-10-01', generated_at: '2026-10-12T02:00:00.123456+00:00', student_plan: studentPlan() }, ctx(), newTally());
  assert.equal(ok!.generated_at, '2026-10-12T02:00:00.123456+00:00');
  assert.equal(ok!.student_plan.priority_nodes.length, 1);
  for (const g of ['2026-10-12Tgarbage', '2026-10-12', '2026-10-12T02:00:00', '2026-10-12 02:00:00+08', 20261012, null]) {
    assert.equal(renderStudentView({ month: '2026-10-01', generated_at: g, student_plan: studentPlan() }, ctx(), newTally()), null, String(g));
  }
});

test('成品句子超长（> 400 码位）：那一条不出、不截断', () => {
  // 只为测试直接构造模板库（绕过加载时 120 字的上限）：399 个字 + {count}；count = 2 恰好 400 码位照常出，count = 22 是 401 码位不出
  const exact: TemplateLibrary = { ...LIB, byKey: new Map([...LIB.byKey].map(([k, x]) => [k, k === 'monthly_plan.student.retests' ? { copy: `${'复'.repeat(MAX_TEXT - 1)}{count}`, vars: ['count'] } : x])) };
  const sp = studentPlan();
  sp.retests = { ...sp.retests, count: 2 };
  assert.equal([...renderStudentPlan(sp, ctx({ lib: exact }), newTally())!.retests!.text].length, MAX_TEXT);
  const sp3 = studentPlan();
  sp3.retests = { ...sp3.retests, count: 22, by_subject: [{ subject: 'aya', count: 22 }] };
  const t = newTally();
  assert.equal(renderStudentPlan(sp3, ctx({ lib: exact }), t)!.retests, null, '401 码位不出');
  assert.equal(t.lexicon_hits, 0, '超长不算命中词表');
});

test('“名次 / 概率 / 承诺”词表：BFF 端读得到三类、自检通过；禁止示例不被本类命中或允许示例被误伤时拒绝加载', () => {
  const lex = noPromiseLexicon();
  assert.deepEqual(lex.categories.map((c) => c.id), ['rank', 'probability', 'promise']);
  assert.ok(lex.allowedExamples.length >= 8);
  const text = readFileSync(new URL('../../../docs/design/sie/no-promise.v0.draft.yaml', import.meta.url), 'utf8');
  assert.throws(() => checkNoPromise(parseLexicon(text.replace('      - 这次排名上升了', '      - 这次换了座位'), 3)), /禁止示例/);
  assert.throws(() => checkNoPromise(parseLexicon(text.replace('  - 每周 5 次，每次 30 分钟', '  - 每周 5 次，保证提分'), 3)), /允许示例/);
  assert.throws(() => parseLexicon(text), /不完整/, '按禁用推断词表的十类下限解析会拒绝');
});

test('生成接口客户端：caller = bff 带 actor = 家长账号；200 且月份一致才算 ready；只有 404 CONTENT_VERSION_NOT_FOUND 是 not_available', async () => {
  const S = '9a540000-0000-4000-8000-0000000000aa';
  const G = '9a540000-0000-4000-8000-0000000000bb';
  const seen: { url: string; init: RequestInit }[] = [];
  const reply = (status: number, body: unknown) => (async (url: string, init: RequestInit) => {
    seen.push({ url: String(url), init });
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const ok = { status: 'generated', plan_id: C1, month: '2026-10', generated_at: '2026-10-12 10:00:00+08', inputs_digest: 'a'.repeat(64) };
  const mk = (f: typeof fetch) => httpMonthlyPlanClient({ baseUrl: 'http://127.0.0.1:9/', token: 't'.repeat(40), fetchImpl: f });
  assert.equal(await mk(reply(200, ok)).generate(S, '2026-10', G), 'ready');
  assert.equal(seen[0]!.url, 'http://127.0.0.1:9/internal/v1/monthly-plan/generate');
  assert.equal((seen[0]!.init.headers as Record<string, string>)['authorization'], `Bearer ${'t'.repeat(40)}`);
  assert.deepEqual(JSON.parse(String(seen[0]!.init.body)), { student_id: S, month: '2026-10', actor: { role: 'guardian', actor_ref: G } });
  assert.equal(seen[0]!.init.redirect, 'error');
  assert.equal(await mk(reply(200, { ...ok, status: 'exists' })).generate(S, '2026-10', G), 'ready');
  assert.equal(await mk(reply(200, { ...ok, month: '2026-09' })).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(200, { ...ok, status: 'queued' })).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(404, { code: 'CONTENT_VERSION_NOT_FOUND', status: 404 })).generate(S, '2026-10', G), 'not_available');
  assert.equal(await mk(reply(404, { detail: 'Not Found' })).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(409, { code: 'MONTH_NOT_CURRENT' })).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(409, { code: 'PLAN_REJECTED' })).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(500, 'oops')).generate(S, '2026-10', G), 'unavailable');
  assert.equal(await mk((async () => { throw new Error('down'); }) as unknown as typeof fetch).generate(S, '2026-10', G), 'unavailable');
  const before = seen.length;
  assert.equal(await mk(reply(200, ok)).generate('not-a-uuid', '2026-10', G), 'unavailable');
  assert.equal(await mk(reply(200, ok)).generate(S, '2026-13', G), 'unavailable');
  assert.equal(seen.length, before, '参数不合格时不发请求');
});
