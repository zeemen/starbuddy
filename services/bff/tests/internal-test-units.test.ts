// LT-06 内部成员测试运营（specs/launch-lt06-internal-test-ops）的纯函数单测：开关解析、反馈请求体规则、退出后可用的例外清单、
// 路由表登记与“已退出”判据只在开关打开时生效。不连库。全部合成数据。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { parseInternalTestFeature } from '../src/internal-test/features.ts';
import { ALLOW_AFTER_EXIT, EXIT_GUARD_FUNCTION, exitedGuard, exitGuardAvailable, FEEDBACK_CATEGORIES, FEEDBACK_ROUTES, FORBIDDEN_RANGES, INTERNAL_TEST_ROUTES, parseFeedback }
  from '../src/http/internal-test-routes.ts';
import { activeRoutes, ROUTES, type ShellDeps } from '../src/http/routes.ts';

test('开关：缺省关；dev / test / staging 只认 internal_test；pilot、生产设了就拒绝', () => {
  for (const env of ['dev', 'test', 'staging', 'pilot', 'production'] as const) {
    assert.equal(parseInternalTestFeature(env, undefined), false, env);
    assert.equal(parseInternalTestFeature(env, ' '), false, env);
  }
  for (const env of ['dev', 'test', 'staging'] as const) {
    assert.equal(parseInternalTestFeature(env, 'internal_test'), true, env);
    assert.throws(() => parseInternalTestFeature(env, 'feedback'), /只认 internal_test/);
  }
  for (const env of ['pilot', 'production'] as const) assert.throws(() => parseInternalTestFeature(env, 'internal_test'), /pilot、生产/);
});

test('反馈请求体：固定分类、1–500 字（按码位）、不含控制与格式字符、路由模板只收登记的、未知键拒收', () => {
  const ok = { category: 'display', body: '合成：字太小', route_template: '/s/[studentId]/star-map' };
  assert.deepEqual(parseFeedback(ok), { category: 'display', body: '合成：字太小', route: '/s/[studentId]/star-map' });
  assert.deepEqual(parseFeedback({ category: 'other', body: `第一行\n第二行\t制表` }), { category: 'other', body: '第一行\n第二行\t制表', route: null });
  assert.equal(parseFeedback({ category: 'other', body: '😀'.repeat(500) })?.body.length, 1000, '500 个 emoji 是 500 字');
  const bad: unknown[] = [null, {}, { ...ok, category: 'x' }, { ...ok, body: '' }, { ...ok, body: '   ' }, { ...ok, body: 'a'.repeat(501) },
    { ...ok, body: 1 }, { ...ok, route_template: '/s/9a5b0000-0000-4000-8000-0000000000c1/today' }, { ...ok, route_template: 7 },
    { ...ok, extra: 1 }, { ...ok, source: 'staff' }];
  for (const [a, b] of FORBIDDEN_RANGES) {
    for (const cp of new Set([a, b])) bad.push({ ...ok, body: `合成${String.fromCodePoint(cp)}文本` });
  }
  for (const b of bad) assert.equal(parseFeedback(b as Record<string, unknown> | null), null, JSON.stringify(b)?.slice(0, 60));
  for (const cp of [0x0a, 0x0d, 0x09, 0x20, 0x4e2d, 0x1f600]) {
    assert.ok(parseFeedback({ ...ok, body: `合成${String.fromCodePoint(cp)}文本` }), `码位 ${cp.toString(16)} 可以`);
  }
  assert.deepEqual([...FEEDBACK_CATEGORIES], ['malfunction', 'wrong_content', 'display', 'slow', 'login', 'suggestion', 'other']);
  for (const r of FEEDBACK_ROUTES) assert.match(r, /^\/[a-z]+(\/([a-z][a-z-]*|\[[a-zA-Z]+\]))*$/, r);
});

