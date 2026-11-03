// 家长诉求（PLT-14 第 1 步，specs/family-goals-monthly-plan）开关打开的一套（playwright.config.ts 的 family-on 项目；前端与第六个 BFF 都开
// STARBUDDY_FAMILY_GOALS=family_goals）。合成家庭 HG2：家长 gG，初三 G9（在读数学、语文）、初二 G8（在读数学），两所合成的已发布学校
// （db/seeds/dev/family-goals-synthetic.sql）。核对：
//   - 家长从孩子页进入“我的诉求”，填写关注学科、目标分、关注学校与补充说明，保存为第 1 版；再改一次成为第 2 版，旧版进历史；
//   - 校验错误的提示：补充说明命中禁用推断词表（不存）、目标分超过满分（本地拦下）；初二没有学校选择；
//   - 新页面 axe（wcag2a / wcag2aa）无违规、运行时 DOM 没有内部编码泄露；
//   - 学生端：今日、科目列表、科目、星图页面里没有诉求的元素、字段名、路由与文案占位键；学生直接打开诉求页被转回自己的今日；
//     学生会话直接调 BFF 的诉求接口得到与“不存在”逐字节相同的 404。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { expectNoDomLeak } from './dom-leak.ts';
import { cookieSession, FAMILY_BASE, FAMILY_BFF, loginGuardian, loginStudent } from './session.ts';

const G9 = '57530000-0000-4000-8000-0000000000c9';
const G8 = '57530000-0000-4000-8000-0000000000c8';
const SCHOOL = '57530000-0000-4000-8000-0000000005c1';
const NOTE = '合成说明：每周三晚上先做数学函数练习';
const GOAL_MARKERS = ['family-goals', 'family_goals', 'focus_subjects', 'target_scores', 'school_options', 'guardian_goal', '/goals'] as const;

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
};

async function submit(page: Page): Promise<string> {
  const form = page.getByTestId('family-goals-form');
  await expect(form).toHaveAttribute('data-hydrated', 'true');
  await form.getByRole('button').click();
  const status = form.locator('[role="status"]');
  await expect(status).not.toHaveAttribute('data-code', '');
  return (await status.getAttribute('data-code')) ?? '';
}

test('家长读写诉求：孩子页入口 → 填写保存为第 1 版 → 修改为第 2 版、旧版进历史；axe 与 DOM 泄露核对', async ({ page }) => {
  await loginGuardian(page, 'gG');
  await page.goto(`/family/${G9}`);
  await page.getByTestId('child-family-goals').click();
  await expect(page).toHaveURL(new RegExp(`/family/${G9}/goals$`));
  await expect(page.getByTestId('family-goals-page')).toBeVisible();
  await expect(page.getByTestId('family-goals-current')).toContainText('还没有填写');
  await axe(page);
  await expectNoDomLeak(page);
  // 只列在读学科（数学、语文）；学校名单只有已发布的合成学校
  await expect(page.getByRole('checkbox')).toHaveCount(2);
  await expect(page.locator('#school option')).toHaveCount(3);  // 不选 + 两所
  await page.getByRole('checkbox', { name: '数学' }).check();
  await page.locator('#full-aya').fill('120');
  await page.locator('#target-aya').fill('105.5');
  await page.locator('#school').selectOption(SCHOOL);
  await page.locator('#note').fill(NOTE);
  expect(await submit(page)).toBe('saved');
  await page.reload();
  await expect(page.getByTestId('family-goals-current')).toContainText('第 1 版');
  await expect(page.getByRole('checkbox', { name: '数学' })).toBeChecked();
  await expect(page.locator('#full-aya')).toHaveValue('120');
  await expect(page.locator('#target-aya')).toHaveValue('105.5');
  await expect(page.locator('#school')).toHaveValue(SCHOOL);
  await expect(page.locator('#note')).toHaveValue(NOTE);
  // 第二版：加一科语文、去掉学校
  await page.getByRole('checkbox', { name: '语文' }).check();
  await page.locator('#school').selectOption('');
  expect(await submit(page)).toBe('saved');
  await page.reload();
  await expect(page.getByTestId('family-goals-current')).toContainText('第 2 版');
  await expect(page.getByTestId('family-goals-history').locator('li')).toHaveCount(1);
  await expect(page.locator('#school')).toHaveValue('');
  await axe(page);
  await expectNoDomLeak(page);
});

