// Playwright 配置：由 scripts/e2e.sh 在一次性环境里调用（BFF 地址、端口、AYA_COUNT 由它传入）。
// 视觉回归的用例带 @visual 标签：make web-test 跑其余用例，make web-visual 只跑 @visual（spec 待人确认 #11）。
// 两套前端（冲刺 5-H）：trust-off 连开关全关的 BFF，跑除 trust-on.spec.ts 以外的全部用例（含 4-H“信任界面预留”与 trust-off.spec.ts）；
// trust-on 连 scripts/e2e.sh 起的第二个 BFF（信任界面开关打开），只跑 trust-on.spec.ts。两套共用同一个一次性库与测试构建。
import { defineConfig } from '@playwright/test';

const port = Number(process.env['WEB_PORT'] ?? 3100);
const trustPort = Number(process.env['WEB_TRUST_PORT'] ?? 3101);
// 数据权利用例（导出下载、删除二次确认，标 @data-rights）另在 WebKit 与 Firefox 上跑（安全审查 2）：本机只装了 Chromium，
// 所以按需打开——STARBUDDY_E2E_BROWSERS=webkit,firefox（需要对应的 Playwright 浏览器二进制；lessons 登记）。
const extraBrowsers = (process.env['STARBUDDY_E2E_BROWSERS'] ?? '').split(',').map((s) => s.trim())
  .filter((b): b is 'webkit' | 'firefox' => b === 'webkit' || b === 'firefox');
const serverEnv = {
  STARBUDDY_DEV_PAGES: '1',
  STARBUDDY_ENV: 'test',
  STARBUDDY_REVIEW_URL: process.env['STARBUDDY_REVIEW_URL'] ?? 'http://127.0.0.1:8790',
  NEXT_TELEMETRY_DISABLED: '1',
  // scripts/e2e.sh 的测试构建在 .next-e2e（next.config.ts），next start 要读同一个目录
  STARBUDDY_NEXT_DIST_DIR: process.env['STARBUDDY_NEXT_DIST_DIR'] ?? '.next-e2e',
};

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
  projects: [
    { name: 'trust-off', testIgnore: /trust-on\.spec\.ts$/ },
    { name: 'trust-on', testMatch: /trust-on\.spec\.ts$/, use: { baseURL: `http://127.0.0.1:${trustPort}` } },
    ...extraBrowsers.map((b) => ({
      name: `trust-on-${b}`, testMatch: /trust-on\.spec\.ts$/, grep: /@data-rights/,
      use: { baseURL: `http://127.0.0.1:${trustPort}`, browserName: b },
    })),
  ],
  webServer: [
    {
      command: `npx --no-install next start -p ${port} -H 127.0.0.1`,
      url: `http://127.0.0.1:${port}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { ...serverEnv, STARBUDDY_BFF_URL: process.env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787' },
    },
    {
      command: `npx --no-install next start -p ${trustPort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${trustPort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_TRUST_BFF_URL'] ?? 'http://127.0.0.1:8788',
        STARBUDDY_TRUST_UI: 'why,dispute,reconcile,weekly,data_rights',
      },
    },
  ],
});
