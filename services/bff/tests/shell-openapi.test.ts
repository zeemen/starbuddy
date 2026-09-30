// 统一壳路由表与 openapi/*.v0.yaml 一致（方法 + 路径一一对应；H2 起星图、建档、事件、诊断也挂在路由表上，接口定义仍在各自文件里），
// 且路由表本身守住几条不变量：
// 改变状态的路由都开 CSRF、公开路由只有登录两条、只有 GET 可以不开 CSRF、带学生参数的路由不是 public。
// 不引入 YAML 解析依赖：只按缩进取 paths 下的路径与方法。
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import { readFileSync } from 'node:fs';
const require_fs = () => fs;
import { test } from 'node:test';
import { compile, PREFIX, ROUTES } from '../src/http/routes.ts';

const SPECS = ['shell', 'star-map', 'onboarding', 'events', 'diagnosis']
  .map((n) => readFileSync(new URL(`../openapi/${n}.v0.yaml`, import.meta.url), 'utf8'));

function specOperations(): string[] {
  return SPECS.flatMap(operationsOf).sort();
}

function operationsOf(SPEC: string): string[] {
  const ops: string[] = [];
  const end = SPEC.indexOf('\ncomponents:');
  const section = SPEC.slice(SPEC.indexOf('\npaths:'), end < 0 ? undefined : end);
  let path = '';
  for (const line of section.split('\n')) {
    const p = /^ {2}(\/\S+):\s*$/.exec(line);
    if (p) { path = p[1]!; continue; }
    const m = /^ {4}(get|post|put|delete|patch):\s*$/.exec(line);
    if (m && path) ops.push(`${m[1]!.toUpperCase()} ${path}`);
  }
  return ops;
}

test('路由表与 openapi/*.v0.yaml 一一对应（没有表外的接口，也没有没写进接口定义的路由）', () => {
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

test('开发身份桩已删除：源码与接口定义里没有 x-dev-account-id、STARBUDDY_DEV_AUTH、devAccount', () => {
  const { readdirSync, statSync } = require_fs();
  const walk = (dir: URL): string[] => readdirSync(dir).flatMap((n: string) => {
    const u = new URL(n, dir);
    return statSync(u).isDirectory() ? walk(new URL(`${n}/`, dir)) : [u.pathname];
  });
  const files = [...walk(new URL('../src/', import.meta.url)), ...walk(new URL('../openapi/', import.meta.url))];
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    for (const banned of ['x-dev-account-id', 'STARBUDDY_DEV_AUTH', 'devAccount', 'STARBUDDY_DEV_METER_CLOCK']) {
      assert.ok(!text.includes(banned), `${f} 含 ${banned}`);
    }
  }
});
