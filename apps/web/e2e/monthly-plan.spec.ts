// 月度计划（PLT-14 第 3 步，specs/family-goals-monthly-plan/step3-spec.md）开关打开的一套（playwright.config.ts 的 family-on 项目；
// 前端与第六个 BFF 都开 STARBUDDY_MONTHLY_PLAN=monthly_plan 与 STARBUDDY_TRUST_UI=why；BFF 连真实洞察服务，scripts/e2e.sh）。
// 合成家庭 HM（db/seeds/dev/monthly-plan-synthetic.sql）：家长 gM；M1 专业档（三个“待点亮”的掌握判断、一条到期复测、一次期中成绩与
// 引用它的考试判断、gM 的数学目标分）；M2 基础档（没有月度计划权益）。核对：
//   - 家长从孩子页进入“月度计划”：打开即生成（真实洞察服务按合成输入生成快照），四块齐全——诉求摘要（只读，链到“我的诉求”）、
//     上月回顾与分差区间（10–15，不出成绩与目标分）、学习计划（每条优先知识点带 data-claim-id 与“为什么”入口）、家长注意事项；
//     同月再次打开不再生成（生成时间不变）；“为什么”能打开；
//   - 没有权益的孩子显示升级说明占位，没有任何计划内容；
//   - 学生端：今日页有“本月计划”入口，页面只有学习计划（没有诉求、注意事项、回顾、分差）；还没有生成时是空状态；
//     学生直接打开家长端页面被转回自己的今日；学生会话调 BFF 的家长端接口得到与“不存在”逐字节相同的 404；
//   - 新页面 axe（wcag2a / wcag2aa）无违规、运行时 DOM 没有内部编码泄露。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { expectNoDomLeak } from './dom-leak.ts';
import { cookieSession, FAMILY_BFF, loginGuardian, loginStudent } from './session.ts';
import { crawlClaimBinding, ledger } from './claim-binding.ts';

const M1 = '57540000-0000-4000-8000-0000000000c1';
const M2 = '57540000-0000-4000-8000-0000000000c2';
const MISSING = '57540000-0000-4000-8000-00000000ffff';
/** 学生端不得出现的家长端内容：区块、文案键、字段名、诉求内容 */
const GUARDIAN_ONLY = ['monthly-plan-goals', 'monthly-plan-review', 'monthly-plan-gaps', 'monthly-plan-notes', 'monthly-plan-not-entitled',
                       'monthly_plan.page.', 'monthly_plan.guardian.', 'monthly_plan.review.', 'guardian_notes', 'score_gaps', 'gap_band', 'last_month',
                       'focus_subjects', 'target_scores', 'family-goals', '合成说明', '/monthly-plan'] as const;

const axe = async (page: Page) => {
  await expect(page).toHaveTitle(/\S/);
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
};

async function bffGet(page: Page, path: string): Promise<Response> {
  const s = await cookieSession(page);
  return fetch(`${FAMILY_BFF}/bff/v0${path}`, { headers: { 'x-sb-session': s.token } });
}

test('家长打开即生成：四块齐全、每条关于学生的句子带判断 ID 与“为什么”、分差只有区间；同月再次打开不再生成；axe 与 DOM 泄露核对', async ({ page }) => {
  await loginGuardian(page, 'gM');
  await page.goto(`/family/${M1}`);
  await page.getByTestId('child-monthly-plan').click();
  await expect(page).toHaveURL(new RegExp(`/family/${M1}/monthly-plan$`));
  const root = page.getByTestId('monthly-plan-page');
  await expect(root).toBeVisible();
  // 诉求摘要：家长本人写的（数学，目标 105 / 120），只读，链到“我的诉求”
  const goals = page.getByTestId('monthly-plan-goals');
  await expect(goals).toContainText('数学');
  await expect(goals.locator('[data-value="score"]')).toHaveText(['105', '120']);
  await expect(page.getByTestId('monthly-plan-goals-edit')).toHaveAttribute('href', `/family/${M1}/goals`);
  // 学习计划：三个待点亮的知识点（迁移的那个不是候选），每条一个判断 ID 与“为什么”入口；不编号
  const priority = page.getByTestId('plan-priority');
  await expect(priority.locator('[data-claim-id]')).toHaveCount(3);
  await expect(priority.locator('[data-trust-link="why"]')).toHaveCount(3);
  await expect(priority.locator('ol')).toHaveCount(0);
  await expect(priority).not.toContainText('MATH.SV1B.T1.N044');
  await expect(page.getByTestId('plan-retests').locator('[data-value="count"]')).toHaveText('1');
  await expect(page.getByTestId('plan-mini').locator('li')).toHaveCount(1);
  // 上月回顾：没有上月快照；分差 12.5 → 10–15 一档，带判断 ID，与下一步同屏；不出成绩与精确分差
  await expect(page.getByTestId('monthly-plan-review-none')).toBeVisible();
  const gaps = page.getByTestId('monthly-plan-gaps');
  await expect(gaps.locator('[data-claim-id]')).toHaveCount(1);
  // 区间在它所引判断的 data-claim-id 元素里（陈述与判断一一对应），不在判断之外单独出现
  await expect(gaps.locator('[data-claim-id] [data-value="score-band"]')).toHaveText('10–15');
  await expect(gaps.locator('[data-value="score-band"]')).toHaveCount(1);
  const html = await page.content();
  for (const s of ['92.5', '12.5', '名次', '排名', '概率']) expect(html.includes(s), s).toBe(false);
  // 家长注意事项：按计划事实选出的三条（复测、小复评、每周节奏）
  await expect(page.getByTestId('monthly-plan-notes').locator('li')).toHaveCount(3);
  await axe(page);
  await expectNoDomLeak(page);
  // 同月再次打开：已有快照，不再生成（生成时间不变，响应逐字节相同）
  const first = await bffGet(page, `/students/${M1}/monthly-plan`);
  expect(first.status).toBe(200);
  const a = await first.text();
  const b = await (await bffGet(page, `/students/${M1}/monthly-plan`)).text();
  expect(b).toBe(a);
  expect((JSON.parse(a) as { entitled: boolean }).entitled).toBe(true);
  await page.reload();
  await expect(page.getByTestId('plan-priority').locator('[data-claim-id]')).toHaveCount(3);
  // “为什么”能打开（单条解释经洞察服务 insight.explain）
  const claim = await priority.locator('[data-claim-id]').first().getAttribute('data-claim-id');
  await priority.locator(`[data-trust-link="why"][data-for-claim="${claim}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/family/${M1}/why/${claim}$`));
  await expect(page.getByTestId('why-page')).toBeVisible();
  await expect(page.locator('[data-unavailable="why"]')).toHaveCount(0);
  await expectNoDomLeak(page);
});

