// 家长诉求接口集成测试（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14；迁移 0053；接口定义 openapi/family-goals.v0.yaml）。
// 一次性 Postgres，登录角色 bff_app 只继承 starbuddy_bff；真实会话（tests/shell-harness.ts），写入带 Origin 与 CSRF 令牌。
// 覆盖：开关关时路由不注册（与表外 404 逐字节相同）；家长按绑定读写；学生会话一律 404（不查库、不写库）；别家与不存在同一个 404；
// 两位家长各存各的、更正为新的一版；请求体校验；补充说明命中禁用推断词表 422 不存；初二不能设学校；日志不带诉求内容。
// 全部合成数据（规则 8）：本文件自建一户（两位家长、初三与初二各一名学生）与另一户；招录库的合成学校以超级用户、关闭触发器写入
// （发布本身的签字流程由 db/tests 覆盖，这里只要一所“已发布”的学校）。
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import pg from 'pg';
import { loadAuthConfig } from '../src/http/config.ts';
import { FAMILY_GOALS_ROUTES, familyGoalsReply, familyGoalsWrite } from '../src/http/family-goals-routes.ts';
import { P } from '../src/http/problems.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ID = {
  g1: '6f530000-0000-4000-8000-000000000001',   // 户一的创建者
  g2: '6f530000-0000-4000-8000-000000000002',   // 户一的第二位家长
  gO: '6f530000-0000-4000-8000-000000000003',   // 另一户的家长
  a9: '6f530000-0000-4000-8000-000000000009',
  a8: '6f530000-0000-4000-8000-000000000008',
  aO: '6f530000-0000-4000-8000-00000000000a',
  h1: '6f530000-0000-4000-8000-0000000000a1',
  h2: '6f530000-0000-4000-8000-0000000000a2',
  x9: '6f530000-0000-4000-8000-000000000c09',   // 初三
  x8: '6f530000-0000-4000-8000-000000000c08',   // 初二
  xO: '6f530000-0000-4000-8000-000000000c0a',
  doc: '6f530000-0000-4000-8000-0000000005d1',
  school: '6f530000-0000-4000-8000-0000000005c1',
  missing: '6f530000-0000-4000-8000-00000000ffff',
};

let su: pg.Pool;
let bff: pg.Pool;
let on: ShellHarness;
let off: ShellHarness;
const logs: Record<string, unknown>[] = [];

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 3 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
      CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
    END IF; END $$;`);
  const seeded = await su.query('SELECT 1 FROM platform.household WHERE id = $1', [ID.h1]);
  if (seeded.rowCount === 0) {
    await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian'), ($3, 'guardian'),
                      ($4, 'student'), ($5, 'student'), ($6, 'student')`, [ID.g1, ID.g2, ID.gO, ID.a9, ID.a8, ID.aO]);
    await su.query('INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2), ($3, $4)', [ID.h1, ID.g1, ID.h2, ID.gO]);
    await su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
                      ($1, $2, $3, 9, 'nanjing', 'bubian-2024', '2012-02-01'), ($4, $5, $3, 8, 'nanjing', 'bubian-2024', '2013-02-01'),
                      ($6, $7, $8, 9, 'nanjing', 'bubian-2024', '2012-03-01')`, [ID.x9, ID.a9, ID.h1, ID.x8, ID.a8, ID.xO, ID.aO, ID.h2]);
    await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
                      ($1, $2, 'household_creator'), ($1, $3, 'household_creator'), ($4, $2, 'invite'), ($5, $6, 'household_creator')`,
                   [ID.g1, ID.x9, ID.x8, ID.g2, ID.gO, ID.xO]);
    await su.query(`INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ($1, 'aya'), ($1, 'wenli'), ($2, 'aya'), ($3, 'aya')`,
                   [ID.x9, ID.x8, ID.xO]);
    // 一所“已发布”的合成学校（与学生同地区）：只为这里的接口测试，关闭触发器直写（签字发布流程见 db/tests/79zb、77）
    const c = await su.connect();
    try {
      await c.query('BEGIN');
      await c.query("SET LOCAL session_replication_role = 'replica'");
      await c.query(`INSERT INTO policy.source_domain (host, issuer, added_by) VALUES ('jyj.nanjing-bff.example', '合成', 'synthetic') ON CONFLICT DO NOTHING`);
      await c.query(`INSERT INTO policy.source_document (id, city, issuer, doc_number, title, published_on, url, applies_from_cohort, last_verified_on,
                       status, reviewers, reviewed_at)
                     VALUES ($1, 'nanjing', '合成', '合成〔2026〕99号', '合成名单（非真实）', '2026-05-01', 'https://jyj.nanjing-bff.example/doc/99', 2026,
                             '2026-09-01', 'published', '{syn-a,syn-b}', now())`, [ID.doc]);
      await c.query(`INSERT INTO policy.school (id, city, code, public_name, level_code, source_document_id, review_status, reviewers, reviewed_at)
                     VALUES ($1, 'nanjing', 'synth-bff-goal-1', '合成高中（接口测试）', 'level_a', $2, 'published', '{syn-a,syn-b}', now())`, [ID.school, ID.doc]);
      await c.query('COMMIT');
    } finally {
      c.release(true);
    }
  }
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  on = await startShell(bff, su, { log: (l) => logs.push(l), familyGoals: { enabled: true } });
  off = await startShell(bff, su, { log: () => {} });
});

