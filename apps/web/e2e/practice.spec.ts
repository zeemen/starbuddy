// 错题再练学生端页面（specs/practice-loop-stage1 PR 4；playwright.config.ts 的 practice-on 项目：前端开 practice，第五个 BFF 开 practice，
// 学习服务是合成桩 scripts/fake-learning-practice.ts；合成家庭 HP 见 db/seeds/dev/practice-synthetic.sql）。全程真实会话（短信捕获桩读码）。
// 覆盖：错 → 取题 → 作答 → 闭环；再练做错 → 下一轮（结果未知后用同一张票据再提交）；待复核只显示“等待复核”；409、410、503、无候选；
// 宵禁 402（取题；列表不挡）；没有权益 402（作答）；家长看不到入口、BFF 对家长 403；学生本人看兄妹 404。
// 每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 再练区域里的每个文本节点，要么是 copy.ts 的文案（全是 {{TODO_COPY:practice.*}} 占位），要么在 data-value（数字、日期、节点编号）、
//     data-item-text（BFF 转来的合成题面）里；
//   - 页面上没有桩故意带上的内部字段（题目引用、空原因编码、复测与同族标记）与票据，也没有“粗心”等评价词。
// 视觉回归（@visual，只由 make web-visual 运行）：列表与作答页各一张，基线在 __screenshots__/practice.spec.ts/（首次提交要有视觉负责人签字）。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, PRACTICE, PRACTICE_ERROR, PRACTICE_SOURCE, PRACTICE_VERDICT } from '../src/shell/copy.ts';
import { checkHeaders, checkHtml } from './headers-check.ts';
import { targets } from './crawl-pages.ts';
import { cookieSession, fakeLearningRequests, loginGuardian, loginStudent, PRACTICE_BASE, PRACTICE_BFF } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P2 = '59000000-0000-4000-8000-0000000000c2';
const P3 = '59000000-0000-4000-8000-0000000000c3';
const E = (n: string) => `59000000-0000-4000-8000-000000000${n}`;
const list = (s: string, subject = 'math') => `/s/${s}/learn/${subject}/practice`;
const item = (s: string, entry: string) => `${list(s)}/${entry}`;
const INTERNAL = ['aya:item-SYN', 'source_item_ref', 'item_ref', 'retest_due', 'same_family_only', 'empty_reason', 'no_admitted_candidate',
                  'all_excluded', 'usage_id', 'idempotency_key', 'evidence_id', 'p_mastery', 'error_attribution'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

/** 再练区域的每个文本节点：文案、data-value（数字、日期、节点编号）或 data-item-text（题面），别的一律算违规；没有内部字段与评价词 */
async function onlyCopy(page: Page): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    const root = document.querySelector('[data-practice-root]');
    if (!root) return ['（没有再练区域）'];
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
  expect(html).not.toMatch(/粗心|马虎|薄弱|不认真|掌握度/);
  expect(html, '票据不进 DOM').not.toMatch(/\b(sbp1|pt1)\.[A-Za-z0-9_-]{16,}/);
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

const row = (page: Page, entry: string) => page.locator(`tr[data-entry-id="${entry}"]`);
const itemSection = (page: Page) => page.getByTestId('practice-item');

test('学生本人：科目页有入口 → 列表只陈述再练状态 → 取题 → 做对 → 条目闭环（列表随之更新）', async ({ page }) => {
  const off = watchOrigin(page);
  const stubBefore = fakeLearningRequests();
  await loginStudent(page, P1);
  await page.goto(`/s/${P1}/learn/math`);
  await expect(page.getByTestId('practice-entry')).toHaveText(PRACTICE.entry);
  await page.getByTestId('practice-entry').click();
  await expect(page).toHaveURL(new RegExp(`${list(P1)}$`));
  const rows = page.locator('[data-testid="practice-entries"] tbody tr');
  await expect(rows).toHaveCount(11);
  expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-entry-status'))))
    .toEqual(['due', 'waiting', 'closed', 'exhausted', 'unavailable', 'due', 'due', 'due', 'due', 'due', 'due']);
  await expect(page.getByTestId('practice-start')).toHaveCount(7);  // 只有 due 的条目有“再练一道同类题”
  await expect(row(page, E('102')).locator('[data-value="next-round-at"]')).toHaveText('2026-12-01 10:00');
  // 条目来源只以文案占位出现（不放进 DOM 属性）：按 entry_id 定位这一行，看第一格
  await expect(row(page, E('106')).locator('td').first()).toHaveText(PRACTICE_SOURCE['hint_followup']!);
  await expect(row(page, E('101')).locator('td').first()).toHaveText(PRACTICE_SOURCE['wrong']!);
  await expect(page.locator('[data-entry-source]')).toHaveCount(0);
  // 桩的请求记录确实在写（practice-off.spec.ts 据此核对开关关时 0 个请求）：列表至少调了一次 queue
  expect(fakeLearningRequests()).toBeGreaterThan(stubBefore);
  await onlyCopy(page);
  await axe(page);

  await row(page, E('101')).getByTestId('practice-start').click();
  await expect(page).toHaveURL(new RegExp(`${item(P1, E('101'))}$`));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await expect(page.getByTestId('practice-stem')).toContainText('【合成题面】');
  await onlyCopy(page);
  await axe(page);
  await page.getByRole('radio', { name: /乙/ }).check();
  await page.getByTestId('practice-submit').click();
  const result = page.getByTestId('practice-result');
  await expect(result).toHaveAttribute('data-verdict', 'correct');
  await expect(result).toHaveAttribute('data-entry-status', 'closed');
  await expect(page.getByTestId('practice-verdict')).toHaveText(PRACTICE_VERDICT['correct']!);
  await onlyCopy(page);
  await axe(page);

  await page.getByTestId('practice-back-to-list').click();
  await expect(row(page, E('101'))).toHaveAttribute('data-entry-status', 'closed');
  await expect(row(page, E('101')).getByTestId('practice-start')).toHaveCount(0);
  // 闭环之后再取这一条：BFF 409 PRACTICE_NOT_DUE → “这一条现在不能再练”
  await page.goto(item(P1, E('101')));
  await expect(itemSection(page)).toHaveAttribute('data-error', 'not_due');
  await expect(page.getByTestId('practice-stopped')).toHaveText(PRACTICE_ERROR['not_due']!);
  expect(off).toEqual([]);
});

test('再练做错：结果未知时作答控件禁用，用同一张票据原样再提交首次作答 → 判错，条目等下一轮（显示开放时间）', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(item(P1, E('10b')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  const input = page.getByLabel(/答案/);
  await input.fill('41');
  await page.getByTestId('practice-submit').click();
  // 桩前 4 次 503：BFF 自己重试后回 UPSTREAM_UNAVAILABLE（已计一次）；页面留着票据，按钮变成“再提交一次”
  await expect(itemSection(page)).toHaveAttribute('data-error', 'retry', { timeout: 20_000 });
  await expect(page.getByTestId('practice-status')).toHaveText(PRACTICE_ERROR['retry']!);
  await expect(page.getByTestId('practice-submit')).toHaveText(PRACTICE.retry);
  // 作答控件禁用、保留原答案：改不了作答（同一张票据换了作答会被判“已用”）
  await expect(input).toBeDisabled();
  await expect(input).toHaveValue('41');
  await expect(page.getByTestId('practice-submit')).toBeEnabled();
  await axe(page);
  // 再提交原样发首次作答（桩对同一个键换了作答回 409 IDEMPOTENCY_CONFLICT，那样就到不了下面的判错）
  await page.getByTestId('practice-submit').click();
  const result = page.getByTestId('practice-result');
  await expect(result).toHaveAttribute('data-verdict', 'incorrect');
  await expect(result).toHaveAttribute('data-entry-status', 'waiting');
  await expect(page.getByTestId('practice-verdict')).toHaveText(PRACTICE_VERDICT['incorrect']!);
  await expect(page.getByTestId('practice-after').locator('[data-value="next-round-at"]')).toHaveText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  await onlyCopy(page);
  await page.getByTestId('practice-back-to-list').click();
  await expect(row(page, E('10b'))).toHaveAttribute('data-entry-status', 'waiting');
  await expect(row(page, E('10b')).locator('[data-value="round"]')).toHaveText('2');
});

test('待复核：只显示“等待复核”，不显示判分结论；条目仍可再练', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(item(P1, E('106')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await page.getByLabel(/答案/).fill('合成作答');
  await page.getByTestId('practice-submit').click();
  const result = page.getByTestId('practice-result');
  await expect(result).toHaveAttribute('data-pending-review', 'true');
  await expect(result).toHaveAttribute('data-verdict', '');
  await expect(page.getByTestId('practice-verdict')).toHaveText(PRACTICE.pendingReview);
  await expect(result).not.toContainText(PRACTICE_VERDICT['correct']!);
  await expect(page.getByTestId('practice-another')).toBeVisible();
  await onlyCopy(page);
  await axe(page);
});

test('409、410、503 与无候选：各自的提示；过期票据要换一道题', async ({ page }) => {
  await loginStudent(page, P1);
  // 409：出题之后条目变了（学习服务拒绝）
  await page.goto(item(P1, E('107')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await page.getByRole('checkbox', { name: /甲/ }).check();
  await page.getByRole('checkbox', { name: /丙/ }).check();
  await page.getByTestId('practice-submit').click();
  await expect(itemSection(page)).toHaveAttribute('data-error', 'not_due');
  await expect(page.getByTestId('practice-stopped')).toHaveText(PRACTICE_ERROR['not_due']!);
  await axe(page);
  // 410：票据快过期（BFF 本地判过期，不计次）→ 换一道题
  await page.goto(item(P1, E('108')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await page.getByRole('radio', { name: /甲/ }).check();
  await page.getByTestId('practice-submit').click();
  await expect(itemSection(page)).toHaveAttribute('data-error', 'expired');
  await expect(page.getByTestId('practice-status')).toHaveText(PRACTICE_ERROR['expired']!);
  await expect(page.getByTestId('practice-new-item')).toHaveText(PRACTICE.newItem);
  await axe(page);
  await page.getByTestId('practice-new-item').click();
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await expect(itemSection(page)).toHaveAttribute('data-error', '');
  // 503：学习服务没配题面等确定失败
  await page.goto(item(P1, E('109')));
  await expect(itemSection(page)).toHaveAttribute('data-error', 'unavailable');
  await expect(page.getByTestId('practice-stopped')).toHaveText(PRACTICE_ERROR['unavailable']!);
  // 无候选：200 unavailable（空原因编码不上页面）
  await page.goto(item(P1, E('10a')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'empty');
  await expect(page.getByTestId('practice-empty')).toHaveText(PRACTICE.itemUnavailable);
  await onlyCopy(page);
  await axe(page);
});

test('宵禁：列表照常显示；取题 402 curfew（页面“现在是休息时间”）；BFF 取题 402，不调学习服务（作答的宵禁由 BFF 集成测试覆盖）', async ({ page }) => {
  await loginStudent(page, P2);
  await page.goto(list(P2));
  await expect(page.getByTestId('practice-start')).toHaveCount(1);
  await page.getByTestId('practice-start').click();
  await expect(itemSection(page)).toHaveAttribute('data-error', 'curfew');
  await expect(page.getByTestId('practice-stopped')).toHaveText(PRACTICE_ERROR['curfew']!);
  await onlyCopy(page);
  await axe(page);
  const s = await cookieSession(page);
  const r = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P2}/practice/math/entries/${E('201')}/next`, {
    method: 'POST', headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, origin: PRACTICE_BASE },
  });
  expect(r.status).toBe(402);
  expect(await r.json()).toMatchObject({ code: 'QUOTA_UNAVAILABLE', reason: 'curfew' });
});

test('没有权益：能取题，作答 402 no_entitlement（不调学习服务），页面提示需要家长开通', async ({ page }) => {
  await loginStudent(page, P3);
  await page.goto(item(P3, E('301')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await page.getByRole('radio', { name: /乙/ }).check();
  await page.getByTestId('practice-submit').click();
  await expect(itemSection(page)).toHaveAttribute('data-error', 'no_entitlement');
  await expect(page.getByTestId('practice-stopped')).toHaveText(PRACTICE_ERROR['no_entitlement']!);
  await axe(page);
});

test('授权：家长端没有入口，打开学生地址转到家长那一侧；BFF 对家长 403；学生本人看兄妹 404', async ({ page }) => {
  await loginGuardian(page, 'gP');
  for (const p of ['/family', `/family/${P1}`]) {
    await page.goto(p);
    await expect(page.locator('a[href*="/practice"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="practice-entry"]')).toHaveCount(0);
  }
  await page.goto(list(P1));
  expect(new URL(page.url()).pathname).toBe(`/family/${P1}`);
  await expect(page.locator('a[href*="/practice"]')).toHaveCount(0);
  let s = await cookieSession(page);
  const g = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P1}/practice/math`, { headers: { 'x-sb-session': s.token } });
  expect(g.status).toBe(403);

  await loginStudent(page, P1);
  expect((await page.goto(list(P2)))?.status()).toBe(404);
  expect((await page.goto(item(P2, E('201'))))?.status()).toBe(404);
  s = await cookieSession(page);
  const r = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P2}/practice/math`, { headers: { 'x-sb-session': s.token } });
  expect(r.status).toBe(404);
  // 语文不在再练范围：前端 404；道法有入口，列表为空
  expect((await page.goto(list(P1, 'chinese')))?.status()).toBe(404);
  await page.goto(`/s/${P1}/learn/politics`);
  await expect(page.getByTestId('practice-entry')).toBeVisible();
  await page.goto(list(P1, 'politics'));
  await expect(page.getByTestId('practice-list-empty')).toHaveText(PRACTICE.empty);
  await axe(page);
});

test('安全头：再练两页的 CSP、no-store 等与壳内其他页面一致', async ({ page }) => {
  await loginStudent(page, P1);
  for (const t of targets('student', P1, false, false, true).filter((x) => x.path.includes('/practice'))) {
    const res = await page.goto(t.path);
    expect(res!.status(), t.path).toBe(t.status);
    checkHtml(await res!.text(), checkHeaders(res!.headers(), t.path), t.path);
  }
});

test('@visual 错题再练 列表（light）', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(list(P1));
  await expect(page.locator('[data-testid="practice-entries"] tbody tr')).toHaveCount(11);
  await expect(page).toHaveScreenshot('practice-list-light.png', { fullPage: true });
});

test('@visual 错题再练 作答页（light）', async ({ page }) => {
  await loginStudent(page, P1);
  await page.goto(item(P1, E('101')));
  await expect(itemSection(page)).toHaveAttribute('data-phase', 'item');
  await expect(page).toHaveScreenshot('practice-item-light.png', { fullPage: true });
});
