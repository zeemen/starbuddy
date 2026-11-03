// 月度计划（PLT-14 第 3 步，specs/family-goals-monthly-plan/step3-spec.md）开关关闭时（trust-off 项目：前端与 BFF 的 STARBUDDY_MONTHLY_PLAN 都关）：
// 与开关打开前零差异——家长孩子页与学生今日页没有入口元素，两个页面 404（不读会话、不发请求），BFF 两条路由不注册（与表外路径同一个 404）。
// 合成学生 S1 与家长 gS 见 db/seeds/dev/star-map-synthetic.sql。
import { expect, test } from '@playwright/test';
import { BFF, cookieSession, loginGuardian, loginStudent } from './session.ts';

const S1 = '5a000000-0000-4000-8000-0000000000c1';

test('开关关：家长孩子页没有月度计划入口；页面 404；BFF 的两条路由与表外路径同一个 404', async ({ page }) => {
  await loginGuardian(page, 'gS');
  await page.goto(`/family/${S1}`);
  await expect(page.getByTestId('child-page')).toBeVisible();
  await expect(page.locator('[data-testid="child-monthly-plan"], a[href*="/monthly-plan"]')).toHaveCount(0);
  expect((await page.goto(`/family/${S1}/monthly-plan`))?.status()).toBe(404);
  const s = await cookieSession(page);
  const outside = await fetch(`${BFF}/bff/v0/definitely-not-a-route`, { headers: { 'x-sb-session': s.token } });
  const outsideText = await outside.text();
  for (const leaf of ['monthly-plan', 'my-plan']) {
    const r = await fetch(`${BFF}/bff/v0/students/${S1}/${leaf}`, { headers: { 'x-sb-session': s.token } });
    expect(r.status, leaf).toBe(404);
    expect(await r.text(), leaf).toBe(outsideText);
  }
});

test('开关关：学生今日页没有“本月计划”入口；学生的计划页 404', async ({ page }) => {
  await loginStudent(page, S1);
  await expect(page.getByTestId('today')).toBeVisible();
  await expect(page.locator('[data-testid="today-my-plan"], a[href$="/plan"]')).toHaveCount(0);
  expect((await page.goto(`/s/${S1}/plan`))?.status()).toBe(404);
});
