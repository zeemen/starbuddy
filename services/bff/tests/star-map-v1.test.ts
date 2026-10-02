// H5 星图 v1 单测（specs/star-map-v1-h5/spec.md；统一壳 spec 第 7.2 节）：画星的三条件（纯函数）、学习服务响应的形状核对、
// 开关解析、开关关闭时路由表不变。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { activeRoutes, ROUTES } from '../src/http/routes.ts';
import { parseStarMapFeatures } from '../src/star-map/features.ts';
import { httpStarMapClient, pickNodes, type StarNode } from '../src/star-map/nodes-client.ts';
import { paintSky, type ClaimRow, type PaintRules } from '../src/star-map/v1.ts';

const M = 'syn.math.node_mastery@1';
const T = 'syn.math.node_transfer@1';
const X = 'syn.math.not_listed@1';
const B1 = '00000000-0000-4000-8000-00000000b001';
const B2 = '00000000-0000-4000-8000-00000000b002';
const rules = (o: Partial<PaintRules> = {}): PaintRules =>
  ({ external: new Set([M, T]), mastery: new Set([M]), transfer: new Set([T]), dashed: false, ...o });
const node = (node_id: string, state: StarNode['state'], basis_id: string | null): StarNode =>
  ({ node_id, state, star_lit: state === 'transfer' || state === 'retain', basis_id });
let seq = 0;
const claim = (target: string, tpl: string, basis: string | null, status: ClaimRow['status'] = 'established'): ClaimRow => {
  const [key, v] = tpl.split('@');
  return { id: `00000000-0000-4000-8000-${String(++seq).padStart(12, '0')}`, subject: 'aya', target_id: target, template_key: key!,
           template_version: Number(v), status, basis_id: basis };
};

test('画星：亮且有匹配的知识掌握判断 → 画亮并带 claim_id；保持层带外圈；半亮、未亮同样按状态', () => {
  const c1 = claim('MATH.A.B.C1', M, B1);
  const c2 = claim('MATH.A.B.C2', M, B2, 'hypothesis');
  const p = paintSky([node('MATH.A.B.C1', 'transfer', B1), node('MATH.A.B.C2', 'retain', B2)], [c1, c2], rules());
  assert.deepEqual(p.stars, [
    { node_id: 'MATH.A.B.C1', display: 'lit', retained: false, claim_id: c1.id },
    { node_id: 'MATH.A.B.C2', display: 'lit', retained: true, claim_id: c2.id },
  ]);
  assert.deepEqual(p.masteryClaims.map((c) => c.id), [c1.id, c2.id]);
  const half = claim('MATH.A.B.C3', M, B1);
  assert.equal(paintSky([node('MATH.A.B.C3', 'execute', B1)], [half], rules()).stars[0]!.display, 'half');
  const un = claim('MATH.A.B.C4', M, B1);
  assert.equal(paintSky([node('MATH.A.B.C4', 'understand', B1)], [un], rules()).stars[0]!.display, 'unlit');
});

test('画星：没有判断、basis 不是当前掌握记录、模板不在白名单、只有迁移判断、无掌握记录 → 中性底星（只有 node_id 与 display）', () => {
  const n = node('MATH.A.B.C1', 'transfer', B1);
  const neutral = [{ node_id: 'MATH.A.B.C1', display: 'neutral' }];
  assert.deepEqual(paintSky([n], [], rules()).stars, neutral, '没有判断');
  assert.deepEqual(paintSky([n], [claim('MATH.A.B.C1', M, B2)], rules()).stars, neutral, 'basis 不匹配');
  assert.deepEqual(paintSky([n], [claim('MATH.A.B.C1', M, null)], rules()).stars, neutral, '判断没有依据');
  assert.deepEqual(paintSky([node('MATH.A.B.C1', 'unseen', null)], [claim('MATH.A.B.C1', M, null)], rules()).stars, neutral, '两边都没有依据');
  assert.deepEqual(paintSky([n], [claim('MATH.A.B.C1', X, B1)], rules({ mastery: new Set([M, X]) })).stars, neutral, '模板在掌握表但不在白名单');
  assert.deepEqual(paintSky([n], [claim('MATH.A.B.C1', X, B1)], rules({ external: new Set([M, T, X]) })).stars, neutral, '白名单内但不是掌握维度');
  const tr = claim('MATH.A.B.C1', T, B1);
  const p = paintSky([n], [tr], rules());
  assert.deepEqual(p.stars, neutral, '迁移判断不画星');
  assert.deepEqual(p.transferClaims.map((c) => c.id), [tr.id], '迁移判断只进摘要候选');
  assert.deepEqual(paintSky([n], [claim('MATH.A.B.C9', M, B1)], rules()).stars, neutral, '别的节点上的判断');
});

