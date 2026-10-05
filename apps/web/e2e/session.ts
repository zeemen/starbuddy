// 端到端的真实会话（4-H H2：开发身份桩删除后，所有端到端都走真实登录）：
//   家长：登录页填 +999 合成号码 → 从短信捕获桩的收件箱（SB_SMS_INBOX，BFF 写入）读验证码 → 登录，进家长首页；
//   学生：家长登录后，在家长首页点“打开学生模式”（打开前经 BFF 的同意接口补上合成同意，与家长在界面上操作走同一条路由）。
// 合成家庭与号码见 db/seeds/dev/star-map-synthetic.sql、diagnosis-synthetic.sql、auth-synthetic.sql、shell-synthetic.sql、star-map-v1-synthetic.sql、first-report-synthetic.sql。
import { readFileSync } from 'node:fs';
import { expect, type Page } from '@playwright/test';

export const BFF = process.env['STARBUDDY_BFF_URL'] ?? 'http://127.0.0.1:8787';
export const BASE = `http://127.0.0.1:${Number(process.env['WEB_PORT'] ?? 3100)}`;  // 与 playwright.config.ts 同一个端口
/** 信任界面开关打开的第二套（冲刺 5-H，playwright.config.ts 的 trust-on 项目）：前端与它连的 BFF */
export const TRUST_BFF = process.env['STARBUDDY_TRUST_BFF_URL'] ?? 'http://127.0.0.1:8788';
export const TRUST_BASE = `http://127.0.0.1:${Number(process.env['WEB_TRUST_PORT'] ?? 3101)}`;
/** H5 星图 v1 开关打开的第三套（playwright.config.ts 的 starmap-on 项目）：前端与它连的 BFF */
export const STARMAP_BFF = process.env['STARBUDDY_STARMAP_BFF_URL'] ?? 'http://127.0.0.1:8789';
export const STARMAP_BASE = `http://127.0.0.1:${Number(process.env['WEB_STARMAP_PORT'] ?? 3102)}`;
/** 首份报告开发演示页的第四套（SIE-02b 第二步 PR 2，playwright.config.ts 的 report-on 项目）：前端与它连的 BFF */
export const REPORT_BFF = process.env['STARBUDDY_REPORT_BFF_URL'] ?? 'http://127.0.0.1:8793';
export const REPORT_BASE = `http://127.0.0.1:${Number(process.env['WEB_REPORT_PORT'] ?? 3103)}`;
/** 当前页面所在的那一套：前端来源与它连的 BFF（CSRF 令牌由各自的 BFF 签，不能混用） */
export const endpointsOf = (page: Page): { base: string; bff: string } => {
  const origin = new URL(page.url()).origin;
  if (origin === TRUST_BASE) return { base: TRUST_BASE, bff: TRUST_BFF };
  if (origin === STARMAP_BASE) return { base: STARMAP_BASE, bff: STARMAP_BFF };
  if (origin === REPORT_BASE) return { base: REPORT_BASE, bff: REPORT_BFF };
  return { base: BASE, bff: BFF };
};

/** 合成家长的登录号码（未分配的国家码 +999） */
export const PHONE = { gS: '+99900000101', gO: '+99900000102', gD: '+99900000105', gH: '+99900000106', gV: '+99900000107', gR: '+99900000108' } as const;
/** 学生档案 → 打开学生模式的家长 */
export const GUARDIAN_OF: Record<string, keyof typeof PHONE> = {
  '5a000000-0000-4000-8000-0000000000c1': 'gS',  // S1
  '5a000000-0000-4000-8000-0000000000c2': 'gS',  // S2（S1 的妹妹）
  '5a000000-0000-4000-8000-0000000000c5': 'gD',  // D1（数学诊断演示）
  '5d000000-0000-4000-8000-0000000000c1': 'gH',  // GT（统一壳：试用中）
  '5d000000-0000-4000-8000-0000000000c2': 'gH',  // GR（留存态）
  '5d000000-0000-4000-8000-0000000000c3': 'gH',  // GP（试点，在读数学）
  '5e000000-0000-4000-8000-0000000000c1': 'gV',  // V1（星图 v1：数学、道法、英语）
  '5e000000-0000-4000-8000-0000000000c2': 'gV',  // V2（星图 v1：数学、语文，折叠）
  '5c000000-0000-4000-8000-0000000000c1': 'gR',  // R1（首份报告演示：四条合成判断）
  '5c000000-0000-4000-8000-0000000000c2': 'gR',  // R2（R1 的兄妹，没有判断）
};

