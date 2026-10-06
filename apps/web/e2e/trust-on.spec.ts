// 冲刺 5-H 信任界面：开关打开的一套（playwright.config.ts 的 trust-on 项目；前端 why / dispute / reconcile / weekly / data_rights，
// 第二个 BFF dispute / reconcile / weekly / data_rights；没有洞察服务，预测与对账卡读取 503、“为什么”的 BFF 读取路由还没有）。
// 周报：GT 本周的学生版与家长版来自 db/seeds/dev/weekly-synthetic.sql（两条合成判断），第二个 BFF 的对外白名单只列这个合成模板。
// 全程真实会话（短信捕获桩读码），合成家庭 HG（db/seeds/dev/shell-synthetic.sql）：家长 gH，孩子 GT 试用、GR 留存、GP 试点。
// 每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 信任界面区域里的每个文本节点，要么是 copy.ts 的文案（全是 {{TODO_COPY:…}} 占位），要么在 data-claim-id（判断句）或
//     data-value（数字、日期、编号）里——关于学生的句子都带 claim_id；
//   - 页面上不出现异议类型、偏差方向的编码原文；整页的文本节点与 data-* 属性过 dom-leak.ts 的内部编码核对
//     （判断状态码、原因码、错因编码、模板键、证据 ID、掌握数值等；data-* 只放行登记过的属性与取值）。
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY } from '../src/shell/copy.ts';
import { targets } from './crawl-pages.ts';
import { domLeaks, expectNoDomLeak, FORBIDDEN_CODES } from './dom-leak.ts';
import { crawl } from './headers-check.ts';
import { loginGuardian, loginStudent, TRUST_BASE } from './session.ts';

