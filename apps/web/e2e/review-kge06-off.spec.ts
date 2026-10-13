// KGE-06 审核台开关关着（trust-off 这一套前端，STARBUDDY_REVIEW_FEATURES 显式为空）：两个页面 404，在读会话之前判断，不需要登录。
// 导航入口不出现由单测核对（tests/review-kge06.test.ts）；这里不登录，避免与 review.spec.ts 抢同一个合成教研的 6 位码。
import { expect, test } from '@playwright/test';

test('开关关着：低置信工作单与修订预检页面 404', async ({ page }) => {
  expect((await page.goto('/review/tag-candidates?subject=aya'))?.status()).toBe(404);
  expect((await page.goto('/review/map-versions/5f0e0000-0000-4000-8000-0000000001a2/preflight'))?.status()).toBe(404);
});
