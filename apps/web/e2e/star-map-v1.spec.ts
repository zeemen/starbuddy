// H5 星图 v1（specs/star-map-v1-h5/spec.md 验收；统一壳 spec 第 7.2、8 节与第 400 行“星图 7.2”“陈述—判断一致性”）：
// 开关打开的第三套（playwright.config.ts 的 starmap-on 项目；前端与第三个 BFF 都开 starmap_v1、starmap_dashed，
// 第二个学习服务带合成数学掌握配置；没有洞察服务，星数说明与迁移摘要不出现）。合成家庭 HV 见 db/seeds/dev/star-map-v1-synthetic.sql：
//   V1 在读数学（119 颗，不折叠）、道法（没有已发布地图 →“地图建设中”）、英语（不出现）；V2 在读数学与语文（共 121 颗 → 按领域折叠）。
// 全程真实会话（短信捕获桩读码）。每页另核对：axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
// 星图区域的每个文本节点要么是统一文案文件里的文案，要么在 data-claim-id（关于学生）、data-value（数字、日期）或
// data-node-name（已发布地图里的节点、领域、主题名）里；页面上每个 data-claim-id 都是同一会话 BFF 响应里这名受众可见的判断。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, STAR_DISPLAY_V1 } from '../src/shell/copy.ts';
import { targets } from './crawl-pages.ts';
import { crawl } from './headers-check.ts';
import { cookieSession, loginGuardian, loginStudent, openAs, STARMAP_BASE, STARMAP_BFF } from './session.ts';

const V1 = '5e000000-0000-4000-8000-0000000000c1';
const V2 = '5e000000-0000-4000-8000-0000000000c2';
const S1 = '5a000000-0000-4000-8000-0000000000c1';  // 另一户（家长 gS）的孩子
const node = (n: number) => `MATH.SV1A.T1.N${String(n).padStart(3, '0')}`;
const claim = (n: number) => `5e000000-0000-4000-8000-0000000002${String(n).padStart(2, '0')}`;
const studentMap = (s: string) => `/s/${s}/star-map`;
const familyMap = (s: string) => `/family/${s}/star-map`;

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

function watchOrigin(page: Page): string[] {
  const all: string[] = [];
  page.on('request', (r) => all.push(r.url()));
  return all;
}

/** 同一会话直接读 BFF 的 v1 接口（GET 只带会话头，同 src/lib/bff.ts） */
async function bffV1(page: Page, student: string): Promise<{ status: number; body: Record<string, unknown> | null }> {
  const { token } = await cookieSession(page);
  const r = await fetch(`${STARMAP_BFF}/bff/v0/students/${student}/star-map/v1`, { headers: { 'x-sb-session': token } });
  return { status: r.status, body: r.headers.get('content-type')?.includes('json') ? await r.json() as Record<string, unknown> : null };
}

/** BFF 响应里全部判断 ID（星与陈述） */
const claimIdsOf = (body: unknown): string[] => [...JSON.stringify(body).matchAll(/"claim_id":"([0-9a-f-]{36})"/g)].map((m) => m[1]!).sort();

/** 星图区域的文本节点核对 + data-claim-id 都来自同一会话的 BFF 响应 */
async function onlyCopyClaimsOrNames(page: Page, student: string): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    for (const root of Array.from(document.querySelectorAll('main'))) {
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        const t = (n.textContent ?? '').trim();
        const el = n.parentElement;
        if (t === '' || !el || el.closest('[data-claim-id], [data-value], [data-node-name], script, style')) continue;
        if (!known.has(t)) out.push(t);
      }
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  // 状态词（STAR_DISPLAY_V1，不在壳文案表里）：每一次出现都在 data-claim-id 元素内
  const words = Object.values(STAR_DISPLAY_V1);
  const misplaced = await page.evaluate((ws) => {
    const out: string[] = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = (n.textContent ?? '').trim();
      if (ws.includes(t) && !n.parentElement?.closest('[data-claim-id]')) out.push(t);
    }
    return out;
  }, words);
  expect(misplaced).toEqual([]);
  const api = await bffV1(page, student);
  expect(api.status).toBe(200);
  const dom = (await page.locator('main [data-claim-id]').evaluateAll((els) => els.map((e) => e.getAttribute('data-claim-id') ?? '')));
  expect([...new Set(dom)].sort()).toEqual([...new Set(claimIdsOf(api.body))]);
}

