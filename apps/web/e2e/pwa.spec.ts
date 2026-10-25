// LT-07 PWA 端到端（specs/launch-lt07-mobile-pwa 决定记录第 1 条；开关全关的一套 trust-off，本文件单独放行 Service Worker）：
//   - manifest、占位图标、/sw.js、离线页的响应头与内容；页面带 manifest、apple-touch-icon、theme-color、viewport-fit=cover；
//   - Service Worker 注册（作用域 /、脚本 /sw.js、缓存名带本次构建号）；
//   - 缓存范围：家长与学生走完一圈后，缓存里只有静态壳与 /_next/static 构建产物——没有任何页面 HTML、API、BFF 响应、RSC、带查询串的地址；
//   - 离线：导航回固定离线页（不含任何学习记录与学生编号），恢复联网后照常；
//   - 登出（家长退出登录、退出学生模式）：全部缓存被清掉（预先放进去的合成标记消失），只重建静态壳。
// 全程真实会话（短信捕获桩读码），合成家庭 gS / S1。
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { STATIC_SHELL } from '../src/pwa/constants.ts';
import { OFFLINE } from '../src/pwa/copy.ts';
import { BASE, loginGuardian, loginStudent } from './session.ts';

test.use({ serviceWorkers: 'allow' });
test.describe.configure({ mode: 'serial' });

const S1 = '5a000000-0000-4000-8000-0000000000c1';
const BUILD_ID = readFileSync(new URL('../.next-e2e/BUILD_ID', import.meta.url), 'utf8').trim();
const MARKER = '/_next/static/chunks/synthetic-logout-marker.js';

async function swReady(page: Page): Promise<void> {
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
}

/** 本站全部缓存条目：“缓存名 路径?查询串” */
async function entries(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const out: string[] = [];
    for (const k of await caches.keys()) {
      const c = await caches.open(k);
      for (const r of await c.keys()) { const u = new URL(r.url); out.push(`${k} ${u.pathname}${u.search}`); }
    }
    return out.sort();
  });
}

