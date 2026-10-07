// 审核台端到端（spec 冲刺 1 验收“端到端测试”）：合成教研经登录页登录（口令 + 6 位码，PR 4）→ 打开草稿 → 改一个节点的“不包含” → 看到差异与校验 →
// 签字 → 发布 → 旧版本变为已撤下，版本记录里有这一行。种子见 db/seeds/dev/review-synthetic.sql（语文合成地图）。
// 另测：登录失败、退出、看到的内容过期时签字被拒、学生身份进不了审核台、无障碍、不缓存。
// 统一复核队列（specs/review-queue-sprint2）：看到合成任务、领取、他人领不了、放回、结案。
// 用例之间有先后（发布后的状态），按顺序执行（test.describe.serial）。
import { execFileSync } from 'node:child_process';
import { createHmac } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expectNoDomLeak } from './dom-leak.ts';
import { openAs } from './session.ts';
import { expect, test, type Page } from '@playwright/test';

// 等页面上所有审核台表单水合完成再操作：导航或刷新后立即点击可能在 React 接管前发生，提交丢失（负载高时偶发超时的根因）
async function hydrated(page: Page): Promise<void> {
  await expect(page.locator('form.review-form:not([data-hydrated])')).toHaveCount(0);
}

// 合成凭据：与 db/seeds/dev/review-synthetic.sql 一致（只用于一次性测试库）
interface Staff { login: string; password: string; secret: Buffer }
const T1: Staff = { login: 'teacher.t1', password: 'synthetic-teacher-t1', secret: Buffer.from('11'.repeat(20), 'hex') };
const T2: Staff = { login: 'teacher.t2', password: 'synthetic-teacher-t2', secret: Buffer.from('22'.repeat(20), 'hex') };
const S1 = '5a000000-0000-4000-8000-0000000000b1';
const V101 = '5f0e0000-0000-4000-8000-0000000001a2';

/** RFC 6238 的 6 位码（测试方独立实现） */
function totp(secret: Buffer, step: number): string {
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const h = createHmac('sha1', secret).update(msg).digest();
  const o = h[19]! & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, '0');
}

// 同一个码不能用两次：每个账号记下用过的时间步，下一次用更晚的一步（库里容差 1 步，超了就等到下一步）
const usedStep = new Map<string, number>();
async function nextCode(page: Page, who: Staff): Promise<string> {
  for (;;) {
    const now = Math.floor(Date.now() / 30_000);
    const step = Math.max(now, (usedStep.get(who.login) ?? -1) + 1);
    if (step <= now + 1) {
      usedStep.set(who.login, step);
      return totp(who.secret, step);
    }
    await page.waitForTimeout(30_000 - (Date.now() % 30_000) + 200);
  }
}

async function asStaff(page: Page, who: Staff) {
  await page.goto('/review/login');
  await page.getByLabel('登录名').fill(who.login);
  await page.getByLabel('口令').fill(who.password);
  await page.getByLabel('6 位码').fill(await nextCode(page, who));
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/review$/);
  await expect(page.getByTestId('whoami')).toHaveText(who.login);
}

/** 以 BFF 的数据库身份读已发布的地图版本（BFF 只看得到已发布的），验收“BFF 读到新版本”（契约审查 PR 3 B3） */
function bffPublishedVersions(subject: string): string[] {
  const out = execFileSync('psql', ['-X', '-At', '-U', 'bff_app', '-c',
    `SELECT version FROM kge.map_version WHERE subject = '${subject}' ORDER BY version`], { encoding: 'utf8' });
  return out.split('\n').filter((x) => x.length > 0);
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(r.violations.map((v) => v.id)).toEqual([]);
}

test.describe.configure({ mode: 'serial' });

test('学生身份进不了审核台（去审核台登录页）；开发身份桩已删除', async ({ page }) => {
  await openAs(page, '5a000000-0000-4000-8000-0000000000c1', '/student');  // S1 的学生会话（4-H H2：真实会话）
  await page.goto('/review?subject=wenli');
  await expect(page).toHaveURL(/\/review\/login$/);
  expect((await page.goto(`/dev/session?staff=${S1}`))?.status()).toBe(404);
  expect((await page.goto('/dev/login'))?.status()).toBe(404);
  await expect(page.getByText('合成教研')).toHaveCount(0);
});

