// 建档接口集成测试（SIE-01 验收：写入授权、互不可见、取值校验、404 不泄露、日志不带答案）。
// 一次性 Postgres，登录角色 bff_app 只继承 starbuddy_bff；合成家庭来自 db/seeds/dev/star-map-synthetic.sql（规则 8）。
// 4-H H2：挂在统一壳路由表上，身份只来自真实会话（tests/shell-harness.ts），写入另带 Origin 与 CSRF 令牌。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { fetchAs, startShell, type ShellHarness } from './shell-harness.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ROOT = new URL('../../../', import.meta.url);
const ID = {
  gS: '5a000000-0000-4000-8000-0000000000a1',  // S1、S2 的家长
  gO: '5a000000-0000-4000-8000-0000000000a2',  // 无关家庭的家长
  S1acc: '5a000000-0000-4000-8000-0000000000b1',
  S2acc: '5a000000-0000-4000-8000-0000000000b2',
  S1: '5a000000-0000-4000-8000-0000000000c1',
  S2: '5a000000-0000-4000-8000-0000000000c2',
  missing: '5a000000-0000-4000-8000-00000000ffff',
};

let su: pg.Pool;
let bff: pg.Pool;
let shell: ShellHarness;
let base = '';
const logs: Record<string, unknown>[] = [];

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 3 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
      CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
      CREATE ROLE scheduler_app LOGIN IN ROLE starbuddy_scheduler;
    END IF; END $$;`);
  const seeded = await su.query(`SELECT 1 FROM platform.household WHERE id = '5a000000-0000-4000-8000-0000000000d1'`);
  if (seeded.rowCount === 0) {
    const register = execFileSync(process.execPath, ['--no-warnings', 'scripts/register-content.ts'],
      { cwd: new URL('adapters/aya/', ROOT), encoding: 'utf8', maxBuffer: 16 << 20 });
    await su.query(register);
    const seeder = await su.connect();
    try {
      await seeder.query(`SET starbuddy.allow_synthetic_seed = 'on'`);
      await seeder.query(readFileSync(new URL('db/seeds/dev/star-map-synthetic.sql', ROOT), 'utf8'));
    } finally {
      seeder.release(true);
    }
  }
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  shell = await startShell(bff, su, { log: (l) => logs.push(l) });
  base = shell.base;
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await bff.end();
  await su.end();
});

const url = (student: string, sub = '') => `${base}/bff/v0/students/${student}/onboarding${sub}`;
const call = (method: string, u: string, account?: string, body?: unknown) => fetchAs(shell, method, u, account, body);
const view = async (student: string, account: string) => (await (await call('GET', url(student), account)).json()) as any;

test('学生写自述、家长写家长观察；互不可见', { skip: !HAS_DB }, async () => {
  let r = await call('PUT', url(ID.S1, '/answers'), ID.S1acc, { field_id: 'through.g9.near_term_goal', value: { text: '合成：期末数学提高十分' } });
  assert.equal(r.status, 200);
  r = await call('PUT', url(ID.S1, '/answers'), ID.S1acc, { field_id: 'outer.g1.weekly_available_time', value: { minutes: 300 } });
  assert.equal(r.status, 200);
  r = await call('PUT', url(ID.S1, '/answers'), ID.gS, { field_id: 'outer.g1.weekly_available_time', value: { minutes: 120 } });
  assert.equal(r.status, 200);
  const me = await view(ID.S1, ID.S1acc);
  assert.equal(me.role, 'self');
  assert.equal(me.reports['outer.g1.weekly_available_time'].minutes, 300);
  assert.ok(!JSON.stringify(me).includes('120'), '学生看不到家长说的 120 分钟');
  const parent = await view(ID.S1, ID.gS);
  assert.equal(parent.role, 'guardian');
  assert.equal(parent.reports['outer.g1.weekly_available_time'].minutes, 120);
  assert.ok(!JSON.stringify(parent).includes('期末数学'), '家长看不到学生自述原文');
  // 同一题，家长用学生专属字段、学生用家长专属字段：取值不合要求
  assert.equal((await call('PUT', url(ID.S1, '/answers'), ID.gS, { field_id: 'through.g9.near_term_goal', value: { text: 'x' } })).status, 422);
  assert.equal((await call('PUT', url(ID.S1, '/answers'), ID.S1acc, { field_id: 'outer.g8.cooperation_capacity', value: { level: 'regular' } })).status, 422);
});

test('兄妹、另一户家长、不存在、非法 ID：同一个 404', { skip: !HAS_DB }, async () => {
  const ref = await call('GET', url(ID.missing), ID.S1acc);
  assert.equal(ref.status, 404);
  const refBody = await ref.text();
  for (const [student, account] of [[ID.S2, ID.S1acc], [ID.S1, ID.gO], [ID.S1, ID.S2acc], ['not-a-uuid', ID.S1acc]] as const) {
    const r = await call('GET', url(student), account);
    assert.equal(r.status, 404, `${student} ${account}`);
    assert.equal(await r.text(), refBody);
  }
  const w = await call('PUT', url(ID.S1, '/answers'), ID.gO, { field_id: 'outer.g1.devices', value: { devices: ['phone'] } });
  assert.equal(w.status, 404);
  assert.equal(await w.text(), refBody);
});

test('考试记录、教材版本、进度；学生看不到排名区间', { skip: !HAS_DB }, async () => {
  const exam = { subject: 'aya', exam_type: 'midterm', exam_date: '2026-11-10', full_score: 100, raw_score: 92, rank_band: 'top30' };
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.gS, exam)).status, 200);
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.S1acc, { ...exam, raw_score: 95, rank_band: 'top10' })).status, 422, '学生不能录排名区间');
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.S1acc, { ...exam, raw_score: 95, rank_band: 7 })).status, 422, '非字符串不当成 null');
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.S1acc, { ...exam, raw_score: 95, rank_band: null })).status, 200);
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.S1acc, { ...exam, raw_score: 101 })).status, 422);
  assert.equal((await call('POST', url(ID.S1, '/exams'), ID.S1acc, { ...exam, exam_date: '11/10' })).status, 422);
  const me = await view(ID.S1, ID.S1acc);
  assert.equal(me.exams.length, 1);
  assert.ok(!('rank_band' in me.exams[0]));
  assert.equal((await view(ID.S1, ID.gS)).exams.length, 2);
  assert.equal((await call('PUT', url(ID.S1, '/textbooks'), ID.S1acc, { subject: 'aya', textbook_version: 'suke-2024' })).status, 200);
  assert.equal((await call('PUT', url(ID.S1, '/textbooks'), ID.gS, { subject: 'zhixing', textbook_version: null })).status, 200);
  assert.equal((await call('PUT', url(ID.S1, '/textbooks'), ID.gS, { subject: 'aya', textbook_version: 'Bad Code' })).status, 422);
  assert.deepEqual((await view(ID.S1, ID.S1acc)).textbooks, { aya: 'suke-2024' }, '学生只看到自己填的教材版本');
  assert.deepEqual((await view(ID.S1, ID.gS)).textbooks, { zhixing: null }, '家长只看到自己填的教材版本');
  assert.equal((await call('POST', url(ID.S1, '/progress'), ID.S1acc, { step: 'onboarding', status: 'started' })).status, 200);
  assert.deepEqual((await view(ID.S1, ID.S1acc)).progress, [{ step: 'onboarding', status: 'started' }]);
  assert.deepEqual((await view(ID.S1, ID.gS)).progress, [], '进度按填写人分开');
  assert.equal((await call('POST', url(ID.S1, '/progress'), ID.S1acc, { step: 'made_up', status: 'started' })).status, 422, '步骤名只收白名单');
});

test('请求体：不是 JSON 对象 422，超过 16 KB 413；方法不对 405；未登录 401', { skip: !HAS_DB }, async () => {
  assert.equal((await call('PUT', url(ID.S1, '/answers'), ID.S1acc, '{"field_id":')).status, 422);
  assert.equal((await call('PUT', url(ID.S1, '/answers'), ID.S1acc, '[1,2]')).status, 422);
  assert.equal((await call('PUT', url(ID.S1, '/answers'), ID.S1acc, { field_id: 'through.g9.near_term_goal', value: { text: 'x'.repeat(20000) } })).status, 413);
  assert.equal((await call('POST', url(ID.S1, '/answers'), ID.S1acc, {})).status, 405);
  assert.equal((await call('GET', url(ID.S1, '/exams'), ID.S1acc)).status, 405);
  assert.equal((await call('GET', url(ID.S1))).status, 401);
  const plain = await fetchAs(shell, 'PUT', url(ID.S1, '/answers'), ID.S1acc,
    JSON.stringify({ field_id: 'through.g9.near_term_goal', value: { text: '合成' } }), { 'content-type': 'text/plain' });
  assert.equal(plain.status, 415, 'Content-Type 必须是 application/json');
});

test('写入要 CSRF：缺令牌、令牌属于别的会话、Origin 不符都 403 CSRF，库里不变（H2 挂到路由表后与认证路由同一套）', { skip: !HAS_DB }, async () => {
  const body = { field_id: 'through.g9.near_term_goal', value: { text: '合成：CSRF 探测' } };
  const other = await shell.sessionFor(ID.S2acc);
  for (const extra of [{ 'x-csrf-token': '' }, { 'x-csrf-token': other.csrf }, { origin: 'http://evil.test' }] as Record<string, string>[]) {
    const r = await fetchAs(shell, 'PUT', url(ID.S1, '/answers'), ID.S1acc, body, extra);
    assert.equal(r.status, 403, JSON.stringify(extra));
    assert.equal(((await r.json()) as any).code, 'CSRF');
    assert.equal(r.headers.get('access-control-allow-origin'), null);
  }
  const { rows } = await su.query(`SELECT count(*)::int AS n FROM insight.self_report WHERE student_id = $1 AND value::text LIKE '%CSRF%'`, [ID.S1]);
  assert.equal(rows[0].n, 0);
});

test('同一字段并发提交：不出 500，最后只有一条当前答案', { skip: !HAS_DB }, async () => {
  const body = (i: number) => ({ field_id: 'middle.g6.learning_mode_preference', value: { mode: i % 2 ? 'challenge' : 'independent' } });
  const statuses = (await Promise.all(Array.from({ length: 8 }, (_, i) => call('PUT', url(ID.S2, '/answers'), ID.S2acc, body(i))))).map((r) => r.status);
  assert.ok(statuses.every((s) => s === 200 || s === 409), statuses.join(','));
  const { rows } = await su.query(`SELECT count(*)::int AS n FROM insight.self_report
    WHERE student_id = $1 AND field_id = 'middle.g6.learning_mode_preference' AND superseded_at IS NULL`, [ID.S2]);
  assert.equal(rows[0].n, 1);
});

test('访问日志不含答案、学生 ID 原文', { skip: !HAS_DB }, () => {
  const text = JSON.stringify(logs);
  for (const s of ['期末数学', 'suke-2024', ID.S1]) assert.ok(!text.includes(s), s);
  assert.ok(logs.some((l) => l['route'] === 'PUT /students/{student_id}/onboarding/answers'));
});
