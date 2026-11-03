// 星图页端到端（3-H 验收，4-H H2 改走真实会话、断言不变）：合成学生 S1 在学生模式下打开 /s/S1/star-map，
// 同一页上有“数学”“道法”两片星空，各态星数（按 data-display 计）等于种子按第 2 节映射得到的期望值。
// 家长在 /family/{id}/star-map。另测授权、旧地址 404、无障碍、不缓存、不外链、不用存储。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { openAs } from './session.ts';

const STU = {
  S1: '5a000000-0000-4000-8000-0000000000c1',
  S2: '5a000000-0000-4000-8000-0000000000c2',
};
const A = Number(process.env['AYA_COUNT'] ?? NaN);
// db/seeds/dev/star-map-synthetic.sql 末尾的期望值
const EXPECTED_S1: Record<string, { name: string; lit: number; half: number; unlit: number; retained: number; verify: number }> = {
  aya: { name: '数学', lit: 3, half: 3, unlit: A - 6, retained: 1, verify: 2 },
  zhixing: { name: '道法', lit: 2, half: 1, unlit: 1, retained: 1, verify: 0 },
};
const STATUS_TEXT: Record<string, string> = { unlit: '未亮', half: '半亮', lit: '亮' };

const studentMap = (student: string) => `/s/${student}/star-map`;
const familyMap = (student: string) => `/family/${student}/star-map`;

test('S1 本人：数学与道法两片星空，各态计数与种子一致；列表视图与摘要一致', async ({ page }) => {
  expect(Number.isFinite(A) && A > 0, 'AYA_COUNT 由 scripts/e2e.sh 传入').toBeTruthy();
  const res = await openAs(page, STU.S1, studentMap(STU.S1));
  expect(res?.status()).toBe(200);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByTestId('watermark')).toHaveText('原型 · 合成数据');
  await expect(page.locator('section.sky h2')).toHaveText(['数学', '道法']);
  for (const [subject, e] of Object.entries(EXPECTED_S1)) {
    const sky = page.locator(`section.sky[data-subject="${subject}"]`);
    const field = sky.locator('.sky-field');
    for (const d of ['lit', 'half', 'unlit'] as const) {
      await expect(field.locator(`svg.star[data-display="${d}"]`)).toHaveCount(e[d]);
    }
    await expect(field.locator('svg.star[data-retained="true"]')).toHaveCount(e.retained);
    // 星空的无障碍摘要与计数一致
    await expect(field).toHaveAttribute('role', 'img');
    await expect(field).toHaveAttribute('aria-label', `${e.name}星空：亮 ${e.lit} 颗，半亮 ${e.half} 颗，未亮 ${e.unlit} 颗`);
    // 列表视图：行数 = 星数，状态文字与 data-display 一致
    await sky.locator('summary').click();
    const rows = sky.locator('tbody tr');
    await expect(rows).toHaveCount(e.lit + e.half + e.unlit);
    const pairs = await rows.evaluateAll((trs) =>
      trs.map((tr) => [tr.getAttribute('data-display'), tr.getAttribute('data-retained'), tr.children[1]!.textContent, tr.children[2]!.textContent]));
    for (const [d, retained, text] of pairs) {
      expect(text).toBe(d === 'lit' && retained === 'true' ? '亮·保持' : STATUS_TEXT[d!]);
    }
    expect(pairs.filter((p) => p[3] === '待验证').length).toBe(e.verify);
    await sky.locator('summary').click();
  }
  // 截图存档：可以作为 Gate 0 星图截图的原型版本
  await page.screenshot({ path: 'test-results/star-map-s1.png', fullPage: true });
});

test('家长看到的星空与学生本人相同；同一家长的另一个孩子只显示在读学科', async ({ page }) => {
  await openAs(page, STU.S1, studentMap(STU.S1));
  const self = await page.locator('.sky-field').evaluateAll((els) => els.map((e) => e.innerHTML));
  await openAs(page, 'gS', familyMap(STU.S1));
  const parent = await page.locator('.sky-field').evaluateAll((els) => els.map((e) => e.innerHTML));
  expect(parent).toEqual(self);
  // 家长首页的星图入口指向家长侧的地址
  await page.goto('/family');
  await expect(page.getByTestId('star-map-1')).toHaveAttribute('href', familyMap(STU.S1));
  await page.goto(familyMap(STU.S2));
  await expect(page.locator('section.sky h2')).toHaveText(['数学']);  // S2 的道法已暂停
  const aya = page.locator('section.sky[data-subject="aya"] .sky-field');
  await expect(aya.locator('svg.star[data-display="lit"]')).toHaveCount(2);
  await expect(aya.locator('svg.star[data-display="half"]')).toHaveCount(2);
});