test('登录失败统一提示；退出后回登录页，旧会话失效', async ({ page }) => {
  await page.goto('/review/login');
  await axe(page);
  await page.getByLabel('登录名').fill(T2.login);
  await page.getByLabel('口令').fill(T2.password);
  await page.getByLabel('6 位码').fill(totp(T2.secret, Math.floor(Date.now() / 30_000) + 10));  // 超出容差
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page.getByTestId('login').getByRole('status')).toHaveAttribute('data-code', 'LOGIN_FAILED');
  await expect(page).toHaveURL(/\/review\/login$/);
  // 错口令：同一个提示（不区分原因）
  await page.getByLabel('口令').fill('wrong-password-000');
  await page.getByLabel('6 位码').fill(await nextCode(page, T2));
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page.getByTestId('login').getByRole('status')).toHaveAttribute('data-code', 'LOGIN_FAILED');
  await asStaff(page, T2);
  const cookie = (await page.context().cookies()).find((c) => c.name === 'sb_staff');
  expect(cookie?.httpOnly && cookie.sameSite === 'Strict').toBeTruthy();
  await page.getByRole('button', { name: '退出' }).click();
  await expect(page).toHaveURL(/\/review\/login$/);
  // 把旧令牌放回去也没用：库里的会话已撤销
  await page.context().addCookies([{ ...cookie!, expires: Math.floor(Date.now() / 1000) + 3600 }]);
  await page.goto('/review?subject=wenli');
  await expect(page).toHaveURL(/\/review\/login$/);
});

