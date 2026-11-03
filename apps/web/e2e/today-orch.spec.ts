// 今日计划跨科编排学生端（PLT-11，specs/today-plan-orchestration-plt11 范围第 3 条；playwright.config.ts 的 practice-on 项目：前端与第五个 BFF
// 另开 STARBUDDY_TODAY_ORCH=today_orch，学习服务是合成桩 scripts/fake-learning-practice.ts 的 /internal/v1/today 剧本；合成家庭 HP 见
// db/seeds/dev/practice-synthetic.sql）。全程真实会话。
// 覆盖：今日页在按科列表之前显示跨科列表（三条：验证 → 复测页、再练 → 再练页、计划任务 → 科目页），截掉的条数只以 data-value 出现，
// 有来源不可用时只显示占位；“去做”能走到复测、再练页；家长看不到跨科列表；每页都核对：
//   - axe（wcag2a / wcag2aa）无违规；浏览器只请求本前端；
//   - 跨科区域里的每个文本节点，要么是 copy.ts 的文案（新文案全是 {{TODO_COPY:today.*}} 占位），要么在 data-value 里；
//   - 页面上没有桩故意带上的掌握、置信度、证据 ID、优先级、单元层次、任务原因、来源与原因编码；整页过 dom-leak.ts。
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ALL_SHELL_COPY, CONTENT_RATIONALE, TODAY_MARK, TODAY_ORCH, TODAY_ORCH_KIND } from '../src/shell/copy.ts';
import { expectNoDomLeak } from './dom-leak.ts';
import { cookieSession, loginGuardian, loginStudent, PRACTICE_BASE, PRACTICE_BFF } from './session.ts';

const P1 = '59000000-0000-4000-8000-0000000000c1';
const P2 = '59000000-0000-4000-8000-0000000000c2';
const R = (n: string) => `59000000-0000-4000-8000-000000000${n}`;
// 桩故意带上的内部字段与编码（任务原因、来源状态、掌握数值等）：任何位置都不许出现
const INTERNAL = ['p_mastery', 'evidence_id', 'cognitive_level', 'verify_self_report', 'source_unavailable', 'source_disabled', 'rationale_code', 'budget_minutes',
                  'high_exam_weight', '0.42', '0.77', R('9e1'), 'MATH.SYN.P1'];

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

async function onlyCopy(page: Page): Promise<void> {
  const offenders = await page.evaluate((copy) => {
    const known = new Set(copy);
    const out: string[] = [];
    const root = document.querySelector('[data-testid="today-orch"]');
    if (!root) return ['（没有跨科区域）'];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = (n.textContent ?? '').trim();
      const el = n.parentElement;
      if (t === '' || !el || el.closest('[data-value], [data-claim-id], script, style')) continue;
      if (!known.has(t)) out.push(t);
    }
    return out;
  }, ALL_SHELL_COPY);
  expect(offenders).toEqual([]);
  const html = await page.content();
  for (const leak of INTERNAL) expect(html, leak).not.toContain(leak);
  expect(html).not.toMatch(/粗心|马虎|薄弱|不认真|掌握度|置信度|星亮/);
  await expectNoDomLeak(page, { forbidden: INTERNAL });
}

function watchOrigin(page: Page): string[] {
  const off: string[] = [];
  page.on('request', (r) => { if (!r.url().startsWith(PRACTICE_BASE) && !r.url().startsWith('data:')) off.push(r.url()); });
  return off;
}

