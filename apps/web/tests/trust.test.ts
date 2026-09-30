// 冲刺 5-H 信任界面（“为什么”“我不同意”、考试录入与对账卡、家长数据权利）的单测与静态检查：
//   - 前端再断言（src/trust/view.ts）：陈述属于所问判断、待验证只能虚线、证据链只挑四个字段（不带对话原文）；对账卡无 claim_id 的句子丢弃、
//     契约外的方向或调整整张卡不显示、所依据的预测对不上整张卡不显示；异议类型按受众；表单解析；删除申请状态、回执与冷静期倒计时；
//   - 开关：关时客户端不发任何请求；每个页面组件先过开关闸门；下载路由先判断开关；
//   - 文案：全部是 {{TODO_COPY:…}} 占位；编码映射的占位不附提示、键名与文案不含编码原文（one_off_slip 等不上页面）；不出现禁用词；
//   - 渲染 data-claim-id 的只有登记的几个组件，句子文字只从判断来。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import {
  ADJUSTMENT, ALL_SHELL_COPY, ALL_TRUST_COPY, DATA_RIGHTS, DEVIATION_DIRECTION, DISPUTE_KIND, DISPUTE_RESULT, DISPUTE_STATUS, ERASURE_SCOPE,
  ERASURE_STATUS, EVIDENCE_LEVEL, EVIDENCE_SOURCE, WHY_STATUS,
} from '../src/shell/copy.ts';
import {
  ADJUSTMENTS, coolingLeft, DEVIATION_DIRECTIONS, DISPUTE_KINDS, disputeKindsFor, IDEMPOTENCY_KEY, newIdempotencyKey, NEXT_STEPS, parseExamPlan,
  parseScore, pickDisputes, pickErasures, pickPrediction, pickReconciliation, pickWhy, defaultFullScore,
} from '../src/trust/view.ts';

const WEB = new URL('..', import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? files(p) : [p];
});
const src = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
const code = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const read = (p: string) => readFileSync(join(WEB, p), 'utf8');

const C1 = '00000000-0000-4000-8000-00000000f001';
const C2 = '00000000-0000-4000-8000-00000000f002';
const P1 = '00000000-0000-4000-8000-00000000b001';
const form = (o: Record<string, string>) => ({ get: (k: string) => (k in o ? o[k] : null) });

// ---------------------------------------------------------------- “为什么”

const explain = (over: Record<string, unknown> = {}) => ({
  statement: { claim_id: C1, template_key: 'syn.math.mastery', template_version: 1, status: 'hypothesis', display: 'solid',
               text: '（合成）模板句', confidence_band: 'moderate' },
  evidence_chain: [
    { evidence_id: '00000000-0000-4000-8000-00000000e101', occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective', evidence_level: 'A',
      node_id: 'MATH.FUNC.QUAD', utterance: '（合成）逐句对话原文', response_text: '（合成）作答原文' },
    { occurred_at: 'not-a-date', source_type: 'objective' },
    { occurred_at: '2026-10-10T08:00:00Z', source_type: 'chat_transcript' },
    { occurred_at: '2026-10-11T08:00:00Z', source_type: 'self', node_id: 'lower.case' },
  ],
  source_version: 'algo:sie-explain@0.1.0',
  ...over,
});

test('“为什么”：陈述属于所问的判断；证据链只挑时间、种类、等级、节点四个字段，不带对话与作答原文', () => {
  const v = pickWhy(explain(), C1)!;
  assert.equal(v.statement.text, '（合成）模板句');
  assert.equal(v.evidence.length, 2);
  for (const e of v.evidence) assert.deepEqual(Object.keys(e).sort(), ['evidence_level', 'node_id', 'occurred_at', 'source_type']);
  assert.ok(!JSON.stringify(v).includes('原文'));
  assert.equal(v.evidence[1]!.node_id, null);
  assert.equal(v.source_version, 'algo:sie-explain@0.1.0');
  assert.equal(pickWhy(explain(), C2), null, '别的判断的陈述不显示');
  assert.equal(pickWhy(explain({ statement: { ...explain().statement, claim_id: C2 } }), C1), null);
});

