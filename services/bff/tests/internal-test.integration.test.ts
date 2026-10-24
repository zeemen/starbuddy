// LT-06 内部成员测试运营（specs/launch-lt06-internal-test-ops 范围第 2–4 条，决定记录第 1–3 条；迁移 0059）的集成测试：
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，真实会话、真实 RLS 与库函数。
//   一、问题反馈：学生本人与有效绑定家长各写各的（来源由会话决定），字段规则，越权与不存在同一个 404，文本不进访问日志与指标；
//   二、退出测试端到端：二次确认、撤回同意、作废学生会话、写退出记录、幂等；之后统一判据拦下一切新写入与触发模型处理的请求
//       （数据权利与退出状态除外），已有数据保留，兄妹不受影响，别家仍是 404；开关关时路由不注册，但退出判据照样生效
//       （迁移 0059 执行之后无条件生效，决定记录（续）第 4 条；判据函数不在时不判）；
//   三、staging 白名单固定码：按账号作废——登录成功时条目与账号对应，退出时只作废已没有别的在测孩子的有效绑定家长的条目
//       （审计 revoked，重启后仍作废），还有别的在测孩子的家长与别的家长不受影响；
//   四、两份文书：内测开关打开时最新一条撤回后不能再打开学生模式；开关关时保持原口径（已满 14 周岁只要隐私政策）。
// 全部合成：号码用未分配的国家码 +999，码是合成的 6 位数字，只写在临时目录里（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { loadAuthConfig, randomKeys } from '../src/http/config.ts';
import { FEEDBACK_ROUTES } from '../src/http/internal-test-routes.ts';
import { P } from '../src/http/problems.ts';
import { createShellServer } from '../src/http/server.ts';
import { LOCK_STALE_MS, NoDeliverySender, StagingOtp } from '../src/http/staging-otp.ts';
import { render } from '../src/observability/metrics.ts';
import { TRUST_FEATURES } from '../src/trust/features.ts';
import { createFamily, HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, ORIGIN, startShell, type ShellHarness } from './shell-harness.ts';

let h: Harness;
let shell: ShellHarness;
const logs: Record<string, unknown>[] = [];
const F = { guardian: '', S: '', aS: '', S2: '', aS2: '' };
const O = { guardian: '', T: '', aT: '' };
const MISSING = '9a5b0000-0000-4000-8000-0000000000ff';
const BODY_MARK = '合成反馈标记q6lt06zz：今日计划打不开';

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  const f = await createFamily(h, { source: 'pilot_virtual', children: 2 });
  const o = await createFamily(h, { source: 'pilot_virtual' });
  Object.assign(F, { guardian: f.guardian, S: f.students[0]!.id, aS: f.students[0]!.account, S2: f.students[1]!.id, aS2: f.students[1]!.account });
  Object.assign(O, { guardian: o.guardian, T: o.students[0]!.id, aT: o.students[0]!.account });
  shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l), internalTest: { enabled: true },
                                          trust: { features: new Set(TRUST_FEATURES) } });
  for (const a of [F.guardian, F.aS, F.aS2, O.guardian, O.aT]) await shell.sessionFor(a);
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await h.close();
});

const call = async (method: string, path: string, who: string | null, body?: unknown) =>
  callAs(shell.base, method, path, who ? await shell.sessionFor(who) : null, body);
const fb = (student: string, who: string, body: Record<string, unknown>) => call('POST', `/students/${student}/feedback`, who, body);
const ok = { category: 'malfunction', body: BODY_MARK, route_template: '/s/[studentId]/today' };

// ---------------------------------------------------------------- 一、问题反馈