test('分类、文本上限与禁止字符和迁移 0059 的 CHECK 同口径', () => {
  const sql = readFileSync(new URL('../../../db/migrations/0059_platform__internal_test.up.sql', import.meta.url), 'utf8');
  for (const c of FEEDBACK_CATEGORIES) assert.ok(sql.includes(`'${c}'`), c);
  assert.ok(sql.includes('char_length(body) BETWEEN 1 AND 500'));
  const chrs = [...sql.matchAll(/chr\((\d+)\)/g)].map((m) => Number(m[1]));
  const want = FORBIDDEN_RANGES.flatMap(([a, b]) => (a === b ? [a] : [a, b])).map((x) => (x === 0 ? 1 : x));  // 库里 NUL 本来存不进 text
  assert.deepEqual(chrs, want);
});

test('路由表：三条路由只随开关注册；退出后可用的例外只有数据权利与退出', () => {
  assert.deepEqual(activeRoutes(), ROUTES);
  const on = activeRoutes(new Set(), new Set(), false, false, false, false, true);
  for (const r of INTERNAL_TEST_ROUTES) assert.ok(on.includes(r) && !ROUTES.includes(r), r.path);
  assert.deepEqual([...ALLOW_AFTER_EXIT].sort(), [
    'GET /students/{student_id}/erasure', 'GET /students/{student_id}/internal-test', 'POST /students/{student_id}/erasure',
    'POST /students/{student_id}/erasure/{request_id}/cancel', 'POST /students/{student_id}/erasure/{request_id}/confirm',
    'POST /students/{student_id}/exports', 'POST /students/{student_id}/exports/{export_job_id}/download',
    'POST /students/{student_id}/internal-test/exit']);
});

test('退出判据：库里没有判据函数（0059 未执行）、没有会话、路径里没有学生、例外路由都不查库（直接放行）；与内测开关无关', async () => {
  let queried = 0;
  const pool = { connect: async () => { queried++; throw new Error('不应查库'); } } as unknown as ShellDeps['pool'];
  const session = { token: 't', accountId: '9a5b0000-0000-4000-8000-0000000000a1', kind: 'guardian' as const, origin: 'otp' };
  const route = ROUTES.find((r) => r.path === '/students/{student_id}/consents')!;
  const exitRoute = INTERNAL_TEST_ROUTES.find((r) => r.path.endsWith('/exit'))!;
  const noFunction = { pool, internalTest: { enabled: true } } as ShellDeps;
  const on = { pool, exitGuard: true } as ShellDeps;  // 内测开关关（没设），判据照样生效
  assert.equal(await exitedGuard(noFunction, route, session, 's'), false, '开关开但判据函数不在：不查');
  assert.equal(await exitedGuard(on, route, null, 's'), false);
  assert.equal(await exitedGuard(on, route, session, undefined), false);
  assert.equal(await exitedGuard(on, exitRoute, session, 's'), false);
  assert.equal(queried, 0);
  await assert.rejects(exitedGuard(on, route, session, 's'), /不应查库/, '判据函数在、内测开关关、普通路由：查库');
});

test('退出判据函数核对：to_regprocedure 与执行权一起查；查不到或没有执行权为假', async () => {
  const seen: string[] = [];
  const q = (ok: boolean | null) => async (sql: string, args: unknown[]) => { seen.push(`${sql}|${String(args[0])}`); return { rows: [{ ok }] }; };
  assert.equal(await exitGuardAvailable(q(true)), true);
  assert.equal(await exitGuardAvailable(q(false)), false);
  assert.equal(await exitGuardAvailable(q(null)), false);
  assert.equal(await exitGuardAvailable(async () => ({ rows: [] })), false);
  assert.ok(seen.every((s) => s.includes('to_regprocedure($1)') && s.includes('has_function_privilege') && s.endsWith(`|${EXIT_GUARD_FUNCTION}`)));
  assert.equal(EXIT_GUARD_FUNCTION, 'platform.internal_test_exited(uuid)');
});
