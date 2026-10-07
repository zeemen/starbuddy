// 届别为真相源（specs/policy-decisions-v0.9.1 第 1 节，PR2）的 BFF 部分：
//   - 年级读点改读 platform.current_grade(届别, 请求时钟)：GET /me 的 children[].grade、选科的学科目录（按年级筛）；
//     时钟是 BFF 注入的 deps.now（不依赖墙钟），跨 9 月 1 日（北京时间）切学年；已毕业（不在 7–9）为 null；存下的建档年级不再被读；
//   - 家长届别更正入口 POST /students/{id}/exam-cohort：只经 platform.correct_exam_cohort（绑定家长、每次 ±1、更正后当前年级 7–9、
//     来源记家长自述、写更正记录）；未绑定与不存在同一个 404；步长与越界 422；频控按（家长账号、学生）成对，每天 3 次。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，真实会话与真实 RLS。全部合成数据（规则 8）。
// 更正函数在库里用 clock_timestamp() 核对“更正后当前年级在 7–9”：更正用例的学生按墙钟建档（推算届别也按墙钟），与库的核对同一时间轴。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';
import { loadAuthConfig } from '../src/http/config.ts';
import { P } from '../src/http/problems.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const BUCKET = 'cohort_correction.link.day';
const ACCOUNT_BUCKET = 'cohort_correction.account.day';
const SEP1_2027 = Date.parse('2027-08-31T16:00:00Z');  // 北京时间 2027-09-01 00:00
let h: Harness;
let shell: ShellHarness;
let nowMs = Date.now();
const logs: Record<string, unknown>[] = [];

