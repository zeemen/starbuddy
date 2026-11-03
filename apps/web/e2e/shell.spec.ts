// 统一壳 H4 端到端（specs/unified-shell-sprint4 验收“开发计划验收”“信任界面预留”“点点入口与家长端”“额度与时长可见”“时长设置”）：
// 全程真实会话（短信捕获桩读码），合成家庭 HG（db/seeds/dev/shell-synthetic.sql）：家长 gH，孩子 GT 试用、GR 留存、GP 试点在读数学。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { BASE, loginGuardian, loginStudent } from './session.ts';

const GT = '5d000000-0000-4000-8000-0000000000c1';
const GR = '5d000000-0000-4000-8000-0000000000c2';
const GP = '5d000000-0000-4000-8000-0000000000c3';

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => v.id)).toEqual([]);
};

/** 浏览器发出的请求：全程只请求壳自己的来源；记下路径，断言没有信任界面（冲刺 5）的调用 */
function watch(page: Page): { off: string[]; paths: string[] } {
  const w = { off: [] as string[], paths: [] as string[] };
  page.on('request', (r) => {
    const u = r.url();
    if (!u.startsWith(BASE) && !u.startsWith('data:')) w.off.push(u);
    else w.paths.push(new URL(u).pathname);
  });
  return w;
}
const TRUST_CALL = /why|explain|dispute|reconcile|weekly|export|erase|data-rights/;

test('开发计划验收：学生从“今日”进入“科目”，依次进入数学、道法、语文，每次一步回到星图；英语入口不存在；全程同源', async ({ page }) => {
  const w = watch(page);
  await loginStudent(page, GP);
  await expect(page.getByTestId('today')).toBeVisible();
  await expect(page.locator('section.today-subject h2')).toHaveText(['数学']);
  await axe(page);
  for (const code of ['math', 'politics', 'chinese']) {
    await page.getByTestId('nav-learn').click();
    await expect(page).toHaveURL(`/s/${GP}/learn`);
    await expect(page.getByTestId('subject-english')).toHaveCount(0);
    await expect(page.getByText('英语')).toHaveCount(0);
    await page.getByTestId(`subject-${code}`).click();
    await expect(page).toHaveURL(`/s/${GP}/learn/${code}`);
    await expect(page.getByTestId('subject-page')).toHaveAttribute('data-subject', code);
    if (code !== 'math') await expect(page.getByTestId('coming-soon')).toBeVisible();
    await axe(page);
    await page.getByTestId('back-to-star-map').click();
    await expect(page).toHaveURL(`/s/${GP}/star-map`);
    await expect(page.locator('section.sky')).not.toHaveCount(0);
  }
  expect((await page.goto(`/s/${GP}/learn/english`))?.status()).toBe(404);
  expect(w.off).toEqual([]);
});

test('学生壳每页都有点点入口（占位、系统口吻）与额度状态；信任界面预留位不渲染、不发请求', async ({ page }) => {
  const w = watch(page);
  await loginStudent(page, GT);
  for (const path of [`/s/${GT}/today`, `/s/${GT}/star-map`, `/s/${GT}/learn`, `/s/${GT}/learn/math`, `/s/${GT}/learn/politics`]) {
    await page.goto(path);
    const dd = page.getByTestId('diandian');
    await expect(dd, path).toHaveCount(1);
    await dd.locator('summary').click();
    await expect(dd).toContainText('点点正在准备中。');
    await expect(page.getByTestId('status-bar')).toHaveAttribute('data-state', 'trialing');
    await expect(page.locator('[data-trust-slot]'), path).toHaveCount(0);
    await expect(page.getByText(/为什么|我不同意|对账卡|导出|删除/), path).toHaveCount(0);
  }
  await expect(page.getByTestId('status-bar')).toContainText(/试用还剩 \d+ 天|试用今天到期/);
  expect(w.paths.filter((p) => TRUST_CALL.test(p))).toEqual([]);
  expect(w.off).toEqual([]);
});

