// SIE-02b 第二步 PR 2：BFF 读洞察服务首份报告（insight.report）与单条解释（insight.explain）的单测（src/trust/report.ts）。
//   - 请求体：caller = bff、受众与 actor.role 一致、家长带 link_id、学生不带；
//   - HTTP 客户端：200 + 合格信封 → ok（只带 result 与两项记日志的外层字段）；404 → not_found；其余状态、信封不合、超时、抛错 → unavailable；
//   - 报告核对：键恰好七个、受众等于请求受众、每句按契约（假设必带“初步判断”、成立不带、家长每句带置信度、学生不带）、
//     claim_ids 恰好是各句 ID 按出现顺序、上限与长度；任何一处不合整份 null；
//   - 解释核对：陈述属于所问判断、待验证只能虚线、证据链不带 evidence_id、多键少键整份 null；
//   - 响应体上限（约 1 MB）：content-length 超限或不合直接 cancel，无 content-length 时流式累计超限即 cancel，都按 unavailable；
//   - 节键与句子模板类别对应；解释按受众裁剪（学生无置信度分档、待验证无文字）。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  explainForAudience, explainReadBody, httpInsightReadClient, MAX_READ_BYTES, pickExplain, pickReport, REPORT_LIMITS, reportBody, reportTexts,
  sectionMismatches, templateSection, type FirstReport,
} from '../src/trust/report.ts';

const S = '5c000000-0000-4000-8000-0000000000c1';
const A = '5c000000-0000-4000-8000-0000000000b1';
const G = '5c000000-0000-4000-8000-0000000000a1';
const L = '5c000000-0000-4000-8000-0000000000e1';
const C1 = '5c000000-0000-4000-8000-000000000201';
const C2 = '5c000000-0000-4000-8000-000000000202';
const C3 = '5c000000-0000-4000-8000-000000000203';
const E1 = '5c000000-0000-4000-8000-000000000301';
const OUTER = ['confidence', 'evidence_ids', 'source_version', 'model_version', 'human_review_required', 'review_reasons', 'contract_version'];

const sentence = (id: string, status: 'hypothesis' | 'established', guardian: boolean) => ({
  claim_id: id, template_key: 'syn.report.node_mastery', template_version: 1, status, display: 'solid', text: `（合成）模板句 ${id.slice(-3)}`,
  ...(status === 'hypothesis' ? { label: 'preliminary' } : {}), ...(guardian ? { confidence_band: status === 'hypothesis' ? 'few_evidence' : 'strong' } : {}),
});
const result = (audience: 'student' | 'guardian', over: Record<string, unknown> = {}) => ({
  format: 'sie-first-report/0.1', audience, policy_version: 'syn-0.1.0', notice: '【占位·待人审】提示语', title: '【占位·待人审】标题',
  sections: [
    { key: 'node_mastery', connective: '【占位衔接·甲】', sentences: [sentence(C1, 'established', audience === 'guardian'), sentence(C2, 'hypothesis', audience === 'guardian')] },
    { key: 'node_transfer', connective: '【占位衔接·乙】',
      sentences: [{ ...sentence(C3, 'established', audience === 'guardian'), template_key: 'syn.report.node_transfer' }] },
  ],
  claim_ids: [C1, C2, C3],
  ...over,
});
const envelope = (r: unknown, over: Record<string, unknown> = {}) => ({
  contract_version: '0.9.0', result: r, evidence_ids: [E1], confidence: 0.4, source_version: 'algo:sie-report@0.1.0',
  model_version: 'algo:sie-report@0.1.0', human_review_required: false, ...over,
});
const statement = (over: Record<string, unknown> = {}) => ({
  claim_id: C1, template_key: 'syn.report.node_mastery', template_version: 1, status: 'established', display: 'solid',
  text: '（合成）模板句', confidence_band: 'moderate', ...over,
});
const explainResult = (over: Record<string, unknown> = {}) => ({
  statement: statement(),
  evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T16:00:00+08:00', source_type: 'objective', evidence_level: 'A', node_id: 'MATH.SYN.R1' }],
  confidence_band: 'moderate', expires_at: '2026-11-09T00:00:00+08:00', ...over,
});
/** 深度找键：结果里任何一层都不能出现信封外层的键 */
const keysDeep = (v: unknown): string[] => (Array.isArray(v) ? v.flatMap(keysDeep)
  : v && typeof v === 'object' ? Object.entries(v).flatMap(([k, x]) => [k, ...keysDeep(x)]) : []);

