// LT-07 PWA 停用开关（安全审查 1；playwright.config.ts 的 pwa-off 项目：第七个前端 STARBUDDY_PWA_KILL_SWITCH=1）：
//   - /sw.js 发自毁版（no-store，不接管请求）；页面加载后不注册任何 Service Worker；
//   - 已有注册与缓存的浏览器（这里先放进合成缓存、再按旧注册的路径注册 /sw.js，等同于旧注册在更新检查时取到自毁版）：
//     激活后全部缓存被删、注册被注销、已打开的页面被重新导航，之后页面不受任何 Service Worker 控制。
// 只看未登录的登录页，不发任何写请求；合成数据。
import { expect, test, type Page } from '@playwright/test';

test.use({ serviceWorkers: 'allow' });

async function state(page: Page): Promise<{ regs: number; caches: string[]; controlled: boolean }> {
  return page.evaluate(async () => ({
    regs: (await navigator.serviceWorker.getRegistrations()).length,
    caches: await caches.keys(),
    controlled: navigator.serviceWorker.controller !== null,
  }));
}

test('/sw.js 是自毁版；页面不注册 Service Worker', async ({ page, request }) => {
  const sw = await request.get('/sw.js');
  expect(sw.status()).toBe(200);
  expect(sw.headers()['cache-control']).toContain('no-store');
  const src = await sw.text();
  expect(src).toContain('self.registration.unregister()');
  expect(src).not.toContain("addEventListener('fetch'");
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  await page.waitForLoadState('networkidle');
  expect(await state(page)).toEqual({ regs: 0, caches: [], controlled: false });
});

test('已安装的注册换成自毁版后：全部缓存删除、注册注销、已打开的页面重新导航', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  // 合成的“旧版”缓存（与正常版同名前缀）与本页标记
  await page.evaluate(async () => {
    for (const name of ['sb-shell-synthetic-old', 'sb-static-synthetic-old']) {
      const c = await caches.open(name);
      await c.put('/_next/static/chunks/synthetic-old.js', new Response('/* synthetic */'));
    }
    (window as unknown as { __sbBefore: boolean }).__sbBefore = true;
  });
  expect((await state(page)).caches.sort()).toEqual(['sb-shell-synthetic-old', 'sb-static-synthetic-old']);
  const navigated = page.waitForEvent('framenavigated');
  await page.evaluate(async () => { await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }); });
  await navigated;
  await page.waitForLoadState('load');
  await expect.poll(async () => page.evaluate(() => (window as unknown as { __sbBefore?: boolean }).__sbBefore === true)).toBe(false);
  await expect.poll(async () => JSON.stringify(await state(page)), { timeout: 10_000 })
    .toBe(JSON.stringify({ regs: 0, caches: [], controlled: false }));
  await expect(page.getByTestId('login')).toBeVisible();
});