const GT = '5d000000-0000-4000-8000-0000000000c1';
const GR = '5d000000-0000-4000-8000-0000000000c2';
const GP = '5d000000-0000-4000-8000-0000000000c3';
const CLAIM = '00000000-0000-4000-8000-00000000f001';
/** 原来在这里单列的异议类型、偏差方向、下一步编码：现由 dom-leak.ts 的 FORBIDDEN_CODES 从文案映射的键推出，探针用例核对仍然覆盖 */
const CODES = ['one_off_slip', 'i_know', 'data_error', 'item_type_transfer', 'time_shortage', 'calculation_slip', 'diagnosis_optimistic',
               'verification_scheduled', 'observe_followups', 'open_fact_for_correction'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

/** 信任界面区域的每个文本节点：文案、判断句（data-claim-id）或数值（data-value），别的一律算违规 */
async function onlyCopyOrClaims(page: Page): Promise<void> {
  const roots = await page.locator('[data-trust-root], [data-trust-nav]').count();
  expect(roots).toBeGreaterThan(0);
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    for (const root of Array.from(document.querySelectorAll('[data-trust-root], [data-trust-nav]'))) {
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        const t = (n.textContent ?? '').trim();
        const el = n.parentElement;
        if (t === '' || !el || el.closest('[data-claim-id], [data-value], script, style')) continue;
        if (!known.has(t)) out.push(t);
      }
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  await expectNoDomLeak(page);  // 整页文本节点与 data-* 属性：没有内部编码（含上面 CODES）
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(TRUST_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

/** 上海时区的 N 天后（考试日期须在今天起一年内） */
const dayAfter = (n: number): string => new Date(Date.now() + 8 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);

test('学生：入口行、异议状态页、“为什么”（读取通道未接：暂时无法获取，不显示异议按钮）', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, GT);
  const nav = page.locator('[data-trust-nav="student"]');
  await expect(nav.locator('[data-trust-link]')).toHaveCount(3);  // 考试、我提出的异议、成长手记
  await expect(nav.locator('[data-trust-link="data_rights"]')).toHaveCount(0);  // 数据权利只给家长
  await onlyCopyOrClaims(page);
  await axe(page);

  await nav.locator('[data-trust-link="dispute"]').click();
  await expect(page).toHaveURL(`/s/${GT}/disputes`);
  await expect(page.getByTestId('disputes-empty')).toBeVisible();
  await onlyCopyOrClaims(page);
  await axe(page);

  await page.goto(`/s/${GT}/why/${CLAIM}`);
  await expect(page.getByTestId('why-page')).toBeVisible();
  await expect(page.locator('[data-unavailable="why"]')).toBeVisible();
  await expect(page.getByTestId('dispute-panel')).toHaveCount(0);
  await expect(page.locator('[data-claim-id]')).toHaveCount(0);
  await onlyCopyOrClaims(page);
  await axe(page);
  expect((await page.goto(`/s/${GT}/why/not-a-uuid`))?.status()).toBe(404);
  // 学生侧信任页只给本人：打开别的孩子（同一户的 GP）一律 404
  for (const p of [`/s/${GP}/why/${CLAIM}`, `/s/${GP}/disputes`, `/s/${GP}/exams`]) expect((await page.goto(p))?.status(), p).toBe(404);
  // 下载路由只给已绑定家长：学生会话（带着自己的 CSRF）也是 404
  const csrf = (await page.context().cookies()).find((c) => c.name === 'sb_csrf')?.value ?? '';
  const dl = await page.request.post(`/family/${GT}/data/download`, { form: { csrf, job: CLAIM, download_ref: `dl_${'0'.repeat(64)}` } });
  expect(dl.status()).toBe(404);
  expect(dl.headers()['cache-control']).toBe('no-store');
  // 数据权利页只给家长：学生会话转到自己的“今日”
  await page.goto(`/family/${GT}/data`);
  await expect(page).toHaveURL(`/s/${GT}/today`);
  expect(off).toEqual([]);
});

test('学生：录入考试安排与成绩；预测与对账卡读取通道未接时显示“暂时无法获取”；不合格的输入有提示', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, GT);
  await page.locator('[data-trust-link="reconcile"]').click();
  await expect(page).toHaveURL(`/s/${GT}/exams`);
  const form = page.getByTestId('exam-plan-form');
  await expect(form).toHaveAttribute('data-hydrated', 'true');
  await expect(page.locator('#exam-full')).toHaveValue('100');
  await onlyCopyOrClaims(page);
  await axe(page);
  // 节点编号不合格式：前端先挡（不到 BFF）
  await page.locator('#exam-date').fill(dayAfter(30));
  await page.locator('#exam-scope').fill('math.lower-case');
  await form.getByRole('button').click();
  await expect(form.getByRole('status')).toHaveAttribute('data-code', 'INVALID');
  // 合格：保存后进这场考试的页面
  await page.locator('#exam-subject').selectOption('aya');
  await page.locator('#exam-type').selectOption('midterm');
  await page.locator('#exam-date').fill(dayAfter(30));
  await page.locator('#exam-full').fill('100');
  await page.locator('#exam-scope').fill('MATH.FUNC.QUAD MATH.GEO.TRI');
  await form.getByRole('button').click();
  await expect(page).toHaveURL(new RegExp(`/s/${GT}/exams/[0-9a-f-]{36}$`));
  await expect(page.locator('[data-unavailable="prediction"]')).toBeVisible();
  await expect(page.locator('[data-unavailable="reconciliation"]')).toBeVisible();
  await expect(page.getByTestId('reconcile-card')).toHaveCount(0);
  await onlyCopyOrClaims(page);
  await axe(page);
  const score = page.getByTestId('exam-score-form');
  await expect(score).toHaveAttribute('data-hydrated', 'true');
  await page.locator('#exam-raw').fill('101');  // 超过满分：库函数拒绝
  await score.getByRole('button').click();
  await expect(score.getByRole('status')).toHaveAttribute('data-code', 'INVALID');
  await page.locator('#exam-raw').fill('88');
  await score.getByRole('button').click();
  await expect(score.getByRole('status')).toHaveAttribute('data-code', 'saved');
  expect(off).toEqual([]);
});

