// 审核台前端单测（不需要浏览器）：后端地址只允许 loopback、表单校验、staff 会话 cookie、开发 staff 身份桩已删除。
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { isLoopbackHost } from '../src/lib/dev-auth.ts';
import { isBasis, isDigest, isNodeId, isSubject, isVersionId, lines, problemFrom, RESULT_LABEL, reviewBaseUrl } from '../src/lib/review.ts';
import { isSessionToken, staffCookieName, staffCookieOptions } from '../src/lib/staff-cookie.ts';

test('审核台后端地址只能是 loopback 上的 http', () => {
  assert.equal(reviewBaseUrl({}), 'http://127.0.0.1:8790');
  assert.equal(reviewBaseUrl({ STARBUDDY_REVIEW_URL: 'http://localhost:9000/x' }), 'http://localhost:9000');
  for (const bad of ['https://127.0.0.1:1', 'http://10.0.0.2:8790', 'http://review.example.test', 'file:///tmp/x']) {
    assert.throws(() => reviewBaseUrl({ STARBUDDY_REVIEW_URL: bad }), bad);
  }
});

test('表单字段校验', () => {
  assert.ok(isNodeId('MATH.FUNC.CON.K_FUN_0901') && isNodeId('CHN.RC.T.A'));
  assert.ok(!isNodeId('math.func') && !isNodeId('MATH') && !isNodeId('MATH.F/../x'));
  assert.ok(isDigest('a'.repeat(64)) && !isDigest('A'.repeat(64)) && !isDigest('a'.repeat(63)));
  assert.ok(isBasis('wrong_version') && !isBasis('Wrong Version') && !isBasis(''));
  assert.ok(isVersionId('c0100000-0000-4000-8000-0000000000c1') && !isVersionId('../records'));
  assert.ok(isSubject('aya') && !isSubject('echo') && !isSubject('english'));
  assert.deepEqual(lines(' a \n\n b\r\nc  '), ['a', 'b', 'c']);
});

test('staff 会话 cookie：正式环境用 __Host- 前缀且 Secure；本地开发与测试不带前缀；都是 httpOnly、SameSite=Strict、12 小时', () => {
  assert.equal(staffCookieName({}), '__Host-sb_staff');
  assert.equal(staffCookieName({ STARBUDDY_ENV: 'staging' }), '__Host-sb_staff');
  assert.equal(staffCookieName({ STARBUDDY_ENV: 'test' }), 'sb_staff');
  assert.deepEqual(staffCookieOptions({}), { httpOnly: true, sameSite: 'strict', path: '/', secure: true, maxAge: 43200 });
  assert.equal(staffCookieOptions({ STARBUDDY_ENV: 'dev' }).secure, false);
  assert.ok(isSessionToken('a'.repeat(64)) && !isSessionToken('A'.repeat(64)) && !isSessionToken('x') && !isSessionToken(undefined));
});

test('Host 必须是本机地址（开发身份桩用，挡 DNS 重绑定）', () => {
  for (const h of ['127.0.0.1', '127.0.0.1:3100', 'localhost:3100', '[::1]:3100']) assert.ok(isLoopbackHost(h), h);
  for (const h of ['evil.example.test:3100', '127.0.0.1.evil.test', 'localhost.evil.test:80', '', null, undefined]) {
    assert.ok(!isLoopbackHost(h), String(h));
  }
});

test('后端错误只对规则类透传说明，并截断', () => {
  assert.deepEqual(problemFrom(409, { code: 'RULE_VIOLATION', detail: 'x'.repeat(300) }), { status: 409, code: 'RULE_VIOLATION', detail: 'x'.repeat(200) });
  assert.deepEqual(problemFrom(403, { code: 'FORBIDDEN', detail: 'permission denied for table y' }), { status: 403, code: 'FORBIDDEN', detail: undefined });
  assert.deepEqual(problemFrom(500, { code: '<script>', detail: 'x' }), { status: 500, code: 'INTERNAL', detail: undefined });
  assert.deepEqual(problemFrom(502, null), { status: 502, code: 'INTERNAL', detail: undefined });
});

test('结果提示覆盖后端的全部错误码', () => {
  for (const code of ['DIGEST_CHANGED', 'RULE_VIOLATION', 'FORBIDDEN', 'NOT_FOUND', 'INVALID_PARAMETER', 'UNAUTHENTICATED', 'CONFLICT',
    'INVALID_REFERENCE', 'INTERNAL', 'LOGIN_FAILED']) {
    assert.ok(Object.hasOwn(RESULT_LABEL, code), code);
  }
});

test('开发 staff 身份桩已删除（前后端源码里都找不到）', () => {
  const root = new URL('../../../', import.meta.url).pathname;
  assert.ok(!existsSync(join(root, 'apps/web/src/dev/staff.ts')));
  assert.ok(!existsSync(join(root, 'services/learning/review_console/devauth.py')));
  const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return n === '__pycache__' ? [] : statSync(p).isDirectory() ? files(p) : [p];
  });
  for (const f of [...files(join(root, 'apps/web/src')), ...files(join(root, 'services/learning/review_console'))]) {
    const text = readFileSync(f, 'utf8');
    assert.ok(!/x-dev-staff-id|sb_dev_staff|SYNTHETIC_STAFF|REVIEW_CONSOLE_DEV_AUTH/.test(text), f);
  }
});