test('兄妹互看、另一户家长、不存在的学生：同一个“没有找到”页面，状态码与缓存头也相同', async ({ page }) => {
  const shape = async (res: Awaited<ReturnType<Page['goto']>>) => ({
    status: res?.status(),
    cache: res?.headers()['cache-control'] ?? '',
    text: await page.locator('main').innerText(),
  });
  const sibling = await shape(await openAs(page, STU.S1, studentMap(STU.S2)));
  expect(sibling.status).toBe(404);
  expect(sibling.cache).toContain('no-store');
  expect(sibling.text).toContain('没有找到');
  expect(await shape(await openAs(page, 'gO', familyMap(STU.S1)))).toEqual(sibling);
  expect(await shape(await page.goto(familyMap('5a000000-0000-4000-8000-00000000ffff')))).toEqual(sibling);
  // 3-H 的旧地址已删除（第 7.1 节）：与“没有找到”同一个页面
  // （路由不存在时 Next 自己写的缓存头更长，但同样含 no-store）
  const old = await shape(await page.goto(`/star-map/${STU.S1}`));
  expect({ ...old, cache: old.cache.includes('no-store') }).toEqual({ ...sibling, cache: true });
  await expect(page.locator('.sky-field')).toHaveCount(0);
});

test('未登录打开星图：回登录页；开发登录与写 cookie 的路由已删除（404）', async ({ page }) => {
  await page.context().clearCookies();
  for (const path of [studentMap(STU.S1), familyMap(STU.S1)]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/login$/);
  }
  for (const path of ['/dev/login', `/dev/session?account=5a000000-0000-4000-8000-0000000000b1&next=${studentMap(STU.S1)}`]) {
    const r = await page.request.get(path, { maxRedirects: 0 });
    expect(r.status(), path).toBe(404);
  }
  // 只有请求头冒充身份：没有会话就是没登录
  const r = await page.request.get(studentMap(STU.S1), { headers: { 'x-dev-account-id': '5a000000-0000-4000-8000-0000000000b1' },
                                                          maxRedirects: 0 });
  expect(r.status()).toBeGreaterThanOrEqual(300);
  expect(r.status()).toBeLessThan(400);
  expect(r.headers()['location'] ?? '').toMatch(/\/login$/);
});

test('学生会话打开家长侧地址转到学生侧；家长会话打开学生侧地址转到家长侧', async ({ page }) => {
  await openAs(page, STU.S1, familyMap(STU.S1));
  await expect(page).toHaveURL(new RegExp(`${studentMap(STU.S1)}$`));
  await openAs(page, 'gS', studentMap(STU.S1));
  await expect(page).toHaveURL(new RegExp(`${familyMap(STU.S1)}$`));
});

test('无障碍：星图页与符号样张页没有 serious / critical 问题', async ({ page }) => {
  await openAs(page, STU.S1, studentMap(STU.S1));
  for (const url of [studentMap(STU.S1), '/dev/star-glyphs']) {
    await page.goto(url);
    const { violations } = await new AxeBuilder({ page }).analyze();
    const bad = violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    expect(bad.map((v) => `${v.id}: ${v.help}`), url).toEqual([]);
  }
});

test('不外链、不用浏览器存储：所有请求都是同源；没有 localStorage、IndexedDB、Service Worker', async ({ page, baseURL }) => {
  const foreign: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!['data:', 'blob:'].includes(u.protocol) && u.origin !== new URL(baseURL!).origin) foreign.push(r.url());
  });
  await openAs(page, STU.S1, studentMap(STU.S1));
  await page.goto('/dev/star-glyphs');
  expect(foreign).toEqual([]);
  const storage = await page.evaluate(async () => ({
    local: localStorage.length,
    session: sessionStorage.length,
    idb: (await indexedDB.databases()).length,
    sw: (await navigator.serviceWorker?.getRegistrations?.())?.length ?? 0,
  }));
  expect(storage).toEqual({ local: 0, session: 0, idb: 0, sw: 0 });
});

test('手机宽度与 200% 缩放下没有横向滚动；星仍是 16px', async ({ page }) => {
  await openAs(page, STU.S1, studentMap(STU.S1));
  for (const width of [360, 512]) {  // 512 = 1024 宽的 200% 缩放
    await page.setViewportSize({ width, height: 800 });
    await page.goto(studentMap(STU.S1));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${width}px`).toBeLessThanOrEqual(0);
    const box = await page.locator('.sky-field svg.star').first().boundingBox();
    expect(box?.width).toBe(16);
  }
});