test('画星：同一条掌握记录同时挂掌握与迁移两条判断——画星只用掌握，迁移进摘要（lessons 约束 5）', () => {
  const m = claim('MATH.A.B.C1', M, B1);
  const t = claim('MATH.A.B.C1', T, B1);
  const p = paintSky([node('MATH.A.B.C1', 'transfer', B1)], [t, m], rules());
  assert.deepEqual(p.stars, [{ node_id: 'MATH.A.B.C1', display: 'lit', retained: false, claim_id: m.id }]);
  assert.deepEqual(p.transferClaims.map((c) => c.id), [t.id]);
  // 迁移判断的依据是旧记录：不进摘要
  assert.deepEqual(paintSky([node('MATH.A.B.C1', 'transfer', B1)], [claim('MATH.A.B.C1', T, B2)], rules()).transferClaims, []);
});

test('待验证：虚线开关关 → 中性；开 → dashed 带 claim_id，不带状态；成立判断优先于待验证', () => {
  const n = node('MATH.A.B.C1', 'stable', B1);
  const v = claim('MATH.A.B.C1', M, B1, 'to_verify');
  assert.deepEqual(paintSky([n], [v], rules()).stars, [{ node_id: 'MATH.A.B.C1', display: 'neutral' }]);
  assert.deepEqual(paintSky([n], [v], rules({ dashed: true })).stars, [{ node_id: 'MATH.A.B.C1', display: 'dashed', claim_id: v.id }]);
  assert.deepEqual(paintSky([n], [v], rules({ dashed: true })).masteryClaims, [], '虚线不作为星数说明的候选');
  const s = claim('MATH.A.B.C1', M, B1);
  assert.equal(paintSky([n], [v, s], rules({ dashed: true })).stars[0]!.display, 'half');
  // 待验证判断的依据不是当前记录：开关开也是中性
  assert.equal(paintSky([n], [claim('MATH.A.B.C1', M, B2, 'to_verify')], rules({ dashed: true })).stars[0]!.display, 'neutral');
});

// ---------------------------------------------------------------- 学习服务响应

const okBody = (nodes: unknown[], extra: Record<string, unknown> = {}) => ({ subject: 'aya', status: 'ok', map_version: '1.0.0', nodes, ...extra });

test('学习服务响应：按契约形状挑字段；多字段、不一致、学科不对、重复节点一律整科不可用', () => {
  const good = { node_id: 'MATH.A.B.C1', state: 'transfer', star_lit: true, basis_id: B1 };
  assert.deepEqual(pickNodes(okBody([good, { node_id: 'MATH.A.B.C2', state: 'unseen', star_lit: false, basis_id: null }]), 'aya'),
    { kind: 'ok', mapVersion: '1.0.0', nodes: [good, { node_id: 'MATH.A.B.C2', state: 'unseen', star_lit: false, basis_id: null }] });
  assert.deepEqual(pickNodes({ subject: 'wenli', status: 'no_map', map_version: null, nodes: [] }, 'wenli'), { kind: 'no_map' });
  const unavailable = { kind: 'unavailable' };
  for (const [what, body] of [
    ['多给 p_mastery', okBody([{ ...good, p_mastery: 0.9 }])],
    ['多给 confidence', okBody([{ ...good, confidence: 0.9 }])],
    ['顶层多字段', okBody([good], { evidence_ids: [] })],
    ['star_lit 与 state 不一致', okBody([{ ...good, star_lit: false }])],
    ['没有依据却不是 unseen', okBody([{ ...good, basis_id: null }])],
    ['依据不是 UUID', okBody([{ ...good, basis_id: 'x' }])],
    ['节点 ID 不合格', okBody([{ ...good, node_id: 'math.a' }])],
    ['重复节点', okBody([good, good])],
    ['版本号不合格', okBody([good], { map_version: 'v1' })],
    ['no_map 带星', { subject: 'aya', status: 'no_map', map_version: null, nodes: [good] }],
    ['不是对象', []],
  ] as const) {
    assert.deepEqual(pickNodes(body, 'aya'), unavailable, what);
  }
  assert.deepEqual(pickNodes(okBody([good]), 'wenli'), unavailable, '学科不对');
});

