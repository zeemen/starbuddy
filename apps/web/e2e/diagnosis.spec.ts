// 数学自适应诊断演示页端到端（SIE-02a 验收第 3、5 条）：合成学生 D1 登录 → 演示页 → 每题前“能做对吗”（答或跳过）→ 作答 →
// 中途暂停、刷新页面续做同一个会话 → 过程题写全过程、讲解题讲给老师听 → 结束页只说“完成了，老师会看你的过程”。
// 库里核对：一个会话、预留一次并在结束时结算、过程观察进复核队列、探针关联作答；家长打开孩子的演示页开始不了、不建会话。
// axe 无违规（探针、选择题、过程题、暂停、结束各查一次）。演示页只在测试构建里存在（scripts/e2e.sh）。全部合成数据。
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const ACC = { D1: '5a000000-0000-4000-8000-0000000000b5', gD: '5a000000-0000-4000-8000-0000000000a5' };
const D1 = '5a000000-0000-4000-8000-0000000000c5';

function sql(q: string): string {
  return execFileSync('psql', ['-X', '-At', '-c', q], { encoding: 'utf8' }).trim();
}
const sessions = () => Number(sql(`SELECT count(*) FROM platform.diagnosis_session WHERE student_id = '${D1}'`));

async function open(page: Page, account: string) {
  const res = await page.goto(`/dev/session?account=${account}&next=${encodeURIComponent(`/dev/diagnosis/${D1}`)}`);
  expect(res?.status()).toBe(200);
  expect(res?.headers()['cache-control'] ?? '').toContain('no-store');
  await expect(page.getByTestId('diagnosis-page')).toBeVisible();
}
const axe = async (page: Page) =>
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.map((v) => v.id)).toEqual([]);

/** 当前题作答：选择题选第一个选项，填空题写 1；过程题、讲解题另写一段 */
async function answerCurrent(page: Page) {
  const form = page.locator('form.diagnosis-item');
  await expect(form).toBeVisible();
  const kind = await form.getAttribute('data-kind');
  if (kind !== 'adaptive') {
    await page.getByLabel(kind === 'process' ? '解题过程' : '讲给老师听').fill('合成过程：先写已知条件，再列式，最后求解。');
  }
  const options = form.locator('input[type=radio], input[type=checkbox]');
  if (await options.count()) await options.first().check();
  else await page.getByLabel('答案').fill('1');
  const heading = page.locator('form.diagnosis-item h3');
  const seq = await heading.innerText();
  await form.getByRole('button', { name: '提交' }).click();
  // 提交后换到下一步（探针、下一题或结束页）
  await expect.poll(async () => (await page.getByTestId('diagnosis').getAttribute('data-phase')) !== 'item'
    || (await heading.count() === 0) || (await heading.innerText()) !== seq, { timeout: 15_000 }).toBe(true);
  return kind;
}

test.describe.configure({ mode: 'serial' });

test('家长打开孩子的诊断演示页：开始不了，不建会话、不占额度', async ({ page }) => {
  const before = sessions();
  await open(page, ACC.gD);
  await expect(page.getByText('现在还不能开始，请稍后再来。')).toBeVisible();
  expect(sessions()).toBe(before);
  expect(Number(sql(`SELECT count(*) FROM platform.usage_reservation WHERE student_id = '${D1}'`))).toBe(0);
  await axe(page);
});

