// 错题再练开关关闭时（trust-off 项目：前端与 BFF 的 practice 都关；specs/practice-loop-stage1 PR 4）：与开关打开前零差异——
// 科目页没有入口元素，再练页面 404（不读会话、不发请求），BFF 三条路由不注册（与表外路径同一个 404）；
// 这期间合成学习服务桩（scripts/fake-learning-practice.ts）收到 0 个请求（按桩的请求记录前后行数核对；playwright 只有一个 worker，
// 这条用例运行时没有别的用例在跑；记录确实在写由 practice.spec.ts 的第一条用例核对）。
// 合成学生 P1 见 db/seeds/dev/practice-synthetic.sql（同一个一次性库）。
import { expect, test } from '@playwright/test';
import { BASE, BFF, cookieSession, fakeLearningRequests, loginStudent } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const E1 = '59000000-0000-4000-8000-000000000101';

test('开关关：科目页没有再练入口；再练页面 404；BFF 的再练路由 404；合成学习服务桩收到 0 个请求', async ({ page }) => {
  const before = fakeLearningRequests();
  await loginStudent(page, P1);
  for (const subject of ['math', 'politics', 'chinese']) {
    await page.goto(`/s/${P1}/learn/${subject}`);
    await expect(page.getByTestId('subject-page')).toBeVisible();
    await expect(page.getByTestId('practice-entry')).toHaveCount(0);
    await expect(page.locator('a[href*="/practice"]')).toHaveCount(0);
  }
  expect((await page.goto(`/s/${P1}/learn/math/practice`))?.status()).toBe(404);
  expect((await page.goto(`/s/${P1}/learn/math/practice/${E1}`))?.status()).toBe(404);
  const s = await cookieSession(page);
  const get = await fetch(`${BFF}/bff/v0/students/${P1}/practice/math`, { headers: { 'x-sb-session': s.token } });
  expect(get.status).toBe(404);
  for (const tail of [`entries/${E1}/next`, 'answer']) {
    const r = await fetch(`${BFF}/bff/v0/students/${P1}/practice/math/${tail}`, {
      method: 'POST', headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, origin: BASE, 'content-type': 'application/json' },
      body: tail === 'answer' ? JSON.stringify({ ticket: 'sbp1.AAAA', answer: { choice: ['A'] } }) : undefined,
    });
    expect(r.status, tail).toBe(404);
  }
  expect(fakeLearningRequests() - before, '开关关时合成学习服务桩收到的请求数').toBe(0);
});