test('教研改“不包含” → 差异与校验 → 签字 → 发布，旧版本撤下，版本记录有这一行', async ({ page, browser }) => {
  // 多步流程（两名教研各自会话、十余次导航、三次 axe 扫描），默认 30 秒只够空载本机；
  // 严格检查满载时两次超时在最后一次 axe 扫描上（断言本身都已通过），与复核队列用例同样放宽总时限，断言不变
  test.setTimeout(90_000);
  await asStaff(page, T1);
  const home = await page.goto('/review?subject=wenli');
  expect(home?.headers()['cache-control'] ?? '').toContain('no-store');
  const rows = page.getByTestId('versions').locator('tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(page.locator('tr[data-version="1.0.0"]')).toHaveAttribute('data-status', 'published');
  await expect(page.locator('tr[data-version="1.0.1"]')).toHaveAttribute('data-status', 'draft');
  await axe(page);

  await page.getByRole('link', { name: '审核 1.0.1' }).click();
  await expect(page).toHaveURL(new RegExp(`/review/map-versions/${V101}$`));
  await expect(page.getByTestId('no-changes')).toBeVisible();
  const digest0 = await page.getByTestId('digest').textContent();

  // 改节点 A 的“不包含”
  const editor = page.locator('details.node-editor[data-node="CHN.RC.T.A"]');
  await hydrated(page);
  await editor.locator('summary').click();
  await editor.getByLabel('不包含（一行一条）').fill('整篇主旨\n合成：段落之间的过渡');
  await editor.getByRole('button', { name: '保存修改' }).click();
  await expect(editor.getByRole('status')).toHaveText('已保存');

  // 差异：一条中风险（阶段 0 按高审），不是结构性，一人签字即可
  const change = page.getByTestId('changes').locator('tbody tr');
  await expect(change).toHaveCount(1);
  await expect(change.first()).toHaveAttribute('data-key', 'CHN.RC.T.A');
  await expect(change.first()).toHaveAttribute('data-field', 'excludes');
  await expect(change.first()).toHaveAttribute('data-level', 'medium');
  await expect(change.first()).toContainText('["整篇主旨","合成：段落之间的过渡"]');
  await expect(change.first()).toContainText('按高审');
  await expect(change.first()).toContainText('边界补充');
  await expect(page.getByTestId('summary')).toHaveAttribute('data-structural', 'false');
  await expect(page.getByTestId('summary')).toHaveAttribute('data-signers', '1');
  await expect(page.getByTestId('summary')).toContainText('阻断 0 条');
  await expect(page.getByTestId('summary')).toHaveAttribute('data-approvals', '0');
  await expect(page.getByTestId('publish')).toContainText('发布后，当前已发布的 1.0.0 将自动撤下');
  expect(bffPublishedVersions('wenli')).toEqual(['1.0.0']);
  expect(await page.getByTestId('digest').textContent()).not.toBe(digest0);
  await axe(page);

  // 看到的内容过期：T2 在另一个会话里又改了一次，T1 用旧页面签字被拒
  const other = await browser.newContext();
  const p2 = await other.newPage();
  await asStaff(p2, T2);
  await p2.goto(`/review/map-versions/${V101}`);
  const e2 = p2.locator('details.node-editor[data-node="CHN.RC.T.B"]');
  await hydrated(p2);
  await e2.locator('summary').click();
  await e2.getByLabel('典型题例（一行一条）').fill('合成例题 2\n合成例题 3');
  await e2.getByRole('button', { name: '保存修改' }).click();
  await expect(e2.getByRole('status')).toHaveText('已保存');
  await other.close();

  const sign = page.getByTestId('sign');
  await sign.getByRole('button', { name: '批准' }).click();
  await expect(sign.getByRole('status')).toHaveAttribute('data-code', 'DIGEST_CHANGED');

  // 刷新后看到两条差异，再签字、发布
  await page.reload();
  await expect(page.getByTestId('changes').locator('tbody tr')).toHaveCount(2);
  // 等表单水合完成再点：刷新后立即点击可能在 React 接管前发生，提交丢失（负载高时偶发超时的根因）
  await hydrated(page);
  await page.getByTestId('sign').getByRole('button', { name: '批准' }).click();
  await expect(page.getByTestId('sign').getByRole('status')).toHaveText('已签字');
  await expect(page.getByTestId('signoffs').locator('tbody tr[data-current="true"]')).toHaveCount(1);
  await expect(page.getByTestId('summary')).toHaveAttribute('data-approvals', '1');
  await page.getByTestId('publish').getByRole('button', { name: '发布这个版本' }).click();
  // 发布后页面按新状态重新渲染：发布表单消失，显示已发布说明
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('地图版本 1.0.1（已发布）');
  await expect(page.getByTestId('status-note')).toContainText('本版本已发布');

  await page.goto('/review?subject=wenli');
  await expect(page.locator('tr[data-version="1.0.0"]')).toHaveAttribute('data-status', 'retired');
  await expect(page.locator('tr[data-version="1.0.1"]')).toHaveAttribute('data-status', 'published');
  // BFF 读到新版本，旧版本对它不可见（已撤下）
  expect(bffPublishedVersions('wenli')).toEqual(['1.0.1']);

  await page.getByRole('link', { name: '版本记录' }).click();
  const rec = page.getByTestId('records').locator('tbody tr');
  await expect(rec.filter({ has: page.locator('td', { hasText: '1.0.1' }) }).and(page.locator('[data-action="publish"]'))).toHaveCount(1);
  await expect(page.locator('tr[data-action="retire"][data-version="1.0.0"]')).toHaveCount(1);
  await axe(page);
});

test('已发布版本不能再签字或发布，也没有编辑表单与风险摘要；撤下须勾选确认', async ({ page }) => {
  await asStaff(page, T2);
  await page.goto(`/review/map-versions/${V101}`);
  await expect(page.getByTestId('sign')).toHaveCount(0);
  await expect(page.getByTestId('publish')).toHaveCount(0);
  await expect(page.getByTestId('summary')).toHaveCount(0);
  await expect(page.locator('details.node-editor')).toHaveCount(0);
  const retire = page.getByTestId('retire');
  await expect(retire.getByRole('checkbox')).toHaveAttribute('required', '');
  await expect(retire).toContainText('本学科将没有已发布的地图版本');
});

// ---------- 统一复核队列（specs/review-queue-sprint2 验收“前端端到端：staff 登录后看到队列、领取、结案”） ----------
// 以超级用户（e2e 的迁移执行者身份）播种一条合成的知行判分（两个主观维，需复核）与它的复核任务；学生、证据来自星图合成种子。
const QG = '5c0e0000-0000-4000-8000-0000000000a1';   // grade_result
const QA = '5c0e0000-0000-4000-8000-0000000000a2';   // attempt
const QT = '5c0e0000-0000-4000-8000-0000000000a3';   // review_task
const QS = '5a000000-0000-4000-8000-0000000000c1';   // 星图合成种子的学生 S1（知行在读）
const QE = '5a000000-0000-4000-8000-0000000000f2';   // S1 的知行合成证据
function seedReviewTask() {
  const sql = `BEGIN;
INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, request_idempotency_key)
VALUES ('${QA}', '2026-10-15 10:00:00+00', '${QS}', 'zhixing', 'SYN-ZX-ITEM-01', '${QE}', 'synthetic-review-queue-e2e-0001');
INSERT INTO platform.grade_result (id, student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total,
  max_total, calibrated, confidence, source_version, model_version, human_review_required, review_reasons, semantics, evidence_ids,
  source_request_id)
VALUES ('${QG}', '${QS}', 'zhixing', '${QA}', '2026-10-15 10:00:00+00', 'partial', 'zhixing-synthetic@0.1.0', 2, 4, false, 0.4,
        'rubric:zhixing-synthetic@0.1.0', 'model:fake-a/fake-reasoner@2026-09-01', true, '{subjective_dimension}', 'formal',
        '{${QE}}', gen_random_uuid());
INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective, rule_ref, confidence, evidence_ids)
VALUES ('${QG}', 'p1', 2, 2, 'rubric_model', true, NULL, 0.4, '{${QE}}'),
       ('${QG}', 'p2', 0, 2, 'rubric_model', true, NULL, 0.4, '{${QE}}');
INSERT INTO platform.review_task (id, subject, student_id, source_kind, source_id, reasons)
VALUES ('${QT}', 'zhixing', '${QS}', 'grade_result', '${QG}', '{subjective_dimension}');
COMMIT;`;
  execFileSync('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', '-c', sql], { encoding: 'utf8' });
}