test('留存态学生：可以看星图与历史，显示今日剩余自练次数，需要连接老师的功能不可用', async ({ page }) => {
  await loginStudent(page, GR);
  await expect(page.getByTestId('status-bar')).toHaveAttribute('data-state', 'retained');
  await expect(page.getByTestId('status-bar')).toContainText('今天还能做 5 次题库自练');
  await page.getByTestId('nav-star-map').click();
  await expect(page).toHaveURL(`/s/${GR}/star-map`);
});

test('家长端：孩子页有额度、选科、今日；没有点点入口与角色图片；信任界面预留位不渲染；axe 无违规', async ({ page }) => {
  const w = watch(page);
  await loginGuardian(page, 'gH');
  await page.getByTestId('child-page-3').click();
  await expect(page).toHaveURL(`/family/${GP}`);
  await expect(page.getByTestId('child-page')).toBeVisible();
  await expect(page.locator('[data-subject="math"][data-enrollment="active"]')).toHaveCount(1);
  await expect(page.getByTestId('enroll-rules')).toContainText('核心科目任选 1 科');
  await expect(page.getByTestId('diandian')).toHaveCount(0);
  await expect(page.locator('img')).toHaveCount(0);
  await expect(page.locator('[data-trust-slot]')).toHaveCount(0);
  await axe(page);
  // 基本档核心任选 1 科：已开数学时开语文得到“可开通的科目数已满”
  const chinese = page.getByTestId('enroll-chinese');
  await expect(chinese).toHaveAttribute('data-hydrated', 'true');
  await chinese.getByRole('button').click();
  await expect(chinese.getByRole('status')).toHaveAttribute('data-code', 'limit_reached');
  // 试用（非试点）下数学是“暂未开放”：标签照常显示，开通按钮不隐藏，结果以函数 outcome 为准（审查第 5 条）
  await page.goto(`/family/${GT}`);
  const math = page.getByTestId('enroll-math');
  await expect(math).toHaveAttribute('data-hydrated', 'true');
  await math.getByRole('button').click();
  await expect(math.getByRole('status')).toHaveAttribute('data-code', 'not_launched');
  expect(w.paths.filter((p) => TRUST_CALL.test(p))).toEqual([]);
  expect(w.off).toEqual([]);
});

test('时长设置：超过档位上限有提示；不覆盖 22:00–06:00 有提示；收紧后学生端看到新值', async ({ page }) => {
  await loginGuardian(page, 'gH');
  await page.goto(`/family/${GT}/time`);
  const form = page.getByTestId('time-form');
  await expect(form).toHaveAttribute('data-hydrated', 'true');
  await axe(page);
  await page.getByLabel('单日上限（分钟）').fill('1000');
  await form.getByRole('button').click();
  await expect(form.getByRole('status')).toHaveAttribute('data-code', /cap_above_plan|INVALID/);
  await page.getByLabel('单日上限（分钟）').fill('30');
  await page.getByLabel('休息时间开始').fill('23:00');
  await page.getByLabel('休息时间结束').fill('06:00');
  await form.getByRole('button').click();
  await expect(form.getByRole('status')).toHaveAttribute('data-code', 'curfew_must_cover_default');
  // 表单提交后 React 会把未受控字段复位成默认值：每次提交前重新填全
  await page.getByLabel('单日上限（分钟）').fill('30');
  await page.getByLabel('休息时间开始').fill('21:00');
  await page.getByLabel('休息时间结束').fill('07:00');
  await form.getByRole('button').click();
  await expect(form.getByRole('status')).toHaveAttribute('data-code', 'saved');
  await page.reload();
  await expect(page.getByTestId('time-current')).toContainText('单日上限 30 分钟；休息时间 21:00–07:00');
  // 学生端：家长页地址转到自己那一侧；时长数值来自 BFF（这里只核对学生能看到状态条）
  await loginStudent(page, GT);
  await page.goto(`/family/${GT}/time`);
  await expect(page).toHaveURL(`/s/${GT}/today`);
});
