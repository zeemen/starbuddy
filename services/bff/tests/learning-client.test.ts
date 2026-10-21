// 学习服务响应的三分类：成功 / 确定失败 / 结果未知（learning-internal 的重试约定；LT-04 起写入走 v1 信封）
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';
import { classifyReplay, classifyResponse, httpLearningClient, httpLearningReplay } from '../src/metering/learning-client.ts';

test('按 status 与 Problem.retryable 分类', () => {
  assert.equal(classifyResponse(200, { verdict: 'correct' }).kind, 'success');
  assert.equal(classifyResponse(422, { code: 'INVALID_REQUEST', retryable: false }).kind, 'failure');
  assert.equal(classifyResponse(503, { code: 'UPSTREAM_UNAVAILABLE', retryable: true }).kind, 'unknown');
  // 5xx 即使写着 retryable = false 也算结果未知：学习服务提交阶段出错时事务可能已经提交（契约守护 H2）
  assert.equal(classifyResponse(500, { code: 'INTERNAL', retryable: false }).kind, 'unknown');
  assert.equal(classifyResponse(409, { code: 'IDEMPOTENCY_CONFLICT', retryable: false }).kind, 'failure');
  // LT-04：计量核对不通过（该幂等键无有效计量）是确定失败，BFF 释放预留
  assert.equal(classifyResponse(409, { code: 'IDEMPOTENCY_KEY_SEALED', retryable: false }).kind, 'failure');
  const ok = classifyResponse(200, {}, true);
  assert.ok(ok.kind === 'success' && ok.replayed);
  assert.equal(classifyResponse(502, null).kind, 'unknown');
  assert.equal(classifyResponse(429, null).kind, 'unknown');
  assert.equal(classifyResponse(404, null).kind, 'failure');
});

test('HTTP 客户端：带令牌、发 learning-internal v1 信封；超时与断连算结果未知', async () => {
  const seen: { auth: string | undefined; body: string; url: string | undefined }[] = [];
  let mode = 'ok';
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ auth: req.headers.authorization, body, url: req.url });
      if (mode === 'ok') { res.writeHead(200, { 'content-type': 'application/json', 'idempotent-replayed': 'true' }); res.end('{"verdict":"correct"}'); }
      else if (mode === 'redirect') { res.writeHead(307, { location: '/elsewhere' }); res.end(); }
      else if (mode === 'reject') { res.writeHead(422, { 'content-type': 'application/problem+json' }); res.end('{"code":"INVALID_REQUEST","retryable":false}'); }
      else if (mode === 'drop') { req.socket.destroy(); }
      // mode === 'hang'：不响应
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const call = httpLearningClient({ baseUrl: `http://127.0.0.1:${port}/`, token: 'synthetic-token', timeoutMs: { grade: 200 } });
  try {
    const first = await call('grade', { a: 1 });
    assert.ok(first.kind === 'success' && first.replayed, '读取 idempotent-replayed 响应头');
    assert.equal(seen[0]?.auth, 'Bearer synthetic-token');
    // LT-04：写入切到 /internal/v1，请求体是信封 {request, ingest}；request 的契约版本写成 1.0.0，ingest 只有键盘通道（用途与等级由学习服务推导）
    assert.equal(seen[0]?.url, '/internal/v1/grade');
    assert.deepEqual(JSON.parse(seen[0]?.body ?? ''), { request: { a: 1, context: { contract_version: '1.0.0' } }, ingest: { channel: 'keypad' } });
    const body: { context: Record<string, unknown>; attempt: unknown } = {
      context: { request_id: 'r', student_ref: 's', usage_id: 'u', idempotency_key: 'k' }, attempt: { x: 1 } };
    await call('diagnose', body);
    assert.equal(seen[1]?.url, '/internal/v1/diagnose');
    assert.deepEqual(JSON.parse(seen[1]?.body ?? ''), {
      request: { context: { request_id: 'r', contract_version: '1.0.0', student_ref: 's', usage_id: 'u', idempotency_key: 'k' }, attempt: { x: 1 } },
      ingest: { channel: 'keypad' } }, '其余字段原样转发，契约版本缺省时补 1.0.0；不带 purpose、evidence_level');
    assert.equal(body.context['contract_version'], undefined, '不改调用方的对象（计量指纹在预留前按它算，算法不变）');
    // 调用方显式给 1.0.0：原样发出
    await call('grade', { context: { contract_version: '1.0.0' }, a: 2 });
    assert.deepEqual(JSON.parse(seen[2]?.body ?? '').request, { context: { contract_version: '1.0.0' }, a: 2 });
    // 调用方给了别的版本（旧的 0.9.0、候选版 1.1.0、非字符串）：抛 TypeError，不发请求，不悄悄改写成 1.0.0
    for (const v of ['0.9.0', '1.1.0', '', 1, null]) {
      await assert.rejects(call('grade', { context: { contract_version: v }, a: 3 }), TypeError, String(v));
    }
    assert.equal(seen.length, 3, '版本不对的请求一个都没有发出去');
    mode = 'reject';
    assert.equal((await call('grade', { a: 1 })).kind, 'failure');
    mode = 'redirect';
    assert.equal((await call('grade', { a: 1 })).kind, 'unknown', '不跟随重定向');
    mode = 'drop';
    assert.equal((await call('grade', { a: 1 })).kind, 'unknown');
    mode = 'hang';
    assert.equal((await call('grade', { a: 1 })).kind, 'unknown');
  } finally {
    server.closeAllConnections();
    server.close();
  }
});

