// 讲评课页面（specs/stage-lesson-session ③b-5；playwright.config.ts 的 practice-on 项目：前端开 lesson，第五个 BFF 开 lesson，
// 学习服务是合成桩 scripts/fake-learning-practice.ts 的讲评课剧本；P1 的讲评课额度来自 db/seeds/dev/practice-synthetic.sql 的一笔合成调整 +5，
// P3 没有权益）。全程真实会话（短信捕获桩读码），计量在一次性库里真实发生（meter_reserve / settle / release）。
// 覆盖（规格第四节验收的端到端部分）：
//   - 打开：页面载入不打开、点“开始”才打开（扣一次）；讲解与字幕台词逐步播放；
//   - 提问：指着这一步问“为什么”→ 回答显示在字幕区下方；问“是什么”→ 没有答疑，显示兜底与“再讲一遍”；到了上限只剩“再讲一遍”，提问不扣次；
//   - 兜底：这节课打不开（学习服务确定拒绝，BFF 释放预留，不扣）；额度不足（P3，402）；
//   - 页面上没有票据与内部字段；axe 无违规；浏览器只请求本前端；地址栏参数不合格 404。
//   - 安全头：讲评课页面（普通页面，specs/stage-production-build）的 CSP、no-store 等与壳内其他页面一致。
//   - 讲评课目录（specs/lesson-catalog PR D，第六节 D）：科目页列表 → 点进讲评课页面打开、播放；再练（106）与复测（402）作答之后
//     出现“看这道题的讲评课”，作答前不出现；地址只带编号与版本，以前带 node、item 的地址 404。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { LESSON, LESSON_ERROR, PRACTICE, RETEST, STAGE_ASK } from '../src/shell/copy.ts';
import { checkHeaders, checkHtml } from './headers-check.ts';
import { targets } from './crawl-pages.ts';
import { loginStudent, PRACTICE_BASE } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P3 = '59000000-0000-4000-8000-0000000000c3';
const ANSWER = '（合成答疑）因为这样写能看清楚每一步。';
const url = (s: string, lesson = 'syn.lesson.e2e') => `/s/${s}/learn/math/lesson/${lesson}?v=0.1.0`;
const E = (n: string) => `59000000-0000-4000-8000-000000000${n}`;
const board = (page: Page) => page.locator('svg[data-stage-layout]');
const INTERNAL = ['sbl1.', 'lt1.', 'usage_id', 'session_id', 'question_seq', 'idempotency_key', 'syn.lesson.e2e@', 'skill_node', 'item_ref', 'K-QF-0301'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

async function noLeak(page: Page): Promise<void> {
  const html = await page.content();
  for (const s of INTERNAL) expect(html.includes(s), s).toBe(false);
}

test('打开、播放、提问：点“开始”才打开；回答在字幕区下方；没有答疑兜底；到上限只剩“再讲一遍”', async ({ page }) => {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  await loginStudent(page, P1);
  await page.goto(url(P1));
  await expect(page.getByTestId('lesson-page')).toBeVisible();
  // 页面载入不打开：只有“开始”按钮，白板还没有
  await expect(page.locator('[data-lesson-start]')).toHaveText(LESSON.start);
  await expect(board(page)).toHaveCount(0);
  await axe(page);
  await page.locator('[data-lesson-start]').click();
  await expect(board(page)).toBeVisible();
  await expect(page.locator('[data-value="questions-left"]')).toHaveText('3');
  // 逐步播放：第一步的字幕台词
  await page.locator('[data-stage-action="next"]').click();
  await expect(page.locator('[data-stage-caption]')).toHaveText('（合成台词）先看第一行。');
  await axe(page);
  // 问这一步“为什么” → 回答显示在字幕区下方
  await page.locator('[data-stage-ask-step]').click();
  await page.locator('[data-stage-ask-intent="why_step"]').click();
  await expect(page.locator('[data-lesson-answer]')).toHaveText(ANSWER);
  await expect(page.locator('[data-value="questions-left"]')).toHaveText('2');
  // 指着白板元素问“是什么” → 没有答疑：兜底与“再讲一遍”
  await board(page).locator('[data-ask="element:t1"]').click();
  await page.locator('[data-stage-ask-intent="what_is"]').click();
  await expect(page.locator('[data-lesson-no-answer="not_available"]')).toContainText(STAGE_ASK.no_answer);
  await expect(page.locator('[data-stage-ask-again]')).toHaveText(STAGE_ASK.again);
  await expect(page.locator('[data-value="questions-left"]')).toHaveText('1');
  // 第三问用掉最后一次；之后菜单只剩“再讲一遍”
  await page.locator('[data-stage-ask-step]').click();
  await page.locator('[data-stage-ask-intent="where_from"]').click();
  await expect(page.locator('[data-lesson-answer]')).toHaveText(ANSWER);
  await expect(page.locator('[data-value="questions-left"]')).toHaveText('0');
  await page.locator('[data-stage-ask-step]').click();
  await expect(page.locator('[data-stage-ask-intent]')).toHaveCount(1);
  await expect(page.locator('[data-stage-ask-intent="again"]')).toBeVisible();
  // “再讲一遍”本地重放：回到这一步开头再播放，不发提问
  await page.locator('[data-stage-ask-intent="again"]').click();
  await expect(page.locator('[data-stage-status]')).toHaveAttribute('data-stage-status', /playing|ended/);
  await noLeak(page);
  await axe(page);
  expect(off).toEqual([]);
  // 浏览器存储里没有票据
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(stored.includes('sbl1')).toBe(false);
});

test('这节课打不开（学习服务确定拒绝）：显示“暂时打不开”，不出白板；可以重新开始', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(url(P1, 'syn.lesson.broken'));
  await page.locator('[data-lesson-start]').click();
  await expect(page.locator('[data-lesson-error="unavailable"]')).toHaveText(LESSON_ERROR.unavailable);
  await expect(board(page)).toHaveCount(0);
  await expect(page.locator('[data-lesson-start]')).toBeVisible();
  await axe(page);
});

