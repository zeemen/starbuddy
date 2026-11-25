// 学生端诊断开关关闭时（trust-off 项目：前端 STARBUDDY_DIAGNOSIS_UI 不设；specs/diagnosis-student-entry）：与开关打开前零差异——
// 数学科目页没有诊断入口，诊断页 404（不读会话、不发请求）。开发演示页不受这个开关影响（e2e/diagnosis.spec.ts 照常）。
import { expect, test } from '@playwright/test';
import { S1 } from './crawl-pages.ts';
import { loginStudent } from './session.ts';

test('开关关：科目页没有诊断入口；诊断页 404', async ({ page }) => {
  await loginStudent(page, S1);
  for (const subject of ['math', 'politics', 'chinese']) {
    await page.goto(`/s/${S1}/learn/${subject}`);
    await expect(page.getByTestId('subject-page')).toBeVisible();
    await expect(page.getByTestId('diagnosis-entry')).toHaveCount(0);
    await expect(page.locator('a[href*="/diagnosis"]')).toHaveCount(0);
  }
  expect((await page.goto(`/s/${S1}/learn/math/diagnosis`))?.status()).toBe(404);
});
