// 探针演示页端到端（冲刺 2-L 验收）：学生登录 → 演示页 → 选“不确定” → 库里一条 source_type=self 的探针；
// 会话开始、结束各一条 source_type=objective 的行为事件；跳过不提交；家长打开同一页提交被拒、不写入；axe 无违规。
// 演示页只在测试构建里存在（scripts/e2e.sh 做的构建）。合成家庭来自 db/seeds/dev/star-map-synthetic.sql。
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const ACC = { S1: '5a000000-0000-4000-8000-0000000000b1', S2: '5a000000-0000-4000-8000-0000000000b2', gS: '5a000000-0000-4000-8000-0000000000a1' };
const STU = { S1: '5a000000-0000-4000-8000-0000000000c1', S2: '5a000000-0000-4000-8000-0000000000c2' };

/** 以迁移执行者身份核对库里的结果（只读查询；一次性测试库） */
function sql(q: string): string {
  return execFileSync('psql', ['-X', '-At', '-c', q], { encoding: 'utf8' }).trim();
}
const events = (student: string, kind: string) =>
  Number(sql(`SELECT count(*) FROM insight.behavior_event WHERE student_id = '${student}' AND kind = '${kind}' AND source_type = 'objective'`));
const probes = (student: string) => Number(sql(`SELECT count(*) FROM insight.confidence_probe WHERE student_id = '${student}'`));

async function open(page: Page, account: string, student: string) {
  const res = await page.goto(`/dev/session?account=${account}&next=${encodeURIComponent(`/dev/probe/${student}`)}`);
  expect(res?.status()).toBe(200);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByTestId('probe-page')).toBeVisible();
}
const axe = async (page: Page) =>
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.map((v) => v.id)).toEqual([]);

test.describe.configure({ mode: 'serial' });

test('学生选“不确定”：一条学生自述探针；会话开始、结束各一条客观记录', async ({ page }) => {
  const [starts, ends, n] = [events(STU.S1, 'session_start'), events(STU.S1, 'session_end'), probes(STU.S1)];
  await open(page, ACC.S1, STU.S1);
  await expect.poll(() => events(STU.S1, 'session_start')).toBe(starts + 1);
  // 题目展示前先问；按钮有可访问名称，整组由问题句标注；不显示题干
  const group = page.getByRole('group', { name: '这道题你觉得能做对吗？' });
  await expect(group).toBeVisible();
  for (const name of ['能', '不能', '不确定', '跳过']) await expect(group.getByRole('button', { name, exact: true })).toBeVisible();
  await expect(page.getByTestId('item-placeholder')).toHaveCount(0);
  await axe(page);
  // 键盘操作：Tab 到“不确定”，回车
  await group.getByRole('button', { name: '能', exact: true }).focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(group.getByRole('button', { name: '不确定' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('item-placeholder')).toContainText('SYN-ZX-ITEM-01');
  await expect(page.getByRole('heading', { name: '题目' })).toBeFocused();
  await expect(page.getByTestId('confidence-probe')).toHaveCount(0);  // 答完就收起：不显示刚才的选择，也不评价
  await expect(page.getByRole('status')).toHaveText('');
  await axe(page);
  expect(probes(STU.S1)).toBe(n + 1);
  expect(sql(`SELECT source_type || ':' || prediction || ':' || subject || ':' || item_id || ':' || (attempt_id IS NULL)
               FROM insight.confidence_probe WHERE student_id = '${STU.S1}' ORDER BY created_at DESC LIMIT 1`))
    .toBe('self:unsure:zhixing:SYN-ZX-ITEM-01:true');
  await page.getByRole('button', { name: '结束' }).click();
  await expect(page.getByRole('status')).toHaveText('本次已结束。');
  await expect.poll(() => events(STU.S1, 'session_end')).toBe(ends + 1);
  expect(sql(`SELECT payload->>'entry' FROM insight.behavior_event WHERE student_id = '${STU.S1}' AND kind = 'session_start' ORDER BY created_at DESC LIMIT 1`))
    .toBe('other');
  expect(Number(sql(`SELECT (payload->>'duration_sec')::int FROM insight.behavior_event WHERE student_id = '${STU.S1}' AND kind = 'session_end'
                     ORDER BY created_at DESC LIMIT 1`))).toBeGreaterThanOrEqual(0);
  await axe(page);
});

test('跳过不提交；离开页面也记会话结束', async ({ page }) => {
  const [ends, n] = [events(STU.S2, 'session_end'), probes(STU.S2)];
  await open(page, ACC.S2, STU.S2);
  await page.getByRole('button', { name: '跳过' }).click();
  await expect(page.getByTestId('item-placeholder')).toBeVisible();
  expect(probes(STU.S2)).toBe(n);
  await page.goto('/dev/star-glyphs');  // 离开页面：pagehide 时经 server action 记 session_end（尽力而为）
  await expect.poll(() => events(STU.S2, 'session_end'), { timeout: 10_000 }).toBe(ends + 1);
});

test('家长打开孩子的演示页：提交被拒，只提示没记下，库里不多任何行', async ({ page }) => {
  const [starts, n] = [events(STU.S1, 'session_start'), probes(STU.S1)];
  await open(page, ACC.gS, STU.S1);
  await page.getByRole('button', { name: '能', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('这次没有记下来，不影响做题。');
  await page.waitForTimeout(500);
  expect(probes(STU.S1)).toBe(n);
  expect(events(STU.S1, 'session_start')).toBe(starts);
});
