// Gate 0 证据包的星图截图（specs/e2e-integration-sprint5/spec.md 第 3 节、待人确认 #4）：
// 由 tools/e2e-cohort（make gate0-evidence GATE0_STARMAP=1）在合成联调完成后的同一一次性库上调用；BFF 与 next start 由调用方起好。
// 登录走真实会话（同 e2e/session.ts）：登录页填 +999 合成号码 → 从短信捕获桩的收件箱读验证码 → 家长首页；
// 学生视角另经 BFF 同意接口补合成同意，再点“打开学生模式”。只用本机 Chromium（PLAYWRIGHT_BROWSERS_PATH），不下载浏览器。
// 每张截图另用同一会话 cookie 取 BFF 星图 JSON（节点身份以其中的 skill = 科目:local_id 为准，DOM 只出计数），
// 并试开一名未绑定学生（另一户）的星图页与 BFF 星图接口（应都是 404）。
// 依赖界面文案与 data-testid 定位（登录页的“手机号”“获取验证码”“登录”、家长首页的“打开学生模式”），改版时同步这里。
// 输出：截图写到 plan.out_dir；标准输出一行 JSON（DOM 统计、BFF JSON、跨学生状态码、文件与像素 SHA-256）。对账在 Python 侧做。
// 用法：node --no-warnings scripts/gate0-starmap.ts <plan.json>（只用合成数据，规则 8）
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium, type Browser, type Page } from '@playwright/test';

interface Shot {
  name: string;
  kind: 'student' | 'guardian';
  student_id: string;
  phone: string;
  path: string;
  other_student_id: string;
  other_path: string;
}
interface Plan {
  base: string;
  bff: string;
  inbox: string;
  out_dir: string;
  viewport: { width: number; height: number };
  image: { type: 'jpeg' | 'png'; quality?: number };
  shots: Shot[];
}

const plan = JSON.parse(readFileSync(process.argv[2] ?? '', 'utf8')) as Plan;
const TIMEOUT = 30_000;

async function codeFor(phone: string, after: number): Promise<string> {
  for (let i = 0; i < 100; i++) {
    const lines = readFileSync(plan.inbox, 'utf8').split('\n').filter(Boolean)
      .map((l) => JSON.parse(l) as { phone: string; code: string; at: string });
    const hit = lines.filter((m) => m.phone === phone && Date.parse(m.at) >= after).at(-1);
    if (hit) return hit.code;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`收件箱里没有 ${phone} 的验证码`);
}

async function loginGuardian(page: Page, phone: string): Promise<void> {
  await page.goto(`${plan.base}/login`);
  await page.locator('[data-testid="login"][data-hydrated="true"]').waitFor({ timeout: TIMEOUT });
  const t = Date.now() - 1000;
  await page.getByLabel('手机号').fill(phone);
  await page.getByRole('button', { name: '获取验证码' }).click();
  await page.locator('[data-testid="login"][data-step="code"]').waitFor({ timeout: TIMEOUT });
  await page.getByLabel('验证码').fill(await codeFor(phone, t));
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL(/\/family$/, { timeout: TIMEOUT });
}

/** 家长经 BFF 的同意接口补合成同意（与 e2e/session.ts 同一条路由、同一套 CSRF） */
async function grantConsents(page: Page, student: string): Promise<void> {
  const jar = await page.context().cookies();
  const get = (n: string) => jar.find((c) => c.name === n)?.value ?? '';
  for (const [type, doc] of [['privacy_policy', 'privacy-policy@synthetic-0'], ['guardian_consent_under14', 'guardian-consent@synthetic-0']]) {
    const r = await fetch(`${plan.bff}/bff/v0/students/${student}/consents`, {
      method: 'POST',
      headers: { 'x-sb-session': get('sb_session'), 'x-csrf-token': get('sb_csrf'), origin: plan.base, 'content-type': 'application/json' },
      body: JSON.stringify({ consent_type: type, document_version: doc, decision: 'granted' }),
    });
    if (r.status !== 201) throw new Error(`合成同意 ${type} 失败：HTTP ${r.status}`);
  }
}

async function loginStudent(page: Page, phone: string, student: string): Promise<void> {
  await loginGuardian(page, phone);
  await grantConsents(page, student);
  const form = page.locator('form[data-testid^="open-student-"]', { has: page.locator(`input[name="student"][value="${student}"]`) });
  await form.and(page.locator('[data-hydrated="true"]')).waitFor({ timeout: TIMEOUT });
  await form.getByRole('button', { name: /打开学生模式/ }).click();
  await page.waitForURL(new RegExp(`/s/${student}/`), { timeout: TIMEOUT });
}

/** 同一会话直接读 BFF 星图接口（GET 只带会话头，同 src/lib/bff.ts）：返回状态码与 JSON */
async function bffStarMap(page: Page, student: string): Promise<{ status: number; body: unknown }> {
  const token = (await page.context().cookies()).find((c) => c.name === 'sb_session')?.value ?? '';
  const r = await fetch(`${plan.bff}/bff/v0/students/${student}/star-map`, { headers: { 'x-sb-session': token } });
  const body: unknown = r.headers.get('content-type')?.includes('json') ? await r.json() : null;
  return { status: r.status, body };
}

