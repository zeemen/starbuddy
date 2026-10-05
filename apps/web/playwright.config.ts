// Playwright 配置：由 scripts/e2e.sh 在一次性环境里调用（BFF 地址、端口、AYA_COUNT 由它传入）。
// 视觉回归的用例带 @visual 标签：make web-test 跑其余用例，make web-visual 只跑 @visual（spec 待人确认 #11）。
// 两套前端（冲刺 5-H）：trust-off 连开关全关的 BFF，跑除 trust-on.spec.ts 以外的全部用例（含 4-H“信任界面预留”与 trust-off.spec.ts）；
// trust-on 连 scripts/e2e.sh 起的第二个 BFF（信任界面开关打开），只跑 trust-on.spec.ts。两套共用同一个一次性库与测试构建。
import { defineConfig } from '@playwright/test';

// 第三套（H5 星图 v1）：starmap-on 连 scripts/e2e.sh 起的第三个 BFF（STARBUDDY_STARMAP=starmap_v1,starmap_dashed，显示规则把合成星图模板
// 列进白名单）与第二个学习服务（带合成数学掌握配置），只跑 star-map-v1.spec.ts 与 visual-v1.spec.ts；前端同样打开两个星图开关。
// 另两套的星图开关都关（v0 原样，v0 的视觉回归与端到端断言不变）。
const port = Number(process.env['WEB_PORT'] ?? 3100);
const trustPort = Number(process.env['WEB_TRUST_PORT'] ?? 3101);
const starmapPort = Number(process.env['WEB_STARMAP_PORT'] ?? 3102);
const STARMAP_SPECS = /(star-map-v1|visual-v1)\.spec\.ts$/;
// 第四套（SIE-02b 第二步 PR 2 首份报告开发演示页）：report-on 连 scripts/e2e.sh 起的第四个 BFF（STARBUDDY_TRUST_UI=report,why，
// 洞察服务是合成桩 scripts/fake-insight.ts），前端只开 report；只跑 first-report.spec.ts（含 @visual）。另三套前端的 report 都关
// （trust-off 里另核对演示页 404、BFF 路由不注册）。
const reportPort = Number(process.env['WEB_REPORT_PORT'] ?? 3103);
const REPORT_SPECS = /first-report\.spec\.ts$/;
// 数据权利用例（导出下载、删除二次确认，标 @data-rights）另在 WebKit 与 Firefox 上跑（安全审查 2）：本机只装了 Chromium，
// 所以按需打开——STARBUDDY_E2E_BROWSERS=webkit,firefox（需要对应的 Playwright 浏览器二进制；lessons 登记）。
const extraBrowsers = (process.env['STARBUDDY_E2E_BROWSERS'] ?? '').split(',').map((s) => s.trim())
  .filter((b): b is 'webkit' | 'firefox' => b === 'webkit' || b === 'firefox');
const serverEnv = {
  STARBUDDY_DEV_PAGES: '1',
  STARBUDDY_ENV: 'test',
  // 星图开关显式关闭（不从调用方环境继承）：前两套是 v0；只有 starmap-on 的前端在下面另行打开
  STARBUDDY_STARMAP: '',
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
    { name: 'trust-off', testIgnore: [/trust-on\.spec\.ts$/, STARMAP_SPECS, REPORT_SPECS] },
    { name: 'trust-on', testMatch: /trust-on\.spec\.ts$/, use: { baseURL: `http://127.0.0.1:${trustPort}` } },
    { name: 'starmap-on', testMatch: STARMAP_SPECS, use: { baseURL: `http://127.0.0.1:${starmapPort}` } },
    { name: 'report-on', testMatch: REPORT_SPECS, use: { baseURL: `http://127.0.0.1:${reportPort}` } },
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
    {
      command: `npx --no-install next start -p ${starmapPort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${starmapPort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_STARMAP_BFF_URL'] ?? 'http://127.0.0.1:8789',
        STARBUDDY_STARMAP: 'starmap_v1,starmap_dashed',
      },
    },
    {
      command: `npx --no-install next start -p ${reportPort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${reportPort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_REPORT_BFF_URL'] ?? 'http://127.0.0.1:8793',
        STARBUDDY_TRUST_UI: 'report',
      },
    },
  ],
});
