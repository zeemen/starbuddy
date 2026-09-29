// 建档端到端（SIE-01 验收）：学生版、家长版各走三条路径（全答、全跳过、返回改答案）；两方互不可见；别家看不到；
// 进度表记录齐全、统计函数给出完成率与时长。合成家庭来自 db/seeds/dev/star-map-synthetic.sql（S1、S2 与家长 gS，无关家长 gO）。
// 用例之间有先后（答案与进度累积），按顺序执行。
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const ACC = { S1: '5a000000-0000-4000-8000-0000000000b1', S2: '5a000000-0000-4000-8000-0000000000b2',
  gS: '5a000000-0000-4000-8000-0000000000a1', gO: '5a000000-0000-4000-8000-0000000000a2' };
const STU = { S1: '5a000000-0000-4000-8000-0000000000c1', S2: '5a000000-0000-4000-8000-0000000000c2' };

/** 以迁移执行者身份核对库里的结果（只读查询；一次性测试库） */
function sql(q: string): string {
  return execFileSync('psql', ['-X', '-At', '-c', q], { encoding: 'utf8' }).trim();
}

async function as(page: Page, account: string, student: string) {
  const res = await page.goto(`/dev/session?account=${account}&next=${encodeURIComponent(`/onboarding/${student}`)}`);
  expect(res?.status()).toBe(200);
  await expect(page.getByTestId('onboarding')).toBeVisible();
}

const next = (page: Page) => page.getByRole('button', { name: /下一步|完成/ }).click();
const skip = (page: Page) => page.getByRole('button', { name: '跳过' }).click();

test.describe.configure({ mode: 'serial' });

test('学生版全答：7 页走完，自述、教材、成绩都写进去', async ({ page }) => {
  const t0 = Date.now();
  await as(page, ACC.S1, STU.S1);
  const res = await page.goto(`/onboarding/${STU.S1}`);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-role', 'self');
  await page.getByRole('button', { name: '开始' }).click();
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'focus_subject');
  await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze().then((r) => expect(r.violations.map((v) => v.id)).toEqual([]));
  await page.getByLabel('数学').check(); await next(page);
  await page.getByLabel(/目标/).fill('合成：期末数学提高十分'); await next(page);
  await page.getByLabel('每周约 5 小时').check(); await next(page);
  await page.getByLabel('先讲再练').check(); await next(page);
  await page.getByLabel('审题出错').check(); await page.getByLabel('时间不够').check(); await next(page);
  await page.getByLabel('数学', { exact: true }).selectOption('suke-2024'); await page.getByLabel('道法').selectOption('unknown'); await next(page);
  await page.getByLabel('日期').fill('2026-11-10'); await page.getByLabel('得分').fill('95'); await next(page);
  await expect(page.getByTestId('done')).toBeVisible();
  console.log(`学生版全答用时（自动化，不代表真人）：${Date.now() - t0} ms`);
  expect(sql(`SELECT count(*) FROM insight.self_report WHERE student_id = '${STU.S1}' AND superseded_at IS NULL`)).toBe('5');
  expect(sql(`SELECT count(*) FROM platform.student_subject_textbook WHERE student_id = '${STU.S1}'`)).toBe('2');
  expect(sql(`SELECT raw_score || '/' || full_score || '/' || entered_by_role FROM insight.exam_record WHERE student_id = '${STU.S1}'`)).toBe('95.0/100.0/self');
  expect(sql(`SELECT string_agg(status, ',' ORDER BY id) FROM insight.onboarding_progress WHERE student_id = '${STU.S1}' AND step = 'onboarding'`))
    .toBe('started,completed');
});

test('学生版返回改答案：旧答案保留为历史，页面显示新答案', async ({ page }) => {
  await as(page, ACC.S1, STU.S1);
  await page.goto(`/onboarding/${STU.S1}?step=3`);
  await expect(page.getByLabel('每周约 5 小时')).toBeChecked();  // 显示自己上次的答案
  await page.getByRole('link', { name: '上一步' }).click();
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'goal');
  await expect(page.getByLabel(/目标/)).toHaveValue('合成：期末数学提高十分');
  await page.getByLabel(/目标/).fill('合成：先把函数学扎实'); await next(page);
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'time');
  expect(sql(`SELECT count(*) || ':' || count(*) FILTER (WHERE superseded_at IS NULL) FROM insight.self_report
               WHERE student_id = '${STU.S1}' AND field_id = 'through.g9.near_term_goal'`)).toBe('2:1');
  // 空答案提交：本页提示，不前进、不写入
  await page.goto(`/onboarding/${STU.S1}?step=2`);
  await page.getByLabel(/目标/).fill('');
  await next(page);
  await expect(page.getByTestId('error')).toBeVisible();
});

