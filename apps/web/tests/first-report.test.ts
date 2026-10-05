// 首份报告开发演示页（SIE-02b 第二步 PR 2，开关 report）的单测与静态检查：
//   - 前端再断言（src/trust/report-view.ts）：受众对得上；每句带合法 claim_id 与非空文字；假设才标“初步判断”；置信度分档只在家长版；
//     BFF 转来的报告里出现信封外层的键（confidence、evidence_ids、source_version、model_version）整份不显示；report 为 null 是“还没有报告”；
//   - 待验证判断：查询参数只收小写 UUID、去重、至多 5 条；单条解释必须受众对得上、属于所问判断、待验证、虚线、不带文字，
//     学生受众不带置信度分档；
//   - 开关：默认关；关时 BFF 客户端不发请求；演示页组件先过开关闸门（读会话之前），页面文件先过本机与环境关卡；
//   - 渲染：每句一个 data-claim-id，句子文字只来自 BFF；虚线只显示占位标签、不显示结论文字；置信度只在家长版；文案全是占位。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { ALL_TRUST_COPY, FIRST_REPORT, FIRST_REPORT_SECTION } from '../src/shell/copy.ts';
import { trustFeatures } from '../src/shell/features.ts';
import { MAX_VERIFY, parseVerifyIds, pickFirstReport, pickVerify } from '../src/trust/report-view.ts';

const WEB = new URL('..', import.meta.url).pathname;
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

const S = '5c000000-0000-4000-8000-0000000000c1';
const C1 = '5c000000-0000-4000-8000-000000000201';
const C2 = '5c000000-0000-4000-8000-000000000202';
const C4 = '5c000000-0000-4000-8000-000000000204';

const sentence = (id: string, status: 'hypothesis' | 'established', guardian: boolean, over: Record<string, unknown> = {}) => ({
  claim_id: id, template_key: 'syn.report.node_mastery', template_version: 1, status, display: 'solid', text: `（合成）模板句 ${id.slice(-3)}`,
  ...(status === 'hypothesis' ? { label: 'preliminary' } : {}), ...(guardian ? { confidence_band: 'moderate' } : {}), ...over,
});
const body = (audience: 'student' | 'guardian', over: Record<string, unknown> = {}, sentences?: unknown[]) => ({
  student_id: S,
  report: {
    format: 'sie-first-report/0.1', audience, policy_version: 'syn-0.1.0', notice: '【占位·待人审】提示语', title: '【占位·待人审】标题',
    sections: [{ key: 'node_mastery', connective: '【占位衔接·甲】',
                 sentences: sentences ?? [sentence(C1, 'established', audience === 'guardian'), sentence(C2, 'hypothesis', audience === 'guardian')] }],
    claim_ids: [C1, C2], ...over,
  },
});

test('首份报告：合格的学生版与家长版——假设才标“初步判断”，置信度只在家长版；report 为 null 是“还没有报告”', () => {
  const st = pickFirstReport(body('student'), 'student');
  assert.ok(st && st !== 'none');
  assert.equal(st.notice, '【占位·待人审】提示语');
  assert.deepEqual(st.sections[0]!.sentences.map((s) => [s.claim_id, s.preliminary, s.confidence_band]), [[C1, false, null], [C2, true, null]]);
  const g = pickFirstReport(body('guardian'), 'guardian');
  assert.ok(g && g !== 'none');
  assert.deepEqual(g.sections[0]!.sentences.map((s) => s.confidence_band), ['moderate', 'moderate']);
  assert.equal(pickFirstReport({ student_id: S, report: null }, 'student'), 'none');
});

