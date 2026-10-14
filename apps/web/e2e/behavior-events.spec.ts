// 学生壳真实页面写会话行为事件（SIE-05 PR2，specs/sie05-silent-metrics/spec.md 范围第 4、5 条；审查后本 PR 只交付 session_start / session_end）。
// trust-off 项目，合成家庭 HG（db/seeds/dev/shell-synthetic.sql：家长 gH，孩子 GT、GR）。全程真实会话，只经 BFF 现有事件接口写入。
// 会话状态 cookie 带 HMAC 签名：端到端的前端用 playwright.config.ts 里固定的合成密钥，用例据此构造“已超时”“被篡改”的 cookie。
// 覆盖：进入壳写 session_start、壳内换页接着同一次会话；超时后再进入补记 session_end 并开始新会话（同一结束只记一次）；被篡改的 cookie 不补记；
// 页面开着无活动满 30 分钟写 session_end（Playwright 假时钟）；退出学生模式先补记 session_end 再删 cookie；
// 越权学生被拒（BFF 403、经本站路由也写不进）；家长会话 POST 不改 cookie、不写库；axe 不退化。
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { E2E_PRESENCE_KEY } from '../playwright.config.ts';
import { signPresence } from '../src/behavior/presence-server.ts';
import { BASE, BFF, cookieSession, loginGuardian, loginStudent } from './session.ts';

const GT = '5d000000-0000-4000-8000-0000000000c1';
const GR = '5d000000-0000-4000-8000-0000000000c2';
const COOKIE = 'sb_presence';

/** 以迁移执行者身份核对库里的结果（只读查询；一次性测试库） */
const sql = (q: string): string => execFileSync('psql', ['-X', '-At', '-c', q], { encoding: 'utf8' }).trim();
const count = (student: string, kind: string, extra = '') =>
  Number(sql(`SELECT count(*) FROM insight.behavior_event WHERE student_id = '${student}' AND kind = '${kind}' AND source_type = 'objective'
              AND recorded_by = 'student' ${extra}`));
const lastEntry = (student: string) =>
  sql(`SELECT payload->>'entry' FROM insight.behavior_event WHERE student_id = '${student}' AND kind = 'session_start' ORDER BY created_at DESC LIMIT 1`);

const isPresence = (u: string) => new URL(u).pathname.endsWith('/presence');
/** 等某一种报告的响应（换页时旧页面还会报一次 touch，按请求体里的 op 区分） */
const presenceDone = (page: Page, op: 'ping' | 'touch' | 'end' = 'ping') => page.waitForResponse((r) =>
  isPresence(r.url()) && r.request().method() === 'POST' && (r.request().postData() ?? '').includes(`"op":"${op}"`));

const axe = async (page: Page) => {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
};

const presenceCookie = async (page: Page) => (await page.context().cookies()).find((c) => c.name === COOKIE);
const setPresence = (page: Page, value: string) =>
  page.context().addCookies([{ name: COOKIE, value, url: BASE, httpOnly: true, sameSite: 'Lax' }]);

/** 用当前会话签一份“最近活动在 31 分钟前、开始已记下”的会话状态（同服务端的签名做法，src/behavior/presence-server.ts） */
async function staleCookie(page: Page, endKey: string): Promise<{ value: string; start: number; last: number }> {
  const { token } = await cookieSession(page);
  const now = Math.floor(Date.now() / 1000);
  const p = { student: GT, start: now - 3600, last: now - 1900, entry: 'today' as const, started: true, startKey: randomUUID(), endKey };
  return { value: signPresence(p, { key: Buffer.from(E2E_PRESENCE_KEY, 'utf8'), accountKind: 'student', token }), start: p.start, last: p.last };
}

/** 学生登录并等壳的第一次 ping 写完 session_start */
async function enter(page: Page): Promise<number> {
  const s0 = count(GT, 'session_start');
  const first = presenceDone(page);
  await loginStudent(page, GT);
  expect((await first).status()).toBe(204);
  await expect.poll(() => count(GT, 'session_start')).toBe(s0 + 1);
  return s0 + 1;
}

