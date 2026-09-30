// SIE-03 PR③ 信任界面的单测（不需要数据库）：功能开关解析、洞察服务异议客户端（请求形状、状态映射、fact_ref 不外传）、
// 预测与对账卡的契约形状核对（无 claim_id 的句子丢弃并计数、方向只收四值）、导出包暂存（一次性、绑定账号、过期、上限）。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { disputeBody, httpDisputeClient, pickDispute, pickPrediction, pickReconciliation } from '../src/trust/clients.ts';
import { randomKeys } from '../src/http/config.ts';
import { MAX_HOLD_MS, MemoryExportStore } from '../src/trust/exports.ts';
import { REAUTH_WINDOW_MS, RecentLogins, trustMac } from '../src/trust/proof.ts';
import { parseTrustFeatures } from '../src/trust/features.ts';

const U = (n: string) => `5e000000-0000-4000-8000-0000000000${n}`;

test('开关：缺省全关；dev / test 可开；staging 以上设了就拒绝；不认识的开关拒绝', () => {
  assert.equal(parseTrustFeatures('production', undefined).size, 0);
  assert.equal(parseTrustFeatures('test', '').size, 0);
  assert.deepEqual([...parseTrustFeatures('dev', 'dispute, data_rights')].sort(), ['data_rights', 'dispute']);
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseTrustFeatures(env, 'dispute'), /dev \/ test/);
  assert.throws(() => parseTrustFeatures('test', 'dispute,everything'), /不认识/);
});

test('异议结果：只留 dispute_id、next_step、verify_by；fact_ref 与验证题引用不外传；形状不对为 null', () => {
  const v = pickDispute({ dispute_id: U('01'), next_step: 'open_fact_for_correction', fact_ref: 'attempt:synthetic-0001-abcd' });
  assert.deepEqual(v, { dispute_id: U('01'), next_step: 'open_fact_for_correction', verify_by: null });
  const w = pickDispute({ dispute_id: U('02'), next_step: 'verification_scheduled', verification_item_refs: ['aya:stub-verify-1'],
                          verify_by: '2026-10-10T08:00:00Z' });
  assert.deepEqual(w, { dispute_id: U('02'), next_step: 'verification_scheduled', verify_by: '2026-10-10T08:00:00.000Z' });
  assert.equal(JSON.stringify(w).includes('stub-verify'), false);
  assert.equal(pickDispute({ dispute_id: U('03'), next_step: 'verification_scheduled' }), null, '安排了验证却没有时限');
  assert.equal(pickDispute({ dispute_id: 'x', next_step: 'observe_followups' }), null);
  assert.equal(pickDispute({ dispute_id: U('04'), next_step: 'careless_recorded' }), null);
  // 契约：open_fact_for_correction 必带 fact_ref（BFF 核对它在，但不转发）
  assert.equal(pickDispute({ dispute_id: U('05'), next_step: 'open_fact_for_correction' }), null);
  assert.equal(pickDispute({ dispute_id: U('05'), next_step: 'open_fact_for_correction', fact_ref: 'phone:+99900000001' }), null);
});

test('异议请求：caller = bff，带 actor（家长带 link_id）与幂等键；受众与 actor 一致', () => {
  const b = disputeBody({ studentRef: U('10'), audience: 'guardian', actorRef: U('11'), linkId: U('12'), claimId: U('13'), kind: 'data_error',
                          idempotencyKey: 'bff.0123456789abcdef' }, U('14')) as any;
  assert.equal(b.context.caller, 'bff');
  assert.equal(b.context.audience, 'guardian');
  assert.deepEqual(b.context.actor, { role: 'guardian', actor_ref: U('11'), link_id: U('12') });
  assert.equal(b.context.idempotency_key, 'bff.0123456789abcdef');
  assert.deepEqual(Object.keys(b).sort(), ['claim_id', 'context', 'kind']);
});

