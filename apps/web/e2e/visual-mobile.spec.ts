// LT-07 移动端视觉回归（@visual，只由 make web-visual 运行）：三个移动设备项目（mobile-iphone13、mobile-pixel7、mobile-360）各截
// 登录页、离线页、学生星图（S1）、家长首页（gS，“添加到主屏幕”引导收起）。合成数据、固定布局；截图按 DPR 1（基线小、与桌面一套一致），
// 文件名带项目名（snapshotPathTemplate 不含项目）。基线首次提交要有视觉负责人签字（lessons 待人确认，同 visual.spec.ts）。
import { expect, test } from '@playwright/test';
import { openAs } from './session.ts';

const S1 = '5a000000-0000-4000-8000-0000000000c1';

test.use({ deviceScaleFactor: 1 });

test('@visual 移动端：登录页', async ({ page }, info) => {
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  await expect(page).toHaveScreenshot(`${info.project.name}-login.png`, { fullPage: true });
});

test('@visual 移动端：离线页', async ({ page }, info) => {
  await page.goto('/offline.html');
  await expect(page).toHaveScreenshot(`${info.project.name}-offline.png`, { fullPage: true });
});

test('@visual 移动端：学生星图 S1', async ({ page }, info) => {
  await openAs(page, S1, `/s/${S1}/star-map`);
  await expect(page).toHaveScreenshot(`${info.project.name}-star-map-s1.png`, { fullPage: true });
});

test('@visual 移动端：家长首页 gS', async ({ page }, info) => {
  await openAs(page, 'gS', '/family');
  await expect(page.getByTestId('logout')).toHaveAttribute('data-hydrated', 'true');
  await expect(page).toHaveScreenshot(`${info.project.name}-family-home.png`, { fullPage: true });
});