test('“为什么”：待验证只能虚线、成立与假设只能实线；缺文字、置信度不认识都不显示', () => {
  const st = explain().statement;
  assert.equal(pickWhy(explain({ statement: { ...st, status: 'to_verify', display: 'solid' } }), C1), null);
  assert.equal(pickWhy(explain({ statement: { ...st, status: 'established', display: 'dashed' } }), C1), null);
  assert.equal(pickWhy(explain({ statement: { ...st, status: 'to_verify', display: 'dashed' } }), C1)!.statement.display, 'dashed');
  assert.equal(pickWhy(explain({ statement: { ...st, status: 'refuted' } }), C1), null);
  assert.equal(pickWhy(explain({ statement: { ...st, text: '  ' } }), C1), null);
  assert.equal(pickWhy(explain({ statement: { ...st, confidence_band: 'high' } }), C1), null);
  assert.equal(pickWhy(null, C1), null);
});

// ---------------------------------------------------------------- 异议

test('“我不同意”：学生三条路，家长没有“那次只是失误”；幂等键合格式；下一步只认三个编码', () => {
  assert.deepEqual(disputeKindsFor('student'), ['i_know', 'one_off_slip', 'data_error']);
  assert.deepEqual(disputeKindsFor('guardian'), ['i_know', 'data_error']);
  assert.match(newIdempotencyKey('4f0c1c5e-7e0b-4c8e-9a52-5d0f7a1b2c3d'), IDEMPOTENCY_KEY);
  assert.deepEqual([...NEXT_STEPS], ['verification_scheduled', 'observe_followups', 'open_fact_for_correction']);
  for (const n of NEXT_STEPS) assert.ok(DISPUTE_RESULT[n]);
});

test('异议列表：只挑编码列；不认识的类型或状态整行丢弃；结论码不带出来', () => {
  const row = { dispute_id: P1, claim_id: C1, kind: 'one_off_slip', status: 'open', result_code: 'x', created_at: '2026-10-01T00:00:00Z', resolved_at: null };
  const out = pickDisputes({ student_id: C2, disputes: [row, { ...row, kind: 'careless' }, { ...row, status: 'weird' }] })!;
  assert.equal(out.length, 1);
  assert.ok(!('result_code' in out[0]!));
  assert.equal(pickDisputes({}), null);
});

// ---------------------------------------------------------------- 考试、预测与对账卡

const prediction = { prediction_id: P1, claim_id: C2, low: 70, high: 85, interval_level: 0.8, frozen_at: '2026-10-20T16:00:00Z', frozen: true };
const card = (over: Record<string, unknown> = {}) => ({
  reconciliation: {
    prediction_id: P1, actual_score: 62, within_interval: false, deviation_nodes: ['MATH.FUNC.QUAD', 'bad node'],
    deviation_directions: ['diagnosis_optimistic'], adjustments: ['claims_to_verify'],
    statements: [
      { claim_id: C1, text: '（合成）对账句', confidence_band: 'strong' },
      { text: '（合成）没有判断 ID 的句子', confidence_band: 'strong' },
      { claim_id: 'not-a-uuid', text: '（合成）坏 ID', confidence_band: 'strong' },
      { claim_id: C1, text: '', confidence_band: 'strong' },
    ],
    ...over,
  },
});

test('预测：区间、水平 0.8、冻结标记都要合格式，否则不显示', () => {
  assert.deepEqual(pickPrediction({ prediction }), prediction);
  assert.equal(pickPrediction({ prediction: null }), null);
  assert.equal(pickPrediction({ prediction: { ...prediction, claim_id: undefined } }), null);
  assert.equal(pickPrediction({ prediction: { ...prediction, low: 90 } }), null);
  assert.equal(pickPrediction({ prediction: { ...prediction, interval_level: 0.9 } }), null);
});

test('对账卡：无 claim_id 的句子不渲染（计数）；偏差方向只有四个编码之一；契约外编码整卡不显示；预测对不上整卡不显示', () => {
  const p = pickPrediction({ prediction })!;
  const r = pickReconciliation(card(), p)!;
  assert.deepEqual(r.card.statements.map((s) => s.claim_id), [C1]);
  assert.equal(r.dropped, 3);
  assert.deepEqual(r.card.deviation_nodes, ['MATH.FUNC.QUAD']);
  assert.equal(pickReconciliation(card({ deviation_directions: ['careless'] }), p), null);
  assert.equal(pickReconciliation(card({ deviation_directions: ['time_shortage', 'time_shortage'] }), p), null);
  assert.equal(pickReconciliation(card({ adjustments: ['rewrote_claim'] }), p), null);
  assert.equal(pickReconciliation(card({ prediction_id: C1 }), p), null);
  assert.equal(pickReconciliation(card(), null), null, '没有带 claim_id 的预测就没有对账卡');
  assert.deepEqual([...DEVIATION_DIRECTIONS].sort(), Object.keys(DEVIATION_DIRECTION).sort());
  assert.deepEqual([...ADJUSTMENTS].sort(), Object.keys(ADJUSTMENT).sort());
});