test('学习服务客户端：Bearer、caller = bff、不跟随重定向、带超时；409 MASTERY_UNAVAILABLE 单独归类；其他状态与异常为不可用', async () => {
  const seen: { url: string; init: RequestInit }[] = [];
  const reply = (status: number, body: unknown) => async (url: string | URL | Request, init?: RequestInit) => {
    seen.push({ url: String(url), init: init! });
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  };
  const mk = (f: typeof fetch) => httpStarMapClient({ baseUrl: 'http://127.0.0.1:9/', token: 't'.repeat(40), timeoutMs: 500, fetchImpl: f });
  const ok = await mk(reply(200, okBody([])) as typeof fetch).nodes('00000000-0000-4000-8000-00000000a001', 'aya');
  assert.deepEqual(ok, { kind: 'ok', mapVersion: '1.0.0', nodes: [] });
  const sent = seen[0]!;
  assert.equal(sent.url, 'http://127.0.0.1:9/internal/v1/star-map/nodes');
  assert.equal(sent.init.redirect, 'error');
  assert.ok(sent.init.signal);
  assert.equal((sent.init.headers as Record<string, string>)['authorization'], `Bearer ${'t'.repeat(40)}`);
  const body = JSON.parse(String(sent.init.body));
  assert.deepEqual(Object.keys(body).sort(), ['caller', 'request_id', 'student_ref', 'subject']);
  assert.equal(body.caller, 'bff');
  assert.deepEqual(await mk(reply(409, { code: 'MASTERY_UNAVAILABLE' }) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'mastery_unavailable' });
  assert.deepEqual(await mk(reply(409, { code: 'BUSY' }) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'unavailable' });
  assert.deepEqual(await mk(reply(500, {}) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'unavailable' });
  assert.deepEqual(await mk(reply(401, {}) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'unavailable' });
  assert.deepEqual(await mk((async () => { throw new Error('boom'); }) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'unavailable' });
  // 响应体上限：声明的 content-length 超限、或流式累计超限，都整科不可用
  const small = (f: typeof fetch) => httpStarMapClient({ baseUrl: 'http://127.0.0.1:9', token: 't'.repeat(40), timeoutMs: 500, fetchImpl: f, maxBodyBytes: 64 });
  const big = okBody([{ node_id: 'MATH.A.B.C1', state: 'unseen', star_lit: false, basis_id: null }]);
  assert.ok(JSON.stringify(big).length > 64);
  assert.deepEqual(await small(reply(200, big) as typeof fetch).nodes(body.student_ref, 'aya'), { kind: 'unavailable' }, '声明长度超限');
  const streamed = (async () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(JSON.stringify(big))); c.close(); } }),
    { status: 200 })) as typeof fetch;
  assert.deepEqual(await small(streamed).nodes(body.student_ref, 'aya'), { kind: 'unavailable' }, '流式累计超限');
  assert.deepEqual(await mk(streamed).nodes(body.student_ref, 'aya'), { kind: 'ok', mapVersion: '1.0.0', nodes: big.nodes }, '上限内照常');
});

// ---------------------------------------------------------------- 开关

test('开关：缺省全关；只在 dev / test 可开；不认识的值、只开虚线都拒绝', () => {
  assert.equal(parseStarMapFeatures('production', undefined).size, 0);
  assert.equal(parseStarMapFeatures('test', '').size, 0);
  assert.deepEqual([...parseStarMapFeatures('test', 'starmap_v1')], ['starmap_v1']);
  assert.deepEqual([...parseStarMapFeatures('dev', 'starmap_dashed, starmap_v1')], ['starmap_v1', 'starmap_dashed']);
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseStarMapFeatures(env, 'starmap_v1'), /dev \/ test/);
  assert.throws(() => parseStarMapFeatures('test', 'starmap_v2'), /不认识/);
  assert.throws(() => parseStarMapFeatures('test', 'starmap_dashed'), /starmap_v1/);
});

test('开关关闭：注册的路由表与改动前相同（没有 v1 路由，v0 星图仍在）；打开才多一条 v1', () => {
  assert.deepEqual(activeRoutes(), ROUTES);
  assert.deepEqual(activeRoutes(new Set(), new Set()), ROUTES);
  assert.deepEqual(activeRoutes(new Set(), new Set(['starmap_dashed'])), ROUTES, '只有虚线开关不注册 v1');
  assert.ok(ROUTES.some((r) => r.method === 'GET' && r.path === '/students/{student_id}/star-map'));
  const on = activeRoutes(new Set(), new Set(['starmap_v1']));
  assert.deepEqual(on.filter((r) => !ROUTES.includes(r)).map((r) => `${r.method} ${r.path}`), ['GET /students/{student_id}/star-map/v1']);
});
