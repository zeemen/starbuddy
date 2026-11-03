// H5 星图 v1 的视觉回归（@visual，只由 make web-visual 运行；playwright.config.ts 的 starmap-on 项目，前端与 BFF 都开 starmap_v1、starmap_dashed）
// 与 Gate 0 的 v1 星图截图（@gate0，只由 make gate0-starmap-v1 运行；spec 待人确认 #7）。
// 合成家庭 HV（db/seeds/dev/star-map-v1-synthetic.sql）：V1 学生端（亮、保持、半亮、画了的未亮、中性底星、虚线，道法“地图建设中”）
// 在浅色、深色、reduce-motion 下各一张，V1 家长端与 V2（按领域折叠）浅色各一张。基线在 __screenshots__/visual-v1.spec.ts/，
// v0 的基线（__screenshots__/visual.spec.ts/）不动。基线首次提交要有视觉负责人签字（lessons 记录）。
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, realpathSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, resolve, sep } from 'node:path';
import { expect, test, type Browser, type Page } from '@playwright/test';
import { cookieSession, openAs, STARMAP_BFF } from './session.ts';

const V1 = '5e000000-0000-4000-8000-0000000000c1';
const V2 = '5e000000-0000-4000-8000-0000000000c2';

const ENVS = [
  { name: 'light', colorScheme: 'light' as const, reducedMotion: 'no-preference' as const },
  { name: 'dark', colorScheme: 'dark' as const, reducedMotion: 'no-preference' as const },
  { name: 'reduce-motion', colorScheme: 'light' as const, reducedMotion: 'reduce' as const },
];

for (const env of ENVS) {
  test.describe(`@visual v1 ${env.name}`, () => {
    test.use({ colorScheme: env.colorScheme, contextOptions: { reducedMotion: env.reducedMotion } });

    test(`@visual 星图 v1 V1 学生端（${env.name}）`, async ({ page }) => {
      await openAs(page, V1, `/s/${V1}/star-map`);
      await expect(page.locator('[data-starmap="v1"]')).toBeVisible();
      await expect(page).toHaveScreenshot(`star-map-v1-v1-student-${env.name}.png`, { fullPage: true });
    });
  });
}

test('@visual 星图 v1 V1 家长端（light）', async ({ page }) => {
  await openAs(page, 'gV', `/family/${V1}/star-map`);
  await expect(page.locator('[data-starmap="v1"]')).toBeVisible();
  await expect(page).toHaveScreenshot('star-map-v1-v1-guardian-light.png', { fullPage: true });
});

test('@visual 星图 v1 V2 按领域折叠（light）', async ({ page }) => {
  await openAs(page, V2, `/s/${V2}/star-map`);
  await expect(page.locator('[data-starmap="v1"]')).toHaveAttribute('data-folded', 'true');
  await expect(page).toHaveScreenshot('star-map-v1-v2-folded-light.png', { fullPage: true });
});

// ---------------------------------------------------------------- Gate 0：v1 星图截图（未启用）
// 不替换证据包里的 v0 截图（tools/e2e-cohort/sample/starmap/）；另写到 STARBUDDY_GATE0_V1_OUT（make gate0-starmap-v1 缺省为
// tools/e2e-cohort/sample/starmap-v1/）。确定性做法同 scripts/gate0-starmap.ts：视口 1024×768、DPR 1、浅色、zh-CN、上海时区、整页、
// 关动画；PNG（无损），另记文件与像素 SHA-256（像素在浏览器画布里解码后求哈希，请求不出浏览器）。没设输出目录时跳过。
const OUT = process.env['STARBUDDY_GATE0_V1_OUT'] ?? '';
const PIXEL_PAGE = 'http://127.0.0.1/__gate0_pixels';