/** 直接调审核台后端（带某个 staff 的会话令牌），断言后端的 409（页面上已不给他人领取的按钮） */
async function backendPost(page: Page, path: string): Promise<{ status: number; code: string }> {
  const session = (await page.context().cookies()).find((c) => c.name === 'sb_staff')?.value ?? '';
  const r = await fetch(`${process.env['STARBUDDY_REVIEW_URL']}${path}`, { method: 'POST', headers: { 'x-review-session': session } });
  const body = (await r.json().catch(() => ({}))) as { code?: string };
  return { status: r.status, code: body.code ?? '' };
}

test('复核队列：看到合成任务 → 领取 → 他人看到“他人已领取”且领不了 → 放回、再领取 → 结案（白名单码）→ 已结案', async ({ page, browser }) => {
  test.setTimeout(150_000);
  seedReviewTask();
  await asStaff(page, T1);
  await page.getByRole('navigation', { name: '审核台' }).getByRole('link', { name: '复核队列' }).click();
  await expect(page).toHaveURL(/\/review\/queue$/);
  expect((await page.goto('/review/queue?subject=zhixing'))?.headers()['cache-control'] ?? '').toContain('no-store');
  const row = page.locator(`tr[data-task="${QT}"]`);
  await expect(row).toHaveAttribute('data-status', 'open');
  await expect(row).toContainText('道法');
  await expect(row).toContainText('判分');
  await expect(row).toContainText('主观维度');
  await expect(row).toContainText('待处理');
  await expect(row).toContainText('未领取');
  // 只有不透明学生指针：不出现学生 id 与作答原文
  const ref = (await row.locator('td code').textContent()) ?? '';
  expect(ref).toMatch(/^s-[0-9a-f]{12}$/);
  await expect(page.locator('body')).not.toContainText(QS);
  await axe(page);
  // 原因筛选：选别的原因就看不到这条
  await page.getByLabel('原因').selectOption('student_appeal');
  await page.getByRole('button', { name: '筛选' }).click();
  await expect(page).toHaveURL(/reason=student_appeal/);
  await expect(page.locator(`tr[data-task="${QT}"]`)).toHaveCount(0);
  await page.goto('/review/queue?subject=zhixing&status=open&reason=subjective_dimension');

  await page.getByRole('link', { name: `查看任务 ${ref}` }).click();
  await expect(page).toHaveURL(new RegExp(`/review/queue/${QT}$`));
  await expect(page.getByTestId('student-ref')).toHaveText(ref);
  await expect(page.getByTestId('dimensions').locator('tbody tr')).toHaveCount(2);
  await expect(page.locator('tr[data-dimension="p2"]')).toContainText('0 / 2');
  await expect(page.locator('tr[data-dimension="p2"]')).toContainText('Rubric 模型');
  await expect(page.getByTestId('grade-source')).toContainText('总分 2 / 4');
  await expect(page.locator('body')).not.toContainText('synthetic-star-map-answer');
  await axe(page);

  await hydrated(page);
  await page.getByTestId('take').getByRole('button', { name: '领取' }).click();
  await expect(page.getByTestId('assignment')).toHaveText('我已领取');
  await expect(page.getByTestId('task-status')).toHaveText('处理中');
  // 结案码只列适用于判分的（没有“已转洞察服务”）
  const options = page.getByTestId('resolve').getByLabel('结案码').locator('option:not([disabled])');
  await expect(options).toHaveText(['题目或 Rubric 有问题', '无需处理', '需要改判', '维持原结果']);
  await axe(page);

  // 另一位 staff：列表显示“他人已领取”，详情没有领取按钮；直接调后端领取回 409
  const other = await browser.newContext();
  const p2 = await other.newPage();
  await asStaff(p2, T2);
  await p2.goto('/review/queue?subject=zhixing&status=in_progress');
  await expect(p2.locator(`tr[data-task="${QT}"]`)).toContainText('他人已领取');
  await p2.goto(`/review/queue/${QT}`);
  await expect(p2.getByTestId('assigned-other')).toBeVisible();
  await expect(p2.getByTestId('take')).toHaveCount(0);
  await expect(p2.getByTestId('resolve')).toHaveCount(0);
  expect(await backendPost(p2, `/review/v0/queue/${QT}/take`)).toEqual({ status: 409, code: 'RULE_VIOLATION' });
  await axe(p2);

  // 放回后回到未领取，再领取
  await page.getByTestId('release').getByRole('button', { name: '放回' }).click();
  await expect(page.getByTestId('assignment')).toHaveText('未领取');
  await page.getByTestId('take').getByRole('button', { name: '领取' }).click();
  await expect(page.getByTestId('assignment')).toHaveText('我已领取');

  await page.getByTestId('resolve').getByLabel('结案码').selectOption('upheld');
  await page.getByTestId('resolve').getByRole('button', { name: '结案' }).click();
  await expect(page.getByTestId('task-status')).toHaveText('已结案');
  await expect(page.getByTestId('resolved-note')).toContainText('维持原结果');
  await expect(page.getByTestId('resolved-note')).toContainText('由我结案');
  await expect(page.getByTestId('take')).toHaveCount(0);
  await axe(page);

  // 另一位 staff 看到已结案、不是他结的
  await p2.reload();
  await expect(p2.getByTestId('resolved-note')).toContainText('由他人结案');
  await other.close();

  await page.goto('/review/queue?subject=zhixing&status=resolved');
  await expect(page.locator(`tr[data-task="${QT}"]`)).toHaveAttribute('data-status', 'resolved');
  await page.goto('/review/queue?subject=zhixing');
  await expect(page.locator(`tr[data-task="${QT}"]`)).toHaveCount(0);
});