test('反馈：学生本人写自己的（学生自述），有效绑定家长写孩子的（家长观察）；来源由会话决定，请求里不收', { skip: !HAS_DB }, async () => {
  const s = await fb(F.S, F.aS, ok);
  assert.equal(s.status, 201, s.text);
  assert.deepEqual(s.body, { status: 'recorded' });
  const g = await fb(F.S, F.guardian, { category: 'slow', body: `家长${BODY_MARK}\n第二行` });
  assert.equal(g.status, 201, g.text);
  const { rows } = await h.su.query(`SELECT submitter_account_id AS who, source, category, route_template, status FROM platform.feedback
                                      WHERE student_id = $1 ORDER BY source`, [F.S]);
  assert.deepEqual(rows, [
    { who: F.guardian, source: 'guardian_observation', category: 'slow', route_template: null, status: 'new' },
    { who: F.aS, source: 'student_self_report', category: 'malfunction', route_template: '/s/[studentId]/today', status: 'new' },
  ]);
  // 请求里自报来源、提交者、状态：未知键 422
  for (const extra of [{ source: 'staff' }, { submitter_account_id: F.guardian }, { status: 'closed' }, { student_id: F.S2 }]) {
    assert.equal((await fb(F.S, F.aS, { ...ok, ...extra })).text, P.INVALID, JSON.stringify(extra));
  }
});

test('反馈：字段规则——固定分类、1–500 字、不含控制与格式字符、只收登记的路由模板', { skip: !HAS_DB }, async () => {
  const bad = [
    { ...ok, category: 'bug_report' }, { ...ok, body: '' }, { ...ok, body: ' \n\t ' }, { ...ok, body: '长'.repeat(501) },
    { ...ok, body: `合成${String.fromCodePoint(0x200b)}零宽` }, { ...ok, body: `合成${String.fromCodePoint(0x202e)}方向覆盖` },
    { ...ok, body: `合成${String.fromCodePoint(0x2028)}行分隔` }, { ...ok, body: `合成${String.fromCodePoint(0x01)}控制` },
    { ...ok, route_template: `/s/${F.S}/today` }, { ...ok, route_template: '/s/[studentId]/today?x=1' }, { ...ok, route_template: '/admin' },
    { ...ok, body: 42 }, { category: 'other' }, {},
  ];
  for (const b of bad) assert.equal((await fb(F.S, F.aS, b)).text, P.INVALID, JSON.stringify(b).slice(0, 80));
  assert.equal((await fb(F.S, F.aS, { ...ok, body: '满'.repeat(500), route_template: null })).status, 201, '正好 500 字、不带路由模板');
  assert.equal((await fb(F.S, F.aS, { category: 'suggestion', body: '😀'.repeat(500) })).status, 201, '按码位计数（emoji 也是 1 个字）');
  for (const r of FEEDBACK_ROUTES) assert.match(r, /^\/[a-z]+(\/([a-z][a-z-]*|\[[a-zA-Z]+\]))*$/, `登记的路由模板 ${r} 符合库里的形状`);
});

test('反馈：兄妹、另一户家长与学生、不存在、非 UUID 同一个 404；没登录 401', { skip: !HAS_DB }, async () => {
  const missing = await fb(MISSING, O.guardian, ok);
  assert.equal(missing.status, 404);
  assert.equal(missing.text, P.NOT_FOUND);
  for (const [who, student, what] of [[F.aS2, F.S, '兄妹'], [O.guardian, F.S, '另一户家长'], [O.aT, F.S, '另一户学生'],
                                      [F.aS, MISSING, '学生 + 不存在']] as const) {
    const x = await fb(student, who, ok);
    assert.equal(x.status, 404, what);
    assert.equal(x.text, missing.text, what);
  }
  assert.equal((await call('POST', '/students/not-a-uuid/feedback', F.aS, ok)).text, P.NOT_FOUND);
  assert.equal((await call('POST', `/students/${F.S}/feedback`, null, ok)).status, 401);
});

test('反馈：自由文本不进访问日志与指标（只记 outcome 枚举）', { skip: !HAS_DB }, async () => {
  const text = JSON.stringify(logs);
  assert.ok(logs.some((l) => l['route'] === 'POST /students/{student_id}/feedback' && l['outcome'] === 'recorded'));
  assert.ok(!text.includes('q6lt06zz') && !text.includes('合成反馈') && !text.includes('malfunction'), '访问日志里没有文本与分类');
  assert.ok(!render().includes('q6lt06zz'), '指标里没有文本');
  assert.match(render(), /starbuddy_http_requests_total\{service="bff",route="\/bff\/v0\/students\/\{student_id\}\/feedback",method="POST",status_class="2xx"\}/,
               '指标只按路由模板计数');
});