test('考试表单：学科只有数学、语文、道法（英语不收）；满分 1–300 整数；范围节点 1–64 个、合格式、不重复；成绩两位小数以内', () => {
  const ok = { subject: 'aya', exam_type: 'midterm', exam_date: '2026-11-10', full_score: '120', scope_node_ids: 'MATH.FUNC.QUAD, MATH.GEO.TRI\nMATH.ALG.EQ' };
  assert.deepEqual(parseExamPlan(form(ok)), { subject: 'aya', exam_type: 'midterm', exam_date: '2026-11-10', full_score: 120,
                                               scope_node_ids: ['MATH.FUNC.QUAD', 'MATH.GEO.TRI', 'MATH.ALG.EQ'] });
  for (const bad of [{ subject: 'echo' }, { exam_type: 'mock' }, { exam_date: '2026/11/10' }, { full_score: '0' }, { full_score: '99.5' },
                     { scope_node_ids: '' }, { scope_node_ids: 'MATH.A MATH.A' }, { scope_node_ids: 'math.lower' },
                     { scope_node_ids: Array.from({ length: 65 }, (_, i) => `MATH.N${i}`).join(' ') }]) {
    assert.equal(parseExamPlan(form({ ...ok, ...bad })), null, JSON.stringify(bad));
  }
  assert.equal(parseScore('88.5'), 88.5);
  for (const bad of ['', '-1', '301', '1e2', '88.555', 'abc']) assert.equal(parseScore(bad), null, bad);
  assert.equal(defaultFullScore(9), 120);
  assert.equal(defaultFullScore(8), 100);
  assert.equal(defaultFullScore(null), 100);
});

// ---------------------------------------------------------------- 家长数据权利

test('删除申请：待确认 / 冷静期中 / 已撤回 / 已删除；回执只在执行后、只有表名与行数；冷静期倒计时按天与小时', () => {
  const base = { request_id: P1, requested_at: '2026-10-01T00:00:00Z', confirmed_at: null, cooling_off_until: null, cancelled_at: null,
                 cancel_reason: null, executed_at: null, receipt: null };
  const rows = pickErasures({ requests: [
    base,
    { ...base, request_id: C1, confirmed_at: '2026-10-01T01:00:00Z', cooling_off_until: '2026-10-08T01:00:00Z' },
    { ...base, request_id: C2, status: 'cancelled', cancel_reason: 'guardian' },
    { ...base, request_id: P1, status: 'cancelled', cancel_reason: '__proto__' },
    { ...base, status: 'executed', receipt: { tables: { 'insight.claim': 3, 'platform.attempt': 12, 'bad name!': 1, 'x.y': -1 }, scopes: ['facts', 'bogus'] } },
  ].map((r, i) => ({ status: 'pending', ...r, ...(i === 4 ? { status: 'executed' } : {}) })) })!;
  assert.deepEqual(rows.map((r) => r.state), ['awaiting_confirm', 'cooling_off', 'cancelled', 'cancelled', 'executed']);
  assert.deepEqual(rows.map((r) => r.cancel_reason), [null, null, 'guardian', null, null], '撤回原因只认契约枚举');
  assert.equal(rows[0]!.receipt, null);
  assert.deepEqual(rows[4]!.receipt, { tables: [['insight.claim', 3], ['platform.attempt', 12]], scopes: ['facts'] });
  assert.deepEqual(coolingLeft('2026-10-08T01:00:00Z', Date.parse('2026-10-01T00:00:00Z')), { days: 7, hours: 1 });
  assert.deepEqual(coolingLeft('2026-10-01T00:00:00Z', Date.parse('2026-10-02T00:00:00Z')), { days: 0, hours: 0 });
});

// ---------------------------------------------------------------- 开关：关时不发任何请求