test('报告：请求体 caller = bff、受众与 actor.role 一致；家长带 link_id，学生不带；解释另带 claim_id', () => {
  const st = reportBody({ studentRef: S, audience: 'student', actorRef: A }, E1) as { context: Record<string, unknown> };
  assert.deepEqual(st.context, { request_id: E1, contract_version: '0.9.0', caller: 'bff', data_class: 'L2', student_ref: S, audience: 'student',
                                 actor: { role: 'student', actor_ref: A } });
  const g = reportBody({ studentRef: S, audience: 'guardian', actorRef: G, linkId: L }, E1) as { context: Record<string, unknown> };
  assert.deepEqual(g.context['actor'], { role: 'guardian', actor_ref: G, link_id: L });
  assert.deepEqual(Object.keys(reportBody({ studentRef: S, audience: 'student', actorRef: A, linkId: L }, E1)), ['context']);
  assert.deepEqual((reportBody({ studentRef: S, audience: 'student', actorRef: A, linkId: L }, E1) as { context: { actor: unknown } }).context.actor,
                   { role: 'student', actor_ref: A }, '学生不带 link_id');
  const e = explainReadBody({ studentRef: S, audience: 'student', actorRef: A, claimId: C1 }, E1) as Record<string, unknown>;
  assert.equal(e['claim_id'], C1);
});

test('报告：HTTP 客户端 200 + 合格信封 → ok（只带 result 与两项日志字段）；404 → not_found；其余一律 unavailable', async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  let reply: () => Response = () => new Response(JSON.stringify(envelope(result('student'))), { status: 200 });
  const client = httpInsightReadClient({ baseUrl: 'http://127.0.0.1:8791/', token: 't'.repeat(40),
                                         fetchImpl: (async (url: string, init: RequestInit) => { calls.push({ url, init }); return reply(); }) as typeof fetch });
  const who = { studentRef: S, audience: 'student' as const, actorRef: A };
  const ok = await client.report(who, 1000);
  assert.equal(ok.kind, 'ok');
  assert.deepEqual(Object.keys(ok).sort(), ['envelope', 'kind', 'result']);
  if (ok.kind === 'ok') {
    assert.deepEqual(ok.envelope, { source_version: 'algo:sie-report@0.1.0', human_review_required: false });
    assert.deepEqual(ok.result, result('student'));
  }
  assert.equal(calls[0]!.url, 'http://127.0.0.1:8791/v0/insight.report');
  assert.equal((calls[0]!.init.headers as Record<string, string>)['authorization'], `Bearer ${'t'.repeat(40)}`);
  assert.equal(calls[0]!.init.redirect, 'error');
  reply = () => new Response('{}', { status: 404 });
  assert.deepEqual(await client.report(who, 1000), { kind: 'not_found' });
  assert.deepEqual(await client.explain({ ...who, claimId: C1 }, 1000), { kind: 'not_found' });
  assert.equal(calls.at(-1)!.url, 'http://127.0.0.1:8791/v0/insight.explain');
  for (const status of [400, 401, 403, 409, 429, 500, 501, 503]) {
    reply = () => new Response('{}', { status });
    assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, String(status));
  }
  // 信封不合（缺外层字段、版本不对、置信度越界）→ unavailable
  for (const over of [{ confidence: 2 }, { contract_version: '1.0.0' }, { evidence_ids: ['x'] }, { human_review_required: 'no' }]) {
    reply = () => new Response(JSON.stringify(envelope(result('student'), over)), { status: 200 });
    assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, JSON.stringify(over));
  }
  reply = () => new Response(JSON.stringify({ ...envelope(result('student')), result: undefined }), { status: 200 });
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, '没有 result');
  reply = () => new Response('not json', { status: 200 });
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' });
  reply = () => { throw new Error('network'); };
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' });
});

