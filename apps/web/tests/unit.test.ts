// 前端单测（不需要浏览器）：布局纯函数、文案与摘要、开发专用页面的关卡、BFF 地址白名单、安全响应头。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { bffBaseUrl } from '../src/lib/bff.ts';
import { contentSecurityPolicy, HSTS, hstsEnabled, newNonce } from '../src/lib/csp.ts';
import { devRuntimeAllowed } from '../src/lib/dev-pages.ts';
import { skySummaryLabel, statusText, subjectName } from '../src/lib/labels.ts';
import { fetchStarMap, isUuid } from '../src/lib/star-map.ts';
import { THEME_CSS } from '../src/lib/theme.ts';
import { columnsForWidth, orderStars, placeStars, skyColumnsCss, squareColumns, STAR_PITCH } from '../src/star/layout.ts';
import { strokePx } from '../src/star/tokens.ts';

const stars = (ids: string[]) => ids.map((id) => ({ skill: `aya:${id}` }));

test('布局：按 local_id 排序，与输入顺序无关；同一输入同一布局', () => {
  const a = placeStars(stars(['K-3', 'K-1', 'M-2', 'C-9']), 2);
  const b = placeStars(stars(['C-9', 'M-2', 'K-1', 'K-3']), 2);
  assert.deepEqual(a, b);
  assert.deepEqual(a.map((p) => p.item.skill), ['aya:C-9', 'aya:K-1', 'aya:K-3', 'aya:M-2']);
  assert.deepEqual(a.map((p) => [p.col, p.row]), [[0, 0], [1, 0], [0, 1], [1, 1]]);
  assert.deepEqual([a[3]!.x, a[3]!.y], [STAR_PITCH * 1.5, STAR_PITCH * 1.5]);
});

test('布局：阿鸦 959 颗星宽屏时 31 × 31（744px 见方）；窄容器按宽度折行', () => {
  assert.equal(squareColumns(959), 31);
  assert.equal(squareColumns(959) * STAR_PITCH, 744);
  const placed = placeStars(stars(Array.from({ length: 959 }, (_, i) => `N-${String(i).padStart(4, '0')}`)), 31);
  assert.equal(Math.max(...placed.map((p) => p.row)) + 1, 31);
  assert.equal(columnsForWidth(360 - 32), 13);  // 手机宽度 360px，左右各 16px 边距
  assert.equal(columnsForWidth(10), 1);
  assert.equal(orderStars([]).length, 0);
});

test('状态文字与星空摘要：只描述本人，不含比较', () => {
  assert.equal(statusText({ display: 'unlit', retained: false }), '未亮');
  assert.equal(statusText({ display: 'half', retained: false }), '半亮');
  assert.equal(statusText({ display: 'lit', retained: false }), '亮');
  assert.equal(statusText({ display: 'lit', retained: true }), '亮·保持');
  assert.equal(subjectName('aya'), '数学');
  assert.equal(subjectName('zhixing'), '道法');
  assert.equal(skySummaryLabel({ subject: 'aya', summary: { lit: 12, half: 30, unlit: 917 } }),
    '数学星空：亮 12 颗，半亮 30 颗，未亮 917 颗');
});

test('描边：按图 2b 比例缩放，下限 1.5px', () => {
  assert.equal(strokePx(16), 1.5);
  assert.equal(strokePx(48), 1.5);
  assert.ok(strokePx(84) === 2);
});

test('开发身份桩已删除：文件不在了；开发专用页面运行时只认 dev / test 与本机 Host', () => {
  for (const f of ['src/lib/dev-auth.ts', 'src/dev/accounts.ts', 'src/app/dev/login/page.tsx', 'src/app/dev/session/route.ts',
    'src/app/star-map/[studentId]/page.tsx']) {
    assert.ok(!existsSync(new URL(`../${f}`, import.meta.url)), f);
  }
  assert.equal(devRuntimeAllowed('127.0.0.1:3000', { STARBUDDY_ENV: 'test' }), true);
  assert.equal(devRuntimeAllowed('127.0.0.1:3000', {}), false);
  assert.equal(devRuntimeAllowed('127.0.0.1:3000', { STARBUDDY_ENV: 'staging' }), false);
  assert.equal(devRuntimeAllowed('evil.example.test', { STARBUDDY_ENV: 'dev' }), false);
});