async function pixelSha256(browser: Browser, bytes: Buffer): Promise<{ sha256: string; width: number; height: number }> {
  const ctx = await browser.newContext();
  try {
    await ctx.route(PIXEL_PAGE, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>pixels</title>' }));
    const p = await ctx.newPage();
    await p.goto(PIXEL_PAGE);
    const out = await p.evaluate(async (src) => {
      const img = new Image();
      img.src = src;
      await img.decode();
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const g = canvas.getContext('2d', { colorSpace: 'srgb' })!;
      g.drawImage(img, 0, 0);
      const data = g.getImageData(0, 0, canvas.width, canvas.height).data;
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', data));
      return { hex: [...digest].map((b) => b.toString(16).padStart(2, '0')).join(''), width: canvas.width, height: canvas.height };
    }, `data:image/png;base64,${bytes.toString('base64')}`);
    return { sha256: out.hex, width: out.width, height: out.height };
  } finally {
    await ctx.close();
  }
}

const git = (...args: string[]): string => {
  try { return execFileSync('git', args, { encoding: 'utf8' }).trim(); } catch { return ''; }
};

async function shot(page: Page, browser: Browser, name: string, path: string) {
  await page.goto(path);
  await expect(page.locator('[data-starmap="v1"]')).toBeVisible();
  const bytes = await page.screenshot({ fullPage: true, type: 'png', animations: 'disabled' });
  const file = `${name}.png`;
  writeFileSync(join(OUT, file), bytes);
  const px = await pixelSha256(browser, bytes);
  const stars = await page.locator('.star-slot').evaluateAll((els) => {
    const c: Record<string, number> = {};
    for (const e of els) c[e.getAttribute('data-display') ?? ''] = (c[e.getAttribute('data-display') ?? ''] ?? 0) + 1;
    return c;
  });
  const claims = await page.locator('main [data-claim-id]').count();
  return { name, path: path.replace(/[0-9a-f-]{36}/, '{student_id}'), file, bytes: bytes.length,
           file_sha256: createHash('sha256').update(bytes).digest('hex'), pixel_sha256: px.sha256, pixel_size: { width: px.width, height: px.height },
           dom: { stars, claim_id_elements: claims } };
}

test('@gate0 Gate 0 星图 v1 截图（未启用：开关开、test 环境、合成数据）', async ({ browser }) => {
  test.skip(OUT === '', '只由 make gate0-starmap-v1 运行（STARBUDDY_GATE0_V1_OUT）');
  // 输出目录只能在仓库内（不往仓库外写截图与记录）
  const repo = realpathSync(git('rev-parse', '--show-toplevel') || '/nonexistent');
  const out = resolve(OUT);
  expect(isAbsolute(OUT) && (out === repo || out.startsWith(`${repo}${sep}`)), `输出目录必须在仓库 ${repo} 内：${OUT}`).toBe(true);
  mkdirSync(out, { recursive: true });
  expect(realpathSync(out).startsWith(`${repo}${sep}`), '输出目录（解析符号链接后）必须在仓库内').toBe(true);
  const ctx = await browser.newContext({ viewport: { width: 1024, height: 768 }, deviceScaleFactor: 1, locale: 'zh-CN', timezoneId: 'Asia/Shanghai',
                                         colorScheme: 'light', serviceWorkers: 'block', baseURL: test.info().project.use.baseURL });
  const page = await ctx.newPage();
  const shots = [];
  try {
    await openAs(page, V1, `/s/${V1}/star-map`);
    shots.push(await shot(page, browser, 'V1-student', `/s/${V1}/star-map`));
    const { token } = await cookieSession(page);
    const api = await fetch(`${STARMAP_BFF}/bff/v0/students/${V1}/star-map/v1`, { headers: { 'x-sb-session': token } });
    expect(api.status).toBe(200);
    await openAs(page, 'gV', `/family/${V1}/star-map`);
    shots.push(await shot(page, browser, 'V1-guardian', `/family/${V1}/star-map`));
  } finally {
    await ctx.close();
  }
  const commit = git('rev-parse', 'HEAD');
  const dirty = git('status', '--porcelain') !== '';
  const record = {
    status: 'captured', enabled: false,
    notice: '未启用：星图 v1 挂在开关 starmap_v1 / starmap_dashed 后（默认关），只在 test 环境、合成数据下截取；不是内测结论，不替换 v0 截图',
    page_version: '星图 v1（H5，开关 starmap_v1 + starmap_dashed，test 环境）',
    data: 'db/seeds/dev/star-map-v1-synthetic.sql（合成家庭 HV：V1 学生端与家长端）；合成数学地图 0.9.0 与合成掌握配置，均未经教研签字',
    login: '真实会话：+999 合成号码 → 短信捕获桩 → 家长首页；学生视角经家长“打开学生模式”',
    browser: 'Playwright Chromium（本机）', viewport: { width: 1024, height: 768 }, device_scale_factor: 1, full_page: true, image: { type: 'png' },
    captured_at_commit: { commit, worktree_dirty: dirty },
    shots,
  };
  writeFileSync(join(OUT, 'starmap-v1.json'), `${JSON.stringify(record, null, 1)}\n`);
  writeFileSync(join(OUT, 'provenance.md'), [
    '# 星图 v1 截图来历（未启用）', '',
    '- 截图状态：captured（**未启用**：开关 starmap_v1 / starmap_dashed 默认关，生产启用待 ADR 11 签字、数学地图发布、判断生成接入调度）',
    '- 截图对应的页面版本：星图 v1（H5），test 环境、开关打开、合成数据（db/seeds/dev/star-map-v1-synthetic.sql）',
    `- 截取提交号：${commit}${dirty ? '（工作区有未提交改动）' : ''}`,
    '- 生成方式：`make gate0-starmap-v1`（apps/web/e2e/visual-v1.spec.ts 的 @gate0 用例；视口 1024×768、DPR 1、浅色、整页、PNG）',
    '- 与 v0 的关系：不替换 `../starmap/` 的 v0 截图（spec 待人确认 #7）；Gate 0 判定不因本截图改变',
    '- 像素哈希：见 `starmap-v1.json` 的 `pixel_sha256`（RGBA 解码后求 SHA-256；同一容器与浏览器版本下应稳定）', '',
  ].join('\n'));
});
