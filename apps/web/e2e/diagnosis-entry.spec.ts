// 学生端数学诊断入口与正式做题页（specs/diagnosis-student-entry；practice-on 项目：这一套前端另开 STARBUDDY_DIAGNOSIS_UI=diagnosis）。
// 这一套 BFF 的学习服务是合成桩（诊断接口一律 501），所以这里只核对入口、页面、授权与安全头；诊断全流程（探针、暂停续做、结束页）
// 用同一个组件、同一份 src/diagnosis/ops.ts，由 e2e/diagnosis.spec.ts 在真实学习服务的一套上经开发演示页核对。
// 合成学生 P3（没有任何订阅）：开始诊断时 BFF 预留得到 no_entitlement → 页面显示“现在还不能开始”，不建会话、不占额度、不调学习服务。
// 合成数据见 db/seeds/dev/practice-synthetic.sql（家长 gP，孩子 P1–P3）。
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { DIAGNOSIS_COPY, DIAGNOSIS_ENTRY_COPY } from '../src/diagnosis/copy.ts';
import { checkHeaders, checkHtml } from './headers-check.ts';
import { loginGuardian, loginStudent } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P3 = '59000000-0000-4000-8000-0000000000c3';
const page_ = (s: string, subject = 'math') => `/s/${s}/learn/${subject}/diagnosis`;
const sql = (q: string) => execFileSync('psql', ['-X', '-At', '-c', q], { encoding: 'utf8' }).trim();
const rows = (s: string) => sql(`SELECT (SELECT count(*) FROM platform.diagnosis_session WHERE student_id = '${s}') || ':'
  || (SELECT count(*) FROM platform.usage_reservation WHERE student_id = '${s}' AND resource_type = 'diagnosis_full')`);
const axe = async (page: Page) =>
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.map((v) => v.id)).toEqual([]);

test('学生本人：数学科目页有入口（道法、语文没有）；点进去是诊断页，开始不了时只说“现在还不能开始”，不建会话、不占额度', async ({ page }) => {
  const before = rows(P3);
  await loginStudent(page, P3);
  for (const subject of ['politics', 'chinese']) {
    await page.goto(`/s/${P3}/learn/${subject}`);
    await expect(page.getByTestId('subject-page')).toBeVisible();
    await expect(page.getByTestId('diagnosis-entry')).toHaveCount(0);
  }
  await page.goto(`/s/${P3}/learn/math`);
  const entry = page.getByTestId('diagnosis-entry');
  await expect(entry).toHaveText(DIAGNOSIS_ENTRY_COPY.entry);
  await expect(entry).toHaveAttribute('href', page_(P3));
  await entry.click();
  await expect(page).toHaveURL(new RegExp(`${page_(P3)}$`));
  await expect(page.getByTestId('diagnosis-student-page')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(DIAGNOSIS_COPY.heading);
  await expect(page.getByTestId('diagnosis')).toHaveAttribute('data-phase', 'unavailable');
  await expect(page.getByText(DIAGNOSIS_COPY.unavailable)).toBeVisible();
  expect(rows(P3), '没有权益：不建会话、不占额度').toBe(before);
  await axe(page);
  await page.getByTestId('diagnosis-back').click();
  await expect(page).toHaveURL(new RegExp(`/s/${P3}/learn/math$`));
});

test('安全头：诊断页的 CSP、no-store 等与壳内其他页面一致', async ({ page }) => {
  await loginStudent(page, P3);
  const res = await page.goto(page_(P3));
  expect(res!.status()).toBe(200);
  checkHtml(await res!.text(), checkHeaders(res!.headers(), page_(P3)), page_(P3));
});

test('授权：不是数学 404；学生看兄妹 404；家长打开学生地址转到家长那一侧，家长端没有入口', async ({ page }) => {
  await loginStudent(page, P3);
  expect((await page.goto(page_(P3, 'politics')))?.status()).toBe(404);
  expect((await page.goto(page_(P1)))?.status()).toBe(404);
  await loginGuardian(page, 'gP');
  await page.goto(page_(P3));
  expect(new URL(page.url()).pathname).toBe(`/family/${P3}`);
  for (const p of ['/family', `/family/${P3}`]) {
    await page.goto(p);
    await expect(page.locator('a[href*="/diagnosis"]')).toHaveCount(0);
  }
});