test('开关关时 BFF 客户端不发任何请求（按 404 返回）；打开后才请求', async () => {
  const saved = { env: process.env['STARBUDDY_ENV'], ui: process.env['STARBUDDY_TRUST_UI'], fetch: globalThis.fetch };
  const calls: string[] = [];
  globalThis.fetch = (async (u: string | URL) => { calls.push(String(u)); return new Response('{"code":"UNAVAILABLE"}', { status: 503 }); }) as typeof fetch;
  try {
    const api = await import('../src/lib/trust-api.ts');
    const s = { token: 'a'.repeat(64), csrf: 'b'.repeat(64) };
    process.env['STARBUDDY_ENV'] = 'test';
    delete process.env['STARBUDDY_TRUST_UI'];
    for (const r of [await api.fetchWhy(s, C2, C1), await api.fetchDisputes(s, C2), await api.fetchPrediction(s, C2, P1),
                     await api.fetchReconciliation(s, C2, P1), await api.fetchErasures(s, C2)]) {
      assert.deepEqual(r, { ok: false, status: 404, code: 'NOT_FOUND' });
    }
    assert.deepEqual(await api.downloadExport(s, C2, P1, `dl_${'0'.repeat(64)}`), { ok: false, status: 404 });
    process.env['STARBUDDY_ENV'] = 'staging';
    process.env['STARBUDDY_TRUST_UI'] = 'why,dispute,reconcile,data_rights';
    assert.deepEqual(await api.fetchDisputes(s, C2), { ok: false, status: 404, code: 'NOT_FOUND' }, 'staging 以上开关一律关');
    assert.deepEqual(calls, []);
    process.env['STARBUDDY_ENV'] = 'test';
    assert.deepEqual(await api.fetchPrediction(s, C2, P1), { ok: false, status: 503, code: 'UNAVAILABLE' });
    assert.equal(calls.length, 1);
    assert.match(calls[0]!, new RegExp(`/bff/v0/students/${C2}/exams/${P1}/prediction$`));
  } finally {
    globalThis.fetch = saved.fetch;
    if (saved.env === undefined) delete process.env['STARBUDDY_ENV']; else process.env['STARBUDDY_ENV'] = saved.env;
    if (saved.ui === undefined) delete process.env['STARBUDDY_TRUST_UI']; else process.env['STARBUDDY_TRUST_UI'] = saved.ui;
  }
});

