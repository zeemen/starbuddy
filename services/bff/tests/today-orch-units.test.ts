// 今日计划跨科编排 BFF 侧的纯函数与客户端（PLT-11，specs/today-plan-orchestration-plt11）：开关、响应白名单（pickOrdered）、
// 学习服务客户端（本机假 HTTP 服务核对请求形状、非 200 与断连按不可用）。不连库。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { pickSections } from '../src/shell/plan-client.ts';
import { httpTodayOrchClient, parseTodayOrchFeature, pickOrdered } from '../src/shell/today-orch.ts';

const PLAN = { subjects: [
  { subject: 'aya', status: 'ok', tasks: [{ kind: 'retest', target_node_ids: ['MATH.SYN.T1.A'], priority: 0.9, estimated_minutes: 10,
                                           rationale_code: 'retest_scheduled', p_mastery: 0.4 }] },
  { subject: 'zhixing', status: 'unavailable', tasks: [] },
] };

const S = randomUUID();
const E: string = randomUUID();
const ok = (o: Record<string, unknown> = {}) => ({
  items: [
    { subject: 'aya', kind: 'retest', node_id: 'MATH.SYN.T1.A', estimated_minutes: 5, source: 'retest', entry_id: E, rationale_code: null },
    { subject: 'aya', kind: 'verify', node_id: 'MATH.SYN.T1.B', estimated_minutes: 5, source: 'retest', entry_id: E, rationale_code: null },
    { subject: 'zhixing', kind: 'practice', node_id: null, estimated_minutes: 8, source: 'practice', entry_id: E, rationale_code: null },
    { subject: 'aya', kind: 'lesson', node_id: null, estimated_minutes: 5, source: 'plan', entry_id: null, rationale_code: 'high_exam_weight' },
  ],
  truncated_count: 2, budget_minutes: 30,
  sources: [{ source: 'retest', subject: 'aya', status: 'ok' }, { source: 'plan', subject: 'zhixing', status: 'ok' }],
  ...o,
});

test('开关：只认 today_orch，只在 dev / test；其余环境设了就拒绝启动；不设为关', () => {
  assert.equal(parseTodayOrchFeature('test', undefined), false);
  assert.equal(parseTodayOrchFeature('dev', ' '), false);
  assert.equal(parseTodayOrchFeature('test', 'today_orch'), true);
  assert.equal(parseTodayOrchFeature('dev', ' today_orch '), true);
  assert.throws(() => parseTodayOrchFeature('test', 'retest'));
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseTodayOrchFeature(env, 'today_orch'), env);
});

test('响应白名单：只挑七个字段；来源不可用标 partial；学习服务多给的内部字段不进来', () => {
  const r = pickOrdered({ ...ok(), extra: 1 }, ['aya', 'zhixing'])!;
  assert.equal(r.truncated_count, 2);
  assert.equal(r.partial, false);
  assert.deepEqual(Object.keys(r).sort(), ['items', 'partial', 'truncated_count']);
  assert.ok(r.items.every((i) => Object.keys(i).sort().join(',') === 'entry_id,estimated_minutes,kind,node_id,rationale_code,source,subject'));
  const leaky = ok();
  (leaky.items[0] as Record<string, unknown>)['p_mastery'] = 0.42;
  (leaky.items[0] as Record<string, unknown>)['confidence'] = 0.9;
  (leaky.items[0] as Record<string, unknown>)['evidence_ids'] = [randomUUID()];
  const text = JSON.stringify(pickOrdered(leaky, ['aya', 'zhixing']));
  for (const k of ['p_mastery', 'confidence', 'evidence_ids', '0.42', 'budget_minutes', 'sources']) assert.ok(!text.includes(k), k);
  const partial = pickOrdered(ok({ sources: [{ source: 'practice', subject: 'aya', status: 'source_unavailable' }] }), ['aya', 'zhixing'])!;
  assert.equal(partial.partial, true);
  // 未启用（source_disabled）不算 partial；认不出的状态从严当失败
  const disabled = pickOrdered(ok({ sources: [{ source: 'practice', subject: 'aya', status: 'source_disabled' },
                                              { source: 'plan', subject: 'aya', status: 'ok' }] }), ['aya', 'zhixing'])!;
  assert.equal(disabled.partial, false);
  assert.equal(pickOrdered(ok({ sources: [{ source: 'plan', subject: 'aya', status: 'weird' }] }), ['aya', 'zhixing'])?.partial, true);
  assert.deepEqual(pickOrdered(ok({ items: [] }), ['aya'])!.items, []);
});

