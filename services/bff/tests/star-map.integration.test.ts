// 星图接口集成测试（spec 验收“授权”“不返回敏感字段”“不缓存”）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），登录角色 bff_app 只继承 starbuddy_bff，走真实 RLS。
// 4-H H2：星图挂在统一壳路由表上，身份只来自真实会话（短信捕获桩登录家长、家长打开学生模式），断言与 3-H 相同；
// 开发身份桩已删除，带 x-dev-account-id 一律按未登录处理。
// 数据：阿鸦内容登记（959 个能力点）+ db/seeds/dev/star-map-synthetic.sql，全部合成（规则 8）。
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { assertBffRole } from '../src/star-map/query.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ROOT = new URL('../../../', import.meta.url);
const NOW = new Date('2026-10-20T00:00:00Z');

const ID = {
  gS: '5a000000-0000-4000-8000-0000000000a1', // HS 家长
  gO: '5a000000-0000-4000-8000-0000000000a2', // 无关家庭的家长
  S1acc: '5a000000-0000-4000-8000-0000000000b1',
  S2acc: '5a000000-0000-4000-8000-0000000000b2',
  S3acc: '5a000000-0000-4000-8000-0000000000b3',
  S1: '5a000000-0000-4000-8000-0000000000c1',
  S2: '5a000000-0000-4000-8000-0000000000c2',
  S3: '5a000000-0000-4000-8000-0000000000c3',
  missing: '5a000000-0000-4000-8000-00000000ffff',
};

let su: pg.Pool;
let bff: pg.Pool;
let shell: ShellHarness;
let base = '';
let ayaCount = 0;
const logs: Record<string, unknown>[] = [];

/** 以账号的真实会话读星图；account 为空时不带会话 */
async function starMap(student: string, account?: string, headers: Record<string, string> = {}): Promise<Response> {
  const h: Record<string, string> = { ...headers };
  if (account) h['x-sb-session'] = (await shell.sessionFor(account)).token;
  return fetch(`${base}/bff/v0/students/${student}/star-map`, { headers: h });
}

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
      seeder.release(true);  // 会话级设置不带回连接池
    }
  }
  ayaCount = Number((await su.query(`SELECT count(*) FROM platform.skill_node WHERE subject = 'aya'`)).rows[0].count);
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  shell = await startShell(bff, su, { log: (l) => logs.push(l), now: () => NOW });
  base = shell.base;
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await bff.end();
  await su.end();
});

const counts = (body: any) =>
  Object.fromEntries(body.skies.map((s: any) => [s.subject, s.summary]));

test('学生本人：数学与道法两片星空，计数与种子一致；被取代的历史不参与', { skip: !HAS_DB }, async () => {
  const r = await starMap(ID.S1, ID.S1acc);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  const body = (await r.json()) as any;
  assert.equal(body.student_id, ID.S1);
  assert.deepEqual(body.skies.map((s: any) => s.subject), ['aya', 'zhixing']);
  assert.deepEqual(counts(body), {
    aya: { unlit: ayaCount - 6, half: 3, lit: 3 },
    zhixing: { unlit: 1, half: 1, lit: 2 },
  });
  const aya = body.skies[0].stars;
  assert.equal(aya.length, ayaCount);
  // summary 与逐颗星一致
  for (const sky of body.skies) {
    for (const d of ['unlit', 'half', 'lit']) {
      assert.equal(sky.stars.filter((s: any) => s.display === d).length, sky.summary[d]);
    }
  }
  // n7：历史是 transfer，当前是 stable → 半亮，不是亮
  const n7 = (await su.query(`SELECT skill_local_id FROM platform.mastery_estimate WHERE id = '5a000000-0000-4000-8000-0000000000e7'`)).rows[0].skill_local_id;
  assert.equal(aya.find((s: any) => s.skill === `aya:${n7}`).display, 'half');
  // 保持层与待验证
  assert.equal(aya.filter((s: any) => s.retained).length, 1);
  assert.equal(aya.filter((s: any) => s.needs_verification).length, 2);
  assert.equal(body.skies[1].stars.find((s: any) => s.skill === 'zhixing:SYN-ZX-04').retained, true);
});

