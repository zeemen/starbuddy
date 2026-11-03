// 家庭登录与学生模式入口端到端（specs/unified-shell-sprint4 H1）：真实会话，短信走捕获桩（BFF 把验证码写进 SB_SMS_INBOX）。
// 合成家庭来自 db/seeds/dev/star-map-synthetic.sql 与 auth-synthetic.sql：家长 gS（+99900000101），S1 有同意、S2 没有。
// 用例之间有先后（会话在变），按顺序执行。
import { readFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const PHONE = '+99900000101';
const BFF = process.env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787';

test.describe.configure({ mode: 'serial' });

/** 捕获收件箱里这个号码最近一条验证码（等 BFF 写完） */
async function codeFor(phone: string, after: number): Promise<string> {
  const file = process.env['SB_SMS_INBOX'];
  if (!file) throw new Error('SB_SMS_INBOX 未设置（由 scripts/e2e.sh 提供）');
  for (let i = 0; i < 50; i++) {
    const lines = readFileSync(file, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as { phone: string; code: string; at: string });
    const hit = lines.filter((m) => m.phone === phone && Date.parse(m.at) >= after).at(-1);
    if (hit) return hit.code;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error('收件箱里没有验证码');
}

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => v.id)).toEqual([]);
};

async function sessionCookie(context: BrowserContext) {
  return (await context.cookies()).find((c) => c.name === 'sb_session');
}

const meStatus = async (token: string) => (await fetch(`${BFF}/bff/v0/me`, { headers: { 'x-sb-session': token } })).status;

/** 全程只请求壳自己的来源（不外链、不直连 BFF） */
function sameOriginOnly(page: Page, base: string): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(base) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

let guardianToken = '';
// 登录 → 学生模式 → 退出三步共用一个浏览器上下文（cookie 在步骤之间延续）
let shared: { context: BrowserContext; page: Page };
const BASE = `http://127.0.0.1:${Number(process.env['WEB_PORT'] ?? 3100)}`;  // 与 playwright.config.ts 同一个端口
test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', serviceWorkers: 'block' });
  shared = { context, page: await context.newPage() };
});
test.afterAll(async () => { await shared.context.close(); });

test('没登录访问家长首页与学生页：回登录页', async ({ page }) => {
  await page.goto('/family');
  await expect(page).toHaveURL(/\/login$/);
  await page.goto('/student');
  await expect(page).toHaveURL(/\/login$/);
});

test('验证码不对：统一提示，不登录', async ({ page, context }) => {
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  const t = Date.now() - 1000;
  await page.getByLabel('手机号').fill('+99900000199');
  await page.getByRole('button', { name: '获取验证码' }).click();
  await expect(page.getByTestId('login')).toHaveAttribute('data-step', 'code');
  const code = await codeFor('+99900000199', t);
  await page.getByLabel('验证码').fill(code === '000000' ? '000001' : '000000');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('status')).toHaveAttribute('data-code', 'LOGIN_FAILED');
  await expect(page.getByRole('status')).toHaveText('验证码不对或已失效，请重新获取。');
  expect(await sessionCookie(context)).toBeUndefined();
});

test('家长用 +999 号码登录：从捕获收件箱读码；会话 cookie 是 HttpOnly、SameSite=Lax、Path=/；页面不缓存、axe 无违规', async () => {
  const { page, context } = shared;
  const off = sameOriginOnly(page, BASE);
  const res = await page.goto('/login');
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await axe(page);
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  const t = Date.now() - 1000;
  await page.getByLabel('手机号').fill(PHONE);
  await page.getByRole('button', { name: '获取验证码' }).click();
  await expect(page.getByTestId('login')).toHaveAttribute('data-step', 'code');
  await expect(page.getByRole('status')).toHaveAttribute('data-code', 'SENT');
  await axe(page);
  await page.getByLabel('验证码').fill(await codeFor(PHONE, t));
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).toHaveURL(/\/family$/);
  const c = await sessionCookie(context);
  expect(c).toBeDefined();
  expect(c!.httpOnly).toBe(true);
  expect(c!.sameSite).toBe('Lax');
  expect(c!.path).toBe('/');
  expect(c!.value).toMatch(/^[0-9a-f]{64}$/);
  expect(page.url()).not.toContain(c!.value);
  guardianToken = c!.value;
  await expect(page.getByTestId('child')).toHaveCount(2);
  await expect(page.getByTestId('child').first()).toContainText('孩子 1 · 八年级');
  await axe(page);
  expect(off).toEqual([]);
});

test('学生模式：缺同意时提示先完成同意；有同意的孩子打开后令牌变化、家长旧令牌 401', async () => {
  const { page, context } = shared;
  await page.goto('/family');
  const second = page.getByTestId('open-student-2');
  await expect(second).toHaveAttribute('data-hydrated', 'true');
  await second.getByRole('button').click();
  await expect(second.getByRole('status')).toHaveAttribute('data-code', 'CONSENT_REQUIRED');
  expect(await meStatus(guardianToken)).toBe(200);
  const first = page.getByTestId('open-student-1');
  await first.getByRole('button').click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f-]{36}\/today$/);  // H4：学生模式落在壳的“今日”
  await expect(page.getByTestId('student-mode')).toBeVisible();
  const c = await sessionCookie(context);
  expect(c!.value).not.toBe(guardianToken);
  expect(await meStatus(guardianToken)).toBe(401);
  expect(await meStatus(c!.value)).toBe(200);
  // 学生会话访问家长首页：回学生页
  await page.goto('/family');
  await expect(page).toHaveURL(/\/s\/[0-9a-f-]{36}\/today$/);
  await axe(page);
});

test('退出学生模式：学生会话作废，回登录页', async () => {
  const { page, context } = shared;
  await page.goto('/student');
  const token = (await sessionCookie(context))!.value;
  const exit = page.getByTestId('exit-student-mode');
  await expect(exit).toHaveAttribute('data-hydrated', 'true');
  await exit.getByRole('button').click();
  await expect(page).toHaveURL(/\/login$/);
  expect(await meStatus(token)).toBe(401);
  expect((await sessionCookie(context))?.value ?? '').toBe('');
});