// ---------------------------------------------------------------- 二、退出测试端到端

test('退出：二次确认（confirm 不是 true 不改任何东西）；别户、学生会话、不存在被拒', { skip: !HAS_DB }, async () => {
  const path = `/students/${F.S}/internal-test/exit`;
  const notConfirmed = await call('POST', path, F.guardian, { confirm: false });
  assert.equal(notConfirmed.status, 403);
  assert.equal(notConfirmed.text, P.CONFIRM_REQUIRED);
  assert.equal((await call('POST', path, F.guardian, {})).text, P.INVALID);
  assert.equal((await call('POST', path, F.guardian, { confirm: 'yes' })).text, P.INVALID);
  assert.equal((await call('POST', path, F.guardian, { confirm: true, student_id: F.S2 })).text, P.INVALID);
  const missing = await call('POST', `/students/${MISSING}/internal-test/exit`, O.guardian, { confirm: true });
  assert.equal(missing.text, P.NOT_FOUND);
  assert.equal((await call('POST', path, O.guardian, { confirm: true })).text, missing.text, '另一户家长');
  assert.equal((await call('POST', path, O.guardian, { confirm: false })).text, missing.text, '另一户家长不确认：先按授权拒绝（404，不是 403）');
  assert.equal((await call('POST', path, F.aS, { confirm: true })).text, P.FORBIDDEN, '学生会话不能发起');
  const { rows: [x] } = await h.su.query(`SELECT (SELECT count(*) FROM platform.internal_test_exit)::int AS exits,
                                                 (SELECT count(*) FROM platform.consent_record WHERE student_id = $1 AND decision = 'revoked')::int AS rev`, [F.S]);
  assert.deepEqual(x, { exits: 0, rev: 0 }, '被拒的请求什么都没写');
  assert.deepEqual((await call('GET', `/students/${F.S}/internal-test`, F.guardian)).body, { student_id: F.S, exited: false });
});