test('响应白名单：任何一条不合格就整张列表不可用（不猜）', () => {
  const bad: [string, (x: ReturnType<typeof ok>) => void][] = [
    ['学科不在请求里', (x) => { x.items[2]!.subject = 'wenli'; }],
    ['类别不认识', (x) => { x.items[0]!.kind = 'gifted_track'; }],
    ['来源不认识', (x) => { x.items[0]!.source = 'diagnosis'; }],
    ['节点编号不合格', (x) => { x.items[0]!.node_id = 'math.syn'; }],
    ['分钟不是正整数', (x) => { x.items[0]!.estimated_minutes = 0; }],
    ['分钟是小数', (x) => { x.items[0]!.estimated_minutes = 4.5; }],
    ['分钟超过 600', (x) => { x.items[0]!.estimated_minutes = 601; }],
    ['复测条目没有条目引用', (x) => { x.items[0]!.entry_id = null as unknown as string; }],
    ['复测条目引用不是 UUID', (x) => { x.items[0]!.entry_id = 'E1'; }],
    ['复测条目带理由编码', (x) => { x.items[0]!.rationale_code = 'retest_due' as unknown as null; }],
    ['计划任务带条目引用', (x) => { x.items[3]!.entry_id = E; }],
    ['计划理由编码不合格', (x) => { x.items[3]!.rationale_code = '该复习了'; }],
    ['截掉的条数为负', (x) => { (x as Record<string, unknown>)['truncated_count'] = -1; }],
    ['没有 sources', (x) => { delete (x as Record<string, unknown>)['sources']; }],
  ];
  for (const [name, mutate] of bad) {
    const x = ok();
    mutate(x);
    assert.equal(pickOrdered(x, ['aya', 'zhixing']), null, name);
  }
  for (const body of [null, 'x', [], { items: 'x', truncated_count: 0, sources: [] }]) assert.equal(pickOrdered(body, ['aya']), null);
  assert.equal(pickOrdered(ok({ items: Array.from({ length: 201 }, () => ok().items[0]) }), ['aya']), null, '条数上限');
});

test('今日计划快照（PLT-15）：frozen 时每条带 task_id 并透出；not_frozen 与没有 snapshot 时不带；形状不对整张列表不可用', () => {
  const T = ok().items.map(() => randomUUID());
  const frozen = (o: Record<string, unknown> = {}) => ok({ snapshot: 'frozen', items: ok().items.map((i, k) => ({ ...i, task_id: T[k] })), ...o });
  const r = pickOrdered(frozen(), ['aya', 'zhixing'])!;
  assert.equal(r.snapshot, 'frozen');
  assert.deepEqual(r.items.map((i) => i.task_id), T);
  assert.ok(r.items.every((i) => Object.keys(i).sort().join(',') === 'entry_id,estimated_minutes,kind,node_id,rationale_code,source,subject,task_id'));
  const nf = pickOrdered(ok({ snapshot: 'not_frozen' }), ['aya', 'zhixing'])!;
  assert.equal(nf.snapshot, 'not_frozen');
  assert.ok(nf.items.every((i) => !('task_id' in i)));
  // 学习服务快照开关关：没有 snapshot 键，结果与开关上线前逐字段一致
  const plain = pickOrdered(ok(), ['aya', 'zhixing'])!;
  assert.deepEqual(Object.keys(plain).sort(), ['items', 'partial', 'truncated_count']);
  assert.ok(plain.items.every((i) => !('task_id' in i)));
  const bad: [string, unknown][] = [
    ['认不出的快照状态', ok({ snapshot: 'partial' })],
    ['快照状态是 null', ok({ snapshot: null })],
    ['frozen 缺一条 task_id', frozen({ items: ok().items.map((i, k) => (k === 1 ? i : { ...i, task_id: T[k] })) })],
    ['frozen 的 task_id 不是 UUID', frozen({ items: ok().items.map((i, k) => ({ ...i, task_id: k === 0 ? 'T1' : T[k] })) })],
    ['frozen 的 task_id 是大写', frozen({ items: ok().items.map((i, k) => ({ ...i, task_id: k === 0 ? T[0]!.toUpperCase() : T[k] })) })],
    ['frozen 的 task_id 重复', frozen({ items: ok().items.map((i) => ({ ...i, task_id: T[0] })) })],
    ['not_frozen 却带 task_id', ok({ snapshot: 'not_frozen', items: ok().items.map((i, k) => ({ ...i, task_id: T[k] })) })],
    ['没有 snapshot 却带 task_id', ok({ items: ok().items.map((i, k) => ({ ...i, task_id: T[k] })) })],
  ];
  for (const [name, body] of bad) assert.equal(pickOrdered(body, ['aya', 'zhixing']), null, name);
  // frozen 且没有条目（再读时按本次学科过滤后为空）：合格，空列表
  assert.deepEqual(pickOrdered(ok({ snapshot: 'frozen', items: [] }), ['aya'])!.items, []);
});

