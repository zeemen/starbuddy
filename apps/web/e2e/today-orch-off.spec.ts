// 今日计划跨科编排开关关闭时（trust-off 项目：前端与 BFF 的 STARBUDDY_TODAY_ORCH 都关；PLT-11）：与开关上线前零差异——
// 今日页没有跨科列表与“去做”，BFF 的 today 响应只有 student_id 与 subjects（没有 ordered）；这期间合成学习服务桩收到 0 个请求
// （按桩的请求记录前后行数核对；只有一个 worker）。合成学生 P1 见 db/seeds/dev/practice-synthetic.sql。
import { expect, test } from '@playwright/test';
import { BFF, cookieSession, fakeLearningRequests, loginStudent } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';

test('开关关：今日页没有跨科列表；BFF 的 today 不带 ordered；合成学习服务桩收到 0 个请求', async ({ page }) => {
  const before = fakeLearningRequests();
  await loginStudent(page, P1);
  await expect(page.getByTestId('today')).toBeVisible();
  await expect(page.locator('section.today-subject')).not.toHaveCount(0);
  await expect(page.getByTestId('today-orch')).toHaveCount(0);
  await expect(page.getByTestId('today-orch-go')).toHaveCount(0);
  await expect(page.locator('main a[href*="/retest/"], main a[href*="/practice/"]')).toHaveCount(0);
  const s = await cookieSession(page);
  const r = await fetch(`${BFF}/bff/v0/students/${P1}/today`, { headers: { 'x-sb-session': s.token } });
  expect(r.status).toBe(200);
  expect(Object.keys(await r.json()).sort()).toEqual(['student_id', 'subjects']);
  expect(fakeLearningRequests() - before, '开关关时合成学习服务桩收到的请求数').toBe(0);
});