test('响应只含展示字段：没有 p_mastery、confidence、evidence_ids、algorithm_version', { skip: !HAS_DB }, async () => {
  const body = (await (await starMap(ID.S1, ID.S1acc)).json()) as any;
  assert.deepEqual(Object.keys(body).sort(), ['generated_at', 'skies', 'student_id']);
  for (const sky of body.skies) {
    assert.deepEqual(Object.keys(sky).sort(), ['enrollment_status', 'stars', 'subject', 'summary']);
    assert.deepEqual(Object.keys(sky.summary).sort(), ['half', 'lit', 'unlit']);
    for (const star of sky.stars) {
      assert.deepEqual(Object.keys(star).sort(), ['display', 'label_code', 'needs_verification', 'retained', 'skill']);
    }
  }
  const text = JSON.stringify(body);
  for (const k of ['p_mastery', 'confidence', 'evidence_ids', 'algorithm_version', 'evidence']) {
    assert.ok(!text.includes(k), k);
  }
});

test('已绑定家长看到的与学生本人相同；同一家长的两个孩子互不混入', { skip: !HAS_DB }, async () => {
  const self = await (await starMap(ID.S1, ID.S1acc)).text();
  const parent = await (await starMap(ID.S1, ID.gS)).text();
  assert.equal(parent, self);
  const s2 = (await (await starMap(ID.S2, ID.gS)).json()) as any;
  assert.deepEqual(s2.skies.map((s: any) => s.subject), ['aya']);  // 道法已暂停，默认不显示
  assert.deepEqual(counts(s2), { aya: { unlit: ayaCount - 4, half: 2, lit: 2 } });
});

test('兄妹互看、另一户家长、不存在、非法 ID：都是 404，响应体逐字节相同', { skip: !HAS_DB }, async () => {
  const missing = await starMap(ID.missing, ID.S1acc);
  assert.equal(missing.status, 404);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
  const ref = await missing.text();
  // 响应头也要一致（去掉每次都不同的 date）
  const headersOf = (r: Response) => [...r.headers].filter(([k]) => k !== 'date').sort();
  const refHeaders = headersOf(missing);
  for (const [student, account] of [[ID.S2, ID.S1acc], [ID.S1, ID.S2acc], [ID.S1, ID.gO], [ID.S3, ID.gS], ['not-a-uuid', ID.gS], ['%E0%A4%A', ID.gS]]) {
    const r = await starMap(student!, account);
    assert.equal(r.status, 404, `${student} as ${account}`);
    assert.deepEqual(headersOf(r), refHeaders, `${student} as ${account}`);
    assert.equal(await r.text(), ref);
  }
});

test('未登录：没有会话、令牌格式不对、令牌不存在都是 401', { skip: !HAS_DB }, async () => {
  for (const token of [undefined, 'not-a-token', '', 'ab'.repeat(32)]) {
    const r = await fetch(`${base}/bff/v0/students/${ID.S1}/star-map`, { headers: token === undefined ? {} : { 'x-sb-session': token } });
    assert.equal(r.status, 401, String(token));
    assert.equal(r.headers.get('cache-control'), 'no-store');
  }
});

test('只有 GET；表外路径 404', { skip: !HAS_DB }, async () => {
  const r = await callAs(base, 'POST', `/students/${ID.S1}/star-map`, await shell.sessionFor(ID.S1acc));
  assert.equal(r.status, 405);
  assert.equal((await fetch(`${base}/bff/v0/students`)).status, 405);  // 路由表里只有 POST /students（建档）
  assert.equal((await fetch(`${base}/bff/v0/nothing-here`)).status, 404);
  assert.equal((await fetch(`${base}/star-map/${ID.S1}`)).status, 404);
});

test('开发身份桩已删除：只带 x-dev-account-id（含本机、带或不带转发头）一律 401；带着会话时这个头被忽略', { skip: !HAS_DB }, async () => {
  for (const extra of [{}, { 'x-forwarded-for': '203.0.113.9' }, { forwarded: 'for=203.0.113.9' }] as Record<string, string>[]) {
    const r = await fetch(`${base}/bff/v0/students/${ID.S1}/star-map`, { headers: { 'x-dev-account-id': ID.S1acc, ...extra } });
    assert.equal(r.status, 401);
  }
  // 以 S2 的会话、头里冒充 S1：仍按 S2 授权（读 S1 是 404，读 S2 是 200）
  assert.equal((await starMap(ID.S1, ID.S2acc, { 'x-dev-account-id': ID.S1acc })).status, 404);
  assert.equal((await starMap(ID.S2, ID.S2acc, { 'x-dev-account-id': ID.S1acc })).status, 200);
});