async function account(kind: 'guardian' | 'student'): Promise<string> {
  const id = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, $2)`, [id, kind]);
  return id;
}
/** 家长 g 的家庭里建一个孩子；createdAt 为空时按墙钟建档（库里按建档时刻与建档年级推算届别） */
async function kid(g: string, household: string, grade: number, createdAt: string | null = null): Promise<string> {
  const id = randomUUID();
  const acct = await account('student');
  await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, created_at)
                    VALUES ($1, $2, $3, $4, 'nanjing', coalesce($5::timestamptz, now()))`, [id, acct, household, grade, createdAt]);
  await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`, [g, id]);
  return id;
}
async function family(): Promise<{ g: string; household: string }> {
  const g = await account('guardian');
  const household = randomUUID();
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, g]);
  return { g, household };
}
const cohortOf = async (s: string): Promise<{ exam_cohort: number; source: string; grade: number }> =>
  (await h.su.query(`SELECT exam_cohort, exam_cohort_source AS source, grade FROM platform.student_profile WHERE id = $1`, [s])).rows[0];
const dbGrade = async (cohort: number, at: number): Promise<number | null> =>
  (await h.su.query(`SELECT platform.current_grade($1::smallint, $2::timestamptz) AS g`, [cohort, new Date(at)])).rows[0].g;
const me = async (g: string) => callAs(shell.base, 'GET', '/me', await shell.sessionFor(g));
const correct = async (g: string, s: string, body: unknown) => callAs(shell.base, 'POST', `/students/${s}/exam-cohort`, await shell.sessionFor(g), body);

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l), now: () => new Date(nowMs) });
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await h.close();
});

test('年级读点：/me 的年级由届别按注入的请求时钟推算，跨 9 月 1 日加一，已毕业为 null；不读存下的建档年级', { skip: !HAS_DB }, async () => {
  const F = await family();
  // 建档于 2026-10-01（北京时间）：八年级 → 届别 2028，九年级 → 届别 2027
  const k8 = await kid(F.g, F.household, 8, '2026-10-01T10:00:00+08:00');
  const k9 = await kid(F.g, F.household, 9, '2026-10-01T10:00:00+08:00');
  assert.deepEqual([(await cohortOf(k8)).exam_cohort, (await cohortOf(k9)).exam_cohort], [2028, 2027]);
  const grades = async () => Object.fromEntries((await me(F.g)).body.children.map((c: { student_id: string; grade: number | null }) => [c.student_id, c.grade]));
  nowMs = SEP1_2027 - 1000;  // 北京时间 2027-08-31 23:59:59
  assert.deepEqual(await grades(), { [k8]: 8, [k9]: 9 });
  nowMs = SEP1_2027;         // 北京时间 2027-09-01 00:00:00
  assert.deepEqual(await grades(), { [k8]: 9, [k9]: null }, '跨 9 月 1 日：八年级升九年级，九年级毕业（null，孩子照常列出）');
  // 存下的建档年级（8 / 9）不变，也不被读：改掉它，/me 的年级不变
  await h.su.query(`UPDATE platform.student_profile SET grade = 7 WHERE id = ANY ($1::uuid[])`, [[k8, k9]]);
  assert.deepEqual(await grades(), { [k8]: 9, [k9]: null });
  // 届别更正后（迁移执行者直接改届别与来源，模拟客服更正）：/me 立即按新届别推算
  await h.su.query(`UPDATE platform.student_profile SET exam_cohort = 2029, exam_cohort_source = 'staff_correction' WHERE id = $1`, [k8]);
  assert.deepEqual(await grades(), { [k8]: 8, [k9]: null });
  nowMs = Date.now();
});

test('年级读点：选科的学科目录按推算的当前年级筛（跨 9 月 1 日变化）；已毕业没有可选学科', { skip: !HAS_DB }, async () => {
  const F = await family();
  const k8 = await kid(F.g, F.household, 8, '2026-10-01T10:00:00+08:00');
  const k9 = await kid(F.g, F.household, 9, '2026-10-01T10:00:00+08:00');
  const subjects = async (s: string) => {
    const r = await callAs(shell.base, 'GET', `/students/${s}/enrollments`, await shell.sessionFor(F.g));
    assert.equal(r.status, 200, r.text);
    return r.body.subjects.map((x: { subject: string }) => x.subject).sort();
  };
  // 只在测试库里临时把道法改成只适用九年级，看出目录确实按当前年级筛
  const { rows: [{ grades: saved }] } = await h.su.query(`SELECT grades::int[] AS grades FROM platform.subject_catalog WHERE code = 'politics'`);
  await h.su.query(`UPDATE platform.subject_catalog SET grades = '{9}' WHERE code = 'politics'`);
  try {
    nowMs = SEP1_2027 - 1000;
    const before8 = await subjects(k8);
    assert.ok(!before8.includes('politics') && before8.length > 0, before8.join(','));
    assert.ok((await subjects(k9)).includes('politics'));
    nowMs = SEP1_2027;
    assert.ok((await subjects(k8)).includes('politics'), '升入九年级后目录里有只适用九年级的学科');
    assert.deepEqual(await subjects(k9), [], '已毕业：没有可选学科');
  } finally {
    await h.su.query(`UPDATE platform.subject_catalog SET grades = $1::smallint[] WHERE code = 'politics'`, [saved]);
    nowMs = Date.now();
  }
});

test('届别更正：只经 correct_exam_cohort；绑定家长每次 ±1，来源记家长自述并写更正记录；步长不对、越界、请求体不合格 422；未绑定与不存在同一个 404', { skip: !HAS_DB }, async () => {
  nowMs = Date.now();
  const F = await family();
  const O = await family();
  const s = await kid(F.g, F.household, 8);  // 按墙钟建档：推算届别与库里更正时的核对同一时间轴
  const c0 = (await cohortOf(s)).exam_cohort;
  assert.equal((await cohortOf(s)).source, 'derived');
  for (const body of [{}, { exam_cohort: String(c0 + 1) }, { exam_cohort: c0 + 0.5 }, { exam_cohort: 2014 }, { exam_cohort: 2101 },
                      { exam_cohort: c0 + 1, grade: 7 }, { exam_cohort: c0 + 1, source: 'staff_correction' }]) {
    const r = await correct(F.g, s, body);
    assert.equal(r.status, 422, JSON.stringify(body));
    assert.equal(r.text, P.INVALID);
  }
  assert.equal((await correct(F.g, s, { exam_cohort: c0 + 2 })).text, P.INVALID, '一次跨两届');
  assert.equal((await cohortOf(s)).exam_cohort, c0, '拒绝的请求没有改届别');
  // +1（晚一届 = 低一个年级）：成功，返回新届别与按请求时钟推算的年级
  const ok = await correct(F.g, s, { exam_cohort: c0 + 1 });
  assert.equal(ok.status, 200, ok.text);
  assert.deepEqual(ok.body, { student_id: s, exam_cohort: c0 + 1, grade: await dbGrade(c0 + 1, nowMs) });
  assert.deepEqual(await cohortOf(s), { exam_cohort: c0 + 1, source: 'guardian_report', grade: 8 }, '来源记家长自述；建档年级不变');
  const { rows: changes } = await h.su.query(
    `SELECT old_cohort, new_cohort, source, changed_by FROM platform.exam_cohort_change WHERE student_id = $1 ORDER BY changed_at`, [s]);
  assert.deepEqual(changes, [{ old_cohort: c0, new_cohort: c0 + 1, source: 'guardian_report', changed_by: F.g }]);
  // /me 立即按新届别推算
  assert.equal((await me(F.g)).body.children.find((c: { student_id: string }) => c.student_id === s).grade, await dbGrade(c0 + 1, nowMs));
  // 同一个值重复提交：步长为 0，拒绝（不会连改两次）；再晚一届：当前年级低于七年级，越界拒绝
  assert.equal((await correct(F.g, s, { exam_cohort: c0 + 1 })).text, P.INVALID);
  assert.equal((await correct(F.g, s, { exam_cohort: c0 + 2 })).text, P.INVALID, '更正后当前年级不在 7–9');
  assert.equal((await cohortOf(s)).exam_cohort, c0 + 1);
  // 另一户家长、不存在的学生：同一个 404，逐字节相同；没有写更正记录
  const missing = await correct(O.g, randomUUID(), { exam_cohort: c0 });
  assert.equal(missing.status, 404);
  assert.equal(missing.text, P.NOT_FOUND);
  const other = await correct(O.g, s, { exam_cohort: c0 });
  assert.equal(other.status, missing.status);
  assert.equal(other.text, missing.text);
  assert.equal((await cohortOf(s)).exam_cohort, c0 + 1);
  // 未登录 401（学生会话 403 与 CSRF 由 route-matrix 的授权矩阵从路由表统一测）
  const anon = await callAs(shell.base, 'POST', `/students/${s}/exam-cohort`, null, { exam_cohort: c0 });
  assert.equal(anon.status, 401);
  // 改回（-1）：仍是家长自述，第二条记录
  assert.equal((await correct(F.g, s, { exam_cohort: c0 })).status, 200);
  const { rows: [{ n }] } = await h.su.query(`SELECT count(*)::int AS n FROM platform.exam_cohort_change WHERE student_id = $1`, [s]);
  assert.equal(n, 2);
  // 访问日志只记结果枚举，不记届别与年级
  const line = logs.filter((l) => l['route'] === 'POST /students/{student_id}/exam-cohort' && l['status'] === 200).at(-1)!;
  assert.equal(line['outcome'], 'corrected');
  assert.deepEqual(Object.keys(line).filter((k) => /cohort|grade/.test(k)), []);
});

test('届别更正频控：每个（家长账号、学生）每天 3 次（config/auth.json），超限 429；同一孩子的另一位家长、同一家长的另一个孩子各自计数', { skip: !HAS_DB }, async () => {
  const rule = loadAuthConfig().rate_limits[BUCKET]!;
  assert.deepEqual(rule, { window_seconds: 86400, limit: 3 }, '阈值保守：每天 3 次');
  const saved = shell.config.rate_limits[BUCKET]!;
  shell.config.rate_limits[BUCKET] = { ...rule };  // 测试 harness 默认放宽阈值，这里按配置原值测
  try {
    const F = await family();
    const s1 = await kid(F.g, F.household, 8);
    const s2 = await kid(F.g, F.household, 8);
    const g2 = await account('guardian');
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'invite')`, [g2, s1]);
    const bad = { exam_cohort: 2015 };  // 步长不对（422）：失败的请求同样计数
    for (let i = 0; i < 3; i++) assert.equal((await correct(F.g, s1, bad)).status, 422, `第 ${i + 1} 次`);
    const over = await correct(F.g, s1, { exam_cohort: (await cohortOf(s1)).exam_cohort + 1 });
    assert.equal(over.status, 429);
    assert.equal(over.text, P.TOO_MANY);
    assert.equal((await cohortOf(s1)).source, 'derived', '429 时没有更正');
    assert.equal((await correct(g2, s1, bad)).status, 422, '同一孩子的另一位家长单独计数（无权的家长也不能替别人把次数用光）');
    assert.equal((await correct(F.g, s2, bad)).status, 422, '同一家长的另一个孩子单独计数');
  } finally {
    shell.config.rate_limits[BUCKET] = saved;
  }
});

