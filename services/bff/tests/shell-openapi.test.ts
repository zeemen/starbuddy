// 统一壳路由表与 openapi/shell.v0.yaml 一致（方法 + 路径一一对应），且路由表本身守住几条不变量：
// 改变状态的路由都开 CSRF、公开路由只有登录两条、只有 GET 可以不开 CSRF、带学生参数的路由不是 public。
// 不引入 YAML 解析依赖：只按缩进取 paths 下的路径与方法。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { compile, PREFIX, ROUTES } from '../src/http/routes.ts';

const SPEC = readFileSync(new URL('../openapi/shell.v0.yaml', import.meta.url), 'utf8');

function specOperations(): string[] {
  const ops: string[] = [];
  const section = SPEC.slice(SPEC.indexOf('\npaths:'), SPEC.indexOf('\ncomponents:'));
  let path = '';
  for (const line of section.split('\n')) {
    const p = /^ {2}(\/\S+):\s*$/.exec(line);
    if (p) { path = p[1]!; continue; }
    const m = /^ {4}(get|post|put|delete|patch):\s*$/.exec(line);
    if (m && path) ops.push(`${m[1]!.toUpperCase()} ${path}`);
  }
  return ops.sort();
}

test('路由表与 openapi/shell.v0.yaml 一一对应', () => {
  const table = ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort();
  assert.deepEqual(specOperations(), table);
});

test('路由表不变量：改变状态的都开 CSRF；公开的只有登录两条；带学生参数的都要会话', () => {
  for (const r of ROUTES) {
    if (r.method !== 'GET') assert.notEqual(r.csrf, 'none', `${r.method} ${r.path}`);
    if (r.auth !== 'public' && r.method !== 'GET') assert.equal(r.csrf, 'session', `${r.method} ${r.path}`);
    if (r.path.includes('{student_id}')) assert.notEqual(r.auth, 'public', r.path);
  }
  assert.deepEqual(ROUTES.filter((r) => r.auth === 'public').map((r) => r.path).sort(), ['/auth/otp/request', '/auth/otp/verify']);
  const keys = ROUTES.map((r) => `${r.method} ${r.path}`);
  assert.equal(new Set(keys).size, keys.length, '没有重复路由');
});

test('路径参数只匹配 UUID', () => {
  const c = compile().find((x) => x.route.path === '/students/{student_id}/consents')!;
  assert.ok(c.re.test(`${PREFIX}/students/7b000000-0000-4000-8000-0000000000c1/consents`));
  assert.ok(!c.re.test(`${PREFIX}/students/..%2Fx/consents`));
  assert.ok(!c.re.test(`${PREFIX}/students/7B000000-0000-4000-8000-0000000000C1/consents`));
});
