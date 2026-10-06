// 错题再练 BFF 侧的纯函数与客户端（specs/practice-loop-stage1 PR 3）：票据封装、请求体与作答形状、响应白名单、失败映射、开关、
// 学习服务客户端（本机假 HTTP 服务按 practice-internal.v0.yaml 核对请求形状）。不连库。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { parsePracticeFeature } from '../src/practice/features.ts';
import { httpPracticeLearning } from '../src/practice/learning-client.ts';
import { answerFits, fromFailure, parseAnswer, pickAnswer, pickEntry, RETRY_BUDGET_MS, SUBJECTS, VERDICTS, withRetry } from '../src/practice/service.ts';
import type { PracticeOutcome } from '../src/practice/learning-client.ts';
import { MAX_SEALED, openTicket, sealTicket, type TicketContent } from '../src/practice/ticket.ts';
import { isUuidV4 } from '../src/metering/uuid.ts';
import { P } from '../src/http/problems.ts';

const S = randomUUID();
const S2 = randomUUID();
const LEARNING_TICKET = `pt1.${Buffer.from(JSON.stringify({ student: S, item_id: 'SYN-PR-ITEM-01', node_id: 'MATH.SYN.PR.A' })).toString('base64url')}.c2ln`;
const content = (o: Partial<TicketContent> = {}): TicketContent => ({
  ticket: LEARNING_TICKET, subject: 'aya', entryId: randomUUID(), expiresAt: '2026-10-05T10:00:00Z', format: 'single',
  optionKeys: ['A', 'B', 'C', 'D'], ...o,
});

test('票据：封装后能解开且逐项一致；前端看到的是不透明串，读不到学生 ID、题目与学习服务票据', () => {
  const key = randomBytes(32);
  const c = content();
  const sealed = sealTicket(key, S, c);
  assert.match(sealed, /^sbp1\.[A-Za-z0-9_-]+$/);
  assert.ok(sealed.length <= MAX_SEALED);
  const { idempotencyKey, ...rest } = openTicket(key, S, sealed)!;
  assert.deepEqual(rest, c);
  assert.ok(isUuidV4(idempotencyKey), '票据里带 BFF 生成的幂等键（小写 UUIDv4）');
  assert.equal(openTicket(key, S, sealed)!.idempotencyKey, idempotencyKey, '同一张票据解开总是同一个键');
  const raw = Buffer.from(sealed.slice(5), 'base64url').toString('latin1');
  for (const leak of [S, 'SYN-PR-ITEM-01', 'MATH.SYN.PR.A', 'pt1.', c.entryId, idempotencyKey]) assert.ok(!raw.includes(leak) && !sealed.includes(leak), leak);
  const again = sealTicket(key, S, c);
  assert.notEqual(again, sealed, '每次封装的 IV 不同');
  assert.notEqual(openTicket(key, S, again)!.idempotencyKey, idempotencyKey, '每张票据一个新键（重新取题 = 新的一次作答）');
  // 学习服务票据上限 1024 字符、选项 8 个：封装后仍在上限内
  const big = sealTicket(key, S, content({ ticket: `pt1.${'x'.repeat(1016)}`, optionKeys: ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] }));
  assert.ok(big.length <= MAX_SEALED, String(big.length));
});

test('票据：换学生（兄妹、另一户）、换密钥、篡改任一字节、截断、换前缀一律解不开', () => {
  const key = randomBytes(32);
  const sealed = sealTicket(key, S, content());
  assert.equal(openTicket(key, S2, sealed), null, '换学生');
  assert.equal(openTicket(randomBytes(32), S, sealed), null, '换密钥');
  const body = Buffer.from(sealed.slice(5), 'base64url');
  for (const i of [0, 11, 12, 20, body.length - 17, body.length - 1]) {
    const t = Buffer.from(body);
    t[i] = t[i]! ^ 1;
    assert.equal(openTicket(key, S, `sbp1.${t.toString('base64url')}`), null, `篡改第 ${i} 字节`);
  }
  assert.equal(openTicket(key, S, sealed.slice(0, -4)), null, '截断');
  assert.equal(openTicket(key, S, sealed.replace('sbp1.', 'sbp2.')), null, '换前缀');
  assert.equal(openTicket(key, S, `sbp1.${'A'.repeat(64)}`), null, '格式合法的假票据');
  assert.equal(openTicket(key, S, `${sealed}=`), null, '多出字符');
  assert.equal(openTicket(key, S, `sbp1.${'A'.repeat(MAX_SEALED)}`), null, '超长');
});

