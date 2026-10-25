// Playwright 配置：由 scripts/e2e.sh 在一次性环境里调用（BFF 地址、端口、AYA_COUNT 由它传入）。
// 视觉回归的用例带 @visual 标签：make web-test 跑其余用例，make web-visual 只跑 @visual（spec 待人确认 #11）。
// 两套前端（冲刺 5-H）：trust-off 连开关全关的 BFF，跑除 trust-on.spec.ts 以外的全部用例（含 4-H“信任界面预留”与 trust-off.spec.ts）；
// trust-on 连 scripts/e2e.sh 起的第二个 BFF（信任界面开关打开），只跑 trust-on.spec.ts。两套共用同一个一次性库与测试构建。
import { defineConfig, devices } from '@playwright/test';

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
// 第五套（错题再练学生端页面，specs/practice-loop-stage1 PR 4）：practice-on 连 scripts/e2e.sh 起的第五个 BFF（STARBUDDY_PRACTICE=practice，
// 学习服务是合成桩 scripts/fake-learning-practice.ts），前端只开 practice；只跑 practice.spec.ts（含 @visual）。另四套前端的 practice 都关
// （trust-off 里的 practice-off.spec.ts 核对入口不出现、页面 404、BFF 路由不注册）。
const practicePort = Number(process.env['WEB_PRACTICE_PORT'] ?? 3104);
// 复测与验证题（specs/retest-delivery-kge05）也在这一套：前端与第五个 BFF 另开 STARBUDDY_RETEST_UI=retest，跑 retest.spec.ts；
// 另四套都关（trust-off 里的 retest-off.spec.ts 核对入口不出现、页面 404、BFF 路由不注册）。
// 今日计划跨科编排（PLT-11，e2e/today-orch.spec.ts）也在这一套：前端与第五个 BFF 另开 STARBUDDY_TODAY_ORCH=today_orch（桩另实现
// /internal/v1/today）；另四套都关（trust-off 里的 today-orch-off.spec.ts 核对今日页没有跨科列表、BFF 响应没有 ordered）。
const PRACTICE_SPECS = /(^|\/)(practice|retest|today-orch)\.spec\.ts$/;
// KGE-06 审核台封测周修订支持（e2e/review-kge06.spec.ts）也在这一套前端：另开 STARBUDDY_REVIEW_FEATURES=tag_worklist,revision_preflight
// （审核台后端由 scripts/e2e.sh 打开同名功能）；另五套前端都关（trust-off 里的 review-kge06-off.spec.ts 核对两个页面 404）。
const REVIEW_KGE06_SPECS = /(^|\/)review-kge06\.spec\.ts$/;
// 第六套（家长诉求，PLT-14 第 1 步）：family-on 连 scripts/e2e.sh 起的第六个 BFF（STARBUDDY_FAMILY_GOALS=family_goals），前端只开 family_goals；
// 只跑 family-goals.spec.ts。另五套前端与 BFF 都关（trust-off 里的 family-goals-off.spec.ts 核对入口不出现、页面 404、BFF 路由不注册）。
const familyPort = Number(process.env['WEB_FAMILY_PORT'] ?? 3105);
// 月度计划（PLT-14 第 3 步，e2e/monthly-plan.spec.ts）也在这一套：前端与第六个 BFF 另开 STARBUDDY_MONTHLY_PLAN=monthly_plan 与
// STARBUDDY_TRUST_UI=why（“为什么”入口），BFF 连真实洞察服务（scripts/e2e.sh）；另五套都关（trust-off 里的 monthly-plan-off.spec.ts 核对）。
const FAMILY_SPECS = /(^|\/)(family-goals|monthly-plan)\.spec\.ts$/;
// 数据权利用例（导出下载、删除二次确认，标 @data-rights）另在 WebKit 与 Firefox 上跑（安全审查 2）：本机只装了 Chromium，
// 所以按需打开——STARBUDDY_E2E_BROWSERS=webkit,firefox（需要对应的 Playwright 浏览器二进制；lessons 登记）。
// LT-07 移动端（specs/launch-lt07-mobile-pwa 范围第 1、5、6 条）：四个移动设备项目（iPhone 13、Pixel 7、360 宽、430 宽）连开关全关的一套前端，跑移动端走查（e2e/mobile.spec.ts，
// 其中另按 test.use 切到信任界面、星图 v1、错题再练三套的前端）、统一壳核心流程（e2e/shell.spec.ts，含 axe）与移动端视觉回归
// （e2e/visual-mobile.spec.ts，@visual）。本机只装了 Chromium：iPhone 13 用 Chromium 按其视口、UA、触屏与 isMobile 模拟（WebKit 真机核对
// 登记为待人确认）；360 宽是常见安卓小屏（视口 360×740）。e2e/pwa.spec.ts（Service Worker）只在 trust-off 跑。
const MOBILE_SPECS = /(^|\/)(mobile|visual-mobile|shell)\.spec\.ts$/;
const MOBILE_ONLY_SPECS = /(^|\/)(mobile|visual-mobile)\.spec\.ts$/;
const MOBILE_DEVICES = [
  { name: 'mobile-iphone13', use: { ...devices['iPhone 13'], browserName: 'chromium' as const, defaultBrowserType: 'chromium' as const } },
  { name: 'mobile-pixel7', use: { ...devices['Pixel 7'] } },
  {
    name: 'mobile-360',
    use: {
      viewport: { width: 360, height: 740 }, screen: { width: 360, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
      userAgent: 'Mozilla/5.0 (Linux; Android 13; SYN-360) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
    },
  },
  {
    // 430 宽：大屏 iPhone（Pro Max 一档）的视口，同样用 Chromium 模拟
    name: 'mobile-430',
    use: {
      viewport: { width: 430, height: 739 }, screen: { width: 430, height: 932 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
      browserName: 'chromium' as const,
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    },
  },
];
// PWA 停用开关（安全审查 1，e2e/pwa-off.spec.ts）：第七个前端打开 STARBUDDY_PWA_KILL_SWITCH=1，连开关全关那一套的 BFF，只跑 pwa-off.spec.ts
const pwaOffPort = Number(process.env['WEB_PWA_OFF_PORT'] ?? 3106);
const PWA_OFF_SPECS = /(^|\/)pwa-off\.spec\.ts$/;
const extraBrowsers = (process.env['STARBUDDY_E2E_BROWSERS'] ?? '').split(',').map((s) => s.trim())
  .filter((b): b is 'webkit' | 'firefox' => b === 'webkit' || b === 'firefox');
/** 端到端用的合成签名密钥（见下方 serverEnv 的说明；e2e/behavior-events.spec.ts 用它构造 cookie） */
export const E2E_PRESENCE_KEY = 'synthetic-e2e-presence-signing-material-000000';
const serverEnv = {
  STARBUDDY_DEV_PAGES: '1',
  STARBUDDY_ENV: 'test',
  // 星图开关显式关闭（不从调用方环境继承）：前两套是 v0；只有 starmap-on 的前端在下面另行打开
  STARBUDDY_STARMAP: '',
  // 错题再练开关显式关闭（不从调用方环境继承）：只有 practice-on 的前端在下面另行打开
  STARBUDDY_PRACTICE: '',
  // 复测与验证题开关同样显式关闭：只有 practice-on 的前端另行打开
  STARBUDDY_RETEST_UI: '',
  // 今日计划跨科编排开关同样显式关闭：只有 practice-on 的前端另行打开
  STARBUDDY_TODAY_ORCH: '',
  // 家长诉求开关同样显式关闭：只有 family-on 的前端另行打开
  STARBUDDY_FAMILY_GOALS: '',
  // 月度计划开关同样显式关闭：只有 family-on 的前端另行打开
  STARBUDDY_MONTHLY_PLAN: '',
  // KGE-06 审核台功能开关同样显式关闭：只有 practice-on 的前端另行打开
  STARBUDDY_REVIEW_FEATURES: '',
  // PWA 停用开关同样显式关闭：只有 pwa-off 的前端另行打开
  STARBUDDY_PWA_KILL_SWITCH: '',
  STARBUDDY_REVIEW_URL: process.env['STARBUDDY_REVIEW_URL'] ?? 'http://127.0.0.1:8790',
  NEXT_TELEMETRY_DISABLED: '1',
  // 学生壳会话状态 cookie 的签名密钥（SIE-05 PR2）：端到端固定一个合成值，用例才能构造“已超时”“被篡改”的 cookie（e2e/behavior-events.spec.ts）；
  // 只在这个一次性测试环境用，不是任何真实环境的密钥。dev 没配时进程内随机，正式环境没配拒绝写入（src/behavior/presence-server.ts）
  STARBUDDY_PRESENCE_HMAC_KEY: E2E_PRESENCE_KEY,
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
    { name: 'trust-off', testIgnore: [/trust-on\.spec\.ts$/, STARMAP_SPECS, REPORT_SPECS, PRACTICE_SPECS, FAMILY_SPECS, REVIEW_KGE06_SPECS, MOBILE_ONLY_SPECS, PWA_OFF_SPECS] },
    { name: 'pwa-off', testMatch: PWA_OFF_SPECS, use: { baseURL: `http://127.0.0.1:${pwaOffPort}` } },
    { name: 'trust-on', testMatch: /trust-on\.spec\.ts$/, use: { baseURL: `http://127.0.0.1:${trustPort}` } },
    { name: 'starmap-on', testMatch: STARMAP_SPECS, use: { baseURL: `http://127.0.0.1:${starmapPort}` } },
    { name: 'report-on', testMatch: REPORT_SPECS, use: { baseURL: `http://127.0.0.1:${reportPort}` } },
    { name: 'practice-on', testMatch: [PRACTICE_SPECS, REVIEW_KGE06_SPECS], use: { baseURL: `http://127.0.0.1:${practicePort}` } },
    { name: 'family-on', testMatch: FAMILY_SPECS, use: { baseURL: `http://127.0.0.1:${familyPort}` } },
    ...MOBILE_DEVICES.map((d) => ({ name: d.name, testMatch: MOBILE_SPECS, use: { ...d.use, baseURL: `http://127.0.0.1:${port}` } })),
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
    {
      command: `npx --no-install next start -p ${practicePort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${practicePort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_PRACTICE_BFF_URL'] ?? 'http://127.0.0.1:8794',
        STARBUDDY_PRACTICE: 'practice',
        STARBUDDY_RETEST_UI: 'retest',
        STARBUDDY_TODAY_ORCH: 'today_orch',
        STARBUDDY_REVIEW_FEATURES: 'tag_worklist,revision_preflight',
      },
    },
    {
      command: `npx --no-install next start -p ${familyPort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${familyPort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_FAMILY_BFF_URL'] ?? 'http://127.0.0.1:8795',
        STARBUDDY_FAMILY_GOALS: 'family_goals',
        STARBUDDY_MONTHLY_PLAN: 'monthly_plan',
        STARBUDDY_TRUST_UI: 'why',
      },
    },
    {
      command: `npx --no-install next start -p ${pwaOffPort} -H 127.0.0.1`,
      url: `http://127.0.0.1:${pwaOffPort}/dev/star-glyphs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...serverEnv,
        STARBUDDY_BFF_URL: process.env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787',
        STARBUDDY_PWA_KILL_SWITCH: '1',
      },
    },
  ],
});
