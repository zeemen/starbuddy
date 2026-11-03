// 数学自适应诊断（SIE-02a）BFF 的单测：请求体白名单与前端视图裁剪（不连库）。全部合成数据。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { classify } from '../src/diagnosis/learning-client.ts';
import { parseAbandon, parseAnswer, parsePause, parseProbe, parseStart, probeKeyFor, publicView } from '../src/diagnosis/service.ts';

const k = () => randomUUID();

test('开始：只收 subject = aya 与小写 UUIDv4 幂等键，多一个键都不收', () => {
  assert.deepEqual(parseStart({ subject: 'aya', idempotency_key: '0b9c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3' }),
                   { subject: 'aya', idempotency_key: '0b9c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3' });
  for (const b of [{ subject: 'wenli', idempotency_key: k() }, { subject: 'aya' }, { subject: 'aya', idempotency_key: 'x' },
                   { subject: 'aya', idempotency_key: k().toUpperCase() }, { subject: 'aya', idempotency_key: k(), grade: 9 }]) {
    assert.equal(parseStart(b as Record<string, unknown>), null, JSON.stringify(b));
  }
});

test('作答：text 或 choice 二选一；过程说明是可选字符串；用时 0–7200 整数', () => {
  const ok = parseAnswer({ seq: 3, answer: { choice: ['A'] }, elapsed_sec: 40, idempotency_key: k() });
  assert.ok(ok && 'choice' in ok.answer && ok.work === undefined);
  assert.ok(parseAnswer({ seq: 3, answer: { text: '2,3' }, elapsed_sec: 0, idempotency_key: k(), work: '合成过程' })?.work);
  for (const b of [
    { seq: 3, answer: { choice: ['A'], text: '1' }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 3, answer: { choice: ['A', 'A'] }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 3, answer: { choice: [] }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 3, answer: { text: '' }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 3, answer: { text: 'x'.repeat(201) }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 0, answer: { text: '1' }, elapsed_sec: 40, idempotency_key: k() },
    { seq: 3, answer: { text: '1' }, elapsed_sec: 7201, idempotency_key: k() },
    { seq: 3, answer: { text: '1' }, elapsed_sec: 1.5, idempotency_key: k() },
    { seq: 3, answer: { text: '1' }, elapsed_sec: 5, idempotency_key: k(), work: '' },
    { seq: 3, answer: { text: '1' }, elapsed_sec: 5, idempotency_key: k(), verdict: 'correct' },
  ]) assert.equal(parseAnswer(b as Record<string, unknown>), null, JSON.stringify(b));
});

test('探针、暂停、放弃的请求体', () => {
  assert.ok(parseProbe({ seq: 1, prediction: 'unsure', asked_at: '2026-10-10T09:00:00+08:00', idempotency_key: k() }));
  assert.equal(parseProbe({ seq: 1, prediction: 'maybe', asked_at: '2026-10-10T09:00:00+08:00', idempotency_key: k() }), null);
  assert.equal(parseProbe({ seq: 1, prediction: 'yes', asked_at: '2026-10-10 09:00', idempotency_key: k() }), null);
  assert.deepEqual(parsePause({ idempotency_key: '0b9c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3' }),
                   { seq: null, step_elapsed_sec: null, idempotency_key: '0b9c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3' });
  assert.equal(parsePause({ idempotency_key: k(), seq: 2, step_elapsed_sec: -1 }), null);
  assert.ok(parseAbandon({ idempotency_key: k() }));
  assert.equal(parseAbandon({ idempotency_key: k(), reason: 'bored' }), null);
});

test('前端视图：去掉题号、额度占用、状态机内部状态；选项只留键与文字', () => {
  const v = publicView({
    session_id: 's', status: 'active', state: 'probing', usage_id: 'u', elapsed_sec: 30, time_budget_sec: 2100, item_count: 1, item_limit: 20,
    process_tasks: false,
    step: { seq: 2, kind: 'adaptive', subject: 'aya', item_id: 'Q-FUN-U01-T-002', probe_recorded: false, step_elapsed_sec: 0,
            item: { format: 'single', stem: '合成题面', options: [{ key: 'A', text: '甲', answer: true } as never] } },
  });
  const raw = JSON.stringify(v);
  assert.ok(!/Q-FUN|usage|probing|answer|subject/.test(raw), raw);
  assert.deepEqual((v['step'] as { item: { options: unknown[] } }).item.options, [{ key: 'A', text: '甲' }]);
  assert.equal(v['process_tasks'], false);
});

test('探针幂等键由（会话、序号）派生：同一步同一个键，换一步或换会话就不同（安全审查 S3）', () => {
  const s1 = randomUUID();
  assert.equal(probeKeyFor(s1, 3), probeKeyFor(s1, 3));
  assert.notEqual(probeKeyFor(s1, 3), probeKeyFor(s1, 4));
  assert.notEqual(probeKeyFor(s1, 3), probeKeyFor(randomUUID(), 3));
  assert.match(probeKeyFor(s1, 3), /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test('学习服务响应分类：5xx、429、retryable 是结果未知；4xx 是确定失败', () => {
  assert.equal(classify(500, { code: 'INTERNAL' }, false).kind, 'unknown');
  assert.equal(classify(503, { code: 'BUSY', retryable: true }, false).kind, 'unknown');
  assert.equal(classify(409, { code: 'STEP_NOT_CURRENT' }, false).kind, 'failure');
  assert.equal(classify(422, { code: 'INVALID_ANSWER', retryable: true }, false).kind, 'unknown');
  assert.equal(classify(200, {}, true).kind, 'ok');
});

test('OpenAPI 与代码一致：前端视图与作答响应的键', () => {
  const y = readFileSync(new URL('../openapi/diagnosis.v0.yaml', import.meta.url), 'utf8');
  assert.match(y, /required: \[session_id, status, elapsed_sec, time_budget_sec, item_count, item_limit, process_tasks, step\]/);
  assert.match(y, /reason = curfew/);
  assert.match(y, /required: \[seq, kind, item, probe_recorded, step_elapsed_sec\]/);
  assert.ok(!/item_id|node_id|reason_code|verdict/.test(y.split('components:')[1]!.split('responses:')[0]!.replace(/description:.*$/gm, '')));
});
