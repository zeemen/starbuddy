// 舞台样张页端到端（specs/stage-whiteboard-step1、specs/stage-dsl-v0.2 验收）：合成 DSL 在 /dev/stage 逐步播放（前进、后退、跳转、播放到结束、重来），
// 每步的字幕、讲解权与指向对齐；
// 非法样例被拒；三套布局下白板占舞台高度 ≥ 65%；讲解权只用 1.25 线表示；axe 无违规；全部请求同源、渲染结果无外链；
// 不写任何浏览器存储。样张页只在测试构建里存在（scripts/e2e.sh），生产构建里没有（tests/build-output.test.ts）。全部合成数据。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const board = (page: Page) => page.locator('svg[data-stage-layout]');
const status = (page: Page) => page.locator('[data-stage-status]');
const caption = (page: Page) => page.locator('[data-stage-caption]');
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
  // v0.2（specs/stage-dsl-v0.2）：这一步说一句（字幕取自台词稿、角色与讲解权一致），并用聚光指着题图槽位（空框）
  await expect(caption(page)).toHaveText('（合成台词）先看题目。');
  await expect(caption(page)).toHaveAttribute('data-stage-caption-role', 'teacher');
  await expect(board(page).locator('[data-slot="fig"]')).toHaveAttribute('data-slot-kind', 'item_figure');
  await expect(board(page).locator('[data-point="spotlight"]')).toHaveAttribute('data-target', 'fig');
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
  await expect(caption(page)).toHaveAttribute('data-stage-caption-role', 'buddy');
  await expect(board(page).locator('[data-point]')).toHaveCount(0);  // 第 3 步的激光笔到第 4 步已收起

  // 后退一步：点点的公式消失
  await page.locator('[data-stage-action="prev"]').click();
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');
  await expect(board(page).locator('[data-el="f3"]')).toHaveCount(0);
  await expect(board(page).locator('[data-point="laser"]')).toHaveAttribute('data-target', 'f2');

  // 播放：按计时逐步前进直到结束；第 5 步擦掉 f2 时连同高亮
  await page.locator('[data-stage-action="play"]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');
  await expect(status(page)).toHaveAttribute('data-stage-status', 'ended', { timeout: 10_000 });
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '7');
  await expect(caption(page)).toHaveText('');  // 最后一步不说话
  await expect(board(page).locator('[data-el="f2"]')).toHaveCount(0);
  await expect(board(page).locator('[data-kind="highlight"]')).toHaveCount(0);
  await expect(board(page).locator('[data-speaking="true"]')).toHaveCount(0);
  await expect(page.locator('[data-stage-action="play"]')).toBeDisabled();
  await axe(page);

  // 重来
  await page.locator('[data-stage-action="reset"]').click();
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '0');
  await expect(board(page).locator('[data-el]')).toHaveCount(0);
  await expect(caption(page)).toHaveText('');

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

// 指着元素提问（specs/stage-point-to-ask 第一阶段）：键盘与鼠标都能指着白板元素、槽位或这一步，选四种问题之一；
// “再讲一遍”跳回对应那一步重放；其余三种显示台词稿里的预制答疑，没有就兜底加“再讲一遍”；播放中选中先暂停；Esc 取消后焦点回到原元素；点击区不小于 44 像素；
// 提问不发请求、不写存储。
const ask = (page: Page, key: string) => board(page).locator(`[data-ask="${key}"]`);
const menu = (page: Page) => page.locator('[data-stage-ask-menu]');
const received = (page: Page) => page.locator('[data-stage-ask-received]');