/** 捕获收件箱里这个号码在 after 之后最近一条验证码（等 BFF 写完） */
export async function codeFor(phone: string, after: number): Promise<string> {
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

/** 以家长身份登录（走登录页），停在家长首页 */
export async function loginGuardian(page: Page, who: keyof typeof PHONE): Promise<void> {
  const phone = PHONE[who];
  // 换身份：先丢掉本上下文里原来的会话 cookie（已登录时登录页会直接转走）
  await page.context().clearCookies();
  await page.goto('/login');
  await expect(page.getByTestId('login')).toHaveAttribute('data-hydrated', 'true');
  const t = Date.now() - 1000;
  await page.getByLabel('手机号').fill(phone);
  await page.getByRole('button', { name: '获取验证码' }).click();
  await expect(page.getByTestId('login')).toHaveAttribute('data-step', 'code');
  await page.getByLabel('验证码').fill(await codeFor(phone, t));
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).toHaveURL(/\/family$/);
}

/** 当前浏览器上下文里的会话与 CSRF 令牌（HttpOnly cookie，测试从上下文读） */
export async function cookieSession(page: Page): Promise<{ token: string; csrf: string }> {
  const jar = await page.context().cookies();
  const get = (n: string) => jar.find((c) => c.name === n)?.value ?? '';
  return { token: get('sb_session'), csrf: get('sb_csrf') };
}

/** 家长经 BFF 的同意接口补上打开学生模式需要的合成同意（与家长在界面上操作是同一条路由、同一套 CSRF） */
async function grantConsents(page: Page, student: string): Promise<void> {
  const s = await cookieSession(page);
  const { base, bff } = endpointsOf(page);
  for (const [type, doc] of [['privacy_policy', 'privacy-policy@synthetic-0'], ['guardian_consent_under14', 'guardian-consent@synthetic-0']]) {
    const r = await fetch(`${bff}/bff/v0/students/${student}/consents`, {
      method: 'POST',
      headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, origin: base, 'content-type': 'application/json' },
      body: JSON.stringify({ consent_type: type, document_version: doc, decision: 'granted' }),
    });
    expect(r.status, `consent ${type}`).toBe(201);
  }
}

/** 以学生身份进入：家长登录 → 补同意 → 在家长首页为这个孩子打开学生模式，停在壳的“今日”（H4：/student 转到 /s/{id}/today） */
export async function loginStudent(page: Page, student: string): Promise<void> {
  const guardian = GUARDIAN_OF[student];
  if (!guardian) throw new Error(`没有登记 ${student} 的合成家长`);
  await loginGuardian(page, guardian);
  await grantConsents(page, student);
  // 家长首页每个孩子有两张带学生 ID 的表单（记录同意、打开学生模式，H4）：只取打开学生模式那张
  const form = page.locator('form[data-testid^="open-student-"]', { has: page.locator(`input[name="student"][value="${student}"]`) });
  await expect(form).toHaveAttribute('data-hydrated', 'true');
  await form.getByRole('button', { name: /打开学生模式/ }).click();
  await expect(page).toHaveURL(new RegExp(`/s/${student}/today$`));
}

/** 以某个身份打开一个页面（家长用号码代号，学生用档案 ID） */
export async function openAs(page: Page, who: keyof typeof PHONE | string, path: string) {
  if (who in PHONE) await loginGuardian(page, who as keyof typeof PHONE);
  else await loginStudent(page, who);
  return page.goto(path);
}
