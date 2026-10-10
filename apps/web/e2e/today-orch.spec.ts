// 今日计划跨科编排学生端（PLT-11，specs/today-plan-orchestration-plt11 范围第 3 条；playwright.config.ts 的 practice-on 项目：前端与第五个 BFF
// 另开 STARBUDDY_TODAY_ORCH=today_orch，学习服务是合成桩 scripts/fake-learning-practice.ts 的 /internal/v1/today 剧本；合成家庭 HP 见
// db/seeds/dev/practice-synthetic.sql）。全程真实会话。
// 覆盖：今日页在按科列表之前显示跨科列表（三条：验证 → 复测页、再练 → 再练页、计划任务 → 科目页），截掉的条数只以 data-value 出现，
// 有来源不可用时只显示占位；“去做”能走到复测、再练页；家长看不到跨科列表；每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 跨科区域里的每个文本节点，要么是 copy.ts 的文案（新文案全是 {{TODO_COPY:today.*}} 占位），要么在 data-value 里；
//   - 页面上没有桩故意带上的掌握、置信度、证据 ID、优先级、单元层次、任务原因、来源与原因编码；整页过 dom-leak.ts。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, CONTENT_RATIONALE, TODAY_ORCH, TODAY_ORCH_KIND } from '../src/shell/copy.ts';
import { expectNoDomLeak } from './dom-leak.ts';
import { cookieSession, loginGuardian, loginStudent, PRACTICE_BASE, PRACTICE_BFF } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P2 = '59000000-0000-4000-8000-0000000000c2';
const R = (n: string) => `59000000-0000-4000-8000-000000000${n}`;
// 桩故意带上的内部字段与编码（任务原因、来源状态、掌握数值等）：任何位置都不许出现
const INTERNAL = ['p_mastery', 'evidence_id', 'cognitive_level', 'verify_self_report', 'source_unavailable', 'source_disabled', 'rationale_code', 'budget_minutes',
                  'high_exam_weight', '0.42', '0.77', R('9e1'), 'MATH.SYN.P1'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

async function onlyCopy(page: Page): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    const root = document.querySelector('[data-testid="today-orch"]');
    if (!root) return ['（没有跨科区域）'];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = (n.textContent ?? '').trim();
      const el = n.parentElement;
      if (t === '' || !el || el.closest('[data-value], [data-claim-id], script, style')) continue;
      if (!known.has(t)) out.push(t);
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  const html = await page.content();
  for (const leak of INTERNAL) expect(html, leak).not.toContain(leak);
  expect(html).not.toMatch(/粗心|马虎|薄弱|不认真|掌握度|置信度|星亮/);
  await expectNoDomLeak(page, { forbidden: INTERNAL });
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

test('学生本人：今日页先显示跨科列表（类别占位、预计分钟、去做），截掉的条数与“有内容没列出”只以占位与 data-value 出现', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, P1);
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/today$`));
  const box = page.getByTestId('today-orch');
  await expect(box).toBeVisible();
  await expect(box.locator('h2')).toHaveText(TODAY_ORCH.heading);
  const items = page.getByTestId('today-orch-item');
  await expect(items).toHaveCount(3);
  await expect(items.nth(0).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['verify']!);
  await expect(items.nth(1).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['practice']!);
  await expect(items.nth(2).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['lesson']!);
  expect(await items.locator('[data-value="minutes"]').allTextContents()).toEqual(['5', '8', '5']);
  const hrefs = await page.getByTestId('today-orch-go').evaluateAll((els) => els.map((e) => e.getAttribute('href')));
  expect(hrefs).toEqual([`/s/${P1}/learn/math/retest/${R('402')}`, `/s/${P1}/learn/math/practice/${R('106')}`, `/s/${P1}/learn/math`]);
  await expect(page.getByTestId('today-orch-truncated').locator('[data-value="count"]')).toHaveText('2');
  await expect(page.getByTestId('today-orch-partial')).toHaveText(TODAY_ORCH.partial);
  // 跨科列表在按科列表之前；按科列表用 /today 附带的计划排（桩的 /plan 是 501，开关开时 BFF 不再调它）
  const order = await page.locator('main > section').evaluateAll((els) => els.map((e) => e.getAttribute('data-testid') ?? e.getAttribute('data-subject')));
  expect(order).toEqual(['today-orch', 'math']);
  await expect(page.locator('section.today-subject[data-subject="math"] li.task')).toHaveCount(1);
  // 跨科列表里的计划任务沿用按科那一条的内容类理由（固定短句）；复测、再练条目没有理由
  await expect(items.nth(2).locator('[data-rationale="content"]')).toHaveText(CONTENT_RATIONALE['high_exam_weight']!);
  await expect(items.nth(0).locator('[data-rationale]')).toHaveCount(0);
  await onlyCopy(page);
  await axe(page);
  expect(off).toEqual([]);
});

test('“去做”：验证条目进复测页、再练条目进再练页、计划任务进科目页', async ({ page }) => {
  await loginStudent(page, P1);
  await page.getByTestId('today-orch-go').nth(0).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math/retest/${R('402')}$`));
  await expect(page.getByTestId('retest-item')).toHaveAttribute('data-phase', 'item');
  await page.goto(`/s/${P1}/today`);
  await page.getByTestId('today-orch-go').nth(1).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math/practice/${R('106')}$`));
  await expect(page.getByTestId('practice-item')).toHaveAttribute('data-phase', 'item');
  await page.goto(`/s/${P1}/today`);
  await page.getByTestId('today-orch-go').nth(2).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math$`));
  await expect(page.getByTestId('subject-page')).toBeVisible();
});

test('没有要做的：只显示占位；家长端没有跨科列表，BFF 对家长的 today 不带 ordered', async ({ page }) => {
  await loginStudent(page, P2);
  await expect(page.getByTestId('today-orch-empty')).toHaveText(TODAY_ORCH.empty);
  await expect(page.getByTestId('today-orch-item')).toHaveCount(0);
  // 只有未启用的来源（source_disabled）：不算 partial，不显示“有内容没列出”；没有截掉的也不显示截断
  await expect(page.getByTestId('today-orch-partial')).toHaveCount(0);
  await expect(page.getByTestId('today-orch-truncated')).toHaveCount(0);
  await onlyCopy(page);
  await axe(page);
  await loginGuardian(page, 'gP');
  for (const p of ['/family', `/family/${P1}`]) {
    await page.goto(p);
    await expect(page.getByTestId('today-orch')).toHaveCount(0);
  }
  const s = await cookieSession(page);
  const g = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P1}/today`, { headers: { 'x-sb-session': s.token } });
  expect(g.status).toBe(200);
  expect(Object.keys(await g.json()).sort()).toEqual(['student_id', 'subjects']);
});
