// 学习服务响应的三分类：成功 / 确定失败 / 结果未知（learning-internal.v0 的重试约定）
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';
import { classifyResponse, httpLearningClient } from '../src/metering/learning-client.ts';

test('按 status 与 Problem.retryable 分类', () => {
  assert.equal(classifyResponse(200, { verdict: 'correct' }).kind, 'success');
  assert.equal(classifyResponse(422, { code: 'INVALID_REQUEST', retryable: false }).kind, 'failure');
  assert.equal(classifyResponse(503, { code: 'UPSTREAM_UNAVAILABLE', retryable: true }).kind, 'unknown');
  assert.equal(classifyResponse(500, { code: 'INTERNAL', retryable: false }).kind, 'failure');
  assert.equal(classifyResponse(502, null).kind, 'unknown');
  assert.equal(classifyResponse(429, null).kind, 'unknown');
  assert.equal(classifyResponse(404, null).kind, 'failure');
});

test('HTTP 客户端：带令牌、同一请求体；超时与断连算结果未知', async () => {
  const seen: { auth: string | undefined; body: string }[] = [];
  let mode = 'ok';
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ auth: req.headers.authorization, body });
      if (mode === 'ok') { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"verdict":"correct"}'); }
      else if (mode === 'reject') { res.writeHead(422, { 'content-type': 'application/problem+json' }); res.end('{"code":"INVALID_REQUEST","retryable":false}'); }
      else if (mode === 'drop') { req.socket.destroy(); }
      // mode === 'hang'：不响应
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const call = httpLearningClient({ baseUrl: `http://127.0.0.1:${port}/`, token: 'synthetic-token', timeoutMs: { grade: 200 } });
  try {
    assert.equal((await call('grade', { a: 1 })).kind, 'success');
    assert.equal(seen[0]?.auth, 'Bearer synthetic-token');
    assert.equal(seen[0]?.body, '{"a":1}');
    mode = 'reject';
    assert.equal((await call('grade', { a: 1 })).kind, 'failure');
    mode = 'drop';
    assert.equal((await call('grade', { a: 1 })).kind, 'unknown');
    mode = 'hang';
    assert.equal((await call('grade', { a: 1 })).kind, 'unknown');
  } finally {
    server.closeAllConnections();
    server.close();
  }
});
