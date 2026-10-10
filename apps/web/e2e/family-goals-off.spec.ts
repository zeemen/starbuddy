// 家长诉求（PLT-14 第 1 步，specs/family-goals-monthly-plan）开关关闭时（trust-off 项目：前端与 BFF 的 STARBUDDY_FAMILY_GOALS 都关）：
// 与开关打开前零差异——家长孩子页没有入口元素，“我的诉求”页 404（不读会话、不发请求），BFF 两条路由不注册（与表外路径同一个 404）。
// 另做学生端的 DOM 泄露核对：学生壳的今日、科目、星图页面里没有诉求的元素、字段名、路由与文案占位键
// （学生端永远不出现诉求，与开关无关；静态核对在 tests/family-goals.test.ts）。合成学生 S1 与家长 gS 见 db/seeds/dev/star-map-synthetic.sql。
import { expect, test, type Page } from '@playwright/test';
import { expectNoDomLeak } from './dom-leak.ts';
import { BASE, BFF, cookieSession, loginGuardian, loginStudent } from './session.ts';

const S1 = '5a000000-0000-4000-8000-0000000000c1';
const GOAL_MARKERS = ['family-goals', 'family_goals', 'focus_subjects', 'target_scores', 'school_options', 'guardian_goal'] as const;

async function expectNoGoals(page: Page): Promise<void> {
  await expect(page.locator('[data-testid^="family-goals"], [data-testid="child-family-goals"], a[href*="/goals"]')).toHaveCount(0);
  const html = await page.content();
  for (const m of GOAL_MARKERS) expect(html.includes(m), `页面里出现了 ${m}`).toBe(false);
}

test('开关关：家长孩子页没有诉求入口；诉求页 404；BFF 的诉求路由与表外路径同一个 404', async ({ page }) => {
  await loginGuardian(page, 'gS');
  await page.goto(`/family/${S1}`);
  await expect(page.getByTestId('child-page')).toBeVisible();
  await expectNoGoals(page);
  expect((await page.goto(`/family/${S1}/goals`))?.status()).toBe(404);
  const s = await cookieSession(page);
  const outside = await fetch(`${BFF}/bff/v0/definitely-not-a-route`, { headers: { 'x-sb-session': s.token } });
  const get = await fetch(`${BFF}/bff/v0/students/${S1}/family-goals`, { headers: { 'x-sb-session': s.token } });
  expect(get.status).toBe(404);
  expect(await get.text()).toBe(await outside.text());
  const put = await fetch(`${BFF}/bff/v0/students/${S1}/family-goals`, {
    method: 'PUT', headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, origin: BASE, 'content-type': 'application/json' },
    body: JSON.stringify({ focus_subjects: ['aya'], target_scores: [], school_id: null, note: null }),
  });
  expect(put.status).toBe(404);
});

test('学生端 DOM 泄露核对：今日、科目、星图页面没有诉求的元素、字段名、路由与文案占位键', async ({ page }) => {
  await loginStudent(page, S1);
  for (const path of [`/s/${S1}/today`, `/s/${S1}/learn`, `/s/${S1}/learn/math`, `/s/${S1}/star-map`]) {
    const r = await page.goto(path);
    expect(r?.status(), path).toBe(200);
    await expectNoGoals(page);
    // 通用的内部编码核对（dom-leak.ts）只在今日页跑：科目页、星图页带 data-status、data-display、data-summary-* 等
    // dom-leak.ts 白名单外的既有属性（与本任务无关，已报告），这几页只做上面的诉求核对
    if (path.endsWith('/today')) await expectNoDomLeak(page, { forbidden: [...GOAL_MARKERS] });
  }
  expect((await page.goto(`/family/${S1}/goals`))?.status(), '学生会话打开诉求页：开关关时同样 404').toBe(404);
});