test('报告：合格的学生版与家长版原样通过，结果里没有任何信封外层的键', () => {
  for (const aud of ['student', 'guardian'] as const) {
    const r = pickReport(result(aud), aud);
    assert.ok(r, aud);
    assert.deepEqual(r, result(aud));
    for (const k of keysDeep(r)) assert.ok(!OUTER.includes(k), `${aud} 结果里不该有 ${k}`);
    assert.deepEqual(reportTexts(r!).length, 2 + 2 + 3);
  }
  // 学生版里只有假设句带“初步判断”，没有置信度
  const st = pickReport(result('student'), 'student')!;
  assert.deepEqual(st.sections.flatMap((s) => s.sentences).map((x) => [x.status, x.label ?? null, x.confidence_band ?? null]),
                   [['established', null, null], ['hypothesis', 'preliminary', null], ['established', null, null]]);
});

test('报告：受众对不上、多键少键、格式与策略版本、长度、节数与句数、claim_ids 顺序与重复——任何一处不合整份 null', () => {
  const bad: [string, unknown, 'student' | 'guardian'][] = [
    ['受众对不上', result('guardian'), 'student'],
    ['家长版当学生版', result('student'), 'guardian'],
    // 只有 audience 字段与请求受众不一致（句子形状本身合本次受众）：靠受众核对本身拒绝，而不是靠置信度分档碰巧拒绝
    ['学生形状但 audience 标家长', result('student', { audience: 'guardian' }), 'student'],
    ['家长形状但 audience 标学生', result('guardian', { audience: 'student' }), 'guardian'],
    ['audience 缺值', result('student', { audience: null }), 'student'],
    ['多一个键', { ...result('student'), confidence: 0.4 }, 'student'],
    ['多 evidence_ids', { ...result('student'), evidence_ids: [E1] }, 'student'],
    ['少一个键', (({ notice: _n, ...r }) => r)(result('student')), 'student'],
    ['格式不对', result('student', { format: 'sie-first-report/0.2' }), 'student'],
    ['策略版本不合', result('student', { policy_version: '../x' }), 'student'],
    ['提示语过长', result('student', { notice: '占'.repeat(REPORT_LIMITS.notice + 1) }), 'student'],
    ['标题为空', result('student', { title: '' }), 'student'],
    ['标题只有空白', result('student', { title: '   ' }), 'student'],
    ['标题过长', result('student', { title: '占'.repeat(REPORT_LIMITS.title + 1) }), 'student'],
    ['没有节', result('student', { sections: [], claim_ids: [C1] }), 'student'],
    ['节键重复', result('student', { sections: [result('student').sections[0], { ...result('student').sections[0], sentences: [sentence(C3, 'established', false)] }] }), 'student'],
    ['节键不认识', result('student', { sections: [{ ...result('student').sections[0], key: 'personality' }, result('student').sections[1]] }), 'student'],
    ['衔接过长', result('student', { sections: [{ ...result('student').sections[0], connective: '占'.repeat(REPORT_LIMITS.connective + 1) }, result('student').sections[1]] }), 'student'],
    ['节里多键', result('student', { sections: [{ ...result('student').sections[0], extra: 1 }, result('student').sections[1]] }), 'student'],
    ['claim_ids 顺序不对', result('student', { claim_ids: [C2, C1, C3] }), 'student'],
    ['claim_ids 少一个', result('student', { claim_ids: [C1, C2] }), 'student'],
    ['claim_ids 多一个', result('student', { claim_ids: [C1, C2, C3, E1] }), 'student'],
    ['claim_ids 不是数组', result('student', { claim_ids: C1 }), 'student'],
  ];
  for (const [what, v, aud] of bad) assert.equal(pickReport(v, aud), null, what);
  // 同一个判断在两节都出现
  const dup = result('student');
  (dup.sections[1]!.sentences as unknown[]) = [sentence(C1, 'established', false)];
  assert.equal(pickReport({ ...dup, claim_ids: [C1, C2, C1] }, 'student'), null, '判断重复');
  // 每节句数上限
  const many = Array.from({ length: REPORT_LIMITS.sentencesPerSection + 1 }, (_, i) =>
    sentence(`5c000000-0000-4000-8000-${String(i).padStart(12, '0')}`, 'established', false));
  assert.equal(pickReport(result('student', { sections: [{ key: 'node_mastery', connective: '甲', sentences: many }], claim_ids: many.map((s) => s.claim_id) }), 'student'),
               null, '每节超过 200 句');
  const ok200 = many.slice(0, REPORT_LIMITS.sentencesPerSection);
  assert.ok(pickReport(result('student', { sections: [{ key: 'node_mastery', connective: '甲', sentences: ok200 }], claim_ids: ok200.map((s) => s.claim_id) }), 'student'),
            '恰好 200 句可以');
});

