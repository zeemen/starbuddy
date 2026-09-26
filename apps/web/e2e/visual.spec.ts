// 视觉回归（spec 验收，@visual）：符号样张页与星图页（合成数据，固定布局）在浅色、深色、reduce-motion 下截图，
// 与已提交的基线比较，差异像素比例 ≤ 0.1%（playwright.config.ts）。只由 make web-visual 运行（待人确认 #11）；
// 基线首次提交要有视觉负责人签字（lessons 记录），并且要在固定版本的容器与浏览器里生成。
import { expect, test } from '@playwright/test';

const S1_ACCOUNT = '5a000000-0000-4000-8000-0000000000b1';
const S1 = '5a000000-0000-4000-8000-0000000000c1';

const ENVS = [
  { name: 'light', colorScheme: 'light' as const, reducedMotion: 'no-preference' as const },
  { name: 'dark', colorScheme: 'dark' as const, reducedMotion: 'no-preference' as const },
  { name: 'reduce-motion', colorScheme: 'light' as const, reducedMotion: 'reduce' as const },
];

for (const env of ENVS) {
  test.describe(`@visual ${env.name}`, () => {
    test.use({ colorScheme: env.colorScheme, contextOptions: { reducedMotion: env.reducedMotion } });

    test(`@visual 符号样张页（${env.name}）`, async ({ page }) => {
      await page.goto('/dev/star-glyphs');
      await expect(page).toHaveScreenshot(`star-glyphs-${env.name}.png`, { fullPage: true });
    });

    test(`@visual 星图页 S1（${env.name}）`, async ({ page }) => {
      await page.goto(`/dev/session?account=${S1_ACCOUNT}&next=${encodeURIComponent(`/star-map/${S1}`)}`);
      await expect(page).toHaveScreenshot(`star-map-s1-${env.name}.png`, { fullPage: true });
    });
  });
}
