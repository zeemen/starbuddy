// 舞台样张页端到端（specs/stage-whiteboard-step1 验收）：合成 DSL 在 /dev/stage 逐步播放（前进、后退、跳转、播放到结束、重来）；
// 非法样例被拒；三套布局下白板占舞台高度 ≥ 65%；讲解权只用 1.25 线表示；axe 无违规；全部请求同源、渲染结果无外链；
// 不写任何浏览器存储。样张页只在测试构建里存在（scripts/e2e.sh），生产构建里没有（tests/build-output.test.ts）。全部合成数据。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const board = (page: Page) => page.locator('svg[data-stage-layout]');
const status = (page: Page) => page.locator('[data-stage-status]');
const axe = async (page: Page) =>
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.map((v) => v.id)).toEqual([]);

test('样张页：逐步播放、后退、跳转、播放到结束、重来', async ({ page, baseURL }) => {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(baseURL!) && !r.url().startsWith('data:')) off.push(r.url()); });
  const res = await page.goto('/dev/stage');
  expect(res?.status()).toBe(200);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('舞台样张');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '0');
  await expect(status(page)).toHaveAttribute('data-stage-status', 'idle');
  await axe(page);

  // 下一步：第 1 步老师持有讲解权（1.25 线），白板上写出第一行
  await page.locator('[data-stage-action="next"]').click();
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '1');
  await expect(status(page)).toHaveAttribute('data-stage-status', 'paused');
  await expect(board(page).locator('[data-anchor="teacher"]')).toHaveAttribute('data-speaking', 'true');
  await expect(board(page).locator('[data-el="t1"]')).toHaveText('（合成）解方程');
  const teacher = Number(await board(page).locator('[data-anchor="teacher"] rect').getAttribute('data-outline-width'));
  const buddy = Number(await board(page).locator('[data-anchor="buddy"] rect').getAttribute('data-outline-width'));
  expect(teacher / (143 / 48)).toBeCloseTo(1.25, 2);
  expect(buddy / (121.55 / 48)).toBeCloseTo(1, 2);

  // 跳到第 4 步：点点讲；公式按纯文本排版；第 3 步的高亮还在
  await page.locator('[data-stage-action="seek"]').selectOption('4');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '4');
  await expect(board(page).locator('[data-anchor="buddy"]')).toHaveAttribute('data-speaking', 'true');
  await expect(board(page).locator('[data-anchor="teacher"]')).toHaveAttribute('data-speaking', 'false');
  await expect(board(page).locator('[data-el="f3"]')).toHaveText('x = (4)/(2)');
  await expect(board(page).locator('[data-kind="highlight"]')).toHaveCount(1);

  // 后退一步：点点的公式消失
  await page.locator('[data-stage-action="prev"]').click();
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');
  await expect(board(page).locator('[data-el="f3"]')).toHaveCount(0);

  // 播放：按计时逐步前进直到结束；第 5 步擦掉 f2 时连同高亮
  await page.locator('[data-stage-action="play"]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');
  await expect(status(page)).toHaveAttribute('data-stage-status', 'ended', { timeout: 10_000 });
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '6');
  await expect(board(page).locator('[data-el="f2"]')).toHaveCount(0);
  await expect(board(page).locator('[data-kind="highlight"]')).toHaveCount(0);
  await expect(board(page).locator('[data-speaking="true"]')).toHaveCount(0);
  await expect(page.locator('[data-stage-action="play"]')).toBeDisabled();
  await axe(page);

  // 重来
  await page.locator('[data-stage-action="reset"]').click();
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '0');
  await expect(board(page).locator('[data-el]')).toHaveCount(0);

  expect(off).toEqual([]);
});

test('样张页：三套布局下白板占舞台高度 ≥ 65%，角色占位框不压白板', async ({ page }) => {
  await page.goto('/dev/stage');
  await page.locator('[data-stage-action="seek"]').selectOption('6');
  for (const [label, name] of [['桌面', 'desktop'], ['平板', 'tablet'], ['手机', 'mobile']] as const) {
    await page.getByLabel(label, { exact: true }).check();
    await expect(board(page)).toHaveAttribute('data-stage-layout', name);
    const svg = (await board(page).boundingBox())!;
    const wb = (await board(page).locator('[data-stage-part="whiteboard"]').boundingBox())!;
    expect(wb.height / svg.height).toBeGreaterThanOrEqual(0.65);
    for (const role of ['teacher', 'buddy']) {
      const a = (await board(page).locator(`[data-anchor="${role}"] rect`).boundingBox())!;
      expect(a.y, `${name} ${role} 在白板下方`).toBeGreaterThanOrEqual(wb.y + wb.height);
    }
  }
  await axe(page);
});

test('样张页：非法样例被拒；渲染结果没有外链；不写浏览器存储', async ({ page }) => {
  await page.goto('/dev/stage');
  const codes = await page.locator('[data-stage-invalid] [data-error-code]').evaluateAll((els) => els.map((e) => e.getAttribute('data-error-code')));
  expect(codes).toEqual(['link_or_script', 'html', 'unknown_field', 'bad_latex', 'bad_role']);
  await page.locator('[data-stage-action="seek"]').selectOption('6');
  const markup = await board(page).evaluate((el) => el.outerHTML);
  expect(markup).not.toMatch(/https?:|\/\/|href|<a[\s>]|<script|<image|<img|<iframe|<foreignObject|<use[\s>]|\sstyle=|url\(/i);
  const external = await page.evaluate(() => [...document.querySelectorAll('[src],[href]')]
    .map((e) => e.getAttribute('src') ?? e.getAttribute('href') ?? '')
    .filter((u) => /^(https?:)?\/\//.test(u) && !u.startsWith(location.origin)));
  expect(external).toEqual([]);
  const stored = await page.evaluate(async () => ({
    local: localStorage.length, session: sessionStorage.length,
    idb: typeof indexedDB.databases === 'function' ? (await indexedDB.databases()).length : 0,
  }));
  expect(stored).toEqual({ local: 0, session: 0, idb: 0 });
});