test('届别更正频控：每个家长账号每天总共 10 次（cohort_correction.account.day），换着学生 ID 试探超出总量后 429；别的家长不受影响', { skip: !HAS_DB }, async () => {
  const total = loadAuthConfig().rate_limits[ACCOUNT_BUCKET]!;
  assert.deepEqual(total, { window_seconds: 86400, limit: 10 }, '总量上限：每天 10 次');
  const saved = { link: shell.config.rate_limits[BUCKET]!, account: shell.config.rate_limits[ACCOUNT_BUCKET]! };
  shell.config.rate_limits[BUCKET] = loadAuthConfig().rate_limits[BUCKET]!;
  shell.config.rate_limits[ACCOUNT_BUCKET] = { ...total };
  try {
    const F = await family();
    const O = await family();
    const own = await kid(F.g, F.household, 8);
    // 每次换一个学生 ID（不存在的学生：404，单看成对的桶永远到不了上限）
    for (let i = 0; i < total.limit; i++) {
      const r = await correct(F.g, randomUUID(), { exam_cohort: 2015 });
      assert.equal(r.status, 404, `第 ${i + 1} 次`);
    }
    const over = await correct(F.g, randomUUID(), { exam_cohort: 2015 });
    assert.equal(over.status, 429);
    assert.equal(over.text, P.TOO_MANY);
    // 超出总量后，自己孩子的合法更正也被挡（429 在 handler 之前，没有更正）
    const mine = await correct(F.g, own, { exam_cohort: (await cohortOf(own)).exam_cohort + 1 });
    assert.equal(mine.status, 429);
    assert.equal((await cohortOf(own)).source, 'derived');
    assert.equal((await correct(O.g, randomUUID(), { exam_cohort: 2015 })).status, 404, '别的家长账号单独计数');
  } finally {
    shell.config.rate_limits[BUCKET] = saved.link;
    shell.config.rate_limits[ACCOUNT_BUCKET] = saved.account;
  }
});