/** 星图页 DOM 统计：只读 data 属性与列表视图每行的三态、保持、待验证（节点身份不从 DOM 取） */
async function domStats(page: Page) {
  return page.evaluate(() => {
    const skies = [...document.querySelectorAll('section.sky')].map((sec) => {
      const field = sec.querySelector('.sky-field');
      const svgs = [...sec.querySelectorAll('.sky-field svg.star')];
      const count = (attr: string, v: string) => svgs.filter((s) => s.getAttribute(attr) === v).length;
      const rows = [...sec.querySelectorAll('tbody tr')].map((tr) => ({
        display: tr.getAttribute('data-display') ?? '',
        retained: tr.getAttribute('data-retained') === 'true',
        needs_verification: (tr.children[2]?.textContent ?? '').trim() !== '',
      }));
      return {
        subject: sec.getAttribute('data-subject') ?? '',
        svg: { total: svgs.length, unlit: count('data-display', 'unlit'), half: count('data-display', 'half'),
               lit: count('data-display', 'lit'), retained: count('data-retained', 'true') },
        summary: { unlit: Number(field?.getAttribute('data-summary-unlit')), half: Number(field?.getAttribute('data-summary-half')),
                   lit: Number(field?.getAttribute('data-summary-lit')) },
        rows,
      };
    });
    return {
      watermark: document.querySelector('[data-testid="watermark"]')?.textContent ?? null,
      empty: document.querySelector('[data-testid="empty"]') !== null,
      skies,
    };
  });
}

/** 像素哈希：把截图解码成 RGBA 再求 SHA-256。解码在浏览器画布里做：一个只由本脚本拦截应答的回环地址页面
 *  （127.0.0.1 是安全上下文，才有 crypto.subtle；请求不出浏览器，不经过任何服务） */
const PIXEL_PAGE = 'http://127.0.0.1/__gate0_pixels';
async function pixelSha256(browser: Browser, bytes: Buffer, type: string): Promise<{ sha256: string; width: number; height: number }> {
  const ctx = await browser.newContext();
  try {
    await ctx.route(PIXEL_PAGE, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>pixels</title>' }));
    const page = await ctx.newPage();
    await page.goto(PIXEL_PAGE);
    const out = await page.evaluate(async (src) => {
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
    }, `data:image/${type};base64,${bytes.toString('base64')}`);
    return { sha256: out.hex, width: out.width, height: out.height };
  } finally {
    await ctx.close();
  }
}

async function main(): Promise<void> {
  const browser = await chromium.launch();  // 本机已装的 Chromium；缺浏览器时这里抛错，调用方记 failed
  const results = [];
  try {
    for (const shot of plan.shots) {
      // 每张一个干净的浏览器上下文：会话只来自这一次登录
      const ctx = await browser.newContext({ viewport: plan.viewport, deviceScaleFactor: 1, locale: 'zh-CN', timezoneId: 'Asia/Shanghai',
                                             colorScheme: 'light', serviceWorkers: 'block' });
      try {
        const page = await ctx.newPage();
        if (shot.kind === 'student') await loginStudent(page, shot.phone, shot.student_id);
        else await loginGuardian(page, shot.phone);
        const res = await page.goto(`${plan.base}${shot.path}`);
        const status = res?.status() ?? 0;
        const finalPath = new URL(page.url()).pathname;
        if (status !== 200 || finalPath !== shot.path) throw new Error(`${shot.name}：打开 ${shot.path} 得到 HTTP ${status}、停在 ${finalPath}`);
        await page.locator('main h1').waitFor({ timeout: TIMEOUT });
        const dom = await domStats(page);
        const ext = plan.image.type === 'jpeg' ? 'jpg' : 'png';
        const file = `${shot.name}.${ext}`;
        const bytes = await page.screenshot({ fullPage: true, type: plan.image.type, animations: 'disabled',
                                              ...(plan.image.type === 'jpeg' ? { quality: plan.image.quality ?? 70 } : {}) });
        writeFileSync(join(plan.out_dir, file), bytes);
        const api = await bffStarMap(page, shot.student_id);
        if (api.status !== 200) throw new Error(`${shot.name}：同一会话读 BFF 星图得到 HTTP ${api.status}`);
        // 跨学生：同一会话打开未绑定学生的星图页与 BFF 接口（对账要求都是 404；这里只记录，不判断）
        const crossPage = await page.goto(`${plan.base}${shot.other_path}`);
        const crossBff = await bffStarMap(page, shot.other_student_id);
        const px = await pixelSha256(browser, bytes, plan.image.type);
        results.push({ name: shot.name, kind: shot.kind, student_id: shot.student_id, path: shot.path, http_status: status, file,
                       bytes: bytes.length, file_sha256: createHash('sha256').update(bytes).digest('hex'),
                       pixel_sha256: px.sha256, pixel_size: { width: px.width, height: px.height }, dom, api: api.body,
                       cross: { page_status: crossPage?.status() ?? 0, bff_status: crossBff.status } });
      } finally {
        await ctx.close();
      }
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(`${JSON.stringify({ browser: 'chromium', shots: results })}\n`);
}

main().catch((e: unknown) => {
  process.stderr.write(`gate0-starmap: ${e instanceof Error ? e.message : String(e)}\n`);
  process.exit(1);
});