test('报告：每句按契约——假设必带“初步判断”、成立不带；家长每句带置信度、学生不带；待验证、虚线、多键、文字为空或过长都整份 null', () => {
  const withSentence = (aud: 'student' | 'guardian', s: Record<string, unknown>): FirstReport | null =>
    pickReport(result(aud, { sections: [{ key: 'node_mastery', connective: '甲', sentences: [s] }], claim_ids: [s['claim_id']] }), aud);
  assert.ok(withSentence('student', sentence(C1, 'hypothesis', false)));
  const cases: [string, 'student' | 'guardian', Record<string, unknown>][] = [
    ['假设不带初步判断', 'student', (({ label: _l, ...s }) => s)(sentence(C1, 'hypothesis', false))],
    ['成立带初步判断', 'student', { ...sentence(C1, 'established', false), label: 'preliminary' }],
    ['标注不认识', 'student', { ...sentence(C1, 'hypothesis', false), label: 'tentative' }],
    ['学生版带置信度', 'student', sentence(C1, 'established', true)],
    ['家长版缺置信度', 'guardian', sentence(C1, 'established', false)],
    ['家长版置信度不认识', 'guardian', { ...sentence(C1, 'established', true), confidence_band: 'high' }],
    ['待验证进报告', 'student', { ...sentence(C1, 'established', false), status: 'to_verify' }],
    ['虚线', 'student', { ...sentence(C1, 'established', false), display: 'dashed' }],
    ['多键 confidence', 'student', { ...sentence(C1, 'established', false), confidence: 0.4 }],
    ['多键 evidence_ids', 'student', { ...sentence(C1, 'established', false), evidence_ids: [E1] }],
    ['文字为空', 'student', { ...sentence(C1, 'established', false), text: '' }],
    ['文字过长', 'student', { ...sentence(C1, 'established', false), text: '占'.repeat(REPORT_LIMITS.text + 1) }],
    ['没有 claim_id', 'student', (({ claim_id: _c, ...s }) => s)(sentence(C1, 'established', false))],
    ['claim_id 不是 UUID', 'student', { ...sentence(C1, 'established', false), claim_id: 'C1' }],
    ['模板键不合', 'student', { ...sentence(C1, 'established', false), template_key: 'Bad Key' }],
    ['模板版本为 0', 'student', { ...sentence(C1, 'established', false), template_version: 0 }],
  ];
  for (const [what, aud, s] of cases) assert.equal(withSentence(aud, s), null, what);
});

