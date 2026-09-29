// 审核台端到端（spec 冲刺 1 验收“端到端测试”）：合成教研经登录页登录（口令 + 6 位码，PR 4）→ 打开草稿 → 改一个节点的“不包含” → 看到差异与校验 →
// 签字 → 发布 → 旧版本变为已撤下，版本记录里有这一行。种子见 db/seeds/dev/review-synthetic.sql（语文合成地图）。
// 另测：登录失败、退出、看到的内容过期时签字被拒、学生身份进不了审核台、无障碍、不缓存。
// 统一复核队列（specs/review-queue-sprint2）：看到合成任务、领取、他人领不了、放回、结案。
// 用例之间有先后（发布后的状态），按顺序执行（test.describe.serial）。
import { execFileSync } from 'node:child_process';
import { createHmac } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

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

test('学生身份进不了审核台（去审核台登录页）；开发身份桩没有 staff 入口', async ({ page }) => {
  await page.goto(`/dev/session?account=${S1}&next=${encodeURIComponent('/star-map/5a000000-0000-4000-8000-0000000000c1')}`);
  await page.goto('/review?subject=wenli');
  await expect(page).toHaveURL(/\/review\/login$/);
  expect((await page.goto(`/dev/session?staff=${S1}`))?.status()).toBe(400);
  await page.goto('/dev/login');
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
