// 讲评课会话的单元测试（specs/stage-lesson-session ③b-4b；目录见 specs/lesson-catalog PR C）：开关解析、票据封装、请求体解析、计量指纹、
// 讲评课引用与目录的白名单投影、学习服务客户端的请求形状。
// 不连库；计量与路由在 tests/lesson.integration.test.ts。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { test } from 'node:test';
import { parseLessonFeature } from '../src/lesson/features.ts';
import { httpLessonLearning } from '../src/lesson/learning-client.ts';
import { parseLessonRef } from '../src/lesson/ref.ts';
import { lessonFingerprint, parseAsk, parseOpen, pickCatalog } from '../src/lesson/service.ts';
import { LESSON_SEALED_PREFIX, MAX_SEALED, openLessonTicket, sealLessonTicket } from '../src/lesson/ticket.ts';
import { sealTicket } from '../src/practice/ticket.ts';

const S = randomUUID();
const OPEN = { subject: 'aya', lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }, idempotency_key: randomUUID() };
const Q = { cursor: 2, anchor: { kind: 'element', id: 't1' }, intent: 'what_is' };

test('开关：只认 lesson，只在 dev / test；staging、pilot、生产设了都拒绝；没设是关', () => {
  assert.equal(parseLessonFeature('test', undefined), false);
  assert.equal(parseLessonFeature('dev', ' '), false);
  assert.equal(parseLessonFeature('test', 'lesson'), true);
  assert.throws(() => parseLessonFeature('test', 'lessons'), /STARBUDDY_LESSON/);
  for (const env of ['staging', 'pilot', 'production'] as const) assert.throws(() => parseLessonFeature(env, 'lesson'), /STARBUDDY_LESSON/);
});

test('票据：往返一致；换学生、换密钥、篡改、再练票据、超长一律解不开', () => {
  const key = randomBytes(32);
  const content = { ticket: `lt1.${'x'.repeat(40)}.sig`, sessionId: randomUUID(), expiresAt: new Date().toISOString() };
  const sealed = sealLessonTicket(key, S, content);
  assert.ok(sealed.startsWith(LESSON_SEALED_PREFIX) && sealed.length <= MAX_SEALED);
  assert.deepEqual(openLessonTicket(key, S, sealed), content);
  assert.ok(!sealed.includes('lt1') && !sealed.includes(content.sessionId), '不透明');
  assert.equal(openLessonTicket(key, randomUUID(), sealed), null);
  assert.equal(openLessonTicket(randomBytes(32), S, sealed), null);
  assert.equal(openLessonTicket(key, S, sealed.slice(0, -2) + (sealed.endsWith('A') ? 'BB' : 'AA')), null);
  const practice = sealTicket(key, S, { ticket: 'pt1.a.b', subject: 'aya', entryId: randomUUID(), expiresAt: new Date().toISOString(), format: 'fill',
                                         optionKeys: [] });
  assert.equal(openLessonTicket(key, S, practice), null);
  assert.equal(openLessonTicket(key, S, `${LESSON_SEALED_PREFIX}${'A'.repeat(MAX_SEALED)}`), null);
  assert.equal(openLessonTicket(key, S, 42), null);
  // 一张 1024 字符的学习服务票据封装后仍在上限内
  assert.ok(sealLessonTicket(key, S, { ...content, ticket: 'l'.repeat(1024) }).length <= MAX_SEALED);
});

test('打开的请求体：只收学科、讲评课引用与幂等键；节点与题不再收（学习服务按目录补全）', () => {
  assert.deepEqual(parseOpen({ ...OPEN }), OPEN);
  for (const bad of [{ ...OPEN, subject: 'math' }, { ...OPEN, lesson_ref: { lesson_id: 'Bad', lesson_version: '0.1.0' } },
                     { ...OPEN, lesson_ref: { lesson_id: 'a', lesson_version: 'v1' } }, { ...OPEN, lesson_ref: { lesson_id: 'a', lesson_version: '0.1.0', x: 1 } },
                     { ...OPEN, skill_node: 'aya:K-QF-0301' }, { ...OPEN, item_ref: 'aya:SYN-1' }, { ...OPEN, idempotency_key: randomUUID().toUpperCase() },
                     { ...OPEN, dsl: {} }, { ...OPEN, answer_submitted: true }, { subject: 'aya', idempotency_key: randomUUID() }]) {
    assert.equal(parseOpen(bad as Record<string, unknown>), null, JSON.stringify(bad));
  }
});

test('提问的请求体：只收票据、结构化提问（不收自由文字与“再讲一遍”）与幂等键', () => {
  const ok = { ticket: 'sbl1.x', question: Q, idempotency_key: randomUUID() };
  assert.deepEqual(parseAsk(ok), ok);
  for (const q of [{ ...Q, intent: 'again' }, { ...Q, cursor: 0 }, { ...Q, cursor: 101 }, { ...Q, cursor: 1.5 }, { ...Q, anchor: { kind: 'xy', id: 't1' } },
                   { ...Q, anchor: { kind: 'step', id: 'S1' } }, { ...Q, text: '（合成）自由文字' }, { ...Q, anchor: { kind: 'step', id: 's1', x: 1 } }]) {
    assert.equal(parseAsk({ ...ok, question: q }), null, JSON.stringify(q));
  }
  assert.equal(parseAsk({ ...ok, ticket: '' }), null);
  assert.equal(parseAsk({ ...ok, idempotency_key: 'k' }), null);
  assert.equal(parseAsk({ ...ok, lesson_ref: {} }), null);
});

