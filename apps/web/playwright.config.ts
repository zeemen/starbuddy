// Playwright 配置：由 scripts/e2e.sh 在一次性环境里调用（BFF 地址、端口、AYA_COUNT 由它传入）。
// 视觉回归的用例带 @visual 标签：make web-test 跑其余用例，make web-visual 只跑 @visual（spec 待人确认 #11）。
import { defineConfig } from '@playwright/test';

const port = Number(process.env['WEB_PORT'] ?? 3100);

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}{ext}',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    viewport: { width: 1024, height: 768 },
    deviceScaleFactor: 1,
    colorScheme: 'light',
    serviceWorkers: 'block',
  },
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.001, animations: 'disabled' } },
  webServer: {
    command: `npx --no-install next start -p ${port} -H 127.0.0.1`,
    url: `http://127.0.0.1:${port}/dev/star-glyphs`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      STARBUDDY_DEV_AUTH: '1',
      STARBUDDY_BFF_URL: process.env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787',
      NEXT_TELEMETRY_DISABLED: '1',
    },
  },
});