test('解释：陈述属于所问判断；证据链只留时间、来源、等级、节点（不带 evidence_id）；多键少键、待验证实线、证据条目不合整份 null', () => {
  const v = pickExplain(explainResult(), C1);
  assert.ok(v);
  assert.deepEqual(v.evidence_chain, [{ occurred_at: '2026-10-09T08:00:00.000Z', source_type: 'objective', evidence_level: 'A', node_id: 'MATH.SYN.R1' }]);
  assert.equal(v.expires_at, '2026-11-08T16:00:00.000Z');
  for (const k of keysDeep(v)) assert.ok(!['evidence_id', ...OUTER].includes(k), `解释里不该有 ${k}`);
  const dashed = pickExplain(explainResult({ statement: statement({ status: 'to_verify', display: 'dashed' }) }), C1);
  assert.equal(dashed?.statement.display, 'dashed');
  assert.ok(pickExplain(explainResult({ expires_at: undefined, evidence_chain: [] }), C1), '没有到期时间、证据链为空也可以');
  const bad: [string, unknown, string][] = [
    ['不是所问的判断', explainResult(), C2],
    ['待验证画实线', explainResult({ statement: statement({ status: 'to_verify' }) }), C1],
    ['成立画虚线', explainResult({ statement: statement({ display: 'dashed' }) }), C1],
    ['陈述多键', explainResult({ statement: { ...statement(), evidence_ids: [E1] } }), C1],
    ['结果多键', { ...explainResult(), confidence: 0.7 }, C1],
    ['结果多 source_version', { ...explainResult(), source_version: 'algo:x@1' }, C1],
    ['缺证据链', (({ evidence_chain: _e, ...r }) => r)(explainResult()), C1],
    ['置信度不认识', explainResult({ confidence_band: 'high' }), C1],
    ['到期时间不合', explainResult({ expires_at: 'tomorrow' }), C1],
    ['证据条目缺 evidence_id', explainResult({ evidence_chain: [{ occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective' }] }), C1],
    ['证据条目多键（原文）', explainResult({ evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective', utterance: '（合成）原文' }] }), C1],
    ['证据来源不认识', explainResult({ evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'chat' }] }), C1],
    ['证据等级 C', explainResult({ evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective', evidence_level: 'C' }] }), C1],
    ['节点编号不合', explainResult({ evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective', node_id: 'lower' }] }), C1],
    ['证据链超过 200 条', explainResult({ evidence_chain: Array.from({ length: 201 }, () => ({ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective' })) }), C1],
  ];
  for (const [what, r, id] of bad) assert.equal(pickExplain(r, id), null, what);
});

test('报告：响应体上限——content-length 超限或不合立即 cancel；没有 content-length 时流式累计超限即 cancel；都按 unavailable', async () => {
  assert.equal(MAX_READ_BYTES, 1_048_576);
  const who = { studentRef: S, audience: 'student' as const, actorRef: A };
  const good = JSON.stringify(envelope(result('student')));
  let cancelled = 0;
  let pulled = 0;
  /** 流式响应体：先给合格信封的前缀，再无限给填充块（模拟洞察服务不停地吐） */
  const endless = () => new ReadableStream<Uint8Array>({
    pull(c) { pulled += 1; c.enqueue(new Uint8Array(64 * 1024).fill(0x20)); },
    cancel() { cancelled += 1; },
  });
  const finite = (text: string) => new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(new TextEncoder().encode(text)); c.close(); },
    cancel() { cancelled += 1; },
  });
  let reply: () => Response = () => new Response(good, { status: 200 });
  const client = httpInsightReadClient({ baseUrl: 'http://127.0.0.1:8791', token: 't'.repeat(40), fetchImpl: (async () => reply()) as typeof fetch });
  assert.equal((await client.report(who, 1000)).kind, 'ok', '对照：正常大小');
  // content-length 声明超限：不读、直接 cancel
  reply = () => new Response(finite(good), { status: 200, headers: { 'content-length': String(MAX_READ_BYTES + 1) } });
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, 'content-length 超限');
  assert.equal(cancelled, 1, 'content-length 超限时 cancel 响应体');
  for (const bad of ['abc', '-1', '1e9']) {
    reply = () => new Response(finite(good), { status: 200, headers: { 'content-length': bad } });
    assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, `content-length 不合：${bad}`);
  }
  // 没有 content-length（或声明偏小）：流式累计超限即 cancel，不会一直读下去
  cancelled = 0;
  reply = () => new Response(endless(), { status: 200 });
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, '流式超限');
  assert.equal(cancelled, 1, '流式超限时 cancel');
  assert.ok(pulled <= Math.ceil(MAX_READ_BYTES / (64 * 1024)) + 3, `超限后不再读（读了 ${pulled} 块）`);
  cancelled = 0;
  reply = () => new Response(endless(), { status: 200, headers: { 'content-length': '100' } });
  assert.deepEqual(await client.explain({ ...who, claimId: C1 }, 1000), { kind: 'unavailable' }, '声明偏小也按实际字节数截停');
  assert.equal(cancelled, 1);
  // 上限可调（测试用）：恰好等于上限可以，多一个字节不行
  const exact = Buffer.byteLength(good);
  const at = (max: number) => httpInsightReadClient({ baseUrl: 'http://127.0.0.1:8791', token: 't'.repeat(40), maxBytes: max,
                                                      fetchImpl: (async () => new Response(finite(good), { status: 200 })) as typeof fetch });
  assert.equal((await at(exact).report(who, 1000)).kind, 'ok', '恰好等于上限');
  assert.deepEqual(await at(exact - 1).report(who, 1000), { kind: 'unavailable' }, '超一个字节');
  // 非法 UTF-8 → unavailable
  reply = () => new Response(new Uint8Array([0x7b, 0xff, 0x7d]), { status: 200 });
  assert.deepEqual(await client.report(who, 1000), { kind: 'unavailable' }, '非法 UTF-8');
});

test('报告：节键与句子模板类别对应——掌握节只收 *.node_mastery、迁移节只收 *.node_transfer；类别取模板键最后一段', () => {
  assert.equal(templateSection('syn.report.node_mastery'), 'node_mastery');
  assert.equal(templateSection('syn.math.node_transfer'), 'node_transfer');
  assert.equal(templateSection('syn.report.not_listed'), null);
  assert.equal(templateSection('syn.node_mastery.extra'), null, '只看最后一段');
  assert.equal(templateSection('syn.report.node_mastery_x'), null, '整段相等，不是前缀');
  for (const aud of ['student', 'guardian'] as const) assert.equal(sectionMismatches(pickReport(result(aud), aud)!), 0, aud);
  const swapped = result('student');
  (swapped.sections[1]!.sentences[0] as Record<string, unknown>)['template_key'] = 'syn.report.node_mastery';
  assert.equal(sectionMismatches(pickReport(swapped, 'student')!), 1, '掌握模板的句子在迁移节');
  const both = result('student');
  (both.sections[0]!.sentences[1] as Record<string, unknown>)['template_key'] = 'syn.report.node_transfer';
  (both.sections[1]!.sentences[0] as Record<string, unknown>)['template_key'] = 'syn.report.other';
  assert.equal(sectionMismatches(pickReport(both, 'student')!), 2);
});

test('解释：返回前按受众裁剪——学生去掉顶层与陈述内的置信度分档，家长保留；待验证（虚线）去掉陈述文字', () => {
  const est = pickExplain(explainResult(), C1)!;
  const ver = pickExplain(explainResult({ statement: statement({ status: 'to_verify', display: 'dashed' }) }), C1)!;
  const s = explainForAudience(est, 'student');
  assert.deepEqual(Object.keys(s).sort(), ['audience', 'evidence_chain', 'expires_at', 'statement']);
  assert.deepEqual(Object.keys(s.statement).sort(), ['claim_id', 'display', 'status', 'template_key', 'template_version', 'text']);
  assert.equal(s.audience, 'student');
  const g = explainForAudience(est, 'guardian');
  assert.equal(g.confidence_band, 'moderate');
  assert.equal(g.statement.confidence_band, 'moderate');
  assert.equal(g.statement.text, '（合成）模板句');
  for (const aud of ['student', 'guardian'] as const) {
    const v = explainForAudience(ver, aud);
    assert.equal(v.statement.display, 'dashed');
    assert.ok(!('text' in v.statement), `${aud} 待验证没有文字`);
    assert.ok(!JSON.stringify(v).includes('（合成）模板句'));
    assert.equal('confidence_band' in v.statement, aud === 'guardian');
    assert.equal('confidence_band' in v, aud === 'guardian');
  }
  assert.ok(!('expires_at' in explainForAudience(pickExplain(explainResult({ expires_at: undefined }), C1)!, 'student')), '没有到期时间就不带');
});
