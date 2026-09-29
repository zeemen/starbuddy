// 审核台端到端（spec 冲刺 1 验收“端到端测试”）：合成教研登录 → 打开草稿 → 改一个节点的“不包含” → 看到差异与校验 →
// 签字 → 发布 → 旧版本变为已撤下，版本记录里有这一行。种子见 db/seeds/dev/review-synthetic.sql（语文合成地图）。
// 另测：看到的内容过期时签字被拒、学生身份进不了审核台、无障碍、不缓存。
// 用例之间有先后（发布后的状态），按顺序执行（test.describe.serial）。
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const T1 = '5f0e0000-0000-4000-8000-0000000000e1';
const T2 = '5f0e0000-0000-4000-8000-0000000000e2';
const S1 = '5a000000-0000-4000-8000-0000000000b1';
const V101 = '5f0e0000-0000-4000-8000-0000000001a2';

async function asStaff(page: Page, staff: string) {
  const res = await page.goto(`/dev/session?staff=${staff}`);
  expect(res?.status()).toBe(200);
  await expect(page).toHaveURL(/\/review$/);
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

test('学生身份进不了审核台（去开发登录页）', async ({ page }) => {
  await page.goto(`/dev/session?account=${S1}&next=${encodeURIComponent('/star-map/5a000000-0000-4000-8000-0000000000c1')}`);
  await page.goto('/review?subject=wenli');
  await expect(page).toHaveURL(/\/dev\/login$/);
  expect((await page.goto(`/dev/session?staff=${S1}`))?.status()).toBe(400);
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
