// 复测与验证题学生端页面（specs/retest-delivery-kge05；playwright.config.ts 的 practice-on 项目：前端与第五个 BFF 另开 STARBUDDY_RETEST_UI=retest，
// 学习服务是合成桩 scripts/fake-learning-practice.ts 的 retest 剧本；合成家庭 HP 见 db/seeds/dev/practice-synthetic.sql）。全程真实会话。
// 覆盖：科目页入口 → 列表（类别只以占位出现）→ 取题 → 作答 → 条目做完（列表随之更新）；待复核只显示“等待复核”；无题可出；
// 家长看不到入口、BFF 对家长 403；学生本人看兄妹 404；安全头。每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 复测区域里的每个文本节点，要么是 copy.ts 的文案（全是 {{TODO_COPY:retest.*}} 占位），要么在 data-value、data-item-text 里；
//   - 页面上没有桩故意带上的任务原因、作答用途、题目引用、空原因编码与票据，也没有评价词；整页过 dom-leak.ts。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, RETEST, RETEST_ERROR, RETEST_KIND, RETEST_VERDICT } from '../src/shell/copy.ts';
import { expectNoDomLeak } from './dom-leak.ts';
import { checkHeaders, checkHtml } from './headers-check.ts';
import { targets } from './crawl-pages.ts';
import { cookieSession, loginGuardian, loginStudent, PRACTICE_BASE, PRACTICE_BFF } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P2 = '59000000-0000-4000-8000-0000000000c2';
const R = (n: string) => `59000000-0000-4000-8000-000000000${n}`;
const list = (s: string, subject = 'math') => `/s/${s}/learn/${subject}/retest`;
const item = (s: string, entry: string) => `${list(s)}/${entry}`;
// 桩故意带上的内部字段与编码（任务原因 scheduled / decay 是普通英文词，可能出现在框架脚本里，所以只按 DOM 文本与属性另查，见下）
const INTERNAL = ['aya:item-SYN', 'item_ref', 'empty_reason', 'no_new_family', 'no_source_item', 'verify_self_report',
                  'verify_in_platform', 'transfer_check', 'usage_id', 'idempotency_key', 'evidence_id', 'p_mastery'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

async function onlyCopy(page: Page): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    const root = document.querySelector('[data-retest-root]');
    if (!root) return ['（没有复测区域）'];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = (n.textContent ?? '').trim();
      const el = n.parentElement;
      if (t === '' || !el || el.closest('[data-value], [data-item-text], script, style')) continue;
      if (!known.has(t)) out.push(t);
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  const html = await page.content();
  for (const leak of INTERNAL) expect(html, leak).not.toContain(leak);
  expect(html).not.toMatch(/粗心|马虎|薄弱|不认真|掌握度|星亮/);
  expect(html, '票据不进 DOM').not.toMatch(/\b(sbr1|rt1)\.[A-Za-z0-9_-]{16,}/);
  await expectNoDomLeak(page, { forbidden: [...INTERNAL, 'scheduled', 'decay'] });
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

const row = (page: Page, entry: string) => page.locator(`tr[data-entry-id="${entry}"]`);
const section = (page: Page) => page.getByTestId('retest-item');

test('学生本人：科目页有入口 → 列表只有类别占位与状态 → 取题 → 作答 → 这一条做完（列表随之更新）', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, P1);
  await page.goto(`/s/${P1}/learn/math`);
  await expect(page.getByTestId('retest-entry')).toHaveText(RETEST.entry);
  await page.getByTestId('retest-entry').click();
  await expect(page).toHaveURL(new RegExp(`${list(P1)}$`));
  const rows = page.locator('[data-testid="retest-entries"] tbody tr');
  await expect(rows).toHaveCount(4);
  expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-entry-status')))).toEqual(['due', 'due', 'unavailable', 'due']);
  await expect(page.getByTestId('retest-start')).toHaveCount(3);
  await expect(row(page, R('401')).locator('td').first()).toHaveText(RETEST_KIND['retest']!);
  await expect(row(page, R('402')).locator('td').first()).toHaveText(RETEST_KIND['verify']!);
  await onlyCopy(page);
  await axe(page);

  await row(page, R('401')).getByTestId('retest-start').click();
  await expect(page).toHaveURL(new RegExp(`${item(P1, R('401'))}$`));
  await expect(section(page)).toHaveAttribute('data-phase', 'item');
  await expect(page.getByTestId('retest-stem')).toContainText('【合成题面】');
  await onlyCopy(page);
  await axe(page);
  await page.getByRole('radio', { name: /乙/ }).check();
  await page.getByTestId('retest-submit').click();
  const result = page.getByTestId('retest-result');
  await expect(result).toHaveAttribute('data-verdict', 'correct');
  await expect(result).toHaveAttribute('data-entry-status', 'done');
  await expect(page.getByTestId('retest-verdict')).toHaveText(RETEST_VERDICT['correct']!);
  await expect(page.getByTestId('retest-another')).toHaveCount(0);
  await onlyCopy(page);
  await axe(page);

  await page.getByTestId('retest-back-to-list').click();
  await expect(row(page, R('401'))).toHaveCount(0);
  // 做完之后再取这一条：BFF 409 RETEST_NOT_DUE → “这一条现在不能做”
  await page.goto(item(P1, R('401')));
  await expect(section(page)).toHaveAttribute('data-error', 'not_due');
  await expect(page.getByTestId('retest-stopped')).toHaveText(RETEST_ERROR['not_due']!);
  expect(off).toEqual([]);
});