test('连接角色核对：只继承 starbuddy_bff 的登录角色通过；超级用户、绕过 RLS 的角色拒绝', { skip: !HAS_DB }, async () => {
  await assertBffRole(bff);
  await assert.rejects(() => assertBffRole(su), /BFF 连接角色/);
  // 只继承 starbuddy_bff、但带 BYPASSRLS 的登录角色：RLS 对它无效，必须拒绝
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_bypass_app') THEN
      CREATE ROLE bff_bypass_app LOGIN BYPASSRLS IN ROLE starbuddy_bff;
    END IF; END $$;`);
  const bypass = new pg.Pool({ user: 'bff_bypass_app', max: 1 });
  try {
    await assert.rejects(() => assertBffRole(bypass), /BFF 连接角色/);
  } finally {
    await bypass.end();
  }
});

test('绑定撤销后立刻看不到；停用的学生账号的会话失效', { skip: !HAS_DB }, async () => {
  // 另建一位合成家长绑定 S3，确认能看，再撤销（同一个会话，下一次请求就 404）
  const g = '5a000000-0000-4000-8000-0000000000a9';
  await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian') ON CONFLICT DO NOTHING`, [g]);
  await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  VALUES ($1, $2, 'invite') ON CONFLICT DO NOTHING`, [g, ID.S3]);
  assert.equal((await starMap(ID.S3, g)).status, 200);
  await su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1 AND student_id = $2`, [g, ID.S3]);
  assert.equal((await starMap(ID.S3, g)).status, 404);
  assert.equal((await starMap(ID.S3, ID.gO)).status, 200);  // 另一位家长不受影响

  assert.equal((await starMap(ID.S3, ID.S3acc)).status, 200);
  await su.query(`UPDATE platform.account SET status = 'disabled' WHERE id = $1`, [ID.S3acc]);
  try {
    // 真实会话下，账号停用后会话本身失效（401，比开发身份桩时的 404 更早拦下）
    assert.equal((await starMap(ID.S3, ID.S3acc)).status, 401);
  } finally {
    await su.query(`UPDATE platform.account SET status = 'active' WHERE id = $1`, [ID.S3acc]);
    shell.forget(ID.S3acc);
  }
});

test('没有选科的学生：200，星空为空', { skip: !HAS_DB }, async () => {
  const acc = '5a000000-0000-4000-8000-0000000000b8';
  const stu = '5a000000-0000-4000-8000-0000000000c8';
  await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student') ON CONFLICT DO NOTHING`, [acc]);
  await su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                  VALUES ($1, $2, '5a000000-0000-4000-8000-0000000000d2', 8, 'nanjing', 'bubian-2024', '2013-06-01')
                  ON CONFLICT DO NOTHING`, [stu, acc]);
  // 打开学生模式要有已绑定的家长：用该户的创建人
  await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                  SELECT h.created_by_account, $1, 'household_creator' FROM platform.household h
                   WHERE h.id = '5a000000-0000-4000-8000-0000000000d2'
                     AND NOT EXISTS (SELECT 1 FROM platform.guardian_student_link l WHERE l.student_id = $1 AND l.status = 'active')`, [stu]);
  const r = await starMap(stu, acc);
  assert.equal(r.status, 200);
  assert.deepEqual(((await r.json()) as any).skies, []);
});

test('访问日志只有请求 ID、路由模板、状态码、耗时、账号类型与 student_id 的带密钥 HMAC', { skip: !HAS_DB }, async () => {
  assert.ok(logs.length > 0);
  const text = JSON.stringify(logs);
  for (const raw of [ID.S1, ID.S2, ID.S3, ID.S1acc, ID.gS, 'SYN-ZX', '"lit"', 'unlit']) {
    assert.ok(!text.includes(raw), raw);
  }
  for (const l of logs) {
    for (const k of Object.keys(l)) {
      assert.ok(['request_id', 'route', 'status', 'student', 'duration_ms', 'error_class', 'account_kind', 'outcome', 'sms'].includes(k), k);
    }
  }
  assert.ok(logs.some((l) => l['route'] === 'GET /students/{student_id}/star-map'));
});