test('样张页：指着元素提问（键盘、鼠标、再讲一遍、暂停、取消、点击区尺寸）', async ({ page, baseURL }) => {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(baseURL!) && !r.url().startsWith('data:')) off.push(r.url()); });
  await page.goto('/dev/stage');
  await expect(board(page).locator('[data-ask]')).toHaveCount(0);      // 空白板没有可指的东西
  await expect(page.locator('[data-stage-ask-step]')).toBeDisabled();
  await page.locator('[data-stage-action="seek"]').selectOption('3');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');

  // 只能指已经出现的东西：第 3 步时白板上的元素与题图槽位
  const keys = await board(page).locator('[data-ask]').evaluateAll((els) => els.map((e) => e.getAttribute('data-ask')));
  expect(keys).toContain('slot:fig');
  expect(keys.every((k) => k!.startsWith('element:') || k!.startsWith('slot:'))).toBe(true);
  expect(keys).not.toContain('element:f3');
  for (const k of keys) {
    const b = (await ask(page, k!).boundingBox())!;
    expect(Math.min(b.width, b.height), `${k} 点击区`).toBeGreaterThanOrEqual(44);
  }

  // 键盘：Tab 到第一个元素、回车选中 → 菜单出现、焦点在第一个问题上、选中虚线框
  const first = keys[0]!;
  await ask(page, first).focus();
  await page.keyboard.press('Enter');
  await expect(menu(page)).toHaveAttribute('data-stage-ask-menu', first);
  await expect(page.locator('[data-stage-ask-intent="why_step"]')).toBeFocused();
  await expect(board(page).locator(`[data-ask-selected="${first}"]`)).toHaveAttribute('stroke-dasharray', '4 4');
  await expect(ask(page, first)).toHaveAttribute('aria-pressed', 'true');
  await axe(page);
  // Esc 取消：菜单收起，焦点回到原元素
  await page.keyboard.press('Escape');
  await expect(menu(page)).toHaveCount(0);
  await expect(ask(page, first)).toBeFocused();
  await expect(board(page).locator('[data-ask-selected]')).toHaveCount(0);

  // 鼠标：点题图槽位，选“为什么” → 槽位算当前这一步（第 3 步），显示台词稿里这一步的预制答疑，菜单收起，步数不变
  await ask(page, 'slot:fig').click();
  await page.locator('[data-stage-ask-intent="why_step"]').click();
  await expect(menu(page)).toHaveCount(0);
  await expect(received(page)).toHaveAttribute('data-stage-ask-received', 'why_step');
  await expect(received(page)).toHaveAttribute('data-stage-ask-anchor', 'slot:fig');
  await expect(received(page).locator('[data-stage-ask-answer="a1"]')).toHaveText('（合成答疑）把加三移到右边，就变成减三。');
  await expect(received(page).locator('[data-stage-ask-answer="a1"]')).toHaveAttribute('data-stage-ask-answer-role', 'teacher');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');
  // 第 1 步写的元素问“是什么”：台词稿里没有这一条 → 兜底加“再讲一遍”，点了就跳回第 1 步重放
  await ask(page, first).click();
  await page.locator('[data-stage-ask-intent="what_is"]').click();
  await expect(received(page).locator('[data-stage-ask-answer]')).toHaveCount(0);
  await expect(received(page).locator('[data-stage-ask-fallback]')).toContainText('{{TODO_COPY:ask.no_answer}}');
  await received(page).locator('[data-stage-ask-again]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');
  await page.locator('[data-stage-action="pause"]').click();
  await page.locator('[data-stage-action="seek"]').selectOption('3');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');

  // 点空白处取消
  await ask(page, first).click();
  await expect(menu(page)).toHaveCount(1);
  await page.getByRole('heading', { level: 1 }).click();
  await expect(menu(page)).toHaveCount(0);

  // 问这一步 → 再讲一遍：跳回第 3 步的开头重放（先回到第 2 步之后，再播放）
  await page.locator('[data-stage-ask-step]').click();
  await expect(menu(page)).toHaveAttribute('data-stage-ask-menu', /^step:/);
  await page.locator('[data-stage-ask-intent="again"]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');
  await expect(board(page)).toHaveAttribute('data-stage-cursor', '3');
  await page.locator('[data-stage-action="pause"]').click();

  // 播放中选中先暂停
  await page.locator('[data-stage-action="reset"]').click();
  await page.locator('[data-stage-action="play"]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');
  await expect(board(page).locator('[data-ask]').first()).toBeVisible();
  await board(page).locator('[data-ask]').first().click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'paused');
  const at = await board(page).getAttribute('data-stage-cursor');
  await page.waitForTimeout(1500);
  await expect(board(page)).toHaveAttribute('data-stage-cursor', at!);   // 菜单开着时讲解不往下走
  // 元素的“再讲一遍”：跳回它写上去的那一步重放
  await page.locator('[data-stage-ask-intent="again"]').click();
  await expect(status(page)).toHaveAttribute('data-stage-status', 'playing');

  expect(off).toEqual([]);
  const stored = await page.evaluate(() => localStorage.length + sessionStorage.length);
  expect(stored).toBe(0);
});