test('首份报告：受众对不上、带信封外层键、句子不合（无 claim_id、空文字、假设没标、学生版带置信度、家长版缺置信度、虚线）——整份不显示', () => {
  const bad: [string, unknown, 'student' | 'guardian'][] = [
    ['不是对象', null, 'student'],
    ['没有 report 键', { student_id: S }, 'student'],
    ['受众对不上', body('guardian'), 'student'],
    ['报告带 confidence', body('student', { confidence: 0.4 }), 'student'],
    ['报告带 evidence_ids', body('student', { evidence_ids: [C4] }), 'student'],
    ['报告带 source_version', body('student', { source_version: 'algo:x@1' }), 'student'],
    ['报告带 model_version', body('student', { model_version: 'algo:x@1' }), 'student'],
    ['格式不对', body('student', { format: 'x' }), 'student'],
    ['提示语为空', body('student', { notice: ' ' }), 'student'],
    ['没有节', body('student', { sections: [] }), 'student'],
    ['节键不认识', body('student', { sections: [{ key: 'talent', connective: '甲', sentences: [sentence(C1, 'established', false)] }] }), 'student'],
    ['句子没有 claim_id', body('student', {}, [{ ...sentence(C1, 'established', false), claim_id: undefined }]), 'student'],
    ['句子 claim_id 大写', body('student', {}, [sentence(C1.toUpperCase(), 'established', false)]), 'student'],
    ['句子文字为空', body('student', {}, [sentence(C1, 'established', false, { text: '' })]), 'student'],
    ['假设没标初步判断', body('student', {}, [sentence(C1, 'hypothesis', false, { label: undefined })]), 'student'],
    ['成立标了初步判断', body('student', {}, [sentence(C1, 'established', false, { label: 'preliminary' })]), 'student'],
    ['学生版带置信度', body('student', {}, [sentence(C1, 'established', true)]), 'student'],
    ['家长版缺置信度', body('guardian', {}, [sentence(C1, 'established', false)]), 'guardian'],
    ['虚线混进报告', body('student', {}, [sentence(C1, 'established', false, { display: 'dashed' })]), 'student'],
    ['待验证混进报告', body('student', {}, [sentence(C1, 'established', false, { status: 'to_verify' })]), 'student'],
    ['句子带 evidence_ids', body('student', {}, [sentence(C1, 'established', false, { evidence_ids: [C4] })]), 'student'],
    ['同一判断两句', body('student', {}, [sentence(C1, 'established', false), sentence(C1, 'established', false)]), 'student'],
  ];
  for (const [what, raw, aud] of bad) assert.equal(pickFirstReport(raw, aud), null, what);
});

test('待验证：查询参数只收小写 UUID、去重、至多 5 条；单条解释必须属于所问判断、待验证、虚线', () => {
  assert.deepEqual(parseVerifyIds(`${C4},${C4}, ${C1},not-a-uuid,${C2.toUpperCase()}`), [C4, C1]);
  assert.deepEqual(parseVerifyIds([C4, C1]), [C4, C1]);
  assert.deepEqual(parseVerifyIds(undefined), []);
  const many = Array.from({ length: 9 }, (_, i) => `5c000000-0000-4000-8000-00000000030${i}`);
  assert.equal(parseVerifyIds(many.join(',')).length, MAX_VERIFY);
  // BFF 按受众与状态裁剪后的形状：待验证不带文字；家长带置信度分档（顶层与陈述内），学生两处都不带
  const ex = (over: Record<string, unknown> = {}) => ({
    audience: 'guardian',
    statement: { claim_id: C4, template_key: 'syn.report.node_mastery', template_version: 1, status: 'to_verify', display: 'dashed',
                 confidence_band: 'moderate', ...over },
    evidence_chain: [], confidence_band: 'moderate',
  });
  const exStudent = (over: Record<string, unknown> = {}) => ({
    audience: 'student',
    statement: { claim_id: C4, template_key: 'syn.report.node_mastery', template_version: 1, status: 'to_verify', display: 'dashed', ...over },
    evidence_chain: [],
  });
  assert.deepEqual(pickVerify(ex(), C4, 'guardian'), { claim_id: C4 });
  assert.deepEqual(pickVerify(exStudent(), C4, 'student'), { claim_id: C4 });
  assert.equal(pickVerify(ex(), C1, 'guardian'), null, '不是所问的判断');
  assert.equal(pickVerify(ex({ status: 'established', display: 'solid', text: '（合成）模板句' }), C4, 'guardian'), null, '实线不进待验证区');
  assert.equal(pickVerify(ex({ display: 'solid' }), C4, 'guardian'), null, '待验证画实线');
  assert.equal(pickVerify(ex({ text: '（合成）模板句' }), C4, 'guardian'), null, '待验证带了文字整条不显示');
  assert.equal(pickVerify(ex(), C4, 'student'), null, '受众对不上');
  assert.equal(pickVerify(exStudent({ confidence_band: 'moderate' }), C4, 'student'), null, '学生受众陈述内带置信度');
  assert.equal(pickVerify({ ...exStudent(), confidence_band: 'moderate' }, C4, 'student'), null, '学生受众顶层带置信度');
  assert.equal(pickVerify({ ...ex(), confidence_band: undefined }, C4, 'guardian'), null, '家长受众缺顶层置信度');
  assert.equal(pickVerify(null, C4, 'student'), null);
});