test('每个信任界面页面先过开关闸门（读会话之前）；下载路由先判断开关；BFF 调用只在 trust-api 里、每个都经开关', () => {
  const pages: [string, string][] = [
    ['src/trust/WhyPage.tsx', 'why'], ['src/trust/DisputesPage.tsx', 'dispute'], ['src/trust/DataRightsPage.tsx', 'data_rights'],
  ];
  for (const [p, f] of pages) {
    const body = code(read(p));
    const gate = body.indexOf(`requireTrust('${f}')`);
    assert.ok(gate > 0, p);
    assert.ok(gate < body.indexOf('await require'), `${p}：开关闸门要在读会话之前`);
  }
  const exams = code(read('src/trust/ExamPages.tsx'));
  assert.equal(exams.match(/requireTrust\('reconcile'\);\n\s+(if \(!UUID\.test\(examId\)\) notFound\(\);\n\s+)?const \{ ps/g)?.length, 2);
  const route = code(read('src/app/family/[studentId]/data/download/route.ts'));
  assert.ok(route.indexOf("trustEnabled('data_rights')") < route.indexOf('await actionSession'));
  const api = code(read('src/lib/trust-api.ts'));
  assert.match(api, /async function call<T>\(feature: TrustFeature[^]*?\{\n\s+if \(!trustEnabled\(feature\)\) return OFF;/);
  assert.match(api, /downloadExport[^]*?\{\n\s+if \(!trustEnabled\('data_rights'\)\)/);
  assert.equal(api.match(/fetch\(/g)?.length, 2, '只有 call 与 downloadExport 两处请求');
  // 其余源码不直连信任界面的 BFF 路由（只经 trust-api）
  for (const f of src.filter((x) => /\.(tsx?)$/.test(x.path) && x.path !== 'src/lib/trust-api.ts')) {
    assert.ok(!/\/(claims\/[^'"`]*\/(explain|disputes)|exports|erasure)\b|\/exams\/[^'"`]*\/(score|prediction|reconciliation)/.test(code(f.text)), f.path);
  }
  // 路由页面只挂组件（组件里过闸门）
  for (const p of ['s/[studentId]/why/[claimId]', 'family/[studentId]/why/[claimId]', 's/[studentId]/disputes', 'family/[studentId]/disputes',
                   's/[studentId]/exams', 'family/[studentId]/exams', 's/[studentId]/exams/[examId]', 'family/[studentId]/exams/[examId]',
                   'family/[studentId]/data']) {
    assert.match(read(`src/app/${p}/page.tsx`), /return <(WhyPage|DisputesPage|ExamPlanPage|ExamPage|DataRightsPage) /, p);
  }
  // server action 先判断开关
  const actions = code(read('src/trust/actions.ts'));
  assert.match(actions, /async function begin\(feature: TrustFeature[^]*?\{\n\s+if \(!trustEnabled\(feature\)\) return \{ code: 'NOT_FOUND' \};/);
  assert.equal(actions.match(/await begin\('(dispute|reconcile|data_rights)', f\)/g)?.length, 7);
});

test('开关关时入口组件返回 null；features.ts 注释写明 BFF 不注册对应路由', () => {
  const links = code(read('src/trust/links.tsx'));
  assert.match(links, /export function ClaimLinks[^]*?\{\n\s+if \(!trustEnabled\('why'\)\) return null;/);
  assert.match(links, /export function TrustNav[^]*?if \(!anyTrustEnabled\(features\)\) return null;/);
  assert.match(read('src/shell/features.ts'), /开关关时 BFF 不注册对应路由/);
});

// ---------------------------------------------------------------- 文案

test('信任界面文案全部是 {{TODO_COPY:…}} 占位，已并入壳文案（禁语、禁用推断、比较营销词表的扫描在 shell.test.ts）', () => {
  assert.ok(ALL_TRUST_COPY.length > 80);
  for (const s of ALL_TRUST_COPY) assert.match(s, /^\{\{TODO_COPY(:[a-z0-9_.]+)?\}\}/, s);
  for (const s of ALL_TRUST_COPY) assert.ok(ALL_SHELL_COPY.includes(s), s);
});

test('编码只映射到占位键：异议类型、下一步、状态、偏差方向、调整、证据种类的文案没有提示文字，键与文案都不含编码原文；不出现禁用词', () => {
  const pure = [DISPUTE_KIND, DEVIATION_DIRECTION, ADJUSTMENT, DISPUTE_STATUS, EVIDENCE_SOURCE, EVIDENCE_LEVEL, ERASURE_SCOPE, WHY_STATUS, ERASURE_STATUS,
                Object.fromEntries(NEXT_STEPS.map((n) => [n, DISPUTE_RESULT[n]!]))];
  for (const m of pure) for (const v of Object.values(m)) assert.match(v, /^\{\{TODO_COPY:[a-z_.]+\.\d\}\}$/, v);
  const codes = [...DISPUTE_KINDS, ...DEVIATION_DIRECTIONS, ...ADJUSTMENTS.filter((a) => a !== 'none'), ...NEXT_STEPS];
  for (const s of ALL_TRUST_COPY) for (const c of codes) assert.ok(!s.includes(c), `${s} 含编码 ${c}`);
  for (const s of ALL_TRUST_COPY) assert.ok(!/粗心|马虎|失误|不认真|偏乐观|算错|不够|较差|太差|薄弱|弱项/.test(s), s);
  // 页面源码里也不直接写这些编码的显示（只作映射键、表单值与测试标记）
  for (const f of src.filter((x) => x.path.startsWith('src/trust/') && x.path.endsWith('.tsx'))) {
    assert.ok(!/>\s*\{?['"`]?(one_off_slip|calculation_slip)/.test(code(f.text)), f.path);
  }
});

test('信任界面组件里没有中文文案；data-claim-id 只在登记的组件里渲染，句子文字只来自判断', () => {
  const tsx = src.filter((f) => /^src\/(trust|app\/s|app\/family)\/.*\.tsx$/.test(f.path));
  for (const f of tsx) assert.deepEqual(code(f.text).match(/[一-鿿]+/g) ?? [], [], f.path);
  const renderers = src.filter((f) => /data-claim-id/.test(code(f.text))).map((f) => f.path).sort();
  assert.deepEqual(renderers, ['src/shell/TaskList.tsx', 'src/trust/ExamPages.tsx', 'src/trust/WhyPage.tsx']);
  assert.match(read('src/trust/WhyPage.tsx'), /<span data-claim-id=\{st\.claim_id\}>\{st\.text\}<\/span>/);
  assert.match(read('src/trust/ExamPages.tsx'), /s\.claim_id \? \(\n\s+<li key=\{i\}>\n\s+<span data-claim-id=\{s\.claim_id\}>\{s\.text\}<\/span>/);
  // 学生端不附置信度说明；家长端附（SIE 第八章）
  for (const p of ['src/trust/WhyPage.tsx', 'src/trust/ExamPages.tsx']) {
    for (const m of code(read(p)).matchAll(/CONFIDENCE_BAND\[/g)) {
      const before = code(read(p)).slice(Math.max(0, m.index! - 200), m.index);
      assert.match(before, /audience === 'guardian'/, p);
    }
  }
  for (const f of src.filter((x) => x.path.startsWith('src/trust/'))) assert.ok(!/localStorage|sessionStorage|indexedDB/.test(code(f.text)), f.path);
});

// ---------------------------------------------------------------- 审查修改（安全审查 1–6、契约审查 M3、L1、L3、L6、L7）

test('导出与删除：凭据只在客户端内存——按钮 onClick + startTransition 调 server action，不用 <form>，水合前禁用；下载 fetch 显式 same-origin', () => {
  const panels = code(read('src/trust/DataPanels.tsx'));
  assert.ok(!/<form\b|action=\{/.test(panels), '没有表单提交（没 JS 时不会整页提交、凭据不会渲染进 HTML）');
  assert.ok(!/type="hidden"|name="(confirm_token|download_ref)"/.test(panels), '凭据不进 DOM');
  assert.equal(panels.match(/startTransition\(async \(\) => \{/g)?.length, 1);
  assert.equal(panels.match(/start(Request|Confirm)\(async \(\) => \{/g)?.length, 2);
  assert.equal(panels.match(/disabled=\{!hydrated \|\| (pending|requesting)\}/g)?.length, 2);
  assert.match(panels, /disabled=\{!understood \|\| confirming\}/);
  assert.match(panels, /referrerPolicy: 'same-origin'/);
  assert.ok(!/1 小时|\d+ ?(小时|分钟)/.test(DATA_RIGHTS.exportNote), '导出说明不写具体时长');
});

test('下载路由：只认 POST（其他方法 404 + no-store）；开关之后先核 CSRF，再核会话是家长且孩子已绑定', () => {
  const route = code(read('src/app/family/[studentId]/data/download/route.ts'));
  assert.match(route, /export \{ notHere as GET, notHere as HEAD, notHere as PUT, notHere as PATCH, notHere as DELETE, notHere as OPTIONS \};/);
  assert.match(route, /const notHere = async \(\): Promise<Response> => gone\(\);/);
  assert.match(route, /const gone = \(status = 404\): Response => new Response\(null, \{ status, headers: NO_STORE \}\);/);
  const i = (s: string) => { const k = route.indexOf(s); assert.ok(k > 0, s); return k; };
  assert.ok(i("trustEnabled('data_rights')") < i('await actionSession') && i('await actionSession') < i('await pageSession()'));
  assert.ok(i("ps.me.account_kind !== 'guardian' || !ps.me.children.some((c) => c.student_id === studentId)") < i('await downloadExport'));
});

test('学生侧信任页只给本人：会话里的学生不是路径里的学生时 404', () => {
  assert.match(code(read('src/shell/guard.ts')),
    /export async function requireOwnStudent[^]*?if \(ps\.me\.account_kind !== 'student' \|\| ps\.me\.student_id !== studentId\) notFound\(\);/);
  for (const p of ['src/trust/WhyPage.tsx', 'src/trust/DisputesPage.tsx', 'src/trust/ExamPages.tsx']) {
    const t = code(read(p));
    assert.ok(!/requireStudent\(/.test(t), p);
    assert.match(t, /requireOwnStudent\(studentId\)/, p);
  }
});

test('异议出错码补 CONFLICT；偏差方向序号取 DEVIATION_DIRECTIONS；学生壳“知道了”能回到信任页', async () => {
  assert.ok(DISPUTE_RESULT['CONFLICT']);
  assert.match(read('src/trust/ExamPages.tsx'), /data-direction-index=\{DEVIATION_DIRECTIONS\.indexOf\(d\) \+ 1\}/);
  const m = /const BACK = (\/.*\/);/.exec(read('src/app/s/[studentId]/actions.ts'))!;
  const back = new Function(`return ${m[1]}`)() as RegExp;
  for (const ok of [`/s/${C2}/why/${C1}`, `/s/${C2}/exams/${P1}`, `/s/${C2}/exams`, `/s/${C2}/disputes`, `/s/${C2}/today`]) assert.ok(back.test(ok), ok);
  for (const bad of [`/s/${C2}/why`, `/s/${C2}/exams/x`, `/s/${C2}/data`, `//evil/${C1}`]) assert.ok(!back.test(bad), bad);
});
