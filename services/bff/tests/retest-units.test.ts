// 复测与验证题 BFF 侧的纯函数与客户端（specs/retest-delivery-kge05）：票据与再练互不通用、请求体、响应白名单、失败映射、开关、
// 学习服务客户端（本机假 HTTP 服务按 retest-internal.v0.yaml 核对请求形状）。不连库。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { P } from '../src/http/problems.ts';
import { openTicket, sealTicket, type TicketContent } from '../src/practice/ticket.ts';
import { parseAnswer } from '../src/practice/service.ts';
import { parseRetestFeature } from '../src/retest/features.ts';
import { httpRetestLearning } from '../src/retest/learning-client.ts';
import { fromRetestFailure, parseRetestAnswer, pickRetestAnswer, pickRetestEntry, readRetestReady } from '../src/retest/service.ts';

const S = randomUUID();
const content = (o: Partial<TicketContent> = {}): TicketContent => ({
  ticket: `rt1.${Buffer.from(JSON.stringify({ student: S, item_id: 'SYN-RT-ITEM-01' })).toString('base64url')}.c2ln`, subject: 'aya',
  entryId: randomUUID(), expiresAt: '2026-10-09T10:00:00Z', format: 'single', optionKeys: ['A', 'B'], ...o,
});

test('票据：复测票据（sbr1）与再练票据（sbp1）同一把密钥也互相解不开；只改前缀也解不开；仍绑定学生', () => {
  const key = randomBytes(32);
  const c = content();
  const rt = sealTicket(key, S, c, 'retest');
  const pt = sealTicket(key, S, c);
  assert.match(rt, /^sbr1\.[A-Za-z0-9_-]+$/);
  assert.match(pt, /^sbp1\.[A-Za-z0-9_-]+$/);
  const { idempotencyKey, ...rest } = openTicket(key, S, rt, 'retest')!;
  assert.deepEqual(rest, c);
  assert.ok(idempotencyKey);
  assert.equal(openTicket(key, S, rt), null, '复测票据拿去再练');
  assert.equal(openTicket(key, S, pt, 'retest'), null, '再练票据拿来复测');
  assert.equal(openTicket(key, S, `sbp1.${rt.slice(5)}`), null, '复测票据改成再练前缀（附加数据不同）');
  assert.equal(openTicket(key, S, `sbr1.${pt.slice(5)}`, 'retest'), null, '再练票据改成复测前缀');
  assert.equal(openTicket(key, randomUUID(), rt, 'retest'), null, '换学生');
  for (const leak of [S, 'SYN-RT-ITEM-01', 'rt1.', c.entryId]) assert.ok(!rt.includes(leak), leak);
});

test('作答请求体：只收 sbr1 票据与作答；幂等键、usage_id、purpose、channel、学生一律拒收；再练的请求体解析不收复测票据', () => {
  const ok = { ticket: `sbr1.${'A'.repeat(64)}`, answer: { choice: ['A'] } };
  assert.deepEqual(parseRetestAnswer(ok), ok);
  assert.equal(parseAnswer(ok), null);
  for (const extra of [{ idempotency_key: randomUUID() }, { usage_id: randomUUID() }, { purpose: 'retest' }, { purpose: 'transfer_check' },
                       { channel: 'keypad' }, { student_ref: S }, { kind: 'verify' }]) {
    assert.equal(parseRetestAnswer({ ...ok, ...extra }), null, Object.keys(extra)[0]);
  }
  assert.equal(parseRetestAnswer({ ...ok, ticket: `sbp1.${'A'.repeat(64)}` }), null, '再练票据');
  assert.equal(parseRetestAnswer({ ...ok, answer: { text: ' ' } }), null);
  assert.equal(parseRetestAnswer({ ticket: ok.ticket }), null);
});

const entry = (o: Record<string, unknown> = {}) => ({
  entry_id: randomUUID(), node_id: 'MATH.SYN.RT.A', cognitive_level: 'apply', kind: 'retest', status: 'due', empty_reason: null, ...o,
});