const slot = (page: Page, n: number) => page.locator(`.star-slot[data-node-id="${node(n)}"]`);

test('V1 本人：数学 119 颗不折叠；画了的星带 claim_id；没有判断、依据不对、只有迁移判断、只对家长可见的都是中性底星；虚线只显示下一次验证日期', async ({ page }) => {
  const reqs = watchOrigin(page);
  const res = await openAs(page, V1, studentMap(V1));
  expect(res?.status()).toBe(200);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByTestId('watermark')).toHaveText('原型 · 合成数据');  // spec #6
  await expect(page.locator('[data-starmap="v1"]')).toHaveAttribute('data-folded', 'false');
  await expect(page.locator('.sky-field')).toHaveCount(0);  // v0 的星空不出现
  // 学科：数学、道法（地图建设中）；英语不出现
  await expect(page.locator('section.sky-v1 > h2')).toHaveText(['数学', '道法']);
  await expect(page.locator('section.sky-v1[data-subject="english"]')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText('英语');
  await expect(page.getByTestId('building-politics')).toHaveText('地图建设中');
  await expect(page.locator('section.sky-v1[data-subject="politics"] .star-slot')).toHaveCount(0);
  // 领域是星座、主题是子群
  const math = page.locator('section.sky-v1[data-subject="math"]');
  await expect(math.locator('section.constellation')).toHaveCount(3);
  await expect(math.locator('.subgroup')).toHaveCount(12);
  await expect(math.locator('.star-slot')).toHaveCount(119);
  // 画了的星
  const painted: [number, string, string][] = [[1, 'lit', 'false'], [2, 'lit', 'true'], [3, 'half', 'false'], [4, 'unlit', 'false']];
  for (const [n, d, r] of painted) {
    await expect(slot(page, n)).toHaveAttribute('data-display', d);
    await expect(slot(page, n)).toHaveAttribute('data-retained', r);
    await expect(slot(page, n)).toHaveAttribute('data-claim-id', claim(n));
  }
  await expect(slot(page, 2).locator('svg circle')).toHaveCount(1);  // “保持”外圈
  await expect(slot(page, 1).locator('.visually-hidden')).toHaveText('（合成）知识点 001：亮');
  await expect(slot(page, 2).locator('.visually-hidden')).toHaveText('（合成）知识点 002：亮·保持');
  // 中性底星：不带 claim_id，没有可见文字，读屏读“暂无可展示的结论”
  for (const n of [5, 6, 8, 9, 10, 119]) {
    const s = n === 119 ? page.locator('.star-slot[data-node-id="MATH.SV1C.T4.N119"]') : slot(page, n);
    await expect(s).toHaveAttribute('data-display', 'neutral');
    await expect(s).not.toHaveAttribute('data-claim-id', /.*/);
    await expect(s.locator('.visually-hidden')).toHaveText(/：暂无可展示的结论$/);
    expect(await s.locator(':scope > :not(.visually-hidden)').evaluateAll((els) => els.map((e) => e.textContent).join(''))).toBe('');
  }
  // 中性底星与“没有证据”（画了的未亮）同一外观：SVG 只差 data-display
  const svgOf = async (n: number) => (await slot(page, n).locator('svg').evaluate((e) => e.outerHTML)).replace(/data-display="[a-z]+"/, '');
  expect(await svgOf(5)).toBe(await svgOf(4));
  // 虚线：带 claim_id，只显示下一次验证日期（上海时区），不带结论文字
  await expect(slot(page, 7)).toHaveAttribute('data-display', 'dashed');
  await expect(slot(page, 7)).toHaveAttribute('data-claim-id', claim(7));
  await expect(slot(page, 7).locator('svg polygon')).toHaveAttribute('stroke-dasharray', /\d/);
  const check = math.locator(`.next-checks [data-node-id="${node(7)}"]`);
  await expect(check).toHaveAttribute('data-claim-id', claim(7));
  await expect(check).toHaveText('（合成）知识点 007：下一次验证 2026-11-03');
  await expect(check.locator('[data-value="date"]')).toHaveText('2026-11-03');
  // 可见文字里没有状态词与结论（状态词只在带 data-claim-id 的读屏文字里）
  const visible = await math.evaluate((el) => {
    const c = el.cloneNode(true) as HTMLElement;
    c.querySelectorAll('.visually-hidden').forEach((x) => x.remove());
    return c.textContent ?? '';
  });
  expect(visible).not.toMatch(/亮|掌握|待验证|结论/);
  // 没有洞察服务：星数说明与迁移摘要不出现
  await expect(page.locator('.sky-statement')).toHaveCount(0);
  // 能力视图侧栏：占位，不发数据请求（浏览器只请求本前端的页面与静态资源）
  await expect(page.getByTestId('skill-view')).toContainText('能力视图即将开放');
  expect(reqs.filter((u) => !u.startsWith(STARMAP_BASE) && !u.startsWith('data:'))).toEqual([]);
  expect(reqs.filter((u) => /skill|insight|explain|star-map\/v1/.test(new URL(u).pathname))).toEqual([]);
  await onlyCopyClaimsOrNames(page, V1);
  await axe(page);
});