test('进入壳写 session_start（entry = today）；壳内换页接着同一次会话不再写；cookie 签名、HttpOnly；axe 不退化', async ({ page }) => {
  const ends = count(GT, 'session_end');
  const starts = await enter(page);
  expect(lastEntry(GT)).toBe('today');
  const c = await presenceCookie(page);
  expect(c?.httpOnly).toBe(true);
  expect(c?.sameSite).toBe('Lax');
  expect(c?.value ?? '').toMatch(new RegExp(`^v1\\.${GT}\\.\\d+\\.\\d+\\.today\\.1\\.[0-9a-f-]{36}\\.[0-9a-f-]{36}\\.[A-Za-z0-9_-]{43}$`));
  expect(await page.evaluate(() => document.cookie)).not.toContain(COOKIE);
  await axe(page);
  for (const p of [`/s/${GT}/star-map`, `/s/${GT}/learn`, `/s/${GT}/learn/math`]) {
    const done = presenceDone(page);
    await page.goto(p);
    expect((await done).status(), p).toBe(204);
  }
  await axe(page);
  expect(count(GT, 'session_start')).toBe(starts);
  expect(count(GT, 'session_end')).toBe(ends);
  // 库里的会话事件只有白名单键，不记哪一页
  expect(sql(`SELECT DISTINCT kind || ':' || (SELECT string_agg(k, ',') FROM jsonb_object_keys(payload) k) FROM insight.behavior_event
              WHERE student_id = '${GT}' AND kind IN ('session_start', 'session_end') ORDER BY 1`).split('\n').filter(Boolean)
    .every((r) => r === 'session_start:entry' || r === 'session_end:duration_sec')).toBe(true);
});

test('超时后再进入：补记上一段 session_end（时长 = 最近活动 − 开始，发生时间 = 最近活动），再开始新会话；同一结束只记一次', async ({ page }) => {
  const starts = await enter(page);
  const key = randomUUID();
  const v = await staleCookie(page, key);
  await setPresence(page, v.value);
  let done = presenceDone(page);
  await page.goto(`/s/${GT}/star-map`);
  expect((await done).status()).toBe(204);
  const ended = (k: string) => sql(`SELECT count(*) || ':' || coalesce(max((payload->>'duration_sec')::int), -1) || ':'
    || coalesce(max(abs(extract(epoch FROM occurred_at) - ${v.last}))::int, -1)
    FROM insight.behavior_event WHERE student_id = '${GT}' AND kind = 'session_end' AND idempotency_key = '${k}'`);
  await expect.poll(() => ended(key)).toBe(`1:${v.last - v.start}:0`);
  await expect.poll(() => count(GT, 'session_start')).toBe(starts + 1);
  expect(lastEntry(GT)).toBe('star_map');
  done = presenceDone(page);
  await page.goto(`/s/${GT}/today`);
  await done;
  expect(ended(key)).toBe(`1:${v.last - v.start}:0`);
  expect(count(GT, 'session_start')).toBe(starts + 1);
});

test('篡改：改过的会话状态验签失败、当作没有——不补记结束，只开始新会话', async ({ page }) => {
  const starts = await enter(page);
  const key = randomUUID();
  const v = await staleCookie(page, key);
  const parts = v.value.split('.');
  parts[2] = String(v.start - 3600);  // 改开始时间想拉长时长
  await setPresence(page, parts.join('.'));
  const done = presenceDone(page);
  await page.goto(`/s/${GT}/star-map`);
  await done;
  await expect.poll(() => count(GT, 'session_start')).toBe(starts + 1);
  await page.waitForTimeout(300);
  expect(count(GT, 'session_end', `AND idempotency_key = '${key}'`)).toBe(0);
  // 换了会话（另一次登录）签的 cookie 同样不认
  const other = await staleCookie(page, randomUUID());
  await enter(page);
  await setPresence(page, other.value);
  const again = presenceDone(page);
  await page.goto(`/s/${GT}/learn`);
  await again;
  await page.waitForTimeout(300);
  expect(count(GT, 'session_end', `AND idempotency_key = '${other.value.split('.')[7]}'`)).toBe(0);
});

