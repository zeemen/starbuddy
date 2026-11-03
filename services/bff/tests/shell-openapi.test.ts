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
import { STAR_MAP_V1_ROUTES } from '../src/http/star-map-v1-routes.ts';
import { PRACTICE_ROUTES } from '../src/http/practice-routes.ts';
import { RETEST_ROUTES } from '../src/http/retest-routes.ts';
import { FAMILY_GOALS_ROUTES } from '../src/http/family-goals-routes.ts';
import { MONTHLY_PLAN_ROUTES } from '../src/http/monthly-plan-routes.ts';
import { ALLOW_AFTER_EXIT, INTERNAL_TEST_ROUTES } from '../src/http/internal-test-routes.ts';
import { operations, SPEC_FILES } from './openapi-ops.ts';

// 路由表 + 信任界面路由（SIE-03 PR③，按开关注册；接口定义在 openapi/trust.v0.yaml）+ 星图 v1（H5，开关 starmap_v1；openapi/star-map.v1.yaml）
// + 错题再练（开关 practice；openapi/practice.v0.yaml）+ 复测与验证题（开关 retest；openapi/retest.v0.yaml，KGE-05）
// + 家长诉求（开关 family_goals；openapi/family-goals.v0.yaml，PLT-14）+ 月度计划（开关 monthly_plan；openapi/monthly-plan.v0.yaml，PLT-14 第 3 步）
const ALL = [...ROUTES, ...TRUST_ROUTES, ...STAR_MAP_V1_ROUTES, ...PRACTICE_ROUTES, ...RETEST_ROUTES, ...FAMILY_GOALS_ROUTES, ...MONTHLY_PLAN_ROUTES,
             ...INTERNAL_TEST_ROUTES];

const SPECS = SPEC_FILES.map((n) => readFileSync(new URL(`../openapi/${n}.yaml`, import.meta.url), 'utf8'));

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
    assert.ok(r.feature === 'dispute' || r.feature === 'reconcile' || r.feature === 'weekly' || r.feature === 'data_rights' || r.feature === 'profile'
              || r.feature === 'report' || r.feature === 'why',
              `${r.method} ${r.path} 挂在开关上`);
  }
  const keys = ALL.map((r) => `${r.method} ${r.path}`);
  assert.equal(new Set(keys).size, keys.length, '没有重复路由');
});