test('退出：撤回同意、作废学生会话、写退出记录；重复提交幂等；之后一切新写入与触发模型处理的请求 409，数据权利照常', { skip: !HAS_DB }, async () => {
  const before = (await call('GET', `/students/${F.S}/today`, F.guardian)).status;
  assert.equal(before, 200, '退出之前今日计划照常');
  const stale = await shell.sessionFor(F.aS);  // 学生的旧会话
  const r = await call('POST', `/students/${F.S}/internal-test/exit`, F.guardian, { confirm: true });
  assert.equal(r.status, 200, r.text);
  assert.equal(r.body.status, 'exited');
  assert.equal(r.body.newly_exited, true);
  assert.deepEqual(r.body.consents_revoked, ['guardian_consent_under14', 'privacy_policy']);
  assert.equal(r.body.whitelist_entries_revoked, null, 'test 环境没有白名单固定码');
  assert.ok(!/[0-9a-f]{64}/.test(r.text), '响应不带手机号摘要');
  const again = await call('POST', `/students/${F.S}/internal-test/exit`, F.guardian, { confirm: true });
  assert.equal(again.status, 200);
  assert.equal(again.body.newly_exited, false);
  assert.equal(again.body.exited_at, r.body.exited_at);
  const { rows: latest } = await h.su.query(`SELECT DISTINCT ON (consent_type) consent_type, decision FROM platform.consent_record WHERE student_id = $1
                                              ORDER BY consent_type, decided_at DESC, (decision = 'revoked') DESC`, [F.S]);
  assert.ok(latest.length >= 2 && latest.every((x: { decision: string }) => x.decision === 'revoked'), '每类同意最新一条都是撤回');
  // 学生的旧会话已作废
  assert.equal((await callAs(shell.base, 'GET', '/me', stale)).status, 401);
  shell.forget(F.aS);
  // 统一判据：已退出的档案，读写与触发模型处理一律 409 TEST_EXITED
  const blocked: [string, string, unknown?][] = [
    ['GET', `/students/${F.S}/today`], ['GET', `/students/${F.S}/balance`], ['GET', `/students/${F.S}/star-map`], ['GET', `/students/${F.S}/enrollments`],
    ['POST', `/students/${F.S}/enrollments/politics`], ['PUT', `/students/${F.S}/time-guard`, { daily_cap_minutes: 30, curfew_start: null, curfew_end: null }],
    ['POST', `/students/${F.S}/consents`, { consent_type: 'privacy_policy', document_version: 'privacy-policy@synthetic-0', decision: 'granted' }],
    ['POST', `/students/${F.S}/student-session`], ['POST', `/students/${F.S}/feedback`, ok], ['GET', `/students/${F.S}/weekly-report`],
    ['PUT', `/students/${F.S}/onboarding/answers`, { field_id: 'outer.g1.weekly_available_time', value: { minutes: 300 } }],
  ];
  for (const [m, p, b] of blocked) {
    const x = await call(m, p, F.guardian, b);
    assert.equal(x.status, 409, `${m} ${p}：${x.text}`);
    assert.equal(x.text, P.TEST_EXITED, `${m} ${p}`);
  }
  const { rows: [c] } = await h.su.query(`SELECT (SELECT count(*) FROM platform.consent_record WHERE student_id = $1 AND decision = 'granted' AND decided_at > $2)::int AS granted,
                                                 (SELECT count(*) FROM platform.subject_enrollment WHERE student_id = $1 AND subject = 'zhixing')::int AS enrolled`,
                                         [F.S, r.body.exited_at]);
  assert.deepEqual(c, { granted: 0, enrolled: 0 }, '被拦的写入一行也没写');
  // 数据权利与退出状态照常
  assert.deepEqual((await call('GET', `/students/${F.S}/internal-test`, F.guardian)).body, { student_id: F.S, exited: true });
  assert.equal((await call('GET', `/students/${F.S}/erasure`, F.guardian)).status, 200);
  const ex = await call('POST', `/students/${F.S}/exports`, F.guardian);
  assert.equal(ex.status, 201, ex.text);
  const er = await call('POST', `/students/${F.S}/erasure`, F.guardian);
  assert.equal(er.status, 201, '测试结束统一删除走现有删除流程：退出后仍能发起删除请求');
  // 已有数据保留；兄妹不受影响；别家对这个档案仍是 404（不是 409，不泄露退出状态）
  assert.equal((await h.su.query('SELECT count(*)::int AS n FROM platform.feedback WHERE student_id = $1', [F.S])).rows[0].n, 4, '已有反馈保留');
  assert.equal((await call('GET', `/students/${F.S2}/today`, F.guardian)).status, 200, '兄妹照常');
  assert.equal((await fb(F.S2, F.aS2, ok)).status, 201, '兄妹照常能反馈');
  const other = await call('GET', `/students/${F.S}/balance`, O.guardian);
  assert.equal(other.text, P.NOT_FOUND, '另一户家长读已退出的档案：与不存在相同的 404');
  assert.equal((await call('GET', `/students/${F.S}/internal-test`, O.guardian)).text, P.NOT_FOUND);
});

test('开关关：三条路由不注册（与表外同一个 404），退出判据照样生效（不挂开关）；判据函数不在（0059 未执行）时不判', { skip: !HAS_DB }, async () => {
  const off = await startShell(h.bff, h.su, { log: () => undefined });
  try {
    const g = await off.sessionFor(F.guardian);
    const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', g);
    for (const [m, p, b] of [['POST', `/students/${F.S2}/feedback`, ok], ['GET', `/students/${F.S}/internal-test`, undefined],
                             ['POST', `/students/${F.S}/internal-test/exit`, { confirm: true }]] as const) {
      const x = await callAs(off.base, m, p, g, b);
      assert.equal(x.status, 404, `${m} ${p}`);
      assert.equal(x.text, outside.text, `${m} ${p}`);
    }
    const blocked = await callAs(off.base, 'GET', `/students/${F.S}/balance`, g);
    assert.equal(blocked.text, P.TEST_EXITED, '开关关时已退出的档案照样 409');
    assert.equal((await callAs(off.base, 'GET', `/students/${F.S2}/balance`, g)).status, 200, '没退出的兄妹照常');
  } finally {
    await off.close();
  }
  const noFn = await startShell(h.bff, h.su, { log: () => undefined, exitGuard: false });
  try {
    const g = await noFn.sessionFor(F.guardian);
    assert.equal((await callAs(noFn.base, 'GET', `/students/${F.S}/balance`, g)).status, 200, '库里没有判据函数时不判（与迁移执行之前一致）');
  } finally {
    await noFn.close();
  }
});