test('验证题待复核：只显示“等待复核”，不显示判分结论；条目仍可再做一道。无题可出：只显示占位，不显示原因', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(item(P1, R('402')));
  await expect(section(page)).toHaveAttribute('data-phase', 'item');
  await page.getByLabel(/答案/).fill('合成作答');
  await page.getByTestId('retest-submit').click();
  const result = page.getByTestId('retest-result');
  await expect(result).toHaveAttribute('data-pending-review', 'true');
  await expect(result).toHaveAttribute('data-verdict', '');
  await expect(page.getByTestId('retest-verdict')).toHaveText(RETEST.pendingReview);
  await expect(page.getByTestId('retest-another')).toBeVisible();
  await onlyCopy(page);
  await axe(page);
  await page.goto(item(P1, R('404')));
  await expect(section(page)).toHaveAttribute('data-phase', 'empty');
  await expect(page.getByTestId('retest-empty')).toHaveText(RETEST.itemUnavailable);
  await onlyCopy(page);
  await axe(page);
});

test('授权：家长端没有入口，打开学生地址转到家长那一侧；BFF 对家长 403；学生本人看兄妹 404；语文不在范围 404；道法列表为空', async ({ page }) => {
  await loginGuardian(page, 'gP');
  for (const p of ['/family', `/family/${P1}`]) {
    await page.goto(p);
    await expect(page.locator('a[href*="/retest"]')).toHaveCount(0);
  }
  await page.goto(list(P1));
  expect(new URL(page.url()).pathname).toBe(`/family/${P1}`);
  let s = await cookieSession(page);
  const g = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P1}/retest/math`, { headers: { 'x-sb-session': s.token } });
  expect(g.status).toBe(403);

  await loginStudent(page, P1);
  expect((await page.goto(list(P2)))?.status()).toBe(404);
  s = await cookieSession(page);
  const r = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P2}/retest/math`, { headers: { 'x-sb-session': s.token } });
  expect(r.status).toBe(404);
  expect((await page.goto(list(P1, 'chinese')))?.status()).toBe(404);
  await page.goto(list(P1, 'politics'));
  await expect(page.getByTestId('retest-list-empty')).toHaveText(RETEST.empty);
  await onlyCopy(page);
  await axe(page);
});

test('安全头：复测两页的 CSP、no-store 等与壳内其他页面一致', async ({ page }) => {
  await loginStudent(page, P1);
  for (const t of targets('student', P1, false, false, true).filter((x) => x.path.includes('/retest'))) {
    const res = await page.goto(t.path);
    expect(res!.status(), t.path).toBe(t.status);
    checkHtml(await res!.text(), checkHeaders(res!.headers(), t.path), t.path);
  }
});