test('票据：解开后仍逐项核对（加密前就不合格的内容不认）', () => {
  const key = randomBytes(32);
  for (const bad of [content({ entryId: 'not-a-uuid' }), content({ expiresAt: 'tomorrow' }), content({ format: 'essay' as never }),
                     content({ optionKeys: ['a'] }), content({ ticket: '' }), content({ ticket: 'x'.repeat(1025) })]) {
    assert.equal(openTicket(key, S, sealTicket(key, S, bad)), null, JSON.stringify(bad).slice(0, 60));
  }
});

test('作答请求体：只收票据与作答（幂等键在票据里，前端带了也拒收）；作答是 {text} 或 {choice}；其余字段与不合格的值一律拒收', () => {
  const ok = { ticket: `sbp1.${'A'.repeat(64)}`, answer: { choice: ['A'] } };
  assert.deepEqual(parseAnswer(ok), ok);
  assert.deepEqual(parseAnswer({ ...ok, answer: { text: ' 3 ' } })?.answer, { text: ' 3 ' });
  for (const [what, b] of [
    ['idempotency_key', { ...ok, idempotency_key: randomUUID() }],
    ['usage_id', { ...ok, usage_id: randomUUID() }], ['quantity', { ...ok, quantity: 1 }], ['purpose', { ...ok, purpose: 'practice' }],
    ['channel', { ...ok, channel: 'keypad' }], ['student_ref', { ...ok, student_ref: S }], ['缺票据', { answer: ok.answer }], ['缺作答', { ticket: ok.ticket }],
    ['票据前缀', { ...ok, ticket: `pt1.${'A'.repeat(64)}` }], ['票据超长', { ...ok, ticket: `sbp1.${'A'.repeat(MAX_SEALED)}` }],
    ['空填空', { ...ok, answer: { text: '  ' } }], ['填空超长', { ...ok, answer: { text: 'x'.repeat(201) } }], ['空选择', { ...ok, answer: { choice: [] } }],
    ['重复选项', { ...ok, answer: { choice: ['A', 'A'] } }], ['小写选项', { ...ok, answer: { choice: ['a'] } }],
    ['两种都给', { ...ok, answer: { text: '1', choice: ['A'] } }], ['作答多字段', { ...ok, answer: { text: '1', work: 'x' } }],
    ['作答是数组', { ...ok, answer: ['A'] }],
  ] as const) {
    assert.equal(parseAnswer(b as Record<string, unknown>), null, what);
  }
});

test('作答形状按题型与选项键核对（与学习服务 validate_answer 同一口径）', () => {
  const keys = ['A', 'B', 'C'];
  assert.ok(answerFits('fill', [], { text: '1' }));
  assert.ok(!answerFits('fill', [], { choice: ['A'] }));
  assert.ok(answerFits('single', keys, { choice: ['B'] }));
  assert.ok(!answerFits('single', keys, { choice: ['A', 'B'] }));
  assert.ok(!answerFits('single', keys, { choice: ['D'] }));
  assert.ok(!answerFits('single', keys, { text: 'A' }));
  assert.ok(answerFits('multi', keys, { choice: ['A', 'C'] }));
  assert.ok(!answerFits('multi', keys, { choice: ['A', 'E'] }));
});

const entry = (o: Record<string, unknown> = {}) => ({
  entry_id: randomUUID(), node_id: 'MATH.SYN.PR.A', cognitive_level: 'apply', source: 'wrong', status: 'due', round: 1, max_rounds: 3,
  source_item_ref: 'aya:SYN-PR-ITEM-00', next_round_at: null, retest_due: true, same_family_only: false, empty_reason: null, ...o,
});

test('条目白名单：原题引用、复测标记、同族限定标记与任何多出来的字段（掌握、置信度、证据、错因）都不转；形状不对返回 null', () => {
  const e = entry({ p_mastery: 0.4, confidence: 0.8, evidence_ids: [randomUUID()], error_category: 'calc' });
  const out = pickEntry(e)!;
  assert.deepEqual(Object.keys(out).sort(), ['cognitive_level', 'empty_reason', 'entry_id', 'max_rounds', 'next_round_at', 'node_id', 'round',
                                             'source', 'status']);
  assert.equal(pickEntry(entry({ status: 'waiting', next_round_at: '2026-10-07T02:00:00Z' }))!['next_round_at'], '2026-10-07T02:00:00Z');
  for (const bad of [{ status: 'mastered' }, { entry_id: 'x' }, { round: 0 }, { source: 'teacher' }, { cognitive_level: 'genius' },
                     { next_round_at: 'soon' }, { empty_reason: 'Bad Reason' }, { node_id: '' }]) {
    assert.equal(pickEntry(entry(bad)), null, JSON.stringify(bad));
  }
});