// ---------------------------------------------------------------- 四、两份文书

test('两份文书：最新一条撤回后不能打开学生模式（同一时刻撤回优先）；补签后可以', { skip: !HAS_DB }, async () => {
  const fam = await createFamily(h, { source: 'pilot_virtual' });
  const [s] = fam.students;
  // 已满 14 周岁：库函数只要隐私政策，监护人同意书由 BFF 再核（两份文书）
  await h.su.query(`UPDATE platform.student_profile SET birth_year_month = '2010-01-01' WHERE id = $1`, [s!.id]);
  await h.su.query(`INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
                    SELECT $1, $2, t, replace(t, '_', '-') || '@synthetic-0', 'granted', now() - interval '1 hour'
                      FROM unnest(ARRAY['privacy_policy', 'guardian_consent_under14']) t`, [s!.id, fam.guardian]);
  const g = await shell.sessionFor(fam.guardian);
  const revoke = await callAs(shell.base, 'POST', `/students/${s!.id}/consents`, g,
                              { consent_type: 'guardian_consent_under14', document_version: 'guardian-consent@synthetic-0', decision: 'revoked' });
  assert.equal(revoke.status, 201);
  const open = await callAs(shell.base, 'POST', `/students/${s!.id}/student-session`, g);
  assert.equal(open.text, P.CONSENT_REQUIRED, '监护人同意书最新一条是撤回');
  assert.equal((await callAs(shell.base, 'GET', '/me', g)).status, 200, '没打开时家长会话不作废');
  const regrant = await callAs(shell.base, 'POST', `/students/${s!.id}/consents`, g,
                               { consent_type: 'guardian_consent_under14', document_version: 'guardian-consent@synthetic-0', decision: 'granted' });
  assert.equal(regrant.status, 201);
  assert.equal((await callAs(shell.base, 'POST', `/students/${s!.id}/student-session`, g)).status, 200);
  shell.forget(fam.guardian);
});

test('两份文书：内测开关关时保持原口径——已满 14 周岁只签隐私政策就能打开学生模式，不满 14 周岁仍要监护人同意', { skip: !HAS_DB }, async () => {
  const off = await startShell(h.bff, h.su, { log: () => undefined });
  try {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [older, younger] = fam.students;
    await h.su.query(`UPDATE platform.student_profile SET birth_year_month = '2010-01-01' WHERE id = $1`, [older!.id]);
    await h.su.query(`UPDATE platform.student_profile SET birth_year_month = '2014-06-01' WHERE id = $1`, [younger!.id]);
    await h.su.query(`INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
                      SELECT s, $2, 'privacy_policy', 'privacy-policy@synthetic-0', 'granted', now() - interval '1 hour'
                        FROM unnest($1::uuid[]) s`, [[older!.id, younger!.id], fam.guardian]);
    // 两个孩子：createFamily 可能已签监护人同意书，这里统一追加一条撤回（同意记录只追加）
    await h.su.query(`INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision, decided_at)
                      SELECT s, $2, 'guardian_consent_under14', 'guardian-consent@synthetic-0', 'revoked', now() - interval '30 minutes'
                        FROM unnest($1::uuid[]) s`, [[older!.id, younger!.id], fam.guardian]);
    const g = await off.sessionFor(fam.guardian);
    assert.equal((await callAs(off.base, 'POST', `/students/${younger!.id}/student-session`, g)).text, P.CONSENT_REQUIRED,
                 '不满 14 周岁：库里要监护人同意（原口径）');
    const opened = await callAs(off.base, 'POST', `/students/${older!.id}/student-session`, g);
    assert.equal(opened.status, 200, `已满 14 周岁只签隐私政策：开关关时照常打开（${opened.text}）`);
  } finally {
    await off.close();
  }
});