test('重放响应分类：只有带 REPLAY_NOT_FOUND 的 404 才算已封存', () => {
  assert.deepEqual(classifyReplay(200, { verdict: 'correct' }), { kind: 'found', body: { verdict: 'correct' } });
  assert.equal(classifyReplay(404, { code: 'REPLAY_NOT_FOUND', retryable: false }).kind, 'not_found');
  assert.equal(classifyReplay(409, { code: 'BUSY', retryable: true }).kind, 'busy');
  assert.equal(classifyReplay(404, null).kind, 'unknown', '路由不存在的 404 不能当作已封存');
  assert.equal(classifyReplay(404, { code: 'NOT_FOUND' }).kind, 'unknown');
  assert.equal(classifyReplay(400, { code: 'INVALID_REQUEST' }).kind, 'unknown');
  assert.equal(classifyReplay(401, { code: 'UNAUTHENTICATED' }).kind, 'unknown');
  assert.equal(classifyReplay(500, { code: 'INTERNAL', retryable: true }).kind, 'unknown');
});

test('重放 HTTP 客户端：GET、带令牌、查询参数；断连算结果未知', async () => {
  const seen: { method: string | undefined; url: string | undefined; auth: string | undefined }[] = [];
  let mode = 'found';
  const server = createServer((req, res) => {
    seen.push({ method: req.method, url: req.url, auth: req.headers.authorization });
    if (mode === 'found') { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"verdict":"correct"}'); }
    else if (mode === 'sealed') { res.writeHead(404, { 'content-type': 'application/problem+json' }); res.end('{"code":"REPLAY_NOT_FOUND","retryable":false}'); }
    else if (mode === 'text') { res.writeHead(502); res.end('bad gateway'); }
    else { req.socket.destroy(); }
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const replay = httpLearningReplay({ baseUrl: `http://127.0.0.1:${port}/`, token: 'synthetic-token' });
  const key = '00000000-0000-4000-8000-0000000000b1';
  const student = '00000000-0000-4000-8000-0000000000c1';
  const usage = '00000000-0000-4000-8000-0000000000d1';
  try {
    assert.deepEqual(await replay('grade', key, student, usage), { kind: 'found', body: { verdict: 'correct' } });
    assert.deepEqual(seen[0], { method: 'GET', url: `/internal/v1/replay/grade/${key}?student_ref=${student}&usage_id=${usage}`,
                                auth: 'Bearer synthetic-token' });
    mode = 'sealed';
    assert.equal((await replay('grade', key, student, usage)).kind, 'not_found');
    mode = 'text';
    assert.equal((await replay('grade', key, student, usage)).kind, 'unknown');
    mode = 'drop';
    assert.equal((await replay('grade', key, student, usage)).kind, 'unknown');
  } finally {
    server.closeAllConnections();
    server.close();
  }
});