test('条目白名单：只转条目 ID、节点、层次、类别与状态；空原因编码、任务原因、掌握、置信度、证据 ID 都不转；形状不对返回 null', () => {
  const out = pickRetestEntry(entry({ reason: 'scheduled', p_mastery: 0.4, confidence: 0.8, evidence_ids: [randomUUID()], empty_reason: 'no_new_family',
                                      status: 'unavailable' }))!;
  assert.deepEqual(Object.keys(out).sort(), ['cognitive_level', 'entry_id', 'kind', 'node_id', 'status']);
  assert.equal(out['status'], 'unavailable');
  for (const bad of [{ kind: 'scheduled' }, { kind: 'hint_followup' }, { status: 'done' }, { status: 'mastered' }, { entry_id: 'x' },
                     { cognitive_level: 'genius' }, { node_id: '' }]) {
    assert.equal(pickRetestEntry(entry(bad)), null, JSON.stringify(bad));
  }
});

test('取题白名单：题目引用与学习服务票据之外只留题面、类别与过期时间；类别不认识、条目不对、题型不对返回 null', () => {
  const id = randomUUID();
  const v = { status: 'ready', entry_id: id, kind: 'verify', item_ref: 'aya:SYN-RT-ITEM-01', ticket: 'rt1.x.y', expires_at: '2026-10-09T10:00:00Z',
              item: { format: 'single', stem: '（合成）1 + 1 = ?', options: [{ key: 'A', text: '1' }, { key: 'B', text: '2' }] } };
  const r = readRetestReady(v, id)!;
  assert.deepEqual(Object.keys(r).sort(), ['entry_id', 'expires_at', 'item', 'kind', 'ticket']);
  assert.equal(readRetestReady({ ...v, kind: 'transfer_check' }, id), null);
  assert.equal(readRetestReady(v, randomUUID()), null);
  assert.equal(readRetestReady({ ...v, item: { format: 'essay', stem: 'x' } }, id), null);
  assert.equal(readRetestReady({ ...v, item: { format: 'fill', stem: 'x', options: [{ key: 'A', text: '1' }] } }, id), null);
});

test('作答结果白名单：判分结论（契约枚举）、待复核标记、条目新状态（due / done）；掌握、证据 ID 等一律不转', () => {
  const id = randomUUID();
  const out = pickRetestAnswer({ verdict: 'correct', entry: { entry_id: id, status: 'done', round: 1 }, p_mastery: 0.9, evidence_ids: [randomUUID()] })!;
  assert.deepEqual(out, { verdict: 'correct', entry: { entry_id: id, status: 'done' } });
  assert.deepEqual(pickRetestAnswer({ verdict: 'incorrect', pending_review: true, entry: { entry_id: id, status: 'due' } }),
                   { verdict: 'incorrect', pending_review: true, entry: { entry_id: id, status: 'due' } });
  for (const bad of [{ verdict: 'mastered', entry: { entry_id: id, status: 'done' } }, { verdict: 'correct', entry: { entry_id: id, status: 'closed' } },
                     { verdict: 'correct', pending_review: false, entry: { entry_id: id, status: 'done' } }, { verdict: 'correct' }]) {
    assert.equal(pickRetestAnswer(bad), null, JSON.stringify(bad));
  }
});

test('失败映射：404 与学科没开同一个 404；RETEST_NOT_DUE 取题、作答 409，列表 503；票据类只在作答时映射；其余确定失败 503 RETEST_UNAVAILABLE', () => {
  const f = (status: number, code: string) => ({ status, problem: { code } });
  const code = (r: { body: string }) => (JSON.parse(r.body) as { code: string }).code;
  assert.equal(fromRetestFailure(f(404, 'ENTRY_NOT_FOUND'), 'next').body, P.NOT_FOUND);
  assert.equal(fromRetestFailure(f(400, 'SUBJECT_NOT_SUPPORTED'), 'queue').body, P.NOT_FOUND);
  assert.equal(code(fromRetestFailure(f(409, 'RETEST_NOT_DUE'), 'next')), 'RETEST_NOT_DUE');
  assert.equal(code(fromRetestFailure(f(409, 'RETEST_NOT_DUE'), 'queue')), 'RETEST_UNAVAILABLE');
  assert.equal(code(fromRetestFailure(f(403, 'TICKET_INVALID'), 'answer')), 'TICKET_INVALID');
  assert.equal(code(fromRetestFailure(f(403, 'TICKET_INVALID'), 'next')), 'RETEST_UNAVAILABLE');
  assert.equal(code(fromRetestFailure(f(410, 'TICKET_EXPIRED'), 'answer')), 'TICKET_EXPIRED');
  assert.equal(code(fromRetestFailure(f(409, 'IDEMPOTENCY_CONFLICT'), 'answer')), 'TICKET_USED');
  assert.equal(code(fromRetestFailure(f(409, 'TICKET_USED'), 'answer')), 'TICKET_USED');
  assert.equal(code(fromRetestFailure(f(409, 'IDEMPOTENCY_KEY_SEALED'), 'answer')), 'KEY_SEALED');
  assert.equal(code(fromRetestFailure(f(422, 'INVALID_ANSWER'), 'answer')), 'INVALID_ANSWER');
  assert.equal(code(fromRetestFailure(f(422, 'CONTENT_NOT_REGISTERED'), 'answer')), 'RETEST_UNAVAILABLE');
  assert.equal(code(fromRetestFailure(f(409, 'PRACTICE_NOT_DUE'), 'answer')), 'RETEST_UNAVAILABLE');
});