test('家长：孩子页入口行；“为什么”、异议状态页、考试页；另一户家长 404', async ({ page }) => {
  const off = watchOrigin(page);
  await loginGuardian(page, 'gH');
  await page.goto(`/family/${GP}`);
  const nav = page.locator('[data-trust-nav="guardian"]');
  await expect(nav.locator('[data-trust-link]')).toHaveCount(4);  // 考试、我提出的异议、本周周报、数据权利
  await onlyCopyOrClaims(page);
  await axe(page);
  await nav.locator('[data-trust-link="dispute"]').click();
  await expect(page.getByTestId('disputes-empty')).toBeVisible();
  await axe(page);
  await page.goto(`/family/${GP}/why/${CLAIM}`);
  await expect(page.locator('[data-unavailable="why"]')).toBeVisible();
  await expect(page.getByTestId('dispute-panel')).toHaveCount(0);
  await onlyCopyOrClaims(page);
  await page.goto(`/family/${GP}/exams`);
  const form = page.getByTestId('exam-plan-form');
  await expect(form).toHaveAttribute('data-hydrated', 'true');
  await page.locator('#exam-date').fill(dayAfter(40));
  await page.locator('#exam-scope').fill('MATH.ALG.EQ');
  await form.getByRole('button').click();
  await expect(page).toHaveURL(new RegExp(`/family/${GP}/exams/[0-9a-f-]{36}$`));
  await expect(page.locator('[data-unavailable="prediction"]')).toBeVisible();
  await onlyCopyOrClaims(page);
  await axe(page);
  expect(off).toEqual([]);
  // 另一户（gS）打开 HG 的孩子：与不存在同一个 404
  await loginGuardian(page, 'gS');
  for (const p of [`/family/${GR}/data`, `/family/${GR}/disputes`, `/family/${GR}/exams`]) expect((await page.goto(p))?.status(), p).toBe(404);
});

test('家长数据权利：导出（一次性下载、no-store、附件）→ 删除申请 → 二次确认 → 冷静期倒计时 → 冷静期内不能导出 → 撤回 @data-rights', async ({ page }) => {
  const off = watchOrigin(page);
  await loginGuardian(page, 'gH');
  await page.goto(`/family/${GR}`);
  await page.locator('[data-trust-link="data_rights"]').click();
  await expect(page).toHaveURL(`/family/${GR}/data`);
  // 同一个库上可能另有浏览器项目先跑过（STARBUDDY_E2E_BROWSERS）：只要求开始时没有冷静期中的申请
  await expect(page.locator('li[data-state="cooling_off"]')).toHaveCount(0);
  await onlyCopyOrClaims(page);
  await axe(page);

  // 导出：生成 → 下载一次（附件、no-store、内容不含凭据）→ 同一凭据再下载：转回本页并提示重新生成
  const exp = page.getByTestId('export-form');
  await expect(exp).toHaveAttribute('data-hydrated', 'true');
  await exp.getByRole('button').click();
  await expect(exp.getByRole('status')).toHaveAttribute('data-code', 'ready');
  const [download, response] = await Promise.all([
    page.waitForEvent('download'),
    page.waitForResponse((r) => r.url().endsWith(`/family/${GR}/data/download`)),
    page.getByTestId('download').click(),
  ]);
  expect(response.status()).toBe(200);
  expect(response.headers()['cache-control']).toBe('no-store');
  expect(response.headers()['x-content-type-options']).toBe('nosniff');  // 附件下载也不让浏览器嗅探类型（H6 安全审查 L3）
  expect(response.headers()['content-disposition']).toMatch(/^attachment; filename="starbuddy-export-[0-9a-f-]{36}\.json"$/);
  const body = await (await download.createReadStream()).toArray();
  const pkg = JSON.parse(Buffer.concat(body).toString('utf8')) as Record<string, unknown>;
  expect(pkg['format']).toBe('starbuddy-export@1');
  expect(pkg['student_id']).toBe(GR);
  expect(JSON.stringify(pkg)).not.toMatch(/dl_[0-9a-f]{64}/);
  expect(await page.content()).not.toMatch(/dl_[0-9a-f]{64}/);  // 凭据不在页面 HTML 里（只在客户端内存）
  const [again] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith(`/family/${GR}/data/download`)),
    page.getByTestId('download').click(),
  ]);
  expect(again.status()).toBe(404);
  await expect(page.getByTestId('export-gone')).toBeVisible();
  await axe(page);

  // 删除：申请 → 二次确认（勾选“已了解”）→ 冷静期
  const req = page.getByTestId('erasure-request-form');
  await expect(req).toHaveAttribute('data-hydrated', 'true');
  await req.getByRole('button').click();
  await expect(req.getByRole('status')).toHaveAttribute('data-code', 'requested');
  const confirm = page.getByTestId('erasure-confirm-form');
  await expect(confirm).toBeVisible();
  await axe(page);
  await expect(confirm.getByRole('button')).toBeDisabled();  // 二次确认：不勾选“已了解”不能确认
  await expect(page.locator('input[name="confirm_token"], input[name="download_ref"]')).toHaveCount(0);  // 凭据只在客户端内存
  await confirm.getByRole('checkbox').check();
  await confirm.getByRole('button').click();
  await expect(page.locator('[data-code="confirmed"]')).toBeVisible();
  await page.reload();
  const item = page.locator('[data-testid="erasure-requests"] li[data-state="cooling_off"]');
  await expect(item).toHaveCount(1);
  await expect(item.locator('[data-value="days"]')).toHaveText(/^[67]$/);
  await onlyCopyOrClaims(page);
  await axe(page);

  // 冷静期中不能再导出（BFF 409）
  const exp2 = page.getByTestId('export-form');
  await expect(exp2).toHaveAttribute('data-hydrated', 'true');
  await exp2.getByRole('button').click();
  await expect(exp2.getByRole('status')).toHaveAttribute('data-code', 'STATE_CONFLICT');

  // 撤回
  const cancel = item.locator('form');
  await expect(cancel).toHaveAttribute('data-hydrated', 'true');
  await cancel.getByRole('button').click();
  // 撤回后本页重新取数：这一条变成“已撤回”，撤回按钮随之消失
  await expect(page.locator('[data-testid="erasure-requests"] li[data-state="cooling_off"]')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('[data-testid="erasure-requests"] li[data-state="cancelled"]')).not.toHaveCount(0);
  await expect(page.getByTestId('erasure-receipt')).toHaveCount(0);  // 回执只在执行后
  await onlyCopyOrClaims(page);
  expect(off).toEqual([]);
});