test('作答结果白名单：只有判分结论、待复核标记与条目新状态', () => {
  const id = randomUUID();
  const v = { verdict: 'incorrect', entry: { entry_id: id, status: 'waiting', round: 2, next_round_at: '2026-10-07T02:00:00Z', mastery: 0.2 },
              evidence_ids: [randomUUID()], confidence: 0.9, error_attribution: { category: 'sign' }, p_mastery: 0.3, explanation: '……' };
  assert.deepEqual(pickAnswer(v), { verdict: 'incorrect', entry: { entry_id: id, status: 'waiting', round: 2, next_round_at: '2026-10-07T02:00:00Z' } });
  assert.deepEqual(pickAnswer({ ...v, pending_review: true })!['pending_review'], true);
  for (const verdict of VERDICTS) assert.equal(pickAnswer({ ...v, verdict })!['verdict'], verdict, `契约枚举 ${verdict}`);
  assert.deepEqual([...VERDICTS], ['correct', 'incorrect', 'partial', 'needs_review', 'not_applicable']);
  for (const bad of [{ ...v, verdict: 3 }, { ...v, verdict: 'Correct!' }, { ...v, verdict: 'excellent' }, { ...v, verdict: 'mastered' },
                     { ...v, pending_review: false }, { ...v, entry: { ...v.entry, status: 'unavailable' } }, { ...v, entry: null }]) {
    assert.equal(pickAnswer(bad), null, JSON.stringify(bad.verdict));
  }
});

test('学习服务确定失败的映射（按操作区分）：404 与不存在同一个 404；作答的 409 / 410 / 403 / 422 各有编码；其余与配置类失败 503 PRACTICE_UNAVAILABLE', () => {
  const code = (status: number, c: string | undefined, op: 'queue' | 'next' | 'answer' = 'answer') =>
    JSON.parse(fromFailure({ status, problem: c ? { code: c } : {} }, op).body).code as string;
  for (const op of ['queue', 'next', 'answer'] as const) {
    assert.equal(fromFailure({ status: 404, problem: { code: 'ENTRY_NOT_FOUND' } }, op).body, P.NOT_FOUND);
    assert.equal(fromFailure({ status: 400, problem: { code: 'SUBJECT_NOT_SUPPORTED' } }, op).body, P.NOT_FOUND);
    assert.equal(code(400, 'INVALID_REQUEST', op), 'PRACTICE_UNAVAILABLE', `${op}：兜底不是 422（不是学生能改的）`);
    assert.equal(code(409, 'SOMETHING_NEW', op), 'PRACTICE_UNAVAILABLE', `${op}：不认识的 409`);
    assert.equal(code(501, 'PRACTICE_UNAVAILABLE', op), 'PRACTICE_UNAVAILABLE');
    assert.equal(code(401, 'UNAUTHENTICATED', op), 'PRACTICE_UNAVAILABLE');
  }
  // 作答
  assert.equal(code(409, 'PRACTICE_NOT_DUE'), 'PRACTICE_NOT_DUE');
  assert.equal(code(409, 'TICKET_USED'), 'TICKET_USED');
  assert.equal(code(409, 'IDEMPOTENCY_CONFLICT'), 'TICKET_USED', '同一张票据换作答');
  assert.equal(code(409, 'IDEMPOTENCY_KEY_SEALED'), 'KEY_SEALED');
  assert.equal(code(410, 'TICKET_EXPIRED'), 'TICKET_EXPIRED');
  assert.equal(code(403, 'TICKET_INVALID'), 'TICKET_INVALID');
  assert.equal(code(422, 'INVALID_ANSWER'), 'INVALID_ANSWER');
  assert.equal(code(422, 'CONTENT_NOT_REGISTERED'), 'PRACTICE_UNAVAILABLE');
  // 取题：只有 PRACTICE_NOT_DUE 是 409；票据、作答类编码不会出现在取题上，出现了也是 503
  assert.equal(code(409, 'PRACTICE_NOT_DUE', 'next'), 'PRACTICE_NOT_DUE');
  for (const [s, c] of [[409, 'TICKET_USED'], [409, 'IDEMPOTENCY_CONFLICT'], [410, 'TICKET_EXPIRED'], [403, 'TICKET_INVALID'], [422, 'INVALID_ANSWER']] as const) {
    assert.equal(code(s, c, 'next'), 'PRACTICE_UNAVAILABLE', `取题 ${s} ${c}`);
    assert.equal(code(s, c, 'queue'), 'PRACTICE_UNAVAILABLE', `列表 ${s} ${c}`);
  }
  assert.equal(code(409, 'PRACTICE_NOT_DUE', 'queue'), 'PRACTICE_UNAVAILABLE');
});