test('额度不足（P3 没有权益）：402 → “现在不能开始”，不出白板、没有重试按钮', async ({ page }) => {
  await loginStudent(page, P3);
  await page.goto(url(P3));
  await page.locator('[data-lesson-start]').click();
  await expect(page.locator('[data-lesson-error="quota"]')).toHaveText(LESSON_ERROR.quota);
  await expect(board(page)).toHaveCount(0);
  await expect(page.locator('[data-lesson-start]')).toHaveCount(0);
  await axe(page);
});

test('地址栏参数不合格、学科没有讲评课：404', async ({ page }) => {
  await loginStudent(page, P1);
  for (const bad of [`/s/${P1}/learn/math/lesson/Bad?v=0.1.0`, `/s/${P1}/learn/math/lesson/syn.lesson.e2e?v=v1`,
                     `/s/${P1}/learn/math/lesson/syn.lesson.e2e`, `/s/${P1}/learn/politics/lesson/syn.lesson.e2e?v=0.1.0`,
                     // 以前带节点与题的地址（specs/lesson-catalog 第五节第 4 条）
                     `/s/${P1}/learn/math/lesson/syn.lesson.e2e?v=0.1.0&node=K-QF-0301`, `/s/${P1}/learn/math/lesson/syn.lesson.e2e?v=0.1.0&item=SYN-1`]) {
    const r = await page.goto(bad);
    expect(r?.status(), bad).toBe(404);
  }
});

test('目录（E1）：科目页列出这一科的讲评课（标题与步数），点进讲评课页面打开、播放；列表不含节点与题', async ({ page }) => {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  await loginStudent(page, P1);
  await page.goto(`/s/${P1}/learn/math`);
  const list = page.getByTestId('lesson-catalog');
  await expect(list.getByRole('heading')).toHaveText(LESSON.catalogHeading);
  const links = list.getByTestId('lesson-catalog-link');
  await expect(links).toHaveText(['（合成）配方法讲评课', '（合成）暂时打不开的讲评课']);
  await expect(list.locator('[data-value="step-count"]')).toHaveText(['3', '2']);
  await expect(links.first()).toHaveAttribute('href', url(P1));
  await noLeak(page);
  await axe(page);
  // 只有数学有讲评课：道法、语文的科目页没有这一节
  for (const subject of ['politics', 'chinese']) {
    await page.goto(`/s/${P1}/learn/${subject}`);
    await expect(page.getByTestId('subject-page')).toBeVisible();
    await expect(page.getByTestId('lesson-catalog')).toHaveCount(0);
  }
  await page.goto(`/s/${P1}/learn/math`);
  await page.getByTestId('lesson-catalog-link').first().click();
  await expect(page).toHaveURL(new RegExp(`${url(P1).replace(/[.?]/g, '\\$&')}$`));
  await expect(page.locator('[data-lesson-start]')).toHaveText(LESSON.start);
  await page.locator('[data-lesson-start]').click();
  await expect(board(page)).toBeVisible();
  await page.locator('[data-stage-action="next"]').click();
  await expect(page.locator('[data-stage-caption]')).toHaveText('（合成台词）先看第一行。');
  await noLeak(page);
  expect(off).toEqual([]);
});

for (const [kind, entry, copy] of [['practice', E('106'), PRACTICE], ['retest', E('402'), RETEST]] as const) {
  test(`判分之后的入口（E2，${kind}）：作答前没有“看这道题的讲评课”，判分结果出来之后才有，点进讲评课页面`, async ({ page }) => {
    await loginStudent(page, P1);
    await page.goto(`/s/${P1}/learn/math/${kind}/${entry}`);
    const section = page.getByTestId(`${kind}-item`);
    await expect(section).toHaveAttribute('data-phase', 'item');
    await expect(page.getByTestId(`${kind}-lesson`)).toHaveCount(0);
    await expect(page.locator('a[href*="/lesson/"]')).toHaveCount(0);
    await expect(page.getByText(LESSON.forItem)).toHaveCount(0);
    await page.getByLabel(/答案/).fill('合成作答');
    await page.getByTestId(`${kind}-submit`).click();
    await expect(page.getByTestId(`${kind}-verdict`)).toHaveText(copy.pendingReview);
    const link = page.getByTestId(`${kind}-lesson`);
    await expect(link).toHaveText(LESSON.forItem);
    await expect(link).toHaveAttribute('href', url(P1));
    await noLeak(page);
    await axe(page);
    await link.click();
    await expect(page.getByTestId('lesson-page')).toBeVisible();
    await expect(page.locator('[data-lesson-start]')).toBeVisible();
  });
}

test('安全头：讲评课页面的 CSP、no-store 等与壳内其他页面一致（只打开地址，不点“开始”）', async ({ page }) => {
  await loginStudent(page, P1);
  const ts = targets('student', P1, false, false, true).filter((x) => x.path.includes('/lesson/'));
  expect(ts.length).toBe(1);
  for (const t of ts) {
    const res = await page.goto(t.path);
    expect(res!.status(), t.path).toBe(t.status);
    checkHtml(await res!.text(), checkHeaders(res!.headers(), t.path), t.path);
  }
});