// ---------- 编译页面（specs/release-gate-sprint4 冲刺 3 段：审核台加“页面”入口，列待审合并请求与对账状态，链到托管平台网页） ----------
// 合成数据：页面审核记录来自 db/seeds/dev/review-pages-synthetic.sql；审核台后端配的合并记录与对应表是
// services/learning/tests/fixtures/pages-reconcile/{export,staff-map}.json（e2e.sh）。
test('编译页面：待审合并请求与对账状态、链到托管平台；对账失败时列出跳过的页面；版本记录有页面行', async ({ page }) => {
  test.setTimeout(90_000);
  await asStaff(page, T1);
  await page.getByRole('link', { name: '编译页面' }).click();
  await expect(page).toHaveURL(/\/review\/pages$/);
  expect((await page.goto('/review/pages'))?.headers()['cache-control'] ?? '').toContain('no-store');
  const summary = page.getByTestId('pages-summary');
  await expect(summary).toHaveAttribute('data-ok', 'true');
  const pending = page.getByTestId('pages-pending').locator('tr[data-page="MATH.SYN.T.E"]');
  await expect(pending).toHaveCount(1);
  await expect(pending.getByRole('link')).toHaveAttribute('href', 'https://git.example.cn/synthetic/kge-pages/-/merge_requests/104');
  await expect(page.getByTestId('pages-merged').locator('tbody tr')).toHaveCount(4);
  await expect(page.getByTestId('pages-merged').locator('tr[data-status="ok"]')).toHaveCount(4);
  await expect(page.getByTestId('pages-orphans')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText('syn-teacher');  // 不显示托管平台用户名
  await axe(page);

  // 一方有一方没有：T2 对一个合并记录里没有的提交签字（审核记录只追加，删不掉，用多出来的一条模拟）→ 对账失败，合并窗口跳过该页
  execFileSync('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', '-c', `BEGIN; SET LOCAL ROLE starbuddy_review;
SELECT set_config('app.account_id', '5f0e0000-0000-4000-8000-0000000000e2', true);
SELECT kge.sign_page_review('MATH.SYN.T.D', '${'c3'.repeat(20)}', 'approved', 'low', 'mr_approved'); COMMIT;`], { encoding: 'utf8' });
  await page.reload();
  await expect(summary).toHaveAttribute('data-ok', 'false');
  await expect(summary).toContainText('合并窗口跳过 1 页：MATH.SYN.T.D');
  await expect(page.getByTestId('pages-orphans').locator('tr[data-page="MATH.SYN.T.D"]')).toContainText('提交号不符');
  await expect(page.getByTestId('pages-orphans').locator('tr[data-page="MATH.SYN.T.D"]')).toContainText('扣页');
  await axe(page);

  // 版本记录（spec 第 11 节）：数学的页面行，含页面 ID 与提交号
  await page.goto('/review/records?subject=aya');
  await expect(page.locator('tr[data-line="page"][data-page="MATH.SYN.T.A"]')).toHaveCount(2);
  await expect(page.locator('tr[data-line="page"][data-page="MATH.SYN.T.A"]').first()).toContainText('a1a1a1a1a1a1');
  await axe(page);
});