after(async () => {
  if (!HAS_DB) return;
  await on.close();
  await off.close();
  await bff.end();
  await su.end();
});

const path = (student: string) => `/students/${student}/family-goals`;
const goal = (over: Record<string, unknown> = {}) => ({ focus_subjects: ['aya'], target_scores: [], school_id: null, note: null, ...over });
const rows = async (student: string) => Number((await su.query('SELECT count(*) AS n FROM insight.guardian_goal WHERE student_id = $1', [student])).rows[0].n);

test('开关关闭：两条路由都不注册，与表外路径同一个 404（家长、学生、未登录都一样）', { skip: !HAS_DB }, async () => {
  const g = await off.sessionFor(ID.g1);
  const unknown = await callAs(off.base, 'GET', `/students/${ID.x9}/no-such-route`, g);
  for (const [m, body] of [['GET', undefined], ['PUT', goal()]] as const) {
    const r = await callAs(off.base, m, path(ID.x9), g, body);
    assert.equal(r.status, 404, m);
    assert.equal(r.text, unknown.text, `${m} 与表外路径逐字节相同`);
  }
  assert.equal((await callAs(off.base, 'GET', path(ID.x9), null)).status, 404, '未登录同样 404（没有注册就不会先要会话）');
});

test('家长按绑定读写：提交即新的一版，GET 读到当前版与历史；来源 guardian', { skip: !HAS_DB }, async () => {
  const g = await on.sessionFor(ID.g1);
  let r = await callAs(on.base, 'GET', path(ID.x9), g);
  assert.equal(r.status, 200, r.text);
  assert.equal(r.body.current, null);
  assert.equal(r.body.can_set_school, true);
  assert.deepEqual(r.body.enrolled_subjects, ['wenli', 'aya']);
  assert.deepEqual(r.body.school_options, [{ id: ID.school, public_name: '合成高中（接口测试）' }]);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  r = await callAs(on.base, 'PUT', path(ID.x9), g, goal({ focus_subjects: ['aya', 'wenli'],
    target_scores: [{ subject: 'aya', full_score: 120, target_score: 105.5 }], school_id: ID.school, note: '  合成说明：先补函数  ' }));
  assert.equal(r.status, 200, r.text);
  assert.deepEqual(r.body, { version: 1 });
  r = await callAs(on.base, 'PUT', path(ID.x9), g, goal({ note: '合成说明：第二版' }));
  assert.deepEqual(r.body, { version: 2 });
  r = await callAs(on.base, 'GET', path(ID.x9), g);
  assert.equal(r.body.current.version, 2);
  assert.equal(r.body.current.source_type, 'guardian');
  assert.equal(r.body.current.note, '合成说明：第二版');
  assert.equal(r.body.history.length, 1);
  assert.equal(r.body.history[0].note, '合成说明：先补函数', '首尾空白去掉后存');
  assert.equal(r.body.history[0].school_id, ID.school);
  assert.deepEqual(r.body.history[0].target_scores, [{ subject: 'aya', full_score: 120, target_score: 105.5 }]);
});