test('星图 v1：star-map.v1.yaml 的操作集合恰好等于 v1 路由；v1 路由不在固定路由表里（只随开关 starmap_v1 注册）', () => {
  const doc = operationsOf(readFileSync(new URL('../openapi/star-map.v1.yaml', import.meta.url), 'utf8')).sort();
  assert.deepEqual(doc, STAR_MAP_V1_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of STAR_MAP_V1_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
});

test('错题再练：practice.v0.yaml 的操作集合恰好等于再练路由；再练路由不在固定路由表里（只随开关 practice 注册）；用到的问题码都在枚举里', async () => {
  const spec = readFileSync(new URL('../openapi/practice.v0.yaml', import.meta.url), 'utf8');
  assert.deepEqual(operationsOf(spec).sort(), PRACTICE_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of PRACTICE_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
  const src = readFileSync(new URL('../src/practice/service.ts', import.meta.url), 'utf8');
  const codes = new Set([...src.matchAll(/problem\(\d{3}, '([A-Z_]+)'/g)].map((m) => m[1]!));
  const { P } = await import('../src/http/problems.ts');
  for (const k of [...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!)) codes.add((JSON.parse((P as Record<string, string>)[k]!) as { code: string }).code);
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(spec)![1]!;
  assert.ok(codes.size >= 10, `问题码 ${[...codes].join(',')}`);
  for (const c of codes) assert.ok(enumText.includes(c), `${c} 不在 practice.v0.yaml 的问题码枚举里`);
});

test('复测与验证题：retest.v0.yaml 的操作集合恰好等于复测路由；复测路由不在固定路由表里（只随开关 retest 注册）；用到的问题码都在枚举里', async () => {
  const spec = readFileSync(new URL('../openapi/retest.v0.yaml', import.meta.url), 'utf8');
  assert.deepEqual(operationsOf(spec).sort(), RETEST_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of RETEST_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
  const src = readFileSync(new URL('../src/retest/service.ts', import.meta.url), 'utf8');
  const codes = new Set([...src.matchAll(/problem\(\d{3}, '([A-Z_]+)'/g)].map((m) => m[1]!));
  const { P } = await import('../src/http/problems.ts');
  for (const k of [...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!)) codes.add((JSON.parse((P as Record<string, string>)[k]!) as { code: string }).code);
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(spec)![1]!;
  assert.ok(codes.size >= 10, `问题码 ${[...codes].join(',')}`);
  for (const c of codes) assert.ok(enumText.includes(c), `${c} 不在 retest.v0.yaml 的问题码枚举里`);
  // 与错题再练共用限流桶；路由都只给学生本人
  for (const r of RETEST_ROUTES) assert.deepEqual([r.auth, r.rateLimits.map((x) => x.bucket)], ['student', ['practice.session.minute']], r.path);
});

test('家长诉求：family-goals.v0.yaml 的操作集合恰好等于诉求路由；诉求路由不在固定路由表里（只随开关 family_goals 注册）；用到的问题码都在枚举里', async () => {
  const spec = readFileSync(new URL('../openapi/family-goals.v0.yaml', import.meta.url), 'utf8');
  assert.deepEqual(operationsOf(spec).sort(), FAMILY_GOALS_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of FAMILY_GOALS_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
  const { P } = await import('../src/http/problems.ts');
  const src = readFileSync(new URL('../src/http/family-goals-routes.ts', import.meta.url), 'utf8');
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(spec)![1]!;
  const used = [...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!);
  assert.ok(used.length >= 7, used.join(','));
  for (const k of used) {
    const code = (JSON.parse((P as Record<string, string>)[k]!) as { code: string }).code;
    assert.ok(enumText.includes(code), `${k} → ${code} 不在 family-goals.v0.yaml 的问题码枚举里`);
  }
  // 学生会话 404（不是 403）：路由 auth = session，由 handler 在任何查询之前拒绝；写按（家长，孩子）一对与账号限流
  for (const r of FAMILY_GOALS_ROUTES) assert.equal(r.auth, 'session', r.path);
  assert.deepEqual(FAMILY_GOALS_ROUTES.find((r) => r.method === 'PUT')!.rateLimits.map((x) => x.dim), ['link', 'account']);
});

test('月度计划：monthly-plan.v0.yaml 的操作集合恰好等于月度计划路由；不在固定路由表里（只随开关 monthly_plan 注册）；用到的问题码都在枚举里', async () => {
  const spec = readFileSync(new URL('../openapi/monthly-plan.v0.yaml', import.meta.url), 'utf8');
  assert.deepEqual(operationsOf(spec).sort(), MONTHLY_PLAN_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of MONTHLY_PLAN_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
  const { P } = await import('../src/http/problems.ts');
  const src = readFileSync(new URL('../src/http/monthly-plan-routes.ts', import.meta.url), 'utf8');
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(spec)![1]!;
  const used = [...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!);
  assert.ok(used.length >= 2, used.join(','));
  for (const k of [...used, 'UNAUTHENTICATED', 'FORBIDDEN', 'TOO_MANY', 'METHOD_NOT_ALLOWED']) {
    const code = (JSON.parse((P as Record<string, string>)[k]!) as { code: string }).code;
    assert.ok(enumText.includes(code), `${k} → ${code} 不在 monthly-plan.v0.yaml 的问题码枚举里`);
  }
  // 家长端只给家长（学生会话 404，不是 403）；学生端只给学生本人；两条都只读、不开 CSRF、挂每会话限流桶，都不计量
  const [g, s] = [MONTHLY_PLAN_ROUTES.find((r) => r.path.endsWith('/monthly-plan'))!, MONTHLY_PLAN_ROUTES.find((r) => r.path.endsWith('/my-plan'))!];
  assert.deepEqual([g.auth, g.guardianOnly404, g.csrf], ['session', true, 'none']);
  assert.deepEqual([s.auth, s.guardianOnly404, s.csrf], ['student', undefined, 'none']);
  for (const r of MONTHLY_PLAN_ROUTES) assert.deepEqual(r.rateLimits.map((x) => `${x.bucket}/${x.dim}`), ['monthly_plan.session.minute/session'], r.path);
  assert.ok(!/meter_|usage_id|idempotency/.test(src), '月度计划不计量');
});

test('内部测试运营（LT-06）：internal-test.v0.yaml 的操作集合恰好等于三条路由；不在固定路由表里（只随开关 internal_test 注册）；用到的问题码都在枚举里；退出后可用的例外都是真实路由', async () => {
  const spec = readFileSync(new URL('../openapi/internal-test.v0.yaml', import.meta.url), 'utf8');
  assert.deepEqual(operationsOf(spec).sort(), INTERNAL_TEST_ROUTES.map((r) => `${r.method} ${PREFIX}${r.path}`).sort());
  for (const r of INTERNAL_TEST_ROUTES) assert.ok(!ROUTES.includes(r), r.path);
  const { P } = await import('../src/http/problems.ts');
  const src = readFileSync(new URL('../src/http/internal-test-routes.ts', import.meta.url), 'utf8');
  const enumText = /code:\s*\n\s*enum: \[([^\]]+)\]/.exec(spec)![1]!;
  const used = [...src.matchAll(/\bP\.([A-Z_]+)/g)].map((m) => m[1]!);
  assert.ok(used.length >= 4, used.join(','));
  for (const k of [...used, 'TEST_EXITED', 'UNAUTHENTICATED', 'FORBIDDEN', 'CSRF', 'TOO_MANY', 'METHOD_NOT_ALLOWED']) {
    const code = (JSON.parse((P as Record<string, string>)[k]!) as { code: string }).code;
    assert.ok(enumText.includes(code), `${k} → ${code} 不在 internal-test.v0.yaml 的问题码枚举里`);
  }
  // 反馈给学生与家长（auth session）；退出状态与退出只给家长；都不计量
  assert.deepEqual(INTERNAL_TEST_ROUTES.map((r) => `${r.method} ${r.path} ${r.auth}`).sort(),
                   ['GET /students/{student_id}/internal-test guardian', 'POST /students/{student_id}/feedback session',
                    'POST /students/{student_id}/internal-test/exit guardian']);
  assert.ok(!/meter_|usage_id|idempotency/.test(src), '内部测试运营不计量');
  // 退出后照常可用的路由都真实存在（数据权利、退出状态、退出本身），而且只有这些
  const keys = new Set(ALL.map((r) => `${r.method} ${r.path}`));
  for (const k of ALLOW_AFTER_EXIT) assert.ok(keys.has(k), `${k} 不是路由`);
  for (const k of ALLOW_AFTER_EXIT) assert.ok(/\/(internal-test|erasure|exports)/.test(k), `${k} 不是数据权利或退出`);
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

test('退出判据（LT-06，契约审查 B2）：凡带 {student_id}、不在 ALLOW_AFTER_EXIT 的操作都列 409，所在文件的问题码枚举含 TEST_EXITED', () => {
  const ops = operations();
  const enumHas = new Map<string, boolean>();
  for (const name of SPEC_FILES) {
    const text = readFileSync(new URL(`../openapi/${name}.yaml`, import.meta.url), 'utf8');
    // 问题码枚举里有 TEST_EXITED；没有枚举、只用描述列出问题码的文件（diagnosis.v0）在描述里写明
    enumHas.set(`${name}.yaml`, /\bcode:\s*(?:\{[^}]*?enum: \[|\n\s*enum: \[)[^\]]*\bTEST_EXITED\b/.test(text)
      || /\bcode:\n\s*type: string\n\s*description: >\n(?:\s{12}.*\n)*?\s{12}.*\bTEST_EXITED\b/.test(text));
  }
  const missing: string[] = [];
  let checked = 0;
  for (const r of ALL) {
    if (!r.path.includes('{student_id}') || ALLOW_AFTER_EXIT.has(`${r.method} ${r.path}`)) continue;
    const k = `${r.method} ${PREFIX}${r.path}`;
    const op = ops.get(k)!;
    checked += 1;
    if (!op.statuses.has('409')) missing.push(`${k}（${op.file}）没有 409`);
    if (!enumHas.get(op.file)) missing.push(`${op.file} 的问题码枚举没有 TEST_EXITED`);
  }
  assert.ok(checked >= 40, `检查了 ${checked} 个操作`);
  assert.deepEqual([...new Set(missing)], []);
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