// ---------- 考情 v0.9 待定事项 PR2（specs/policy-decisions-v0.9.1）：控制线签字（迁移 0047）与官方来源清单页面（迁移 0048） ----------
// 合成城市 synth-e2e 的 2033 届合成方案（非真实数据）：以迁移执行者身份播种来源文件与方案（两名合成教研经签字身份签字、发布）与一条控制线草稿
const T1_ID = '5f0e0000-0000-4000-8000-0000000000e1';
const T2_ID = '5f0e0000-0000-4000-8000-0000000000e2';
const CL_DOC = '5c0f0000-0000-4000-8000-0000000000d1';
const CL_SCH = '5c0f0000-0000-4000-8000-0000000000e1';
const CL_LINE = '5c0f0000-0000-4000-8000-0000000000f1';
function seedControlLine() {
  const signAs = (staffId: string) => `SELECT set_config('app.account_id', '${staffId}', true);
SELECT policy.sign_record('source_document', '${CL_DOC}', 'approved', policy.object_digest('source_document', '${CL_DOC}'));
SELECT policy.sign_scheme('${CL_SCH}', 'approved', policy.object_digest('exam_scheme', '${CL_SCH}'));`;
  const sql = `BEGIN;
INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.synth-e2e.example', '合成教育局（端到端）', 'staff-e2e');
SET LOCAL ROLE starbuddy_kge;
INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on)
VALUES ('${CL_DOC}', 'synth-e2e', '合成教育局（端到端）', '合成〔2032〕1号', '合成 2033 届方案（非真实数据）', '2032-03-01',
        'https://jyj.synth-e2e.example/doc/1', 2033, '2032-09-01');
INSERT INTO policy.exam_scheme (id, city, cohort, version, source_document_id) VALUES ('${CL_SCH}', 'synth-e2e', 2033, '1.0.0', '${CL_DOC}');
INSERT INTO policy.subject_config (scheme_id, subject, scored, full_score, proposition, exam_form, taken_in_grade)
VALUES ('${CL_SCH}', 'chinese', true, 120, 'city', 'closed_book', 9);
INSERT INTO policy.admission_rule (scheme_id, batch_code, batch_order, track, control_line_basis)
VALUES ('${CL_SCH}', 'batch_1', 1, 'unified', 'none'), ('${CL_SCH}', 'batch_1', 1, 'quota_allocation', 'total_score');
RESET ROLE;
SET LOCAL ROLE starbuddy_review;
${signAs(T1_ID)}
${signAs(T2_ID)}
SELECT policy.publish_scheme('${CL_SCH}');
RESET ROLE;
SET LOCAL ROLE starbuddy_kge;
INSERT INTO policy.control_line (id, city, year, batch_code, score, source_document_id) VALUES ('${CL_LINE}', 'synth-e2e', 2033, 'batch_1', 98.5, '${CL_DOC}');
COMMIT;`;
  execFileSync('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', '-c', sql], { encoding: 'utf8' });
}