test('校验错误的提示：补充说明命中禁用推断词表不存、目标分超过满分本地拦下；初二没有学校选择', async ({ page }) => {
  await loginGuardian(page, 'gG');
  await page.goto(`/family/${G9}/goals`);
  const before = await page.getByTestId('family-goals-current').textContent();
  await page.locator('#note').fill('孩子有点懒惰');
  expect(await submit(page)).toBe('TEXT_NOT_ACCEPTED');
  await expect(page.getByTestId('family-goals-form').locator('[role="status"]')).toContainText('不能保存的表述');
  await page.reload();
  await expect(page.getByTestId('family-goals-current')).toHaveText(before ?? '');
  await expect(page.locator('#note')).not.toHaveValue('孩子有点懒惰');
  await page.locator('#full-aya').fill('100');
  await page.locator('#target-aya').fill('101');
  expect(await submit(page)).toBe('INVALID');
  await expectNoDomLeak(page);
  // 初二：可以设关注学科与目标分，没有学校选择
  await page.goto(`/family/${G8}/goals`);
  await expect(page.getByTestId('family-goals-school-not-yet')).toBeVisible();
  await expect(page.locator('#school')).toHaveCount(0);
  await page.getByRole('checkbox', { name: '数学' }).check();
  expect(await submit(page)).toBe('saved');
  await axe(page);
  await expectNoDomLeak(page);
});

async function expectNoGoals(page: Page): Promise<void> {
  await expect(page.locator('[data-testid^="family-goals"], [data-testid="child-family-goals"], a[href*="/goals"]')).toHaveCount(0);
  const html = await page.content();
  for (const m of GOAL_MARKERS) expect(html.includes(m), `页面里出现了 ${m}`).toBe(false);
  for (const s of [NOTE, '合成高中甲']) expect(html.includes(s), `学生端出现了家长写的诉求 ${s}`).toBe(false);
}

test('学生端：今日、科目、星图页面没有诉求的元素、字段名、路由与内容；学生直接打开诉求页被转回；BFF 对学生会话 404', async ({ page }) => {
  await loginStudent(page, G9);
  for (const path of [`/s/${G9}/today`, `/s/${G9}/learn`, `/s/${G9}/learn/math`, `/s/${G9}/star-map`]) {
    const r = await page.goto(path);
    expect(r?.status(), path).toBe(200);
    await expectNoGoals(page);
  }
  await page.goto(`/s/${G9}/today`);
  await expectNoDomLeak(page, { forbidden: [...GOAL_MARKERS] });
  await page.goto(`/family/${G9}/goals`);
  await expect(page).toHaveURL(new RegExp(`/s/${G9}/today$`));
  await expectNoGoals(page);
  const s = await cookieSession(page);
  const missing = await fetch(`${FAMILY_BFF}/bff/v0/students/57530000-0000-4000-8000-00000000ffff/family-goals`, { headers: { 'x-sb-session': s.token } });
  for (const [method, body] of [['GET', undefined], ['PUT', JSON.stringify({ focus_subjects: ['aya'], target_scores: [], school_id: null, note: null })]] as const) {
    const r = await fetch(`${FAMILY_BFF}/bff/v0/students/${G9}/family-goals`, {
      method, body,
      headers: { 'x-sb-session': s.token, ...(method === 'PUT' ? { 'x-csrf-token': s.csrf, origin: FAMILY_BASE, 'content-type': 'application/json' } : {}) },
    });
    expect(r.status, method).toBe(404);
    expect(await r.text(), method).toBe(await missing.clone().text());
  }
});
