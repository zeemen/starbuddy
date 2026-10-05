// SIE-02b 第二步 PR 2 首份报告开发演示页（playwright.config.ts 的 report-on 项目：前端开 report，第四个 BFF 开 report、why，
// 洞察服务是合成桩 scripts/fake-insight.ts；合成家庭 HR 见 db/seeds/dev/first-report-synthetic.sql）。全程真实会话（短信捕获桩读码）。
// 每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 演示页区域里的每个文本节点，要么是 copy.ts 的文案（全是 {{TODO_COPY:…}} 占位），要么在 data-claim-id（判断句与待验证虚线）里，
//     要么在 data-fixed-text（洞察服务登记的固定文字：顶部提示语、标题、衔接短语）里——关于学生的句子都带 claim_id；
//   - 页面上没有信封外层的内容（依据版本、证据 ID）：BFF 只转发 result。
// 视觉回归（@visual，只由 make web-visual 运行）：家长版与学生版各一张，基线在 __screenshots__/first-report.spec.ts/（首次提交要有视觉负责人签字）。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, FIRST_REPORT } from '../src/shell/copy.ts';
import { cookieSession, loginGuardian, loginStudent, REPORT_BASE, REPORT_BFF } from './session.ts';

const R1 = '5c000000-0000-4000-8000-0000000000c1';
const R2 = '5c000000-0000-4000-8000-0000000000c2';
const C = { est: '5c000000-0000-4000-8000-000000000201', hyp: '5c000000-0000-4000-8000-000000000202', tra: '5c000000-0000-4000-8000-000000000203',
            ver: '5c000000-0000-4000-8000-000000000204' };
const EVIDENCE = '5c000000-0000-4000-8000-000000000301';
const demo = (student: string, verify?: string) => `/dev/first-report/${student}${verify ? `?verify=${verify}` : ''}`;

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

/** 演示页区域的每个文本节点：文案、判断句（data-claim-id）或洞察服务登记的固定文字（data-fixed-text），别的一律算违规 */
async function onlyCopyOrClaims(page: Page): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    const root = document.querySelector('[data-trust-root="first-report"]');
    if (!root) return ['（没有演示页区域）'];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = (n.textContent ?? '').trim();
      const el = n.parentElement;
      if (t === '' || !el || el.closest('[data-claim-id], [data-fixed-text], script, style')) continue;
      if (!known.has(t)) out.push(t);
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  const html = await page.content();
  for (const leak of ['algo:sie-report-fake', EVIDENCE, 'evidence_ids', 'model_version', 'source_version']) expect(html).not.toContain(leak);
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(REPORT_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

test('家长：家长版——顶部提示语、两节、每句带 data-claim-id；假设标“初步判断”；每句附置信度分档；待验证画虚线（来自单条解释）', async ({ page }) => {
  const off = watchOrigin(page);
  await loginGuardian(page, 'gR');
  await page.goto(demo(R1, C.ver));
  const root = page.locator('[data-trust-root="first-report"]');
  await expect(root).toHaveAttribute('data-audience', 'guardian');
  await expect(root.locator('[data-fixed-text="notice"]')).toHaveText('【占位·待人审】首份报告顶部提示语');
  await expect(root.locator('[data-fixed-text="title"]')).toHaveText('【占位·待人审】首份报告标题（家长版）');
  expect(await root.locator('[data-report-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-report-section'))))
    .toEqual(['node_mastery', 'node_transfer']);
  const sentences = root.locator('.first-report [data-claim-id]');
  expect(await sentences.evaluateAll((els) => els.map((e) => e.getAttribute('data-claim-id')))).toEqual([C.est, C.hyp, C.tra]);
  await expect(root.locator(`[data-claim-id="${C.hyp}"] .report-label`)).toHaveText(FIRST_REPORT.preliminary);
  await expect(root.locator('.first-report .report-label')).toHaveCount(1);
  expect(await root.locator('.first-report [data-confidence]').evaluateAll((els) => els.map((e) => e.getAttribute('data-confidence'))))
    .toEqual(['strong', 'few_evidence', 'moderate']);
  const dashed = root.locator('[data-report-verify] [data-claim-id]');
  await expect(dashed).toHaveCount(1);
  await expect(dashed).toHaveAttribute('data-claim-id', C.ver);
  await expect(dashed).toHaveAttribute('data-display', 'dashed');
  await expect(dashed).toHaveText(FIRST_REPORT.verifyNote);
  expect(await dashed.evaluate((e) => getComputedStyle(e).borderTopStyle)).toBe('dashed');
  await expect(root).not.toContainText('MATH.SYN.R1.N3', { useInnerText: true });  // 虚线不显示结论文字
  await onlyCopyOrClaims(page);
  await axe(page);
  // 成立的判断放进 verify：不是待验证，不进虚线区
  await page.goto(demo(R1, C.est));
  await expect(page.locator('[data-report-verify]')).toHaveCount(0);
  expect(off).toEqual([]);
});