test('学生本人：今日页先显示跨科列表（类别占位、预计分钟、去做），截掉的条数与“有内容没列出”只以占位与 data-value 出现', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, P1);
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/today$`));
  const box = page.getByTestId('today-orch');
  await expect(box).toBeVisible();
  await expect(box.locator('h2')).toHaveText(TODAY_ORCH.heading);
  const items = page.getByTestId('today-orch-item');
  await expect(items).toHaveCount(3);
  await expect(items.nth(0).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['verify']!);
  await expect(items.nth(1).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['practice']!);
  await expect(items.nth(2).locator('.task-kind')).toHaveText(TODAY_ORCH_KIND['lesson']!);
  expect(await items.locator('[data-value="minutes"]').allTextContents()).toEqual(['5', '8', '5']);
  const hrefs = await page.getByTestId('today-orch-go').evaluateAll((els) => els.map((e) => e.getAttribute('href')));
  expect(hrefs).toEqual([`/s/${P1}/learn/math/retest/${R('402')}`, `/s/${P1}/learn/math/practice/${R('106')}`, `/s/${P1}/learn/math`]);
  await expect(page.getByTestId('today-orch-truncated').locator('[data-value="count"]')).toHaveText('2');
  await expect(page.getByTestId('today-orch-partial')).toHaveText(TODAY_ORCH.partial);
  // 跨科列表在按科列表之前；按科列表用 /today 附带的计划排（桩的 /plan 是 501，开关开时 BFF 不再调它）
  const order = await page.locator('main > section').evaluateAll((els) => els.map((e) => e.getAttribute('data-testid') ?? e.getAttribute('data-subject')));
  expect(order).toEqual(['today-orch', 'math']);
  await expect(page.locator('section.today-subject[data-subject="math"] li.task')).toHaveCount(1);
  // 跨科列表里的计划任务沿用按科那一条的内容类理由（固定短句）；复测、再练条目没有理由
  await expect(items.nth(2).locator('[data-rationale="content"]')).toHaveText(CONTENT_RATIONALE['high_exam_weight']!);
  await expect(items.nth(0).locator('[data-rationale]')).toHaveCount(0);
  await onlyCopy(page);
  await axe(page);
  expect(off).toEqual([]);
});

test('“去做”：验证条目进复测页、再练条目进再练页、计划任务进科目页', async ({ page }) => {
  await loginStudent(page, P1);
  await page.getByTestId('today-orch-go').nth(0).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math/retest/${R('402')}$`));
  await expect(page.getByTestId('retest-item')).toHaveAttribute('data-phase', 'item');
  await page.goto(`/s/${P1}/today`);
  await page.getByTestId('today-orch-go').nth(1).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math/practice/${R('106')}$`));
  await expect(page.getByTestId('practice-item')).toHaveAttribute('data-phase', 'item');
  await page.goto(`/s/${P1}/today`);
  await page.getByTestId('today-orch-go').nth(2).click();
  await expect(page).toHaveURL(new RegExp(`/s/${P1}/learn/math$`));
  await expect(page.getByTestId('subject-page')).toBeVisible();
});

// ---------- 今日计划“完成”“先跳过”（PLT-15 PR2）：桩的 P1 快照已冻结（任务 7a1–7a3），这里以超级用户写同 ID 的当天快照行 ----------
const sql = (q: string): string => execFileSync('psql', ['-X', '-At', '-v', 'ON_ERROR_STOP=1', '-c', q], { encoding: 'utf8' }).trim();
const TASKS = [R('7a1'), R('7a2'), R('7a3')];
const P3 = '59000000-0000-4000-8000-0000000000c3';
const OLD = R('7b1');      // P1 三天前的快照行（越日）
const SIB = R('7b2');      // P2 当天的快照行（别人的任务）
const planEvents = (task: string, kind: string): number =>
  Number(sql(`SELECT count(*) FROM insight.behavior_event WHERE kind = '${kind}' AND payload ->> 'plan_task_id' = '${task}'`));

test.beforeAll(() => {
  const today = `(now() AT TIME ZONE 'Asia/Shanghai')::date`;
  sql(`INSERT INTO platform.today_plan_item (id, student_id, plan_date, seq, subject, kind, source, node_id, estimated_minutes, entry_ref, rationale_code) VALUES
         ('${TASKS[0]}', '${P1}', ${today}, 1, 'aya', 'verify', 'verify', 'MATH.SYN.P1.R2', 5, '${R('402')}', NULL),
         ('${TASKS[1]}', '${P1}', ${today}, 2, 'aya', 'practice', 'practice', 'MATH.SYN.P1.N6', 8, '${R('106')}', NULL),
         ('${TASKS[2]}', '${P1}', ${today}, 3, 'aya', 'lesson', 'plan', NULL, 5, NULL, 'high_exam_weight'),
         ('${SIB}', '${P2}', ${today}, 1, 'aya', 'lesson', 'plan', NULL, 5, NULL, NULL)
       ON CONFLICT (id) DO NOTHING`);
  // 越日行只能绕过插入守卫（只收当天）写：同一会话里先切复制模式
  sql(`SET session_replication_role = replica;
       INSERT INTO platform.today_plan_item (id, student_id, plan_date, seq, subject, kind, source, estimated_minutes)
       VALUES ('${OLD}', '${P1}', ${today} - 3, 1, 'aya', 'lesson', 'plan', 5) ON CONFLICT (id) DO NOTHING`);
});

const marksOf = (page: Page, i: number) => page.getByTestId('today-orch-item').nth(i).getByTestId('today-plan-marks');
/** 点按钮并等 server action 的 POST 回来（失败静默时界面没有变化可等） */
async function submitAndWait(page: Page, click: () => Promise<void>): Promise<void> {
  await Promise.all([page.waitForResponse((r) => r.request().method() === 'POST' && new URL(r.url()).pathname === `/s/${P1}/today`), click()]);
}

test('“完成”“先跳过”：学生本人每条都有；同一票据重放不重复记；同一页面内完成→跳过→完成三次都入库、最后一条是完成；刷新后新票据', async ({ page }) => {
  const off = watchOrigin(page);
  await loginStudent(page, P1);
  await expect(page.getByTestId('today-plan-marks')).toHaveCount(3);
  await expect(page.getByTestId('today-plan-done').first()).toHaveText(TODAY_MARK.done);
  await expect(page.getByTestId('today-plan-skip').first()).toHaveText(TODAY_MARK.skip);
  await expect(page.getByTestId('today-plan-status').first()).toHaveText('');
  await onlyCopy(page);
  await axe(page);
  const before = [planEvents(TASKS[2]!, 'plan_done'), planEvents(TASKS[2]!, 'plan_skip')];
  const m = marksOf(page, 2);
  await expect(m).toHaveAttribute('data-hydrated', 'true');
  const first = await m.locator('input[name="ticket"]').inputValue();
  await m.getByTestId('today-plan-done').click();
  await expect(m.getByTestId('today-plan-status')).toHaveText(TODAY_MARK.recorded);
  await expect(m).toHaveAttribute('data-marked', 'done');
  await expect(m.getByTestId('today-plan-done')).toHaveAttribute('aria-pressed', 'true');
  expect(planEvents(TASKS[2]!, 'plan_done')).toBe(before[0]! + 1);
  await onlyCopy(page);
  // 同一张票据重放（模拟网络重试：把表单里的票据换回第一次用的那张再提交）：同一幂等键，库里仍只有一行，界面照样显示已记下
  const ticket = m.locator('input[name="ticket"]');
  const fresh = await ticket.inputValue();
  expect(fresh).not.toBe(first);
  await ticket.evaluate((el, v) => { (el as HTMLInputElement).value = v; }, first);
  await submitAndWait(page, () => m.getByTestId('today-plan-done').click());
  await expect(m).toHaveAttribute('data-marked', 'done');
  expect(planEvents(TASKS[2]!, 'plan_done')).toBe(before[0]! + 1);
  // 同一页面内改主意：完成 → 跳过 → 完成，每次换新票据（新键），三次都入库；库里最后一条与界面一致（完成）
  await submitAndWait(page, () => m.getByTestId('today-plan-skip').click());
  await expect(m).toHaveAttribute('data-marked', 'skip');
  expect(planEvents(TASKS[2]!, 'plan_skip')).toBe(before[1]! + 1);
  await submitAndWait(page, () => m.getByTestId('today-plan-done').click());
  await expect(m).toHaveAttribute('data-marked', 'done');
  await expect(m.getByTestId('today-plan-status')).toHaveText(TODAY_MARK.recorded);
  expect(planEvents(TASKS[2]!, 'plan_done')).toBe(before[0]! + 2);
  const lastKind = () => sql(`SELECT kind FROM insight.behavior_event WHERE payload ->> 'plan_task_id' = '${TASKS[2]}' ORDER BY occurred_at DESC, id DESC LIMIT 1`);
  expect(lastKind()).toBe('plan_done');
  // 刷新后拿到新票据：再点会多记一行（统计按最后一次，结果不变）；按钮照常出现，页面不读事件
  await page.reload();
  const again = marksOf(page, 2);
  await expect(again).toHaveAttribute('data-hydrated', 'true');
  await expect(again.getByTestId('today-plan-status')).toHaveText('');
  await submitAndWait(page, () => again.getByTestId('today-plan-done').click());
  await expect(again).toHaveAttribute('data-marked', 'done');
  expect(planEvents(TASKS[2]!, 'plan_done')).toBe(before[0]! + 3);
  expect(lastKind()).toBe('plan_done');
  const kinds = sql(`SELECT string_agg(kind || ':' || source_type::text, ',' ORDER BY occurred_at, id) FROM insight.behavior_event
                      WHERE payload ->> 'plan_task_id' = '${TASKS[2]}'`);
  // 0057：计划完成 / 跳过是学生自己标记的，来源记为学生自述（self）
  expect(kinds).toBe('plan_done:self,plan_skip:self,plan_done:self,plan_done:self');
  await expectNoDomLeak(page, { forbidden: INTERNAL });
  expect(off).toEqual([]);
});

test('“完成”“先跳过”：别人的、不存在的、越日的任务 ID 一律不记（静默）；家长经 BFF 写孩子的任务被拒', async ({ page }) => {
  await loginStudent(page, P1);
  const m = marksOf(page, 0);
  await expect(m).toHaveAttribute('data-hydrated', 'true');
  const valid = await m.locator('input[name="ticket"]').inputValue();
  // 篡改表单（模拟伪造请求）：换成别人的、越日的、不存在的任务 ID（票据绑定任务，server action 验票不过即止；绕过前端直接写 BFF 时
  // 由库函数 0056 拒绝，见 services/bff/tests/events.integration.test.ts），以及票据被改；一律不记、界面不提示
  for (const [task, tk] of [[SIB, valid], [OLD, valid], [R('7bf'), valid], [TASKS[0]!, `${valid.slice(0, -1)}${valid.endsWith('A') ? 'B' : 'A'}`]] as const) {
    const n = Number(sql(`SELECT count(*) FROM insight.behavior_event WHERE student_id = '${P1}' AND kind IN ('plan_done', 'plan_skip')`));
    await m.locator('input[name="task"]').evaluate((el, v) => { (el as HTMLInputElement).value = v; }, task);
    await m.locator('input[name="ticket"]').evaluate((el, v) => { (el as HTMLInputElement).value = v; }, tk);
    await submitAndWait(page, () => m.getByTestId('today-plan-done').click());
    await expect(m).toHaveAttribute('aria-busy', 'false');
    await expect(m.getByTestId('today-plan-status')).toHaveText('');
    await expect(m).toHaveAttribute('data-marked', '');
    expect(Number(sql(`SELECT count(*) FROM insight.behavior_event WHERE student_id = '${P1}' AND kind IN ('plan_done', 'plan_skip')`))).toBe(n);
    expect(planEvents(task, 'plan_done')).toBe(0);
  }
  // 家长会话直接调 BFF 事件接口写孩子当天的任务：403（只认学生本人），不写库
  await loginGuardian(page, 'gP');
  const s = await cookieSession(page);
  const r = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P1}/events`, {
    method: 'POST',
    headers: { 'x-sb-session': s.token, 'x-csrf-token': s.csrf, 'content-type': 'application/json', origin: PRACTICE_BASE },
    body: JSON.stringify({ kind: 'plan_done', payload: { plan_task_id: TASKS[1] }, occurred_at: new Date().toISOString(), idempotency_key: randomUUID() }),
  });
  expect(r.status).toBe(403);
  expect(planEvents(TASKS[1]!, 'plan_done')).toBe(0);
  // 家长端页面没有按钮
  for (const p of ['/family', `/family/${P1}`]) {
    await page.goto(p);
    await expect(page.getByTestId('today-plan-marks')).toHaveCount(0);
  }
});