test('计量指纹：同一节课同一指纹；换课、换版本、换学生都不同；幂等键不进指纹', () => {
  const o = parseOpen({ ...OPEN })!;
  const fp = lessonFingerprint(S, o);
  assert.match(fp, /^[0-9a-f]{64}$/);
  assert.equal(lessonFingerprint(S, { ...o, idempotency_key: randomUUID() }), fp);
  for (const other of [{ ...o, lesson_ref: { lesson_id: 'syn.lesson.b', lesson_version: '0.1.0' } },
                       { ...o, lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.2.0' } }]) {
    assert.notEqual(lessonFingerprint(S, other), fp);
  }
  assert.notEqual(lessonFingerprint(randomUUID(), o), fp);
});

test('讲评课引用：只收编号与版本两个键，格式不对一律不收', () => {
  assert.deepEqual(parseLessonRef({ lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }), { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' });
  for (const bad of [null, [], 'syn.lesson.a', {}, { lesson_id: 'syn.lesson.a' }, { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0', item_ref: 'aya:SYN-1' },
                     { lesson_id: 'Syn', lesson_version: '0.1.0' }, { lesson_id: 'syn', lesson_version: '0.1' }, { lesson_id: 1, lesson_version: '0.1.0' }]) {
    assert.equal(parseLessonRef(bad), null, JSON.stringify(bad));
  }
});

test('目录投影：逐条只留编号、版本、标题与步数；学科不对、任何一条不合格整份不给', () => {
  const row = { lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }, title: '（合成）讲评课', step_count: 3, skill_node: 'aya:K-1', item_ref: 'aya:I-1' };
  assert.deepEqual(pickCatalog({ subject: 'aya', lessons: [row] }, 'aya'),
                   [{ lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }, title: '（合成）讲评课', step_count: 3 }]);
  assert.deepEqual(pickCatalog({ subject: 'aya', lessons: [] }, 'aya'), []);
  assert.ok(pickCatalog({ subject: 'aya', lessons: [{ ...row, title: '😀'.repeat(40) }] }, 'aya'), '按字符数而不是 UTF-16 码元数');
  for (const bad of [null, { subject: 'zhixing', lessons: [row] }, { subject: 'aya' }, { subject: 'aya', lessons: {} }, { subject: 'aya', lessons: [null] },
                     { subject: 'aya', lessons: [row, { ...row, title: ' ' }] }, { subject: 'aya', lessons: [{ ...row, title: 'x'.repeat(41) }] },
                     { subject: 'aya', lessons: [{ ...row, step_count: 1.5 }] }, { subject: 'aya', lessons: [{ ...row, lesson_ref: undefined }] }]) {
    assert.equal(pickCatalog(bad, 'aya'), null, JSON.stringify(bad));
  }
});

test('学习服务客户端：会话令牌、请求体原样、学生不进 URL、不跟随重定向；5xx 与 retryable 是结果未知，4xx 是确定失败', async () => {
  const seen: { url: string; init: RequestInit }[] = [];
  let reply = new Response(JSON.stringify({ outcome: 'answered', text: 'x', question_seq: 1, questions_left: 19 }), { status: 200 });
  const client = httpLessonLearning({ baseUrl: 'http://learning.test/', token: 'synthetic-session-token', fetchImpl: async (url, init) => {
    seen.push({ url: String(url), init: init! });
    return reply;
  } });
  const body = { student_ref: S, ticket: 'lt1.a.b', question: Q, idempotency_key: randomUUID() };
  const r = await client.ask(body);
  assert.equal(r.kind, 'ok');
  assert.equal(seen[0]!.url, 'http://learning.test/internal/v1/lesson/ask');
  assert.equal((seen[0]!.init.headers as Record<string, string>)['authorization'], 'Bearer synthetic-session-token');
  assert.equal(seen[0]!.init.redirect, 'error');
  assert.deepEqual(JSON.parse(String(seen[0]!.init.body)), body);
  assert.ok(!seen[0]!.url.includes(S));
  reply = new Response(JSON.stringify({ code: 'ADAPTER_RESULT_REJECTED', retryable: false }), { status: 502 });
  assert.equal((await client.open({ ...OPEN, student_ref: S, usage_id: randomUUID() })).kind, 'unknown');
  assert.equal(seen[1]!.url, 'http://learning.test/internal/v1/lesson/open');
  reply = new Response(JSON.stringify({ code: 'LESSON_NOT_AVAILABLE', retryable: false }), { status: 422 });
  const f = await client.open({ ...OPEN, student_ref: S, usage_id: randomUUID() });
  assert.equal(f.kind, 'failure');
  reply = new Response(JSON.stringify({ code: 'UPSTREAM_UNAVAILABLE', retryable: true }), { status: 409 });
  assert.equal((await client.ask(body)).kind, 'unknown');
  reply = new Response(JSON.stringify({ subject: 'aya', lessons: [] }), { status: 200 });
  const c = await client.catalog({ student_ref: S, subject: 'aya' });
  assert.equal(c.kind, 'ok');
  assert.equal(seen.at(-1)!.url, 'http://learning.test/internal/v1/lesson/catalog');
  assert.deepEqual(JSON.parse(String(seen.at(-1)!.init.body)), { student_ref: S, subject: 'aya' });
  assert.equal(seen.at(-1)!.init.method, 'POST');
});