test('开关：只认 retest，只在 dev / test；其余环境设了就拒绝启动；不设为关', () => {
  assert.equal(parseRetestFeature('test', undefined), false);
  assert.equal(parseRetestFeature('dev', ' '), false);
  assert.equal(parseRetestFeature('test', 'retest'), true);
  assert.throws(() => parseRetestFeature('test', 'practice'));
  assert.throws(() => parseRetestFeature('staging', 'retest'));
  assert.throws(() => parseRetestFeature('production', 'retest'));
  assert.throws(() => parseRetestFeature('pilot', 'retest'));
});

// ---------- 学习服务客户端：请求形状 ----------
let server: Server;
let base = '';
const seen: { url: string; auth: string; body: Record<string, unknown> }[] = [];
before(async () => {
  server = createServer((req: IncomingMessage, res) => {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      seen.push({ url: req.url ?? '', auth: String(req.headers['authorization'] ?? ''), body: JSON.parse(raw) as Record<string, unknown> });
      res.setHeader('content-type', 'application/json');
      if (req.url?.endsWith('/answer')) res.setHeader('idempotent-replayed', 'true');
      res.end(JSON.stringify({ ok: true }));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => new Promise<void>((r) => server.close(() => r())));

test('学习服务客户端：三个内部接口的路径与请求体（学生 ID 只在请求体）、Bearer 令牌、重放响应头', async () => {
  const c = httpRetestLearning({ baseUrl: `${base}/`, token: 't'.repeat(40) });
  const entryId = randomUUID();
  const key = randomUUID();
  const usage = randomUUID();
  assert.equal((await c.queue(S, 'aya')).kind, 'ok');
  assert.equal((await c.next(S, 'aya', entryId)).kind, 'ok');
  const a = await c.answer(S, 'aya', { ticket: 'rt1.x.y', answer: { text: '1' }, idempotency_key: key, usage_id: usage });
  assert.ok(a.kind === 'ok' && a.replayed);
  assert.equal((await c.check(S, 'aya', 'rt1.x.y')).kind, 'ok');
  assert.equal((await c.replay(S, 'aya', { ticket: 'rt1.x.y', idempotency_key: key, usage_id: usage })).kind, 'ok');
  assert.deepEqual(seen.map((x) => x.url), ['/internal/v1/retest/queue', '/internal/v1/retest/next', '/internal/v1/retest/answer',
                                            '/internal/v1/retest/check', '/internal/v1/retest/replay']);
  assert.deepEqual(seen[3]!.body, { student_ref: S, subject: 'aya', ticket: 'rt1.x.y' });
  assert.deepEqual(seen[4]!.body, { student_ref: S, subject: 'aya', ticket: 'rt1.x.y', idempotency_key: key, usage_id: usage });
  assert.ok(seen.every((x) => x.auth === `Bearer ${'t'.repeat(40)}` && !x.url.includes(S)));
  assert.deepEqual(seen[0]!.body, { student_ref: S, subject: 'aya' });
  assert.deepEqual(seen[1]!.body, { student_ref: S, subject: 'aya', entry_id: entryId });
  assert.deepEqual(seen[2]!.body, { student_ref: S, subject: 'aya', ticket: 'rt1.x.y', answer: { text: '1' }, idempotency_key: key, usage_id: usage });
  const down = httpRetestLearning({ baseUrl: 'http://127.0.0.1:9', token: 't'.repeat(40), timeoutMs: 200 });
  assert.equal((await down.queue(S, 'aya')).kind, 'unknown');
});