// ---------- 学习服务客户端 ----------
let server: Server;
let base = '';
let status = 200;
let reply: unknown = ok();
const seen: { url: string; method: string; auth: string; body: Record<string, unknown> }[] = [];
before(async () => {
  server = createServer((req: IncomingMessage, res) => {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      seen.push({ url: req.url ?? '', method: req.method ?? '', auth: String(req.headers['authorization'] ?? ''), body: JSON.parse(raw) as Record<string, unknown> });
      res.statusCode = status;
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify(reply));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => new Promise<void>((r) => server.close(() => r())));

test('学习服务客户端：POST /internal/v1/today，学生 ID 只在请求体，Bearer 令牌；非 200、断连、不合格一律 null', async () => {
  const c = httpTodayOrchClient({ baseUrl: `${base}/`, token: 't'.repeat(40), timeoutMs: 2000 });
  reply = ok({ plan: PLAN });
  const r = await c.today(S, ['aya', 'zhixing']);
  assert.equal(r?.ordered?.items.length, 4);
  // 附带的各科计划与 /plan 同一套挑字段（pickSections）
  assert.deepEqual(r?.sections, pickSections(PLAN, ['aya', 'zhixing']));
  assert.deepEqual(r?.sections?.map((s) => s.status), ['ok', 'unavailable']);
  const last = seen.at(-1)!;
  assert.equal(last.url, '/internal/v1/today');
  assert.equal(last.method, 'POST');
  assert.equal(last.auth, `Bearer ${'t'.repeat(40)}`);
  assert.deepEqual(Object.keys(last.body).sort(), ['request_id', 'student_ref', 'subjects']);
  assert.deepEqual([last.body['student_ref'], last.body['subjects']], [S, ['aya', 'zhixing']]);
  assert.match(String(last.body['request_id']), /^[0-9a-f-]{36}$/);
  assert.ok(!last.url.includes(S));
  status = 501;
  reply = { code: 'TODAY_UNAVAILABLE' };
  assert.equal(await c.today(S, ['aya']), null);
  status = 200;
  // 跨科列表不合格：只有它为 null，按科计划照常；计划缺失或为 null：按科为 null（调用方每科“暂无安排”）
  reply = { items: [{ subject: 'aya' }], truncated_count: 0, sources: [], plan: PLAN };
  const half = await c.today(S, ['aya', 'zhixing']);
  assert.equal(half?.ordered, null);
  assert.equal(half?.sections?.length, 2);
  reply = ok({ plan: null });
  const noPlan = await c.today(S, ['aya', 'zhixing']);
  assert.equal(noPlan?.sections, null);
  assert.equal(noPlan?.ordered?.items.length, 4);
  const down = httpTodayOrchClient({ baseUrl: 'http://127.0.0.1:9', token: 't'.repeat(40), timeoutMs: 200 });
  assert.equal(await down.today(S, ['aya']), null);
  reply = ok();
});