test('学生本人：学生版——假设标“初步判断”，不附置信度分档；判断句与家长版同一组 claim_id', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, R1);
  await page.goto(demo(R1, C.ver));
  const root = page.locator('[data-trust-root="first-report"]');
  await expect(root).toHaveAttribute('data-audience', 'student');
  await expect(root.locator('[data-fixed-text="title"]')).toHaveText('【占位·待人审】首份报告标题（学生版）');
  expect(await root.locator('.first-report [data-claim-id]').evaluateAll((els) => els.map((e) => e.getAttribute('data-claim-id'))))
    .toEqual([C.est, C.hyp, C.tra]);
  await expect(root.locator(`[data-claim-id="${C.hyp}"] .report-label`)).toHaveText(FIRST_REPORT.preliminary);
  await expect(root.locator('[data-confidence]')).toHaveCount(0);
  await expect(root.locator('[data-report-verify] [data-display="dashed"]')).toHaveAttribute('data-claim-id', C.ver);
  await onlyCopyOrClaims(page);
  await axe(page);
  expect(off).toEqual([]);
});

test('授权：家长看兄妹 R2（没有判断）是“还没有报告”；学生本人看兄妹、别人家的家长看 R1 一律 404；BFF 对越权同一个 404', async ({ page }) => {
  await loginGuardian(page, 'gR');
  await page.goto(demo(R2));
  await expect(page.getByTestId('first-report-empty')).toHaveText(FIRST_REPORT.empty);
  await expect(page.locator('[data-claim-id]')).toHaveCount(0);
  await axe(page);
  await loginStudent(page, R1);
  expect((await page.goto(demo(R2)))?.status()).toBe(404);
  const s = await cookieSession(page);
  for (const p of [`/students/${R2}/first-report`, `/students/${R2}/claims/${C.est}/explain`, `/students/${R1}/claims/${EVIDENCE}/explain`]) {
    const r = await fetch(`${REPORT_BFF}/bff/v0${p}`, { headers: { 'x-sb-session': s.token } });
    expect(r.status, p).toBe(404);
  }
  await loginGuardian(page, 'gH');  // 另一户（统一壳家庭 HG）
  expect((await page.goto(demo(R1)))?.status()).toBe(404);
});

test('BFF 只转发 result：首份报告与单条解释的响应里没有信封外层的置信度、证据 ID、版本', async ({ page }) => {
  await loginGuardian(page, 'gR');
  const s = await cookieSession(page);
  const keysDeep = (v: unknown): string[] => (Array.isArray(v) ? v.flatMap(keysDeep)
    : v && typeof v === 'object' ? Object.entries(v).flatMap(([k, x]) => [k, ...keysDeep(x)]) : []);
  for (const p of [`/students/${R1}/first-report`, `/students/${R1}/claims/${C.ver}/explain`]) {
    const r = await fetch(`${REPORT_BFF}/bff/v0${p}`, { headers: { 'x-sb-session': s.token } });
    expect(r.status, p).toBe(200);
    const text = await r.text();
    const keys = keysDeep(JSON.parse(text));
    for (const k of ['confidence', 'evidence_ids', 'evidence_id', 'source_version', 'model_version', 'human_review_required']) expect(keys, p).not.toContain(k);
    expect(text).not.toContain(EVIDENCE);
  }
});

test('@visual 首份报告 家长版（light）', async ({ page }) => {
  await loginGuardian(page, 'gR');
  await page.goto(demo(R1, C.ver));
  await expect(page.locator('[data-report-verify]')).toBeVisible();
  await expect(page).toHaveScreenshot('first-report-guardian-light.png', { fullPage: true });
});

test('@visual 首份报告 学生版（light）', async ({ page }) => {
  await loginStudent(page, R1);
  await page.goto(demo(R1, C.ver));
  await expect(page.locator('[data-report-verify]')).toBeVisible();
  await expect(page).toHaveScreenshot('first-report-student-light.png', { fullPage: true });
});