test('家长 gV：只按家长可见范围取判断（N009 对家长亮、学生端中性）；兄妹、另一户、不存在的学生同一个 404', async ({ page }) => {
  await openAs(page, 'gV', familyMap(V1));
  await expect(slot(page, 9)).toHaveAttribute('data-display', 'lit');
  await expect(slot(page, 9)).toHaveAttribute('data-claim-id', claim(9));
  await expect(slot(page, 1)).toHaveAttribute('data-claim-id', claim(1));
  await expect(page.locator('[data-starmap="v1"]')).toHaveAttribute('data-audience', 'guardian');
  await onlyCopyClaimsOrNames(page, V1);
  await axe(page);
  // 另一户的孩子：页面与 BFF 都是 404
  const other = await page.goto(familyMap(S1));
  expect(other?.status()).toBe(404);
  expect((await bffV1(page, S1)).status).toBe(404);
  // 学生会话：兄妹（V2）404，本人的 N009 是中性
  await loginStudent(page, V1);
  expect((await page.goto(studentMap(V2)))?.status()).toBe(404);
  expect((await bffV1(page, V2)).status).toBe(404);
  // 学生会话：另一户的孩子（S1）页面与 BFF v1 也都是 404
  expect((await page.goto(studentMap(S1)))?.status()).toBe(404);
  expect((await bffV1(page, S1)).status).toBe(404);
  await page.goto(studentMap(V1));
  await expect(slot(page, 9)).toHaveAttribute('data-display', 'neutral');
  await expect(slot(page, 9)).not.toHaveAttribute('data-claim-id', /.*/);
  expect((await page.goto(studentMap('5e000000-0000-4000-8000-00000000ffff')))?.status()).toBe(404);
});

test('V2：一页 121 颗（数学 119 + 语文 2）超过 120 颗，按领域折叠；点开一个领域才看到其中的星', async ({ page }) => {
  await openAs(page, V2, studentMap(V2));
  await expect(page.locator('[data-starmap="v1"]')).toHaveAttribute('data-folded', 'true');
  await expect(page.getByTestId('folded-hint')).toBeVisible();
  await expect(page.locator('section.sky-v1 > h2')).toHaveText(['数学', '语文']);
  const folds = page.locator('details.constellation');
  await expect(folds).toHaveCount(4);  // 数学 3 个领域 + 语文 1 个
  for (const d of await folds.all()) await expect(d).not.toHaveAttribute('open', /.*/);
  await expect(page.locator('.star-slot').first()).toBeHidden();
  const first = page.locator('details.constellation[data-domain-id="MATH.SV1A"]');
  await expect(first.locator('summary [data-value="count"]')).toHaveText('40');
  await first.locator('summary').click();
  await expect(first.locator('.star-slot').first()).toBeVisible();
  // V2 没有判断：全部中性（语文的学习服务 409 → 中性）
  await expect(page.locator('main [data-claim-id]')).toHaveCount(0);
  await expect(page.locator('.star-slot[data-display="neutral"]')).toHaveCount(121);
  await onlyCopyClaimsOrNames(page, V2);
  await axe(page);
});

test('星图页的安全头与 nonce（开关打开的一套）；手机宽度没有横向滚动，星仍是 16px', async ({ page }) => {
  await loginStudent(page, V1);
  await crawl(page, targets('student', V1, false).filter((t) => t.path.endsWith('/star-map')));
  for (const width of [360, 512]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(studentMap(V1));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${width}px`).toBeLessThanOrEqual(0);
    expect((await page.locator('.star-slot svg.star').first().boundingBox())?.width).toBe(16);
  }
  await loginGuardian(page, 'gV');
  await crawl(page, targets('guardian', V1, false).filter((t) => t.path.endsWith('/star-map')));
});