test('控制线：待签列表里有控制线草稿 → 两名教研各批准一次，第二名批准时发布 → 版本记录有控制线行（标签“控制线”）', async ({ page, browser }) => {
  test.setTimeout(120_000);
  seedControlLine();
  await asStaff(page, T1);
  const res = await page.goto('/review/policy?city=synth-e2e');
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  const item = page.getByTestId('policy-drafts').locator('li[data-object="control_line"]');
  await expect(item).toHaveCount(1);
  await expect(item).toContainText('控制线');
  await expect(item).toContainText('batch_1 · quota_allocation · 98.5');
  await expect(item).toHaveAttribute('data-approvals', '0');
  await axe(page);
  await hydrated(page);
  const form = page.getByTestId(`policy-record-${CL_LINE}`);
  await form.getByRole('button', { name: '批准' }).click();
  await expect(form.getByRole('status')).toHaveAttribute('data-code', 'signed');

  const other = await browser.newContext();
  const p2 = await other.newPage();
  await asStaff(p2, T2);
  await p2.goto('/review/policy?city=synth-e2e');
  const item2 = p2.getByTestId('policy-drafts').locator('li[data-object="control_line"]');
  await expect(item2).toHaveAttribute('data-approvals', '1');
  await expect(item2).toContainText('你若批准，就是第二名');
  await hydrated(p2);
  // 第二名批准即发布：页面重新渲染，这条控制线从待签列表里消失（表单随之消失）
  await p2.getByTestId(`policy-record-${CL_LINE}`).getByRole('button', { name: '批准' }).click();
  await expect(p2.locator('li[data-object="control_line"]')).toHaveCount(0);
  await p2.reload();
  await expect(p2.locator('li[data-object="control_line"]')).toHaveCount(0);
  await other.close();
  expect(execFileSync('psql', ['-X', '-At', '-c', `SELECT status FROM policy.control_line WHERE id = '${CL_LINE}'`], { encoding: 'utf8' }).trim())
    .toBe('published');

  await page.goto('/review/records?subject=aya');
  const rows = page.locator(`tr[data-line="policy"][data-policy-object="control_line"][data-policy-id="${CL_LINE}"]`);
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText('控制线');
  await expect(rows.first()).toContainText('synth-e2e 2033');
  await expect(page.locator(`tr[data-policy-id="${CL_LINE}"][data-action="publish"]`)).toHaveCount(1);
  await axe(page);
});