// ---------------------------------------------------------------- 三、staging 白名单固定码

test('staging：按账号作废——登录成功时条目与账号对应；退出时只作废已没有别的在测孩子的有效绑定家长的条目（审计 revoked、重启后仍作废）', { skip: !HAS_DB }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'bff-lt06-'));
  const id = (n: string) => `9a5c0000-0000-4000-8000-0000000000${n}`;
  const G = { a: id('a1'), b: id('a2'), c: id('a3'), d: id('a4'), aX: id('b1'), aY: id('b2'), X: id('c1'), Y: id('c2'), H: id('d1'), H2: id('d2') };
  const PH = { a: '+99900006601', b: '+99900006602', c: '+99900006603', d: '+99900006604' };
  const CODE = { a1: '424242', a2: '535353', b1: '646464', b2: '656565', c1: '757575', c2: '767676', d1: '868686' };
  let server: Server | null = null;
  try {
    // 一户：家长 a（创建者）、b 与 c（经邀请）、学生 X；c 另有一个在测的孩子 Y（自己的家庭）；家长 d 与这户无关
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian'), ($3, 'guardian'), ($4, 'guardian'),
                        ($5, 'student'), ($6, 'student')`, [G.a, G.b, G.c, G.d, G.aX, G.aY]);
    await h.su.query(`INSERT INTO platform.account_contact (account_id, phone_e164, verified_at) VALUES ($1, $5, now()), ($2, $6, now()),
                        ($3, $7, now()), ($4, $8, now())`, [G.a, G.b, G.c, G.d, PH.a, PH.b, PH.c, PH.d]);
    await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2), ($3, $4)`, [G.H, G.a, G.H2, G.c]);
    await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 8, 'nanjing'),
                        ($4, $5, $6, 8, 'nanjing')`, [G.X, G.aX, G.H, G.Y, G.aY, G.H2]);
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $4, 'household_creator'),
                        ($2, $4, 'invite'), ($3, $4, 'invite'), ($3, $5, 'household_creator')`, [G.a, G.b, G.c, G.X, G.Y]);
    const entriesFile = join(dir, 'otp.json');
    const auditFile = join(dir, 'audit.jsonl');
    const from = new Date(Date.now() - 3600_000).toISOString();
    const to = new Date(Date.now() + 3600_000).toISOString();
    const e = (eid: string, phone: string, code: string) => ({ id: eid, phone, code, issued_at: from, not_after: to });
    writeFileSync(entriesFile, JSON.stringify({ entries: [
      e('lt06-a-001', PH.a, CODE.a1), e('lt06-a-002', PH.a, CODE.a2), e('lt06-b-001', PH.b, CODE.b1), e('lt06-b-002', PH.b, CODE.b2),
      e('lt06-c-001', PH.c, CODE.c1), e('lt06-c-002', PH.c, CODE.c2), e('lt06-d-001', PH.d, CODE.d1),
    ] }), { mode: 0o600 });
    writeFileSync(auditFile, '', { mode: 0o600 });
    const keys = randomKeys();
    const config = loadAuthConfig();
    config.rate_limits = Object.fromEntries(Object.entries(config.rate_limits).map(([k, v]) => [k, { ...v, limit: v.limit * 1000 }]));
    const ORIG = 'https://shell.example.invalid';
    const stagingLogs: Record<string, unknown>[] = [];
    server = createShellServer({ pool: h.bff, config, keys, env: 'staging', sms: new NoDeliverySender(), shellOrigin: ORIG,
                                 stagingOtp: new StagingOtp({ env: 'staging', keys, entriesFile, auditFile, heartbeat: false, holder: { host: 'lt06', pid: 1 } }),
                                 internalTest: { enabled: true }, log: (l) => stagingLogs.push(l) });
    await new Promise<void>((r) => server!.listen(0, '127.0.0.1', r));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/bff/v0`;
    const post = async (path: string, body: unknown, s?: { token: string; csrf: string }) => {
      const headers: Record<string, string> = { origin: ORIG, 'content-type': 'application/json' };
      if (s) { headers['x-sb-session'] = s.token; headers['x-csrf-token'] = s.csrf; }
      const r = await fetch(`${base}${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
      const text = await r.text();
      return { status: r.status, text, body: (() => { try { return JSON.parse(text); } catch { return null; } })() };
    };
    const login = async (phone: string, code: string) => {
      assert.equal((await post('/auth/otp/request', { phone })).status, 202);
      return post('/auth/otp/verify', { phone, code });
    };
    // 三位家长先各用一条固定码登录（条目与账号对应）
    for (const [phone, code] of [[PH.b, CODE.b1], [PH.c, CODE.c1]] as const) assert.equal((await login(phone, code)).status, 200);
    const a = await login(PH.a, CODE.a1);
    assert.equal(a.status, 200, a.text);
    const exitRes = await post(`/students/${G.X}/internal-test/exit`, { confirm: true }, { token: a.body.session_token, csrf: a.body.csrf_token });
    assert.equal(exitRes.status, 200, exitRes.text);
    assert.equal(exitRes.body.whitelist_entries_revoked, 4, 'a 与 b 的四条条目作废（含用过的）；c 还有在测的 Y，不作废');
    // b 与 a 剩下的条目都不再签发：拿下发的码登录不了；还有在测孩子的 c 与别的家长 d 照常
    assert.equal((await login(PH.b, CODE.b2)).status, 401, '同一学生的另一位家长');
    assert.equal((await login(PH.a, CODE.a2)).status, 401, '发起的家长剩下的条目');
    assert.equal((await login(PH.c, CODE.c2)).status, 200, '还有别的在测孩子的家长不受影响');
    assert.equal((await login(PH.d, CODE.d1)).status, 200, '别的家长不受影响');
    const audit = readFileSync(auditFile, 'utf8');
    const lines = audit.trim().split('\n').map((l) => JSON.parse(l) as Record<string, unknown>);
    assert.deepEqual(lines.filter((l) => l['event'] === 'revoked').map((l) => l['entry']).sort(),
                     ['lt06-a-001', 'lt06-a-002', 'lt06-b-001', 'lt06-b-002']);
    assert.equal(lines.filter((l) => l['event'] === 'consumed' && typeof l['account_hmac'] === 'string').length, 5, '每次登录成功都记下账号摘要');
    for (const s of [...Object.values(PH), ...Object.values(CODE), '99900006', G.a, G.b, G.c, G.d]) {
      assert.ok(!audit.includes(s) && !JSON.stringify(stagingLogs).includes(s), `审计与访问日志不含 ${s}`);
    }
    assert.ok(!exitRes.text.includes(G.b) && !exitRes.text.includes(G.a), '响应不带家长账号');
    // 重启（锁心跳过期后接管）：作废与账号对应都从审计恢复
    const restarted = new StagingOtp({ env: 'staging', keys, entriesFile, auditFile, heartbeat: false, holder: { host: 'lt06', pid: 2 },
                                       now: () => Date.now() + LOCK_STALE_MS + 1 });
    for (const x of ['lt06-a-002', 'lt06-b-002']) assert.equal(restarted.stateOf(x)?.revoked, true, `${x} 重启后仍作废`);
    assert.equal(restarted.stateOf('lt06-d-001')?.revoked ?? false, false);
    assert.equal(restarted.revokeByAccounts(new Set([G.c])).revoked, 2, '重启后按账号对应仍能作废（c 的两条）');
  } finally {
    if (server) await new Promise<void>((r) => server!.close(() => r()));
    rmSync(dir, { recursive: true, force: true });
  }
});
