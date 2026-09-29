// 审核台前端单测（不需要浏览器）：后端地址只允许 loopback、表单校验、开发 staff 桩只认合成 staff 且与学生身份分开。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { SYNTHETIC_ACCOUNTS } from '../src/dev/accounts.ts';
import { SYNTHETIC_STAFF } from '../src/dev/staff.ts';
import { devAccountFromCookie, devStaffFromCookie } from '../src/lib/dev-auth.ts';
import { isBasis, isDigest, isNodeId, isSubject, isVersionId, lines, RESULT_LABEL, reviewBaseUrl } from '../src/lib/review.ts';

const on = { STARBUDDY_DEV_AUTH: '1' };

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

test('开发 staff 桩：只认合成 staff，开关关闭时无效；学生、家长与 staff 身份互不通用', () => {
  const staff = SYNTHETIC_STAFF[0]!.account_id;
  const student = SYNTHETIC_ACCOUNTS[0]!.account_id;
  assert.equal(devStaffFromCookie(staff, on), staff);
  assert.equal(devStaffFromCookie(staff, {}), null);
  assert.equal(devStaffFromCookie(student, on), null);
  assert.equal(devAccountFromCookie(staff, on), null);
  assert.equal(devStaffFromCookie('not-a-uuid', on), null);
});

test('合成 staff 与后端开发身份桩名单一致', () => {
  const py = readFileSync(new URL('../../../services/learning/review_console/devauth.py', import.meta.url), 'utf8');
  const ids = [...py.matchAll(/uuid\.UUID\("([0-9a-f-]{36})"\)/g)].map((m) => m[1]).sort();
  assert.deepEqual(ids, SYNTHETIC_STAFF.map((s) => s.account_id).sort());
});

test('结果提示覆盖后端的全部错误码', () => {
  for (const code of ['DIGEST_CHANGED', 'RULE_VIOLATION', 'FORBIDDEN', 'NOT_FOUND', 'INVALID_PARAMETER', 'UNAUTHENTICATED', 'CONFLICT', 'INVALID_REFERENCE', 'INTERNAL']) {
    assert.ok(Object.hasOwn(RESULT_LABEL, code), code);
  }
});