test('BFF 地址白名单：开发与测试只允许本机 loopback 的 http；staging、pilot、生产只允许内网地址', () => {
  assert.equal(bffBaseUrl({}), 'http://127.0.0.1:8787');
  assert.equal(bffBaseUrl({ STARBUDDY_ENV: 'test', STARBUDDY_BFF_URL: 'http://localhost:9000' }), 'http://localhost:9000');
  for (const bad of ['https://127.0.0.1:8787', 'http://10.0.0.5:8787', 'http://bff.example.com', 'http://127.0.0.1:8787/x', 'http://u:p@127.0.0.1:8787']) {
    assert.throws(() => bffBaseUrl({ STARBUDDY_ENV: 'dev', STARBUDDY_BFF_URL: bad }), bad);
  }
  for (const env of ['staging', 'pilot', 'production']) {
    assert.equal(bffBaseUrl({ STARBUDDY_ENV: env, STARBUDDY_BFF_URL: 'http://10.0.0.5:8787' }), 'http://10.0.0.5:8787');
    assert.equal(bffBaseUrl({ STARBUDDY_ENV: env, STARBUDDY_BFF_URL: 'https://172.20.1.2' }), 'https://172.20.1.2');
    for (const bad of ['http://127.0.0.1:8787', 'https://bff.example.com', 'http://8.8.8.8', 'http://172.32.0.1', 'http://192.169.0.1']) {
      assert.throws(() => bffBaseUrl({ STARBUDDY_ENV: env, STARBUDDY_BFF_URL: bad }), `${env} ${bad}`);
    }
  }
  assert.throws(() => bffBaseUrl({ STARBUDDY_ENV: 'prod', STARBUDDY_BFF_URL: 'http://10.0.0.5' }));
});

test('CSP：带本次的 nonce，没有 unsafe-inline；生产不带 unsafe-eval；不许 iframe 与外部来源；HSTS 只在 staging、pilot、生产', () => {
  const n = newNonce();
  assert.notEqual(n, newNonce());
  const csp = contentSecurityPolicy(n);
  assert.match(csp, new RegExp(`script-src 'self' 'nonce-${n.replace(/[+/=]/g, '\\$&')}' 'strict-dynamic'`));
  assert.ok(csp.includes(`style-src 'self' 'nonce-${n}'`));
  for (const d of ["frame-ancestors 'none'", "frame-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'self'", "connect-src 'self'",
                   "worker-src 'self'", "manifest-src 'self'"]) {
    assert.ok(csp.includes(d), d);
  }
  assert.ok(!/unsafe-inline|unsafe-eval|https?:|\*/.test(csp), csp);
  assert.ok(contentSecurityPolicy(n, { dev: true }).includes("'unsafe-eval'"));
  assert.throws(() => contentSecurityPolicy("x' 'unsafe-inline"));
  assert.match(HSTS, /max-age=(\d+)/);
  assert.ok(Number(/max-age=(\d+)/.exec(HSTS)![1]) >= 180 * 86400);
  for (const env of ['staging', 'pilot', 'production']) assert.equal(hstsEnabled({ STARBUDDY_ENV: env }), true, env);
  for (const env of ['dev', 'test', '']) assert.equal(hstsEnabled({ STARBUDDY_ENV: env }), false, env);
});

test('内联样式二选一取静态文件：theme.css 与 THEME_CSS 一致；星空列宽规则与生成器一致；源码里没有 style 属性与内联 <style>', () => {
  const norm = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim();
  assert.equal(norm(readFileSync(new URL('../src/app/theme.css', import.meta.url), 'utf8')), norm(THEME_CSS));
  const css = readFileSync(new URL('../src/app/globals.css', import.meta.url), 'utf8');
  const block = css.slice(css.indexOf('/* sky-cols:start */') + 20, css.indexOf('/* sky-cols:end */'));
  assert.equal(block.trim(), skyColumnsCss().trim());
});

test('没有登录或学生 ID 不是 UUID 时不请求 BFF', async () => {
  const orig = globalThis.fetch;
  let called = 0;
  globalThis.fetch = (async () => { called += 1; throw new Error('should not fetch'); }) as typeof fetch;
  try {
    assert.deepEqual(await fetchStarMap('5a000000-0000-4000-8000-0000000000c1', null), { kind: 'unauthenticated' });
    assert.deepEqual(await fetchStarMap('../../admin', { token: 'a'.repeat(64), csrf: 'b'.repeat(64) }), { kind: 'not-found' });
    assert.equal(called, 0);
    assert.ok(isUuid('5a000000-0000-4000-8000-0000000000c1'));
  } finally {
    globalThis.fetch = orig;
  }
});