test('“完成”“先跳过”：快照没冻结（not_frozen）时跨科列表照常显示、但没有按钮', async ({ page }) => {
  await loginStudent(page, P3);
  await expect(page.getByTestId('today-orch-item')).toHaveCount(1);
  await expect(page.getByTestId('today-plan-marks')).toHaveCount(0);
  await onlyCopy(page);
  const s = await cookieSession(page);
  const t = await (await fetch(`${PRACTICE_BFF}/bff/v0/students/${P3}/today`, { headers: { 'x-sb-session': s.token } })).json();
  expect(t.ordered.snapshot).toBe('not_frozen');
  expect(t.ordered.items.every((i: Record<string, unknown>) => !('task_id' in i))).toBe(true);
});

test('没有要做的：只显示占位；家长端没有跨科列表，BFF 对家长的 today 不带 ordered', async ({ page }) => {
  await loginStudent(page, P2);
  await expect(page.getByTestId('today-orch-empty')).toHaveText(TODAY_ORCH.empty);
  await expect(page.getByTestId('today-orch-item')).toHaveCount(0);
  // 只有未启用的来源（source_disabled）：不算 partial，不显示“有内容没列出”；没有截掉的也不显示截断
  await expect(page.getByTestId('today-orch-partial')).toHaveCount(0);
  await expect(page.getByTestId('today-orch-truncated')).toHaveCount(0);
  await onlyCopy(page);
  await axe(page);
  await loginGuardian(page, 'gP');
  for (const p of ['/family', `/family/${P1}`]) {
    await page.goto(p);
    await expect(page.getByTestId('today-orch')).toHaveCount(0);
  }
  const s = await cookieSession(page);
  const g = await fetch(`${PRACTICE_BFF}/bff/v0/students/${P1}/today`, { headers: { 'x-sb-session': s.token } });
  expect(g.status).toBe(200);
  expect(Object.keys(await g.json()).sort()).toEqual(['student_id', 'subjects']);
});