test('没有 JS 时：导出与删除按钮不可用、不整页提交，页面 HTML 里没有一次性凭据；下载路由只认 POST @data-rights', async ({ page, browser }) => {
  await loginGuardian(page, 'gH');
  for (const m of ['GET', 'PUT', 'DELETE', 'PATCH'] as const) {
    const r = await page.request.fetch(`/family/${GR}/data/download`, { method: m });
    expect(r.status(), m).toBe(404);
    expect(r.headers()['cache-control'], m).toBe('no-store');
  }
  const ctx = await browser.newContext({ javaScriptEnabled: false, baseURL: TRUST_BASE });
  try {
    await ctx.addCookies(await page.context().cookies());
    const p = await ctx.newPage();
    const r = await p.goto(`/family/${GR}/data`);
    expect(r?.status()).toBe(200);
    await expect(p.getByTestId('export-form').getByRole('button')).toBeDisabled();
    await expect(p.getByTestId('erasure-request-form').getByRole('button')).toBeDisabled();
    await expect(p.locator('[data-testid="export-panel"] form, [data-testid="erasure-panel"] form')).toHaveCount(0);
    const html = await p.content();
    expect(html).not.toMatch(/dl_[0-9a-f]{64}/);
    expect(html).not.toMatch(/confirm_token/);
  } finally {
    await ctx.close();
  }
});

const WEEKLY_CLAIMS = ['5d000000-0000-4000-8000-0000000000f1', '5d000000-0000-4000-8000-0000000000f2'];

