// H5 星图 v1 开关关闭（默认）时：前端与 BFF 都是 3-H v0 原样（playwright.config.ts 的 trust-off 项目；星图开关全关）。
// v0 的断言在 e2e/star-map.spec.ts 与 e2e/visual.spec.ts 里不改；这里只补“开关关时 v1 不出现”：
// 同一名合成学生（V1，db/seeds/dev/star-map-v1-synthetic.sql，开关打开的一套里是 v1 星图）在这一套里打开的是 v0 页面，
// 页面上没有任何 v1 元素与 data-claim-id，BFF 的 v1 路径没有注册（与表外路径同一个 404）。
import { expect, test } from '@playwright/test';
import { BFF, cookieSession, openAs } from './session.ts';

const V1 = '5e000000-0000-4000-8000-0000000000c1';

test('开关关：星图页是 v0（v1 的星座、中性底星、侧栏、data-claim-id 都不出现）；BFF 的 v1 路径 404', async ({ page }) => {
  const paths: string[] = [];
  page.on('request', (r) => paths.push(new URL(r.url()).pathname));
  for (const [who, path] of [[V1, `/s/${V1}/star-map`], ['gV', `/family/${V1}/star-map`]] as const) {
    const res = await openAs(page, who, path);
    expect(res?.status(), path).toBe(200);
    await expect(page.getByTestId('watermark')).toHaveText('原型 · 合成数据');
    await expect(page.locator('section.sky').first()).toBeVisible();  // v0 的星空
    await expect(page.locator('[data-starmap], .sky-v1, .star-slot, [data-testid="skill-view"], [data-testid^="building-"]')).toHaveCount(0);
    await expect(page.locator('[data-claim-id]')).toHaveCount(0);
    await expect(page.locator('main')).not.toContainText(/地图建设中|能力视图|暂无可展示的结论/);
    const { token } = await cookieSession(page);
    const r = await fetch(`${BFF}/bff/v0/students/${V1}/star-map/v1`, { headers: { 'x-sb-session': token } });
    expect(r.status, 'BFF 开关关时 v1 路由未注册').toBe(404);
  }
  expect(paths.filter((p) => /star-map\/v1|skill/.test(p))).toEqual([]);
});
