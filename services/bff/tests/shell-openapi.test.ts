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
import { TRUST_ROUTES } from '../src/http/trust-routes.ts';
import { operations } from './openapi-ops.ts';

// 路由表 + 信任界面路由（SIE-03 PR③，按开关注册；接口定义在 openapi/trust.v0.yaml）
const ALL = [...ROUTES, ...TRUST_ROUTES];

const SPECS = ['shell', 'star-map', 'onboarding', 'events', 'diagnosis', 'trust']
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
  const table = ALL.map((r) => `${r.method} ${PREFIX}${r.path}`).sort();
  assert.deepEqual(specOperations(), table);
});

test('信任界面：trust.v0.yaml 的操作集合恰好等于信任界面路由表（反向也成立：文档里没有表外操作，路由没有漏写文档；契约审查 L5）', () => {
  const doc = operationsOf(TRUST_SPEC).sort();
  const table = TRUST_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort();
  assert.deepEqual(doc, table);
  for (const op of doc) assert.ok(table.includes(op), `文档里的 ${op} 没有路由`);
  for (const op of table) assert.ok(doc.includes(op), `路由 ${op} 没有写进文档`);
});

test('路由表不变量：改变状态的都开 CSRF；公开的只有登录两条；带学生参数的都要会话', () => {
  for (const r of ALL) {
    if (r.method !== 'GET') assert.notEqual(r.csrf, 'none', `${r.method} ${r.path}`);
    if (r.auth !== 'public' && r.method !== 'GET') assert.equal(r.csrf, 'session', `${r.method} ${r.path}`);
    if (r.path.includes('{student_id}')) assert.notEqual(r.auth, 'public', r.path);
  }
  assert.deepEqual(ALL.filter((r) => r.auth === 'public').map((r) => r.path).sort(), ['/auth/otp/request', '/auth/otp/verify']);
  for (const r of TRUST_ROUTES) {
    assert.ok(r.feature === 'dispute' || r.feature === 'reconcile' || r.feature === 'weekly' || r.feature === 'data_rights', `${r.method} ${r.path} 挂在开关上`);
  }
  const keys = ALL.map((r) => `${r.method} ${r.path}`);
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

// 信任界面（SIE-03 PR③，契约审查 L3）：接口定义里的状态码与问题码对得上路由表与 handler
const TRUST_SPEC = readFileSync(new URL('../openapi/trust.v0.yaml', import.meta.url), 'utf8');

function statusesOf(spec: string): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const end = spec.indexOf('\ncomponents:');
  let path = '';
  let op = '';
  for (const line of spec.slice(spec.indexOf('\npaths:'), end).split('\n')) {
    const p = /^ {2}(\/\S+):\s*$/.exec(line);
    if (p) { path = p[1]!; continue; }
    const m = /^ {4}(get|post|put):\s*$/.exec(line);
    if (m) { op = `${m[1]!.toUpperCase()} ${path}`; out.set(op, new Set()); continue; }
    const s = /^ {8}'([0-9]{3})':/.exec(line);
    if (s && op) out.get(op)!.add(s[1]!);
  }
  return out;
}

test('信任界面接口定义：handler 用到的问题码都在枚举里；每条路由列出会话 401、CSRF 403、账号类型 403、限流 429', async () => {
  const { P } = await import('../src/http/problems.ts');
  const src = readFileSync(new URL('../src/http/trust-routes.ts', import.meta.url), 'utf8');
  const used = new Set([...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!));
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(TRUST_SPEC)![1]!;
  const statuses = statusesOf(TRUST_SPEC);
  const all = new Set([...statuses.values()].flatMap((x) => [...x]));
  for (const k of used) {
    const body = JSON.parse((P as Record<string, string>)[k]!) as { code: string; status: number };
    assert.ok(enumText.includes(body.code), `${k} → ${body.code} 不在 trust.v0.yaml 的问题码枚举里`);
    assert.ok(all.has(String(body.status)), `${k} 的状态码 ${body.status} 没有出现在任何接口里`);
  }
  for (const r of TRUST_ROUTES) {
    const s = statuses.get(`${r.method} ${PREFIX}${r.path}`)!;
    assert.ok(s.has('401') && s.has('404'), `${r.method} ${r.path}`);
    if (r.auth === 'guardian' || r.method !== 'GET') assert.ok(s.has('403'), `${r.method} ${r.path} 403`);
    if (r.rateLimits.length > 0) assert.ok(s.has('429'), `${r.method} ${r.path} 429`);
    if (r.body) assert.ok(s.has('422'), `${r.method} ${r.path} 422`);
  }
  // 考试录入与成绩：库里的唯一约束冲突映射 409
  for (const p of ['/students/{student_id}/exams', '/students/{student_id}/exams/{exam_id}/score']) {
    assert.ok(statuses.get(`POST ${PREFIX}${p}`)!.has('409'), p);
  }
});

// 4-H H6（契约审查 M1–M3）：每个操作在接口定义里写明鉴权口径（x-sb-auth）与限流桶（x-sb-rate-buckets），与路由表一致；
// 只认学生本人的路由写明“不是本人”的状态码（x-sb-not-self，授权矩阵按它断言）；状态码从路由表推出，六份接口定义都要列全。
// x-sb-auth 改动是鉴权口径变更，属必停项（lessons H6）。
test('接口定义的 x-sb-auth、x-sb-rate-buckets、x-sb-not-self 与路由表一致（六份 yaml 每个操作都写）', () => {
  const ops = operations();
  for (const r of ALL) {
    const k = `${r.method} ${PREFIX}${r.path}`;
    const op = ops.get(k);
    assert.ok(op, `${k} 不在接口定义里`);
    assert.equal(op.auth, r.auth, `${k} x-sb-auth（${op.file}）`);
    assert.deepEqual(op.buckets, r.rateLimits.map((l) => l.bucket), `${k} x-sb-rate-buckets（${op.file}）`);
    const studentOnly = r.auth === 'student' && r.path.includes('{student_id}');
    assert.equal(op.notSelf !== null, studentOnly, `${k} x-sb-not-self 只在只认学生本人的路由上写`);
    if (op.notSelf !== null) assert.ok(op.notSelf === 403 || op.notSelf === 404, k);
  }
});

test('接口定义的状态码从路由表推出（六份 yaml）：要会话 401；账号类型限定或改变状态 403；带路径参数 404；挂限流桶 429；读请求体 422', () => {
  const ops = operations();
  for (const r of ALL) {
    const k = `${r.method} ${PREFIX}${r.path}`;
    const s = ops.get(k)!.statuses;
    if (r.auth !== 'public') assert.ok(s.has('401'), `${k} 401`);
    if (r.auth === 'guardian' || r.auth === 'student' || r.csrf !== 'none') assert.ok(s.has('403'), `${k} 403`);
    if (r.path.includes('{')) assert.ok(s.has('404'), `${k} 404`);
    if (r.rateLimits.length > 0) assert.ok(s.has('429'), `${k} 429`);
    if (r.body) assert.ok(s.has('422'), `${k} 422`);
  }
});