test('没有权益的孩子：显示升级说明占位，没有任何计划内容', async ({ page }) => {
  await loginGuardian(page, 'gM');
  await page.goto(`/family/${M2}/monthly-plan`);
  await expect(page.getByTestId('monthly-plan-not-entitled')).toBeVisible();
  for (const id of ['plan-priority', 'monthly-plan-review', 'monthly-plan-notes', 'monthly-plan-student']) await expect(page.getByTestId(id)).toHaveCount(0);
  await expect(page.locator('[data-claim-id]')).toHaveCount(0);
  const r = await bffGet(page, `/students/${M2}/monthly-plan`);
  expect(await r.json()).toEqual({ student_id: M2, month: expect.stringMatching(/^\d{4}-\d{2}$/), entitled: false, plan: null });
  await axe(page);
  await expectNoDomLeak(page);
});

async function expectStudentOnlyPlan(page: Page): Promise<void> {
  const html = await page.content();
  for (const m of GUARDIAN_ONLY) expect(html.includes(m), `学生端出现了 ${m}`).toBe(false);
}

test('学生端：今日页有“本月计划”入口，页面只有学习计划；直接打开家长端页面被转回；BFF 家长端接口对学生会话 404', async ({ page }) => {
  await loginStudent(page, M1);
  await page.getByTestId('today-my-plan').click();
  await expect(page).toHaveURL(new RegExp(`/s/${M1}/plan$`));
  await expect(page.getByTestId('my-plan-page')).toBeVisible();
  const priority = page.getByTestId('plan-priority');
  await expect(priority.locator('[data-claim-id]')).toHaveCount(3);
  await expect(page.getByTestId('plan-rhythm')).toBeVisible();
  await expect(page.getByTestId('plan-retests')).toBeVisible();
  await expect(page.getByTestId('plan-mini')).toBeVisible();
  await expectStudentOnlyPlan(page);
  await axe(page);
  await expectNoDomLeak(page, { forbidden: [...GUARDIAN_ONLY] });
  await page.goto(`/s/${M1}/today`);
  await expectStudentOnlyPlan(page);
  await page.goto(`/family/${M1}/monthly-plan`);
  await expect(page).toHaveURL(new RegExp(`/s/${M1}/today$`));
  const missing = await bffGet(page, `/students/${MISSING}/monthly-plan`);
  const own = await bffGet(page, `/students/${M1}/monthly-plan`);
  expect(own.status).toBe(404);
  expect(await own.text()).toBe(await missing.text());
  const my = await bffGet(page, `/students/${M1}/my-plan`);
  const body = await my.text();
  expect(my.status).toBe(200);
  for (const m of ['guardian_notes', 'review', 'score_gaps', 'entitled', 'position', 'focus_factor', 'template_key', 'copy_key']) {
    expect(body.includes(`"${m}"`), m).toBe(false);
  }
});

test('学生端还没有生成本月计划时：空状态，不触发生成', async ({ page }) => {
  await loginStudent(page, M2);
  await page.getByTestId('today-my-plan').click();
  await expect(page.getByTestId('my-plan-empty')).toBeVisible();
  await expect(page.locator('[data-claim-id]')).toHaveCount(0);
  await expectStudentOnlyPlan(page);
  await axe(page);
  await expectNoDomLeak(page, { forbidden: [...GUARDIAN_ONLY] });
});


test('陈述—判断一致性（T21）：月度计划家长页与学生“本月计划”页，每句都挂着账本里本人可展示的判断', async ({ page }) => {
  const claims = ledger();
  await loginGuardian(page, 'gM');
  const seen = await crawlClaimBinding(page, M1, [`/family/${M1}/monthly-plan`, `/family/${M1}`], claims);
  expect(seen.size, '阳性对照：月度计划里确实有挂判断的句子').toBeGreaterThan(0);
  await crawlClaimBinding(page, M2, [`/family/${M2}/monthly-plan`], claims);
  await loginStudent(page, M1);
  await crawlClaimBinding(page, M1, [`/s/${M1}/plan`, `/s/${M1}/today`], claims);
});
