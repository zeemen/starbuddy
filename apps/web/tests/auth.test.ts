// 统一壳 H1（登录与学生模式入口）的单测与静态检查：
//   - 会话 cookie：__Host- 前缀（本地 dev / test 除外）、HttpOnly、Secure（非本地）、SameSite=Lax、Path=/、不设 Domain；
//   - 文案：全部在 src/auth/copy.ts，逐条过 packages/personas 的禁语扫描（python -m personas.guard scan --persona platform --stdin-json，
//     3-F 的命令行），另查比较词与关于孩子的描述；
//   - 会话令牌只在服务端：浏览器组件不引用 shell-auth / session-cookie 的读写，cookie 只在两个 server action 文件里写；
//     登录页与学生模式入口的源码里没有开发身份桩。
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { ALL_AUTH_COPY, childLabel } from '../src/auth/copy.ts';
import { csrfCookieName, isHex64, normalizePhone, sessionCookieName, sessionCookieOptions } from '../src/lib/session-cookie.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}
const sources = files(join(WEB, 'src')).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));

test('会话 cookie：正式环境 __Host- 前缀、HttpOnly、Secure、SameSite=Lax、Path=/、不设 Domain；本地不带前缀与 Secure', () => {
  for (const env of [{ STARBUDDY_ENV: 'staging' }, { STARBUDDY_ENV: 'pilot' }, {}]) {
    assert.equal(sessionCookieName(env), '__Host-sb_session');
    assert.equal(csrfCookieName(env), '__Host-sb_csrf');
    const o = sessionCookieOptions(env);
    assert.deepEqual({ ...o }, { httpOnly: true, sameSite: 'lax', path: '/', secure: true, maxAge: 7 * 24 * 60 * 60 });
    assert.ok(!('domain' in o));
  }
  for (const env of [{ STARBUDDY_ENV: 'dev' }, { STARBUDDY_ENV: 'test' }]) {
    assert.equal(sessionCookieName(env), 'sb_session');
    assert.equal(sessionCookieOptions(env).secure, false);
    assert.equal(sessionCookieOptions(env).httpOnly, true);
  }
  assert.ok(isHex64('a'.repeat(64)) && !isHex64('A'.repeat(64)) && !isHex64('a'.repeat(63)) && !isHex64(undefined));
});

test('手机号：统一成 E.164；格式不对不发请求', () => {
  assert.equal(normalizePhone('139 0000 0000'), '+8613900000000');
  assert.equal(normalizePhone('+99900000101'), '+99900000101');
  for (const bad of ['12345', '0086139', '+0123456789', '13900000000x', '']) assert.equal(normalizePhone(bad), null, bad);
});

test('文案过禁语扫描（personas.guard，platform 口径）', () => {
  const r = spawnSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json'], {
    cwd: ROOT, input: JSON.stringify(ALL_AUTH_COPY), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const out = JSON.parse(r.stdout) as { results: { source: string; hits: unknown[] }[] };
  assert.equal(out.results.length, ALL_AUTH_COPY.length);
  assert.deepEqual(out.results.filter((x) => x.hits.length > 0), []);
  // 扫描确实能命中（防止“什么都没查”）
  const probe = execFileSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json', '--exit-zero'], {
    cwd: ROOT, input: JSON.stringify(['合成：你总是这样']), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.ok((JSON.parse(probe) as { results: { hits: unknown[] }[] }).results[0]!.hits.length > 0);
});

test('文案不含比较、评价、营销类用词；关于孩子只有序号与年级', () => {
  const banned = /别人|同学都|排名|最差|最好|落后|领先|聪明|笨|天赋|性格|焦虑|紧张|懒|购买|升级|会员|优惠|加油|棒/;
  for (const s of ALL_AUTH_COPY) assert.ok(!banned.test(s), s);
  assert.equal(childLabel(1, 8), '孩子 1 · 八年级');
  // /me 的年级由届别推算（迁移 0045），不在 7–9 时为 null：中性措辞
  assert.equal(childLabel(2, null), '孩子 2 · 不在初中年级');
  assert.ok(ALL_AUTH_COPY.every((s) => !s.includes('已毕业')));
});

test('会话令牌只在服务端：浏览器组件不引用会话读写；cookie 只在两个 server action 文件里写', () => {
  const clientFiles = sources.filter((f) => /^['"]use client['"]/m.test(f.text));
  for (const f of clientFiles) {
    assert.ok(!/lib\/shell-auth|session-cookie|next\/headers/.test(f.text), f.path);
  }
  const writers = sources.filter((f) => /sessionCookieName\(\), /.test(f.text)).map((f) => f.path).sort();
  assert.deepEqual(writers, ['src/app/family/actions.ts', 'src/app/login/actions.ts']);
  for (const f of sources.filter((s) => /src\/(app\/(login|family|student)|auth|lib\/(shell-auth|session-cookie))/.test(s.path))) {
    assert.ok(!/x-dev-account-id|sb_dev_account|dev-auth/.test(f.text), `${f.path} 不能用开发身份桩`);
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(f.text), f.path);
  }
});

test('带会话的页面不缓存（next.config.ts：H2 起除带哈希的静态资源外一律 no-store）', () => {
  const cfg = readFileSync(join(WEB, 'next.config.ts'), 'utf8');
  assert.ok(cfg.includes("source: '/((?!_next/static|_next/image).*)',\n        headers: [{ key: 'Cache-Control', value: 'no-store' }]"));
});