test('重试：结果未知才重试；确定性拒绝（501、502 ADAPTER_RESULT_REJECTED）不重试；整体总时限 45 秒（剩余时间传给每次调用作超时上限）', async () => {
  assert.equal(RETRY_BUDGET_MS, 45_000);
  const run = async (outs: PracticeOutcome<unknown>[], perCallMs = 0, delays = [300, 1000, 2000]) => {
    let t = 0;
    const caps: number[] = [];
    const notes: string[] = [];
    let i = 0;
    // 假客户端：每次调用耗时 perCallMs，但不超过传进来的超时上限（与 httpPracticeLearning 的 AbortSignal.timeout 一致）
    const out = await withRetry({ retryDelaysMs: delays, monotonic: () => t, sleep: async (ms) => { t += ms; }, note: (e) => notes.push(e) },
      async (cap) => { caps.push(cap); t += Math.min(cap, perCallMs); return outs[Math.min(i++, outs.length - 1)]!; });
    return { out, calls: caps.length, caps, notes, elapsed: t };
  };
  const unknown: PracticeOutcome<unknown> = { kind: 'unknown', reason: '503 BUSY' };
  const ok: PracticeOutcome<unknown> = { kind: 'ok', status: 200, body: {}, replayed: false };
  assert.equal((await run([ok])).calls, 1);
  assert.equal((await run([unknown, unknown, ok])).calls, 3);
  assert.equal((await run([unknown])).calls, 4, '一直未知：首次 + 3 次重试');
  for (const reason of ['502 ADAPTER_RESULT_REJECTED', '501 PRACTICE_UNAVAILABLE', '501 PRESENTER_UNAVAILABLE']) {
    assert.equal((await run([{ kind: 'unknown', reason }, ok])).calls, 1, `${reason} 不重试`);
  }
  assert.equal((await run([{ kind: 'unknown', reason: '502' }, ok])).calls, 2, '没有编码的 502（代理）仍按结果未知重试');
  // 每次调用都慢（20 秒）：第一次 20 秒；等 0.3 秒，第二次上限 24.7 秒（用 20 秒）；等 1 秒，第三次上限 3.7 秒（超时）；之后没有时间了
  const slow = await run([unknown], 20_000);
  assert.deepEqual(slow.caps, [45_000, 24_700, 3_700]);
  assert.equal(slow.elapsed, 45_000, '总耗时不超过 45 秒');
  assert.ok(slow.notes.includes('learning_retry_budget_exhausted'));
  // 剩余时间连等一下都不够：不再调
  const late = await run([unknown], 44_800);
  assert.equal(late.calls, 1, '第一次用掉 44.8 秒，剩 0.2 秒不够等 0.3 秒：不再调');
  assert.ok(late.elapsed <= 45_000);
});

test('学科码：前端学科码对应学习服务学科码；英语等不在表里', () => {
  assert.deepEqual({ ...SUBJECTS }, { math: 'aya', politics: 'zhixing', chinese: 'wenli' });
  assert.equal(SUBJECTS['english'], undefined);
});

test('开关：缺省关闭；只认 practice；dev / test 以外设了就拒绝启动', () => {
  assert.equal(parsePracticeFeature('test', undefined), false);
  assert.equal(parsePracticeFeature('dev', ' '), false);
  assert.equal(parsePracticeFeature('test', 'practice'), true);
  assert.throws(() => parsePracticeFeature('test', '1'), /只认 practice/);
  for (const env of ['staging', 'pilot', 'production'] as const) {
    assert.throws(() => parsePracticeFeature(env, 'practice'), /只在 dev \/ test/);
    assert.equal(parsePracticeFeature(env, undefined), false);
  }
});