test('两位家长各存各的：第二位家长看不到第一位写的，各自的当前版都在', { skip: !HAS_DB }, async () => {
  const g2 = await on.sessionFor(ID.g2);
  let r = await callAs(on.base, 'GET', path(ID.x9), g2);
  assert.equal(r.status, 200);
  assert.equal(r.body.current, null, '看不到另一位家长的诉求');
  r = await callAs(on.base, 'PUT', path(ID.x9), g2, goal({ focus_subjects: ['wenli'], note: '合成说明：第二位家长' }));
  assert.deepEqual(r.body, { version: 1 });
  const { rows: cur } = await su.query(`SELECT guardian_account_id::text AS g FROM insight.guardian_goal
                                          WHERE student_id = $1 AND superseded_at IS NULL ORDER BY 1`, [ID.x9]);
  assert.deepEqual(cur.map((x) => x.g), [ID.g1, ID.g2].sort());
  r = await callAs(on.base, 'GET', path(ID.x9), await on.sessionFor(ID.g1));
  assert.ok(!r.text.includes('第二位家长'));
});

test('学生会话一律 404：与不存在的学生逐字节相同，不写库（含带合法 CSRF 的 PUT）', { skip: !HAS_DB }, async () => {
  const s = await on.sessionFor(ID.a9);
  const sib = await on.sessionFor(ID.a8);
  const g = await on.sessionFor(ID.g1);
  const missing = await callAs(on.base, 'GET', path(ID.missing), g);
  assert.equal(missing.status, 404);
  const before = await rows(ID.x9);
  for (const [who, sess] of [['本人', s], ['兄妹', sib]] as const) {
    const get = await callAs(on.base, 'GET', path(ID.x9), sess);
    assert.equal(get.status, 404, who);
    assert.equal(get.text, missing.text, `${who} GET 与不存在逐字节相同`);
    assert.ok(!get.text.includes('合成说明'));
    const put = await callAs(on.base, 'PUT', path(ID.x9), sess, goal({ note: '学生不能写' }));
    assert.equal(put.status, 404, who);
    assert.equal(put.text, missing.text);
  }
  assert.equal(await rows(ID.x9), before, '学生的请求一行也没写');
  // 学生会话在 handler 入口就被拒：没有进入诉求逻辑（访问日志没有 route_group，handler 只在通过之后才记）
  const studentLines = logs.filter((l) => l['account_kind'] === 'student' && String(l['route']).endsWith('/family-goals'));
  assert.ok(studentLines.length >= 4);
  assert.ok(studentLines.every((l) => l['route_group'] === undefined), '学生请求进入了诉求逻辑');
  const own = await callAs(on.base, 'GET', path(ID.x8), sib);
  assert.equal(own.status, 404, '学生读自己的档案同样 404');
});

test('学生会话在请求体、词表、CSRF、限流之前就 404：非法请求体、坏 JSON、命中词表、缺 CSRF、错 Origin、超限流都与“不存在”逐字节相同', { skip: !HAS_DB }, async () => {
  const s = await on.sessionFor(ID.a9);
  const g = await on.sessionFor(ID.g1);
  const missing = await callAs(on.base, 'GET', path(ID.missing), g);
  assert.equal(missing.text, P.NOT_FOUND);
  const before = await rows(ID.x9);
  const cases: [string, Awaited<ReturnType<typeof callAs>>][] = [
    ['形状不合的请求体', await callAs(on.base, 'PUT', path(ID.x9), s, { focus_subjects: 'x', extra: 1 })],
    ['坏 JSON', await callAs(on.base, 'PUT', path(ID.x9), s, undefined, { rawBody: '{not json' })],
    ['不是 JSON 的类型', await callAs(on.base, 'PUT', path(ID.x9), s, undefined, { rawBody: 'a=1', contentType: 'text/plain' })],
    ['命中禁用推断词表', await callAs(on.base, 'PUT', path(ID.x9), s, goal({ note: '孩子有点懒惰' }))],
    ['缺 CSRF', await callAs(on.base, 'PUT', path(ID.x9), s, goal(), { csrf: null })],
    ['错 Origin', await callAs(on.base, 'PUT', path(ID.x9), s, goal(), { origin: 'http://evil.test' })],
    ['别的学生的档案', await callAs(on.base, 'PUT', path(ID.xO), s, goal())],
  ];
  // 超限流：把写入桶临时压到 1 次；学生连发三次仍是 404（不计数、不 429），同一设置下家长第二次就 429（证明限流确实开着）
  const bucket = on.config.rate_limits['family_goals_write.link.hour']!;
  const saved = bucket.limit;
  bucket.limit = 1;
  try {
    for (let i = 0; i < 3; i++) cases.push([`超限流第 ${i + 1} 次`, await callAs(on.base, 'PUT', path(ID.x9), s, goal())]);
    const g2 = await on.sessionFor(ID.g2);
    await callAs(on.base, 'PUT', path(ID.x8), g2, goal());  // g2 与 x8 没有绑定：404，但同样计数
    assert.equal((await callAs(on.base, 'PUT', path(ID.x8), g2, goal())).status, 429, '同一设置下家长超限得到 429');
  } finally {
    bucket.limit = saved;
  }
  for (const [what, r] of cases) {
    assert.equal(r.status, 404, what);
    assert.equal(r.text, missing.text, `${what}：与不存在逐字节相同`);
    assert.equal(r.headers.get('content-type'), missing.headers.get('content-type'), what);
  }
  assert.equal(await rows(ID.x9), before, '学生的请求一行也没写');
});