test('开关 report：默认关；只在 dev / test 可开；关时 BFF 客户端不发请求', async () => {
  assert.equal(trustFeatures({ STARBUDDY_ENV: 'test' }).has('report'), false);
  assert.equal(trustFeatures({ STARBUDDY_ENV: 'test', STARBUDDY_TRUST_UI: 'report' }).has('report'), true);
  for (const env of ['staging', 'pilot', 'production']) assert.equal(trustFeatures({ STARBUDDY_ENV: env, STARBUDDY_TRUST_UI: 'report' }).has('report'), false);
  const saved = { env: process.env['STARBUDDY_ENV'], ui: process.env['STARBUDDY_TRUST_UI'], fetch: globalThis.fetch };
  const calls: string[] = [];
  globalThis.fetch = (async (u: string | URL) => { calls.push(String(u)); return new Response('{"code":"UNAVAILABLE"}', { status: 503 }); }) as typeof fetch;
  try {
    const api = await import('../src/lib/trust-api.ts');
    const s = { token: 'a'.repeat(64), csrf: 'b'.repeat(64) };
    process.env['STARBUDDY_ENV'] = 'test';
    process.env['STARBUDDY_TRUST_UI'] = 'why,dispute,reconcile,weekly,data_rights';  // 其余开关都开，report 关
    assert.deepEqual(await api.fetchFirstReport(s, S), { ok: false, status: 404, code: 'NOT_FOUND' });
    assert.deepEqual(await api.fetchReportExplain(s, S, C4), { ok: false, status: 404, code: 'NOT_FOUND' });
    assert.deepEqual(calls, []);
    process.env['STARBUDDY_TRUST_UI'] = 'report';
    assert.deepEqual(await api.fetchFirstReport(s, S), { ok: false, status: 503, code: 'UNAVAILABLE' });
    await api.fetchReportExplain(s, S, C4);
    assert.deepEqual(calls.map((u) => new URL(u).pathname), [`/bff/v0/students/${S}/first-report`, `/bff/v0/students/${S}/claims/${C4}/explain`]);
  } finally {
    globalThis.fetch = saved.fetch;
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.ui === undefined) delete process.env['STARBUDDY_TRUST_UI']; else process.env['STARBUDDY_TRUST_UI'] = saved.ui;
  }
});

test('演示页：页面文件先过本机与环境关卡；组件在读会话之前过开关闸门；只认学生本人或有效绑定家长；只在开发构建里（page.dev.tsx）', () => {
  const page = code(read('src/app/dev/first-report/[studentId]/page.dev.tsx'));
  assert.ok(page.indexOf('devRuntimeAllowed(') < page.indexOf('<FirstReportDemo'));
  const demo = code(read('src/trust/FirstReportDemo.tsx'));
  const gate = demo.indexOf("requireTrust('report')");
  assert.ok(gate > 0 && gate < demo.indexOf('await pageSession()'), '开关闸门要在读会话之前');
  assert.ok(demo.indexOf('if (!audience) notFound();') < demo.indexOf('await fetchFirstReport('), '不是本人或绑定家长：不发请求');
  assert.match(demo, /ps\.me\.student_id === studentId \? 'student' : null/);
  assert.match(demo, /ps\.me\.children\.some\(\(c\) => c\.student_id === studentId\) \? 'guardian' : null/);
});

test('演示页渲染：每句一个 data-claim-id，句子文字只来自 BFF；虚线只显示占位标签；置信度只在家长版；文案全是占位且已并入信任界面文案', () => {
  const demo = code(read('src/trust/FirstReportDemo.tsx'));
  assert.match(demo, /<li key=\{s\.claim_id\} data-claim-id=\{s\.claim_id\} data-status=\{s\.status\}>\n\s+<span>\{s\.text\}<\/span>/);
  assert.match(demo, /data-claim-id=\{d\.claim_id\} data-display="dashed">\{FIRST_REPORT\.verifyNote\}<\/li>/);
  assert.ok(!/d\.text|statement\.text/.test(demo), '虚线不显示结论文字');
  assert.match(demo, /audience === 'guardian' && s\.confidence_band\n\s+\? <>\{' '\}<span className="muted" data-confidence=\{s\.confidence_band\}>\{CONFIDENCE_BAND\[s\.confidence_band\]\}/);
  assert.deepEqual(demo.match(/[一-鿿]+/g) ?? [], []);
  for (const s of [...Object.values(FIRST_REPORT), ...Object.values(FIRST_REPORT_SECTION)]) {
    assert.match(s, /^\{\{TODO_COPY:report\.[a-z0-9_.]+\}\}/, s);
    assert.ok(ALL_TRUST_COPY.includes(s), s);
  }
});