test('官方来源清单：没有维护权限的教研看到 403 提示 → 授权后能列表、加入 punycode 主机名（同时显示 Unicode 形式）、不合格被拒、下线；DOM 不泄露内部编码', async ({ page, browser }) => {
  test.setTimeout(120_000);
  // T2 没有 policy_source_admin：页面只有“没有权限”，不列清单
  const other = await browser.newContext();
  const p2 = await other.newPage();
  await asStaff(p2, T2);
  const r2 = await p2.goto('/review/policy/sources');
  expect(r2?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(p2.getByTestId('sources-forbidden')).toContainText('没有维护官方来源清单的权限');
  await expect(p2.getByTestId('sources')).toHaveCount(0);
  await expect(p2.getByTestId('add-source')).toHaveCount(0);
  await axe(p2);
  await expectNoDomLeak(p2);
  await other.close();

  // DBA 授予 T1（只在一次性测试库里以迁移执行者身份写）
  execFileSync('psql', ['-X', '-q', '-v', 'ON_ERROR_STOP=1', '-c',
    `INSERT INTO platform.staff_permission (staff_id, permission, granted_by) VALUES ('${T1_ID}', 'policy_source_admin', 'synthetic-dba') ON CONFLICT DO NOTHING`],
    { encoding: 'utf8' });
  await asStaff(page, T1);
  await page.goto('/review/policy');
  await page.getByRole('link', { name: '官方来源清单' }).click();
  await expect(page).toHaveURL(/\/review\/policy\/sources$/);
  await expect(page.getByTestId('sources')).toBeVisible();
  await expect(page.getByTestId('sources').locator('code', { hasText: 'jyj.synth-e2e.example' })).toHaveCount(1);
  await expect(page.getByTestId('add-source')).toContainText('公开信息，不得填写个人信息');
  await axe(page);
  await expectNoDomLeak(page);

  // 加入 punycode 主机名（大小写与结尾的点由库里规范化）；页面另列 Unicode 形式
  await hydrated(page);
  const add = page.getByTestId('add-source');
  await add.getByLabel('主机名').fill('XN--55QX5D.Synth-E2E.Example.');
  await add.getByLabel('说明（发布机关）').fill('合成教育局（端到端）');
  await add.getByRole('button', { name: '加入清单' }).click();
  await expect(add.getByRole('status')).toHaveAttribute('data-code', 'source_added');
  await page.reload();
  const row = page.getByTestId('sources').locator('tr', { has: page.locator('code', { hasText: 'xn--55qx5d.synth-e2e.example' }) });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('公司.synth-e2e.example');
  await expect(row).toContainText('国际化域名');
  await expect(row).toContainText('在用');
  await expect(row.getByText(/可能是仿冒官网的同形字/)).toHaveCount(0);  // 全是汉字：不警告
  // 同形字：西里尔字母 а 冒充拉丁 a（аpple）→ 醒目警告
  await hydrated(page);
  await add.getByLabel('主机名').fill('xn--pple-43d.synth-e2e.example');
  await add.getByLabel('说明（发布机关）').fill('合成教育局（端到端）');
  await add.getByRole('button', { name: '加入清单' }).click();
  await expect(add.getByRole('status')).toHaveAttribute('data-code', 'source_added');
  await page.reload();
  const fake = page.getByTestId('sources').locator('tr', { has: page.locator('code', { hasText: 'xn--pple-43d.synth-e2e.example' }) });
  await expect(fake).toContainText('国际化域名');
  await expect(fake.getByText(/可能是仿冒官网的同形字/)).toBeVisible();
  await axe(page);
  await expectNoDomLeak(page);

  // 不合格的主机名（IP）：库函数拒绝，提示说明原因；重复加入：重复操作
  await hydrated(page);
  await add.getByLabel('主机名').fill('10.0.0.1');
  await add.getByLabel('说明（发布机关）').fill('合成教育局（端到端）');
  await add.getByRole('button', { name: '加入清单' }).click();
  await expect(add.getByRole('status')).toHaveAttribute('data-code', 'INVALID_PARAMETER');
  await expect(add.getByRole('status')).toContainText('主机名不合格');
  await add.getByLabel('主机名').fill('xn--55qx5d.synth-e2e.example');
  await add.getByLabel('说明（发布机关）').fill('合成教育局（端到端）');
  await add.getByRole('button', { name: '加入清单' }).click();
  await expect(add.getByRole('status')).toHaveAttribute('data-code', 'CONFLICT');

  // 下线（须勾选确认）：只改状态，行还在
  await page.reload();
  await hydrated(page);
  const retire = row.locator('form');
  await expect(retire.getByRole('checkbox')).toHaveAttribute('required', '');
  await retire.getByRole('checkbox').check();
  await retire.getByRole('button', { name: /下线/ }).click();
  // 下线后页面重新渲染：这一行变为“已下线”，下线表单随之消失
  await expect(row).toContainText('已下线');
  await expect(row.locator('form')).toHaveCount(0);
  await page.reload();
  await expect(row).toContainText('已下线');
  expect(execFileSync('psql', ['-X', '-At', '-c',
    `SELECT string_agg(action, ',' ORDER BY changed_at) FROM platform.policy_source_domain_change WHERE host = 'xn--55qx5d.synth-e2e.example'`],
  { encoding: 'utf8' }).trim()).toBe('add,retire');
  await axe(page);
  await expectNoDomLeak(page);
});