test('家长版：对 S1 填不同的可用时间；两方互不可见', async ({ page }) => {
  await as(page, ACC.gS, STU.S1);
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-role', 'guardian');
  await page.getByRole('button', { name: '开始' }).click();
  await page.getByLabel('每周约 2 小时').check(); await next(page);
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'devices');
  // 家长看不到学生写的目标（整个建档页都没有）
  for (const n of [1, 2, 3, 4, 5, 6]) {
    await page.goto(`/onboarding/${STU.S1}?step=${n}`);
    expect(await page.content()).not.toContain('先把函数学扎实');
  }
  // 两边的“每周可用时间”各存各的
  expect(sql(`SELECT (SELECT value->>'minutes' FROM insight.self_report WHERE student_id = '${STU.S1}' AND field_id = 'outer.g1.weekly_available_time' AND superseded_at IS NULL)
               || '/' || (SELECT value->>'minutes' FROM insight.guardian_report WHERE student_id = '${STU.S1}' AND field_id = 'outer.g1.weekly_available_time' AND superseded_at IS NULL)`))
    .toBe('300/120');
  // 学生回到“时间”这一页，看到的是自己的 5 小时，不是家长的 2 小时
  await as(page, ACC.S1, STU.S1);
  await page.goto(`/onboarding/${STU.S1}?step=3`);
  await expect(page.getByLabel('每周约 5 小时')).toBeChecked();
  await expect(page.getByLabel('每周约 2 小时')).not.toBeChecked();
});

test('家长版全答：S1 的其余 5 页填完（含排名区间）；返回改答案保留旧行', async ({ page }) => {
  await as(page, ACC.gS, STU.S1);
  await page.goto(`/onboarding/${STU.S1}?step=2`);
  await expect(page.getByRole('button', { name: '跳过' })).toBeVisible();
  await expect(page.getByRole('link', { name: '上一步' })).toBeVisible();
  await page.getByLabel('平板').check(); await next(page);
  await page.getByLabel('数学', { exact: true }).check(); await next(page);
  await page.getByLabel('经常').check(); await next(page);
  await page.getByLabel('语文', { exact: true }).selectOption('bubian-2024'); await next(page);
  await page.getByLabel('日期').fill('2026-11-12'); await page.getByLabel('得分').fill('88');
  await page.getByLabel(/大致排名/).selectOption('top30'); await next(page);
  await expect(page.getByTestId('done')).toBeVisible();
  expect(sql(`SELECT raw_score || '/' || entered_by_role || '/' || rank_band FROM insight.exam_record WHERE student_id = '${STU.S1}' AND entered_by_role = 'guardian'`))
    .toBe('88.0/guardian/top30');
  // 返回改“校外补习”：从第 4 页点上一步回到第 3 页，看到自己上次的答案，改成英语
  await page.goto(`/onboarding/${STU.S1}?step=4`);
  await expect(page.getByLabel('经常')).toBeChecked();
  await page.getByRole('link', { name: '上一步' }).click();
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'tutoring');
  await expect(page.getByLabel('数学', { exact: true })).toBeChecked();
  await page.getByLabel('数学', { exact: true }).uncheck(); await page.getByLabel('英语').check(); await next(page);
  await expect(page.getByTestId('onboarding')).toHaveAttribute('data-step', 'cooperation');
  expect(sql(`SELECT count(*) || ':' || (SELECT value->>'subjects' FROM insight.guardian_report WHERE student_id = '${STU.S1}'
               AND field_id = 'outer.g1.tutoring' AND superseded_at IS NULL) FROM insight.guardian_report
               WHERE student_id = '${STU.S1}' AND field_id = 'outer.g1.tutoring'`)).toBe('2:["echo"]');
  // 学生看不到家长录的成绩与排名区间
  await as(page, ACC.S1, STU.S1);
  await page.goto(`/onboarding/${STU.S1}?step=7`);
  expect(await page.content()).not.toContain('top30');
  await expect(page.getByLabel(/大致排名/)).toHaveCount(0);
});

test('家长版全跳过：S2 的 6 页都跳过也能完成，不写任何答案', async ({ page }) => {
  await as(page, ACC.gS, STU.S2);
  await page.getByRole('button', { name: '开始' }).click();
  for (let i = 0; i < 6; i++) await skip(page);
  await expect(page.getByTestId('done')).toBeVisible();
  expect(sql(`SELECT count(*) FROM insight.guardian_report WHERE student_id = '${STU.S2}'`)).toBe('0');
  expect(sql(`SELECT count(*) FROM insight.onboarding_progress WHERE student_id = '${STU.S2}' AND status = 'skipped'`)).toBe('6');
});

test('学生版全跳过（S2）；别家家长看不到；统计给出完成率与时长', async ({ page }) => {
  await as(page, ACC.S2, STU.S2);
  await page.getByRole('button', { name: '开始' }).click();
  for (let i = 0; i < 7; i++) await skip(page);
  await expect(page.getByTestId('done')).toBeVisible();
  await page.goto(`/dev/session?account=${ACC.gO}&next=${encodeURIComponent(`/onboarding/${STU.S1}`)}`);
  expect((await page.goto(`/onboarding/${STU.S1}`))?.status()).toBe(404);
  expect(sql(`SELECT respondent_role || ':' || started || ':' || completed FROM insight.onboarding_stats(now() - interval '1 hour', now() + interval '1 hour') ORDER BY 1`))
    .toBe('guardian:2:2\nself:2:2');
});