test('问题码映射：年级不在 7–9 → 409 GOAL_GRADE_NOT_SUPPORTED；24 小时写入上限 → 429；词表读不到 → 503（不碰库）', { skip: !HAS_DB }, async () => {
  const gO = await on.sessionFor(ID.gO);
  // 模拟已毕业（超级用户改届别，来源一并改；测完改回）
  await su.query(`UPDATE platform.student_profile SET exam_cohort = exam_cohort - 1, exam_cohort_source = 'staff_correction' WHERE id = $1`, [ID.xO]);
  try {
    const r = await callAs(on.base, 'PUT', path(ID.xO), gO, goal());
    assert.equal(r.status, 409);
    assert.equal(r.text, P.GOAL_GRADE_NOT_SUPPORTED);
  } finally {
    await su.query(`UPDATE platform.student_profile SET exam_cohort = exam_cohort + 1, exam_cohort_source = 'derived' WHERE id = $1`, [ID.xO]);
  }
  // 写入上限：超级用户补 20 条 24 小时内的历史版（只追加守卫只管更新与删除），第 21 次提交由库以 54000 拒绝
  await su.query(`INSERT INTO insight.guardian_goal (student_id, guardian_account_id, version, created_at, superseded_at)
                  SELECT $1, $2, 100 + i, now() - interval '1 hour', now() - interval '1 hour' FROM generate_series(1, 20) i`, [ID.xO, ID.gO]);
  const capped = await callAs(on.base, 'PUT', path(ID.xO), gO, goal());
  assert.equal(capped.status, 429);
  assert.equal(capped.text, P.WRITE_CAP);
  // 词表读不到：handler 直接 503，不调库（pool 一旦被用就抛错）
  const pool = { connect: () => { throw new Error('不应碰库'); }, query: () => { throw new Error('不应碰库'); } };
  const ctx = { deps: { pool }, params: { student_id: ID.x9 }, body: goal({ note: '合成' }), session: { kind: 'guardian', accountId: ID.g1 },
                note: () => {} } as never;
  const r503 = await familyGoalsWrite(() => { throw new Error('词表读不到'); })(ctx);
  assert.deepEqual([r503.status, r503.body], [503, P.TRUST_UNAVAILABLE]);
  // 其余映射逐项
  for (const [kind, body] of [['not_found', P.NOT_FOUND], ['invalid', P.INVALID], ['conflict', P.CONFLICT], ['too_many', P.WRITE_CAP],
                              ['school_not_open', P.SCHOOL_NOT_OPEN], ['grade_out_of_range', P.GOAL_GRADE_NOT_SUPPORTED]] as const) {
    assert.equal(familyGoalsReply({ kind } as never).body, body, kind);
  }
});

test('别家家长、不存在的学生、未登录：同一个 404 / 401，不写库', { skip: !HAS_DB }, async () => {
  const gO = await on.sessionFor(ID.gO);
  const g = await on.sessionFor(ID.g1);
  const missing = await callAs(on.base, 'PUT', path(ID.missing), g, goal());
  assert.equal(missing.status, 404);
  const before = await rows(ID.x9);
  const other = await callAs(on.base, 'PUT', path(ID.x9), gO, goal());
  assert.equal(other.status, 404);
  assert.equal(other.text, missing.text);
  assert.equal((await callAs(on.base, 'GET', path(ID.x9), gO)).text, (await callAs(on.base, 'GET', path(ID.missing), gO)).text);
  assert.equal(await rows(ID.x9), before);
  assert.equal((await callAs(on.base, 'GET', path(ID.x9), null)).status, 401);
  const noCsrf = await callAs(on.base, 'PUT', path(ID.x9), g, goal(), { csrf: null });
  assert.equal(noCsrf.status, 403, '写入要 CSRF 令牌');
});