function onlyShellAndStatic(list: string[]): void {
  expect(list.length).toBeGreaterThan(0);
  for (const e of list) {
    const [cache, path] = e.split(' ') as [string, string];
    expect(cache, e).toMatch(new RegExp(`^sb-(shell|static)-${BUILD_ID}$`));
    const ok = (STATIC_SHELL as readonly string[]).includes(path) || /^\/_next\/static\/[^?#]+\.(js|css|woff2?|svg|png)$/.test(path);
    expect(ok, `缓存里不该有 ${e}`).toBe(true);
  }
}

/**
 * 离线导航：Playwright 对 Service Worker 的离线模拟在 SW 重新启动时有竞态（新实例先放过请求、后挂上模拟）。
 * 先用一次静态壳请求唤醒 SW（从缓存回，离线也成功），再重新挂一次离线模拟，然后导航。
 */
async function gotoOffline(page: Page, path: string): Promise<void> {
  expect(await page.evaluate(async () => (await fetch('/offline.css')).status)).toBe(200);
  await page.context().setOffline(false);
  await page.context().setOffline(true);
  await page.goto(path);
}

async function putMarker(page: Page): Promise<void> {
  await page.evaluate(async ([name, path]) => {
    const c = await caches.open(name!);
    await c.put(path!, new Response('/* synthetic marker */', { headers: { 'content-type': 'text/javascript' } }));
  }, [`sb-static-${BUILD_ID}`, MARKER]);
  expect(await entries(page)).toContain(`sb-static-${BUILD_ID} ${MARKER}`);
}

test('manifest、占位图标、/sw.js、离线页：响应头与内容；页面头部带 manifest、图标、主题色与 viewport-fit', async ({ page, request }) => {
  const m = await request.get('/manifest.webmanifest');
  expect(m.status()).toBe(200);
  expect(m.headers()['content-type']).toContain('application/manifest+json');
  expect(m.headers()['x-content-type-options']).toBe('nosniff');
  const body = await m.json() as { display: string; start_url: string; scope: string; icons: { src: string }[] };
  expect(body.display).toBe('standalone');
  expect(body.start_url).toBe('/login');
  expect(body.scope).toBe('/');
  for (const i of body.icons) {
    const r = await request.get(i.src);
    expect(r.status(), i.src).toBe(200);
    expect(r.headers()['content-type']).toBe('image/png');
  }
  expect((await request.get('/icons/apple-touch-icon.png')).status()).toBe(200);
  expect((await request.get('/icons/no-such.png')).status()).toBe(404);
  const sw = await request.get('/sw.js');
  expect(sw.status()).toBe(200);
  expect(sw.headers()['content-type']).toMatch(/^text\/javascript/);
  expect(sw.headers()['cache-control']).toContain('no-store');
  expect(await sw.text()).toContain(`var VERSION = '${BUILD_ID}'`);
  const off = await request.get('/offline.html');
  expect(off.status()).toBe(200);
  expect(off.headers()['cache-control']).toContain('no-store');
  expect(await off.text()).toContain(OFFLINE.title);
  const res = await page.goto('/login');
  expect(res?.headers()['content-security-policy']).toContain("worker-src 'self'");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.webmanifest');
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveAttribute('href', '/icons/apple-touch-icon.png');
  await expect(page.locator('meta[name="theme-color"]')).toHaveCount(2);
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute('content', /viewport-fit=cover/);
});

test('注册：作用域 /、脚本 /sw.js；家长与学生走一圈后缓存里只有静态壳与构建产物，没有页面、API、BFF、RSC', async ({ page }) => {
  await page.goto('/login');
  await swReady(page);
  const reg = await page.evaluate(async () => {
    const r = await navigator.serviceWorker.getRegistration();
    return { scope: r?.scope, script: r?.active?.scriptURL };
  });
  expect(reg).toEqual({ scope: `${BASE}/`, script: `${BASE}/sw.js` });
  await loginGuardian(page, 'gS');
  for (const p of ['/family', `/family/${S1}`, `/family/${S1}/star-map`, `/family/${S1}/time`]) { await page.goto(p); await page.waitForLoadState('networkidle'); }
  await loginStudent(page, S1);
  for (const p of [`/s/${S1}/today`, `/s/${S1}/star-map`, `/s/${S1}/learn`, `/s/${S1}/learn/math`]) { await page.goto(p); await page.waitForLoadState('networkidle'); }
  await page.getByTestId('nav-star-map').click();
  await page.waitForLoadState('networkidle');
  const list = await entries(page);
  onlyShellAndStatic(list);
  expect(list.filter((e) => e.includes(' /_next/static/')).length, '构建产物有缓存').toBeGreaterThan(0);
  for (const p of STATIC_SHELL) expect(list).toContain(`sb-shell-${BUILD_ID} ${p}`);
});

test('离线：导航回固定离线页（不含学生编号与学习记录）；恢复联网后照常', async ({ page, context }) => {
  await loginStudent(page, S1);
  await swReady(page);
  await context.setOffline(true);
  try {
    for (const p of [`/s/${S1}/today`, '/family', '/login']) {
      await gotoOffline(page, p);
      await expect(page.getByTestId('offline'), p).toBeVisible();
      await expect(page).toHaveTitle(OFFLINE.title);
      const html = await page.content();
      expect(html).not.toContain(S1);
      expect(html).not.toMatch(/<script\b/);
    }
  } finally {
    await context.setOffline(false);
  }
  await page.goto(`/s/${S1}/today`);
  await expect(page.getByTestId('today')).toBeVisible();
});

test('登出清缓存：家长退出登录、孩子退出学生模式后全部缓存被清掉，只重建静态壳', async ({ page }) => {
  await loginGuardian(page, 'gS');
  await swReady(page);
  await putMarker(page);
  const logout = page.getByTestId('logout');
  await expect(logout).toHaveAttribute('data-hydrated', 'true');
  await logout.getByRole('button').click();
  await expect(page).toHaveURL(/\/login$/);
  await expect.poll(async () => (await entries(page)).some((e) => e.endsWith(MARKER)), { timeout: 10_000 }).toBe(false);
  await expect.poll(async () => (await entries(page)).filter((e) => e.startsWith(`sb-shell-${BUILD_ID} `)).length, { timeout: 10_000 })
    .toBe(STATIC_SHELL.length);
  onlyShellAndStatic(await entries(page));

  await loginStudent(page, S1);
  await swReady(page);
  await putMarker(page);
  const exit = page.getByTestId('exit-student-mode');
  await expect(exit).toHaveAttribute('data-hydrated', 'true');
  await exit.getByRole('button').click();
  await expect(page).toHaveURL(/\/login$/);
  await expect.poll(async () => (await entries(page)).some((e) => e.endsWith(MARKER)), { timeout: 10_000 }).toBe(false);
  onlyShellAndStatic(await entries(page));
});