test('合成学生做完一次诊断：探针、暂停续做、过程题、结束页不给结论', async ({ page }) => {
  test.setTimeout(180_000);
  await open(page, ACC.D1);
  // 第 1 题：先问“能做对吗”（2-L 探针组件），计时显示“已用时间 mm:ss，共 35:00”
  const probe = page.getByRole('group', { name: '这道题你觉得能做对吗？' });
  await expect(probe).toBeVisible();
  await expect(page.getByRole('timer')).toHaveText(/^\d\d:\d\d，共 35:00$/);
  expect(sessions()).toBe(1);
  expect(sql(`SELECT state FROM platform.usage_reservation WHERE student_id = '${D1}'`)).toBe('reserved');
  await axe(page);
  await probe.getByRole('button', { name: '不确定' }).click();
  await expect(page.getByTestId('diagnosis-stem')).toContainText('合成题面');
  await expect(page.getByRole('heading', { name: /题目/ })).toBeFocused();
  await axe(page);
  await answerCurrent(page);
  // 第 2 题：跳过探针（不写探针）
  await page.getByRole('button', { name: '跳过' }).click();
  await answerCurrent(page);
  // 第 3 题：答了探针后暂停；刷新页面（跨天续做）接着做同一个会话
  await page.getByRole('group', { name: '这道题你觉得能做对吗？' }).getByRole('button', { name: '能', exact: true }).click();
  await expect(page.locator('form.diagnosis-item')).toBeVisible();
  await page.getByRole('button', { name: '暂停' }).click();
  await expect(page.getByRole('heading', { name: '已暂停，暂停期间不计时。' })).toBeVisible();
  await expect.poll(() => sql(`SELECT status FROM platform.diagnosis_session WHERE student_id = '${D1}'`)).toBe('paused');
  await axe(page);
  await page.reload();
  await expect(page.locator('form.diagnosis-item')).toBeVisible();  // 探针已记过：直接看题
  expect(sessions()).toBe(1);
  expect(Number(sql(`SELECT count(*) FROM platform.usage_reservation WHERE student_id = '${D1}'`))).toBe(1);  // 续做不另占额度
  // 其余各题：探针一律跳过；过程题、讲解题写全过程
  const kinds = new Set<string>();
  let checkedProcess = false;
  for (let i = 0; i < 40; i++) {
    if (await page.getByTestId('diagnosis-finished').isVisible()) break;
    const skip = page.getByRole('button', { name: '跳过' });
    if (await skip.isVisible()) await skip.click();
    const form = page.locator('form.diagnosis-item');
    if (!(await form.isVisible())) {
      await page.waitForTimeout(200);
      continue;
    }
    const kind = (await form.getAttribute('data-kind')) ?? '';
    if (kind !== 'adaptive' && !checkedProcess) {
      await expect(page.getByLabel(kind === 'process' ? '解题过程' : '讲给老师听')).toBeVisible();
      await axe(page);
      checkedProcess = true;
    }
    kinds.add((await answerCurrent(page)) ?? '');
  }
  const done = page.getByTestId('diagnosis-finished');
  await expect(done).toHaveText('完成了，老师会看你的过程。');
  expect(kinds.has('process') && kinds.has('explain')).toBe(true);
  // 结束页不给结论：没有对错、分数、掌握之类的字
  await expect(page.getByTestId('diagnosis')).not.toContainText(/对|错|分|掌握|断点|水平/);
  await expect(page.getByRole('timer')).toHaveCount(0);
  await axe(page);
  // 库里：会话结束、额度结算一次；过程观察进复核队列；答过的探针关联了作答
  expect(sql(`SELECT status FROM platform.diagnosis_session WHERE student_id = '${D1}'`)).toBe('finished');
  expect(sql(`SELECT string_agg(state::text, ',') FROM platform.usage_reservation WHERE student_id = '${D1}'`)).toBe('settled');
  const proc = Number(sql(`SELECT count(*) FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id
                           WHERE s.student_id = '${D1}' AND st.kind IN ('process', 'explain')`));
  expect(proc).toBeGreaterThanOrEqual(2);
  expect(Number(sql(`SELECT count(*) FROM platform.review_task WHERE student_id = '${D1}' AND source_kind = 'diagnosis_step'`))).toBe(proc);
  expect(sql(`SELECT count(*) || ':' || count(attempt_id) FROM insight.confidence_probe WHERE student_id = '${D1}'`)).toBe('2:2');
  const elapsed = Number(sql(`SELECT elapsed_sec FROM platform.diagnosis_session WHERE student_id = '${D1}'`));
  expect(elapsed).toBeLessThanOrEqual(2100);
});