// ---------------------------------------------------------------- 客户端（本机假学习服务）
const TOKEN = `synthetic-bff-to-learning-${randomUUID()}`;
const BODY_KEYS: Record<string, string[]> = {
  '/internal/v1/practice/queue': ['student_ref', 'subject'],
  '/internal/v1/practice/next': ['entry_id', 'student_ref', 'subject'],
  '/internal/v1/practice/answer': ['answer', 'idempotency_key', 'student_ref', 'subject', 'ticket', 'usage_id'],
};
let server: Server;
let base = '';
const seen: { path: string; body: Record<string, unknown> }[] = [];
let reply: { status: number; body: unknown; headers?: Record<string, string> } = { status: 200, body: {} };

const read = async (req: IncomingMessage): Promise<string> => {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  return Buffer.concat(chunks).toString('utf8');
};

before(async () => {
  server = createServer((req, res) => {
    void read(req).then((text) => {
      // 按 practice-internal.v0.yaml：只认 BFF 令牌、只有 POST、请求体恰好是这些键、学生 ID 不进 URL
      if (req.headers.authorization !== `Bearer ${TOKEN}`) { res.writeHead(401).end('{}'); return; }
      const keys = BODY_KEYS[req.url ?? ''];
      const body = JSON.parse(text) as Record<string, unknown>;
      if (req.method !== 'POST' || !keys || JSON.stringify(Object.keys(body).sort()) !== JSON.stringify(keys)
          || req.headers['content-type'] !== 'application/json') {
        res.writeHead(400, { 'content-type': 'application/problem+json' }).end(JSON.stringify({ code: 'INVALID_REQUEST', retryable: false }));
        return;
      }
      seen.push({ path: req.url!, body });
      res.writeHead(reply.status, { 'content-type': 'application/json', ...(reply.headers ?? {}) }).end(JSON.stringify(reply.body));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => { await new Promise<void>((r) => server.close(() => r())); });

test('客户端：请求形状按服务目录接口定义（只 POST、只这些键、学生 ID 不进 URL、Bearer 令牌）；响应按 ok / failure / unknown 归类', async () => {
  const c = httpPracticeLearning({ baseUrl: `${base}/`, token: TOKEN, timeoutMs: 2000 });
  reply = { status: 200, body: { subject: 'aya', entries: [] } };
  assert.deepEqual(await c.queue(S, 'aya'), { kind: 'ok', status: 200, body: { subject: 'aya', entries: [] }, replayed: false });
  const e = randomUUID();
  await c.next(S, 'aya', e);
  const k = randomUUID();
  reply = { status: 200, body: { verdict: 'correct' }, headers: { 'idempotent-replayed': 'true' } };
  const a = await c.answer(S, 'aya', { ticket: LEARNING_TICKET, answer: { choice: ['A'] }, idempotency_key: k, usage_id: S2 });
  assert.equal(a.kind === 'ok' && a.replayed, true);
  assert.deepEqual(seen.map((x) => x.path), ['/internal/v1/practice/queue', '/internal/v1/practice/next', '/internal/v1/practice/answer']);
  assert.deepEqual(seen[1]!.body, { student_ref: S, subject: 'aya', entry_id: e });
  assert.deepEqual(seen[2]!.body, { student_ref: S, subject: 'aya', ticket: LEARNING_TICKET, answer: { choice: ['A'] }, idempotency_key: k, usage_id: S2 });
  for (const [status, body, kind] of [[409, { code: 'PRACTICE_NOT_DUE', retryable: false }, 'failure'], [410, { code: 'TICKET_EXPIRED' }, 'failure'],
                                       [503, { code: 'BUSY', retryable: true }, 'unknown'], [500, { code: 'INTERNAL', retryable: true }, 'unknown'],
                                       [409, { code: 'BUSY', retryable: true }, 'unknown'], [429, {}, 'unknown'], [404, { code: 'ENTRY_NOT_FOUND' }, 'failure']] as const) {
    reply = { status, body };
    assert.equal((await c.next(S, 'aya', e)).kind, kind, `${status} ${JSON.stringify(body)}`);
  }
  const wrong = httpPracticeLearning({ baseUrl: base, token: 'x'.repeat(40) });
  const w = await wrong.queue(S, 'aya');
  assert.equal(w.kind === 'failure' && w.status, 401);
  const down = httpPracticeLearning({ baseUrl: 'http://127.0.0.1:9', token: TOKEN, timeoutMs: 500 });
  assert.equal((await down.queue(S, 'aya')).kind, 'unknown', '连不上是结果未知');
});