test('请求体校验：形状不合 422（不调库）；学科不在读、学校不可选由库拒绝，同样 422', { skip: !HAS_DB }, async () => {
  const g = await on.sessionFor(ID.g1);
  const bad: Record<string, unknown>[] = [
    { focus_subjects: ['aya'] },                                                          // 缺键
    { ...goal(), extra: 1 },                                                              // 未知键
    goal({ focus_subjects: ['aya', 'wenli', 'zhixing'] }),                                // 超过 2 科
    goal({ focus_subjects: ['aya', 'aya'] }),
    goal({ focus_subjects: ['math'] }),                                                   // 不是平台学科码
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 101 }] }),     // 超过满分
    goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 95.25 }] }),   // 两位小数
    goal({ target_scores: [{ subject: 'aya', full_score: 100 }] }),
    goal({ school_id: 'not-a-uuid' }),
    goal({ note: 'x'.repeat(201) }),
    goal({ note: '换行\n不行' }),
    goal({ note: '零宽​不行' }),
    goal({ note: 12 }),
  ];
  for (const b of bad) {
    const r = await callAs(on.base, 'PUT', path(ID.x9), g, b);
    assert.equal(r.status, 422, JSON.stringify(b));
    assert.equal(r.text, P.INVALID);
  }
  for (const b of [goal({ focus_subjects: ['zhixing'] }), goal({ target_scores: [{ subject: 'echo', full_score: 100, target_score: 90 }] }),
                   goal({ school_id: ID.missing })]) {
    const r = await callAs(on.base, 'PUT', path(ID.x9), g, b);
    assert.equal(r.status, 422, JSON.stringify(b));
  }
});

test('补充说明命中禁用推断词表：422 TEXT_NOT_ACCEPTED，不存', { skip: !HAS_DB }, async () => {
  const g = await on.sessionFor(ID.g1);
  const before = await rows(ID.x9);
  for (const note of ['孩子有点懒惰', '孩子是中等生', '你们学校的学生一般数学较弱']) {
    const r = await callAs(on.base, 'PUT', path(ID.x9), g, goal({ note }));
    assert.equal(r.status, 422, note);
    assert.equal(r.text, P.TEXT_NOT_ACCEPTED);
  }
  assert.equal(await rows(ID.x9), before, '命中词表的一行也没存');
  const ok = await callAs(on.base, 'PUT', path(ID.x9), g, goal({ note: '每周可用时间由家长填写，可以随时修改或删除' }));
  assert.equal(ok.status, 200, '词表的允许例句不被误伤');
});

test('关注学校：初二设学校 409 SCHOOL_NOT_OPEN_FOR_GRADE；初二的读视图不给可选学校', { skip: !HAS_DB }, async () => {
  const g = await on.sessionFor(ID.g1);
  const r = await callAs(on.base, 'PUT', path(ID.x8), g, goal({ school_id: ID.school }));
  assert.equal(r.status, 409);
  assert.equal(r.text, P.SCHOOL_NOT_OPEN);
  const v = await callAs(on.base, 'GET', path(ID.x8), g);
  assert.equal(v.body.can_set_school, false);
  assert.deepEqual(v.body.school_options, []);
  assert.equal((await callAs(on.base, 'PUT', path(ID.x8), g, goal({ target_scores: [{ subject: 'aya', full_score: 100, target_score: 85 }] }))).status, 200,
               '初二可以设关注学科与目标分');
});

test('访问日志不带诉求内容（只有路由模板、状态码、学生 ID 的哈希）', { skip: !HAS_DB }, async () => {
  const text = JSON.stringify(logs);
  assert.ok(logs.some((l) => l['route'] === 'PUT /students/{student_id}/family-goals'));
  for (const s of ['合成说明', '懒惰', 'target_score', ID.x9]) assert.ok(!text.includes(s), `日志里出现了 ${s}`);
});

test('限流桶都在 config/auth.json 里；诉求模块不调任何计量函数（不是消耗类请求）', async () => {
  const c = loadAuthConfig();
  for (const r of FAMILY_GOALS_ROUTES) for (const l of r.rateLimits) assert.ok(c.rate_limits[l.bucket], l.bucket);
  const { readFileSync } = await import('node:fs');
  for (const f of ['../src/family/goals.ts', '../src/http/family-goals-routes.ts']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8');
    assert.ok(!/meter_|metering\/meter|usage_id|idempotency/i.test(src), `${f} 不应出现计量调用`);
  }
});
