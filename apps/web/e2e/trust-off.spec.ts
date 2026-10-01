// 冲刺 5-H 信任界面：开关全关（默认）的一套（playwright.config.ts 的 trust-off 项目，BFF 也全关）。
// 4-H“信任界面预留”验收继续成立：学生与家长的页面上没有任何信任界面元素、浏览器不发任何信任界面请求，
// 信任界面的页面与下载路由一律 404（前端在读会话之前就判断开关，BFF 的路由也没注册）。合成家庭 HG（db/seeds/dev/shell-synthetic.sql）。
import { expect, test, type Page } from '@playwright/test';
import { BASE, BFF, cookieSession, loginGuardian, loginStudent } from './session.ts';

const GT = '5d000000-0000-4000-8000-0000000000c1';
const GP = '5d000000-0000-4000-8000-0000000000c3';
const CLAIM = '00000000-0000-4000-8000-00000000f001';
const EXAM = '00000000-0000-4000-8000-00000000b001';
const TRUST_PATH = /\/(why|disputes|exams|data|weekly)(\/|$)|explain|reconcil|export|erasure|weekly-report/;
const TRUST_ELEMENTS = '[data-trust-slot], [data-trust-link], [data-trust-nav], [data-trust-root]';

function watch(page: Page): string[] {
  const paths: string[] = [];
  page.on('request', (r) => paths.push(new URL(r.url()).pathname));
  return paths;
}

test('开关关：学生壳各页没有信任界面元素、不发信任界面请求；信任界面页面一律 404', async ({ page }) => {
  const paths = watch(page);
  await loginStudent(page, GT);
  for (const p of [`/s/${GT}/today`, `/s/${GT}/star-map`, `/s/${GT}/learn`, `/s/${GT}/learn/math`]) {
    await page.goto(p);
    await expect(page.locator(TRUST_ELEMENTS), p).toHaveCount(0);
  }
  expect(paths.filter((p) => TRUST_PATH.test(p))).toEqual([]);
  for (const p of [`/s/${GT}/why/${CLAIM}`, `/s/${GT}/disputes`, `/s/${GT}/exams`, `/s/${GT}/exams/${EXAM}`, `/s/${GT}/weekly`]) {
    expect((await page.goto(p))?.status(), p).toBe(404);
    await expect(page.locator(TRUST_ELEMENTS), p).toHaveCount(0);
  }
});

test('开关关：家长孩子页没有信任界面入口；信任界面页面与下载路由 404；BFF 的信任界面路由也不存在', async ({ page }) => {
  const paths = watch(page);
  await loginGuardian(page, 'gH');
  await page.goto(`/family/${GP}`);
  await expect(page.getByTestId('child-page')).toBeVisible();
  await expect(page.locator(TRUST_ELEMENTS)).toHaveCount(0);
  expect(paths.filter((p) => TRUST_PATH.test(p))).toEqual([]);
  for (const p of [`/family/${GP}/why/${CLAIM}`, `/family/${GP}/disputes`, `/family/${GP}/exams`, `/family/${GP}/exams/${EXAM}`, `/family/${GP}/data`,
                   `/family/${GP}/weekly`, `/family/${GT}/weekly`]) {
    expect((await page.goto(p))?.status(), p).toBe(404);
  }
  const s = await cookieSession(page);
  const dl = await page.request.post(`/family/${GP}/data/download`, { form: { csrf: s.csrf, job: EXAM, download_ref: `dl_${'0'.repeat(64)}` } });
  expect(dl.status()).toBe(404);
  const get = await page.request.get(`/family/${GP}/data/download`);
  expect(get.status()).toBe(404);
  expect(get.headers()['cache-control']).toBe('no-store');
  // BFF（开关关）：信任界面路由没注册，与表外路径同一个 404
  for (const [method, p] of [['GET', `/students/${GP}/disputes`], ['GET', `/students/${GP}/erasure`], ['POST', `/students/${GP}/exports`],
                             ['GET', `/students/${GT}/weekly-report`]] as const) {
    const r = await fetch(`${BFF}/bff/v0${p}`, { method, headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, origin: BASE } });
    expect(r.status, p).toBe(404);
  }
});