test('异议客户端：状态映射；网络错误与形状不对都是 unavailable；响应里的 fact_ref 丢掉', async () => {
  let status = 200;
  const env = { contract_version: '0.9.0', evidence_ids: [U('29')], confidence: 1, source_version: 'algo:sie-dispute@0.1.0',
                model_version: 'algo:sie-dispute@0.1.0', human_review_required: false };
  let payload: unknown = { ...env, result: { dispute_id: U('20'), next_step: 'open_fact_for_correction', fact_ref: 'exam_record:synthetic-0001' } };
  const seen: { url: string; auth: string; body: any }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    seen.push({ url, auth: String((init.headers as Record<string, string>)['authorization']), body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify(payload), { status, headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  const c = httpDisputeClient({ baseUrl: 'http://127.0.0.1:9/', token: 's'.repeat(40), fetchImpl });
  const req = { studentRef: U('21'), audience: 'student' as const, actorRef: U('22'), claimId: U('23'), kind: 'i_know' as const,
                idempotencyKey: 'bff.abcdefabcdefabcdef' };
  const ok = await c.dispute(req, 1000);
  assert.deepEqual(ok, { kind: 'ok', value: { dispute_id: U('20'), next_step: 'open_fact_for_correction', verify_by: null } });
  assert.equal(seen[0]!.url, 'http://127.0.0.1:9/v0/insight.dispute');
  assert.equal(seen[0]!.body.context.actor.link_id, undefined);
  for (const [s, kind] of [[404, 'not_found'], [409, 'conflict'], [429, 'too_many'], [400, 'invalid'], [501, 'unavailable'], [503, 'unavailable']] as const) {
    status = s;
    payload = { code: 'X' };
    assert.equal((await c.dispute(req, 1000)).kind, kind, String(s));
  }
  status = 200;
  payload = { ...env, result: { dispute_id: 'nope' } };
  assert.equal((await c.dispute(req, 1000)).kind, 'unavailable');
  // 统一信封不合契约（缺字段、版本不对）：当不可用
  for (const bad of [{ result: { dispute_id: U('20'), next_step: 'observe_followups' } },
                     { ...env, contract_version: '1.0.0', result: { dispute_id: U('20'), next_step: 'observe_followups' } },
                     { ...env, human_review_required: 'no', result: { dispute_id: U('20'), next_step: 'observe_followups' } }]) {
    payload = bad;
    assert.equal((await c.dispute(req, 1000)).kind, 'unavailable', JSON.stringify(bad).slice(0, 60));
  }
  payload = { ...env, result: { dispute_id: U('20'), next_step: 'observe_followups' } };
  assert.equal((await c.dispute(req, 1000)).kind, 'ok');
  const broken = httpDisputeClient({ baseUrl: 'http://127.0.0.1:9', token: 's'.repeat(40),
                                     fetchImpl: (async () => { throw new Error('down'); }) as unknown as typeof fetch });
  assert.equal((await broken.dispute(req, 1000)).kind, 'unavailable');
});

const stmt = (id: string, extra: Record<string, unknown> = {}) => ({ claim_id: id, template_key: 'syn.trust.reconcile', template_version: 1,
  status: 'established', display: 'solid', text: '（合成模板句）', confidence_band: 'moderate', ...extra });

test('对账卡：没有 claim_id 或形状不对的句子丢弃并计数；方向只收四值；区间内外与方向要一致', () => {
  const card = { prediction_id: U('30'), actual_score: 81, within_interval: false, deviation_nodes: ['MATH.T.A'],
                 deviation_directions: ['diagnosis_optimistic'], adjustments: ['claims_to_verify'],
                 statements: [stmt(U('31')), { ...stmt(U('32')), claim_id: undefined }, { text: '（没有判断的句子）' }, stmt(U('33'), { display: 'dashed' })] };
  const r = pickReconciliation(card)!;
  assert.equal(r.dropped, 3);
  assert.deepEqual(r.card.statements.map((s) => s.claim_id), [U('31')]);
  assert.equal(pickReconciliation({ ...card, deviation_directions: ['careless'] }), null, '四方向以外的编码');
  assert.equal(pickReconciliation({ ...card, deviation_directions: [] }), null, '区间外却没有方向');
  assert.equal(pickReconciliation({ ...card, adjustments: ['label_added'] }), null);
  assert.equal(pickReconciliation({ ...card, actual_score: 151 }), null);
});

test('预测：按契约核对（区间 0–150、level 0.8、带 claim_id）', () => {
  const p = { prediction_id: U('40'), claim_id: U('41'), low: 88, high: 104, interval_level: 0.8, frozen_at: '2026-11-05T00:00:00+08:00',
              basis: { node_count: 2, evidence_count: 1 } };
  assert.deepEqual(pickPrediction(p), { prediction_id: U('40'), claim_id: U('41'), low: 88, high: 104, interval_level: 0.8,
                                        frozen_at: '2026-11-04T16:00:00.000Z' });
  assert.equal(pickPrediction({ ...p, claim_id: undefined }), null);
  assert.equal(pickPrediction({ ...p, low: 110 }), null);
  assert.equal(pickPrediction({ ...p, interval_level: 0.95 }), null);
});

test('导出包暂存：只能兑换一次；账号、任务、学生不对不兑换也不作废；过期与上限', () => {
  const s = new MemoryExportStore(2, 64);
  const ref = `dl_${'a'.repeat(64)}`;
  const e = { jobId: U('50'), studentId: U('51'), accountId: U('52'), expiresAt: 1000, body: '{"format":"starbuddy-export@1"}' };
  assert.ok(s.put(ref, e, 0));
  assert.equal(s.take(ref, { jobId: U('50'), studentId: U('51'), accountId: U('53') }, 1), null, '别的账号');
  assert.equal(s.take(ref, { jobId: U('59'), studentId: U('51'), accountId: U('52') }, 1), null, '别的任务');
  assert.equal(s.take(ref, { jobId: U('50'), studentId: U('51'), accountId: U('52') }, 1), e.body);
  assert.equal(s.take(ref, { jobId: U('50'), studentId: U('51'), accountId: U('52') }, 2), null, '只能一次');
  assert.ok(s.put(ref, e, 0));
  assert.equal(s.take(ref, { jobId: U('50'), studentId: U('51'), accountId: U('52') }, 1000), null, '到期');
  assert.equal(s.put(`dl_${'b'.repeat(64)}`, { ...e, body: 'x'.repeat(65) }, 0), false, '单包超上限');
  assert.ok(s.put(`dl_${'c'.repeat(64)}`, e, 0));
  assert.ok(s.put(`dl_${'d'.repeat(64)}`, e, 0));
  assert.equal(s.put(`dl_${'e'.repeat(64)}`, e, 0), false, '条数上限');
});

test('导出包暂存：最长 1 小时（即使库里 24 小时）；总字节上限；按学生（或学生 + 账号）清掉', () => {
  const s = new MemoryExportStore(10, 100, 150);
  const e = { jobId: U('60'), studentId: U('61'), accountId: U('62'), expiresAt: 24 * 3600_000, body: 'x'.repeat(60) };
  const m = { jobId: U('60'), studentId: U('61'), accountId: U('62') };
  assert.ok(s.put(`dl_${'1'.repeat(64)}`, e, 0));
  assert.equal(s.take(`dl_${'1'.repeat(64)}`, m, MAX_HOLD_MS), null, '1 小时后失效');
  assert.ok(s.put(`dl_${'2'.repeat(64)}`, e, 0));
  assert.ok(s.put(`dl_${'3'.repeat(64)}`, { ...e, accountId: U('63') }, 0));
  assert.equal(s.put(`dl_${'4'.repeat(64)}`, e, 0), false, '总字节超上限');
  assert.equal(s.totalBytes, 120);
  assert.equal(s.purgeStudent(U('61'), U('63')), 1, '只清这位家长的');
  assert.equal(s.take(`dl_${'2'.repeat(64)}`, m, 1), e.body);
  assert.ok(s.put(`dl_${'5'.repeat(64)}`, e, 0));
  assert.equal(s.purgeStudent(U('61')), 1);
  assert.equal(s.size, 0);
  assert.equal(s.totalBytes, 0);
});

test('近期登录：10 分钟内算；按会话；专用密钥（换密钥即不认，与限流、CSRF 的密钥分开）', () => {
  const k = randomKeys();
  const r = new RecentLogins();
  r.record(k, 'a'.repeat(64), 1000);
  assert.ok(r.fresh(k, 'a'.repeat(64), 1000 + REAUTH_WINDOW_MS - 1));
  assert.ok(!r.fresh(k, 'a'.repeat(64), 1000 + REAUTH_WINDOW_MS));
  assert.ok(!r.fresh(k, 'b'.repeat(64), 1001), '别的会话');
  assert.ok(!r.fresh({ ...k, trust: randomKeys().trust }, 'a'.repeat(64), 1001), '换了信任界面密钥');
  assert.notEqual(trustMac(k, 'erasure-confirm', 'x'), trustMac({ ...k, trust: k.rate }, 'erasure-confirm', 'x'));
  assert.notEqual(trustMac(k, 'erasure-confirm', 'x'), trustMac(k, 'dispute-idem', 'x'), '按用途分开');
});