test('周报：学生“成长手记”与家长周报——每句带 data-claim-id、客观栏标来源；学生端不附置信度说明；另一户与别的孩子 404；没有周报的孩子显示“还没有”', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, GT);
  await page.locator('[data-trust-nav="student"] [data-trust-link="weekly"]').click();
  await expect(page).toHaveURL(`/s/${GT}/weekly`);
  const root = page.locator('[data-trust-root="weekly"]');
  await expect(root).toHaveAttribute('data-audience', 'student');
  await expect(root.locator('[data-weekly-section]')).toHaveCount(2);
  await expect(root.locator('[data-weekly-section="lit"] [data-claim-id]')).toHaveAttribute('data-claim-id', WEEKLY_CLAIMS[0]!);
  await expect(root.locator('[data-weekly-section="working_on"] [data-claim-id]')).toHaveAttribute('data-claim-id', WEEKLY_CLAIMS[1]!);
  const ids = await root.locator('[data-claim-id]').evaluateAll((els) => els.map((e) => e.getAttribute('data-claim-id')));
  expect(ids).toEqual(WEEKLY_CLAIMS);
  await expect(root.locator('[data-weekly-objective][data-source-type="objective"]')).toBeVisible();
  await expect(root.locator('[data-trust-link="why"]')).toHaveCount(2);  // 每句旁一个“为什么”入口（开关 why）
  expect(await root.innerText()).not.toMatch(/置信度说明/);  // 学生端不附置信度说明
  await onlyCopyOrClaims(page);
  await axe(page);
  // 学生侧只给本人：同一户的别的孩子也是 404
  expect((await page.goto(`/s/${GP}/weekly`))?.status()).toBe(404);
  expect(off).toEqual([]);

  await loginGuardian(page, 'gH');
  await page.goto(`/family/${GT}`);
  await page.locator('[data-trust-nav="guardian"] [data-trust-link="weekly"]').click();
  await expect(page).toHaveURL(`/family/${GT}/weekly`);
  const g = page.locator('[data-trust-root="weekly"]');
  await expect(g).toHaveAttribute('data-audience', 'guardian');
  await expect(g.locator('[data-weekly-section]')).toHaveCount(2);
  expect(await g.locator('[data-weekly-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-weekly-section')))).toEqual(['progress', 'stuck']);
  expect(await g.locator('[data-claim-id]').evaluateAll((els) => els.map((e) => e.getAttribute('data-claim-id')))).toEqual(WEEKLY_CLAIMS);
  expect(await g.innerText()).toMatch(/置信度说明/);  // 家长端附置信度说明（占位）
  await onlyCopyOrClaims(page);
  await axe(page);
  // 没有周报的孩子：显示“还没有”，不出现任何判断句
  await page.goto(`/family/${GP}/weekly`);
  await expect(page.getByTestId('weekly-empty')).toBeVisible();
  await expect(page.locator('[data-claim-id]')).toHaveCount(0);
  await onlyCopyOrClaims(page);
  await axe(page);
  expect(off).toEqual([]);
  // 另一户（gS）打开 HG 的孩子：与不存在同一个 404
  await loginGuardian(page, 'gS');
  expect((await page.goto(`/family/${GT}/weekly`))?.status()).toBe(404);
});

test('DOM 编码核对的探针：往页面里注入合成的泄露（编码属性、模板键、掌握数值、证据 ID、节点编号），核对逐个被抓到；data-value 里的节点编号放行', async ({ page }) => {
  for (const c of CODES) expect(FORBIDDEN_CODES).toContain(c);
  await loginStudent(page, GT);
  await page.goto(`/s/${GT}/disputes`);
  await expect(page.getByTestId('disputes-empty')).toBeVisible();
  expect(await domLeaks(page)).toEqual([]);
  await page.evaluate(() => {
    const root = document.querySelector('[data-trust-root]')!;
    const probe = document.createElement('div');
    probe.innerHTML = [
      '<span data-dispute-kind="x">（合成）甲</span>',
      '<span data-dispute-status="refuted">（合成）乙</span>',
      '<span data-testid="dispute-one_off_slip">（合成）丙</span>',
      '<span data-code="verification_scheduled">（合成）丁</span>',
      '<span aria-label="calculation_slip">（合成）戊</span>',
      '<p>（合成）syn.weekly.node_mastery@1</p>',
      '<p>（合成）p_mastery 0.62</p>',
      '<p>（合成）5d000000-0000-4000-8000-00000000e999</p>',
      '<p>（合成）MATH.FUNC.QUAD</p>',
      '<p><span data-value="node">MATH.ALG.EQ</span></p>',
    ].join('');
    root.append(probe);
  });
  const offs = await domLeaks(page, { forbidden: ['algo:syn-probe'] });
  for (const needle of ['未登记的属性：data-dispute-kind', 'data-dispute-status="refuted"', '禁止编码 one_off_slip', 'data-testid="dispute-one_off_slip"',
                        '禁止编码 verification_scheduled', 'aria-label 含禁止编码 calculation_slip', '模板键', '@版本', '禁止编码 p_mastery', '掌握数值',
                        'UUID', '知识节点编号']) {
    expect(offs.some((o) => o.includes(needle)), needle).toBe(true);
  }
  expect(offs.filter((o) => o.includes('MATH.ALG.EQ'))).toEqual([]);  // data-value 里的编号是有意显示的值
});

test('安全头（第二轮 7.6，H6）：开关打开时信任界面的全部页面（学生与家长）都带本次 nonce 的 CSP、no-store、nosniff', async ({ page }) => {
  await loginStudent(page, GT);
  await crawl(page, targets('student', GT, true, true));
  await loginGuardian(page, 'gH');
  await crawl(page, targets('guardian', GT, true, true));
});