test('页面开着无活动满 30 分钟：写 session_end、清掉会话状态；之后再有操作开始新会话（entry = subject）', async ({ page }) => {
  await enter(page);
  await page.clock.install();
  let done = presenceDone(page);
  await page.goto(`/s/${GT}/learn`);
  await done;
  const starts = count(GT, 'session_start');
  const key = randomUUID();
  const v = await staleCookie(page, key);
  await setPresence(page, v.value);
  done = presenceDone(page, 'end');
  await page.clock.fastForward('31:00');
  expect((await done).status()).toBe(204);
  await expect.poll(() => count(GT, 'session_end', `AND idempotency_key = '${key}' AND (payload->>'duration_sec')::int = ${v.last - v.start}`)).toBe(1);
  expect(await presenceCookie(page)).toBeUndefined();
  expect(count(GT, 'session_start')).toBe(starts);
  done = presenceDone(page);
  await page.keyboard.press('Shift');
  await done;
  await expect.poll(() => count(GT, 'session_start')).toBe(starts + 1);
  expect(lastEntry(GT)).toBe('subject');
  expect(count(GT, 'session_end', `AND idempotency_key = '${key}'`)).toBe(1);
});

test('退出学生模式：先补记这次会话的 session_end，再删会话状态 cookie', async ({ page }) => {
  await enter(page);
  const endKey = (await presenceCookie(page))!.value.split('.')[7]!;
  const exit = page.getByTestId('exit-student-mode');
  await expect(exit).toHaveAttribute('data-hydrated', 'true');
  await exit.getByRole('button').click();
  await expect(page).toHaveURL(/\/login$/);
  await expect.poll(() => count(GT, 'session_end', `AND idempotency_key = '${endKey}'`)).toBe(1);
  expect(await presenceCookie(page)).toBeUndefined();
});

test('越权：学生给别的学生写事件 BFF 403、经本站路由也写不进；家长会话 POST 不改 cookie、不写库，家长直接写 403', async ({ page }) => {
  const other = () => count(GR, 'session_start') + count(GR, 'session_end');
  const before = other();
  await enter(page);
  const s = await cookieSession(page);
  const post = (token: string, csrf: string, student: string, kind: string, payload: Record<string, unknown>) =>
    fetch(`${BFF}/bff/v0/students/${student}/events`, {
      method: 'POST',
      headers: { 'x-sb-session': token, 'x-csrf-token': csrf, origin: BASE, 'content-type': 'application/json' },
      body: JSON.stringify({ kind, payload, occurred_at: new Date().toISOString(), idempotency_key: randomUUID() }),
    });
  for (const [kind, payload] of [['session_start', { entry: 'today' }], ['session_end', { duration_sec: 10 }]] as const) {
    expect((await post(s.token, s.csrf, GR, kind, payload)).status, kind).toBe(403);
  }
  const ping = (student: string, csrf: string) => page.evaluate(async ([st, c]) => (await fetch(`/s/${st}/presence`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ csrf: c, op: 'ping', entry: 'today' }),
  })).status, [student, csrf] as const);
  const mine = (await presenceCookie(page))!.value;
  expect(await ping(GR, s.csrf)).toBe(204);
  expect((await presenceCookie(page))!.value, '别的学生的路径不改本人的 cookie').toBe(mine);
  await page.goto(`/s/${GR}/today`);
  await expect(page.getByTestId('student-mode')).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(other()).toBe(before);

  // 家长会话：带着一份（学生会话签的）会话状态 POST 到孩子的会话路由，不改 cookie、不写库
  const stale = await staleCookie(page, randomUUID());
  const gtStarts = count(GT, 'session_start');
  const gtEnds = count(GT, 'session_end');
  await loginGuardian(page, 'gH');
  const paths: string[] = [];
  page.on('request', (r) => paths.push(new URL(r.url()).pathname));
  await page.goto(`/family/${GT}`);
  await expect(page.getByTestId('child-page')).toBeVisible();
  await page.waitForTimeout(300);
  expect(paths.filter((p) => p.endsWith('/presence')), '家长页面不报会话').toEqual([]);
  await setPresence(page, stale.value);
  const g = await cookieSession(page);
  expect(await ping(GT, g.csrf)).toBe(204);
  expect(await page.evaluate(async ([st, c]) => (await fetch(`/s/${st}/presence`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ csrf: c, op: 'end' }),
  })).status, [GT, g.csrf] as const)).toBe(204);
  expect((await presenceCookie(page))?.value).toBe(stale.value);
  expect((await post(g.token, g.csrf, GT, 'session_start', { entry: 'today' })).status).toBe(403);
  await page.waitForTimeout(300);
  expect(count(GT, 'session_start')).toBe(gtStarts);
  expect(count(GT, 'session_end')).toBe(gtEnds);
});
