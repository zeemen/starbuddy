// 月度计划接口集成测试（specs/family-goals-monthly-plan/step3-spec.md，PLT-14 第 3 步；迁移 0054 草稿的两个视图函数；接口定义
// openapi/monthly-plan.v0.yaml）。一次性 Postgres，登录角色 bff_app 只继承 starbuddy_bff；真实会话（tests/shell-harness.ts）。
// 洞察服务的生成接口用假实现：“生成”时以超级用户、复制模式写一份形状合格的快照（真实生成、权益与守卫由 services/insight 的测试覆盖），
// 并记下每次调用的（学生、月份、actor）。覆盖：
//   - 开关关时两条路由不注册（与表外 404 逐字节相同）；
//   - 生成只由家长触发：家长打开即生成、同月第二次不再调；学生会话 404（家长端）且不触发生成；学生端本月没有快照时 plan = null、不触发生成；
//   - 别家、已解除绑定、不存在、学生会话：同一个 404，生成接口一次也没被调；
//   - 没有权益（洞察服务统一的“不可用”回答）：entitled = false、plan = null，不带别的；洞察服务不可用或没配：503；
//   - 模板版本与 BFF 读到的模板库不一致：503；模板库或词表读不到：503；
//   - 成品句子命中词表：那一条不出，日志只记条数；所引判断的模板不在对外白名单：那一条不出；
//   - 学生端只有学习计划（没有注意事项、回顾、分差、诉求与系数）；学生看不到只对家长可见的判断；分差只给作者家长、只有区间。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';
import pg from 'pg';
import type { GenerateOutcome, MonthlyPlanClient, TemplateLibrary } from '../src/family/monthly-plan.ts';
import { loadAuthConfig } from '../src/http/config.ts';
import { P } from '../src/http/problems.ts';
import { loadShellConfig } from '../src/shell/config.ts';
import { lexicon, parseLexicon } from '../src/trust/lexicon.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const HAS_DB = Boolean(process.env['PGHOST']);
const ID = {
  g1: '6f540000-0000-4000-8000-000000000001',   // 户一的创建者（写了诉求）
  g2: '6f540000-0000-4000-8000-000000000002',   // 户一的第二位家长
  gR: '6f540000-0000-4000-8000-000000000003',   // 曾绑定 x1，后解除
  gO: '6f540000-0000-4000-8000-000000000004',   // 另一户的家长
  gU: '6f540000-0000-4000-8000-000000000005',   // 绑定 x3，生成中途被解除绑定
  gL: '6f540000-0000-4000-8000-000000000006',   // 绑定 x3，只用于生成桶（独立的按账号计数）
  a1: '6f540000-0000-4000-8000-000000000011', a2: '6f540000-0000-4000-8000-000000000012', a3: '6f540000-0000-4000-8000-000000000013',
  aO: '6f540000-0000-4000-8000-000000000014',
  h1: '6f540000-0000-4000-8000-0000000000a1', h2: '6f540000-0000-4000-8000-0000000000a2',
  x1: '6f540000-0000-4000-8000-000000000c01',   // 有快照（由家长打开时生成）
  x2: '6f540000-0000-4000-8000-000000000c02',   // 没有权益
  x3: '6f540000-0000-4000-8000-000000000c03',   // 洞察服务不可用
  xO: '6f540000-0000-4000-8000-000000000c0a',
  c1: '6f540000-0000-4000-8000-0000000000e1',   // 对学生与家长都可见的掌握判断
  c2: '6f540000-0000-4000-8000-0000000000e2',   // 只对家长可见的掌握判断（学生端不出）
  c3: '6f540000-0000-4000-8000-0000000000e3',   // 模板不在对外白名单的掌握判断（两端都不出）
  cE: '6f540000-0000-4000-8000-0000000000e4',   // 考试判断（引用成绩记录，对家长可见）
  rec: '6f540000-0000-4000-8000-0000000000f1',
  goal: '6f540000-0000-4000-8000-0000000000f2',
  missing: '6f540000-0000-4000-8000-00000000ffff',
};
const TPL = { mastery: 'syn.mp3.node_mastery', hidden: 'syn.mp3.not_external', exam: 'syn.mp3.exam' };
const NODES = ['MATH.SYN.MP3.ALPHA', 'MATH.SYN.MP3.BETA', 'MATH.SYN.MP3.GAMMA'];
const ALLOWED = new Set([`${TPL.mastery}@1`, `${TPL.exam}@1`]);

let su: pg.Pool;
let bff: pg.Pool;
let on: ShellHarness;
let off: ShellHarness;
let bare: ShellHarness;
let month = '';
const logs: Record<string, unknown>[] = [];

/** 假的洞察服务生成接口：x1 写一份快照并回 ready；x2 回“不可用”（没有权益）；x3 不可用；
 *  家长 gU 代表 x3 时：先在库里解除 gU 的绑定（模拟生成中途解绑），再回 ready（不写快照） */
const generated: { student: string; month: string; actor: string }[] = [];
const fake: MonthlyPlanClient = {
  async generate(student, m, actor): Promise<GenerateOutcome> {
    generated.push({ student, month: m, actor });
    if (actor === ID.gU) {
      await su.query(`UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = $1`, [ID.gU]);
      return 'ready';
    }
    if (student === ID.x1) {
      await writeSnapshot(student);
      return 'ready';
    }
    return student === ID.x2 ? 'not_available' : 'unavailable';
  },
};

async function replica(sql: string, params: unknown[]): Promise<void> {
  const c = await su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(sql, params);
    await c.query('COMMIT');
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    throw e;
  } finally {
    c.release(true);
  }
}

const day = (d: number) => `${month.slice(0, 8)}${String(d).padStart(2, '0')}`;
const node = (i: number, claim: string, tpl: string) => ({
  position: i + 1, subject: 'aya', node_id: NODES[i]!, cognitive_level: 'apply', claim_id: claim, template_key: tpl, template_version: 1,
  copy_key: 'monthly_plan.student.priority_node',
});

/** 形状合格的快照（与 0054 的 CHECK 一致；守卫在复制模式下跳过——真实写入的守卫由洞察服务与 db 测试覆盖） */
async function writeSnapshot(student: string, templateVersion = 'syn-tpl-0.1.0'): Promise<void> {
  const studentPlan = {
    priority_nodes: [node(0, ID.c1, TPL.mastery), node(1, ID.c2, TPL.mastery), node(2, ID.c3, TPL.hidden)],
    weekly_rhythm: { sessions_per_week: 5, minutes_per_session: 30, copy_key: 'monthly_plan.student.weekly_rhythm' },
    retests: { count: 2, dates: [day(1), day(12)], by_subject: [{ subject: 'aya', count: 2 }], copy_key: 'monthly_plan.student.retests' },
    mini_reviews: [{ subject: 'aya', date: day(24), copy_key: 'monthly_plan.student.mini_review' }],
  };
  const notes = [
    { key: 'quiet_time_for_retests', copy_key: 'monthly_plan.guardian.quiet_time_for_retests', vars: { retest_count: 2, sessions_per_week: 5, minutes_per_session: 30 } },
    { key: 'weekly_rhythm_support', copy_key: 'monthly_plan.guardian.weekly_rhythm_support', vars: { sessions_per_week: 5, minutes_per_session: 30 } },
  ];
  const review = { last_month: null, score_gaps: [{ subject: 'aya', goal_id: ID.goal, exam_record_id: ID.rec, claim_id: ID.cE, template_key: TPL.exam,
                                                    template_version: 1, copy_key: 'monthly_plan.review.score_gap' }] };
  const refs = { as_of: '2026-10-01T00:00:00Z', claims: [], mastery: [], exam_records: [], exam_plans: [], goals: [], retest_tasks: [], prev_plan_id: null,
                 fingerprints: { claims_policy: 'a'.repeat(32), config: 'b'.repeat(32), templates: 'c'.repeat(32), today: 'd'.repeat(32) } };
  await replica(`INSERT INTO insight.monthly_plan (student_id, month, student_plan, guardian_notes, review, plan_basis, input_refs, algorithm_version,
                                                    config_version, template_version, policy_weights, inputs_digest)
                 VALUES ($1, $2::date, $3, $4, $5, '{"items": []}', $6, 'sie-monthly@0.1.0', 'syn-0.1.0_syn-0.1.0', $7, 'default', $8)`,
                [student, month, JSON.stringify(studentPlan), JSON.stringify(notes), JSON.stringify(review), JSON.stringify(refs), templateVersion,
                 'e'.repeat(64)]);
}

before(async () => {
  if (!HAS_DB) return;
  su = new pg.Pool({ user: 'postgres', max: 3 });
  await su.query(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bff_app') THEN
      CREATE ROLE bff_app LOGIN IN ROLE starbuddy_bff;
    END IF; END $$;`);
  month = (await su.query(`SELECT to_char(date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD') AS m`)).rows[0].m;
  const seeded = await su.query('SELECT 1 FROM platform.household WHERE id = $1', [ID.h1]);
  if (seeded.rowCount === 0) {
    await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian'), ($3, 'guardian'), ($4, 'guardian'),
                      ($5, 'student'), ($6, 'student'), ($7, 'student'), ($8, 'student')`,
                   [ID.g1, ID.g2, ID.gR, ID.gO, ID.a1, ID.a2, ID.a3, ID.aO]);
    await su.query('INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2), ($3, $4)', [ID.h1, ID.g1, ID.h2, ID.gO]);
    await su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
                      ($1, $2, $9, 8, 'nanjing', 'bubian-2024', '2013-02-01'), ($3, $4, $9, 8, 'nanjing', 'bubian-2024', '2013-03-01'),
                      ($5, $6, $9, 8, 'nanjing', 'bubian-2024', '2013-04-01'), ($7, $8, $10, 8, 'nanjing', 'bubian-2024', '2013-05-01')`,
                   [ID.x1, ID.a1, ID.x2, ID.a2, ID.x3, ID.a3, ID.xO, ID.aO, ID.h1, ID.h2]);
    await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES
                      ($1, $4, 'household_creator'), ($1, $5, 'household_creator'), ($1, $6, 'household_creator'), ($2, $4, 'invite'),
                      ($3, $4, 'invite'), ($7, $8, 'household_creator')`, [ID.g1, ID.g2, ID.gR, ID.x1, ID.x2, ID.x3, ID.gO, ID.xO]);
    await su.query(`INSERT INTO platform.subject_enrollment (student_id, subject) VALUES ($1, 'aya'), ($2, 'aya'), ($3, 'aya'), ($4, 'aya')`,
                   [ID.x1, ID.x2, ID.x3, ID.xO]);
    await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian')`, [ID.gU, ID.gL]);
    await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $3, 'invite'), ($2, $3, 'invite')`,
                   [ID.gU, ID.gL, ID.x3]);
    // 判断模板（合成占位，已审核）与判断：c1 两端可见、c2 只对家长可见、c3 模板不在对外白名单、cE 考试判断（引用成绩记录）
    await su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    VALUES ($1, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）{node_id}', 'approved', 'synthetic', now()),
                           ($2, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）{node_id}', 'approved', 'synthetic', now()),
                           ($3, 1, 'exam', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）对账', 'approved', 'synthetic', now())
                    ON CONFLICT DO NOTHING`, [TPL.mastery, TPL.hidden, TPL.exam]);
    const claim = (id: string, target: string, kind: string, tpl: string, vis: string[]) => replica(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type, status,
                                  change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
       VALUES ($1, $2, 'aya', $3, $4, $5, 1, 0.7, 'inferred', 'established', 'created', $6, now() + interval '30 days', 'mastery_estimate',
               gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], now(), now())`, [id, ID.x1, kind, target, tpl, vis]);
    await claim(ID.c1, NODES[0]!, 'node', TPL.mastery, ['agent', 'student', 'guardian']);
    await claim(ID.c2, NODES[1]!, 'node', TPL.mastery, ['agent', 'guardian']);
    await claim(ID.c3, NODES[2]!, 'node', TPL.hidden, ['agent', 'student', 'guardian']);
    await claim(ID.cE, ID.rec, 'exam', TPL.exam, ['agent', 'student', 'guardian']);
    // 最近一次数学成绩 92.5 / 120（学生自录）与 g1 的诉求（数学目标 105 / 120）：分差 12.5 → 10–15 一档
    await replica(`INSERT INTO insight.exam_record (id, student_id, subject, exam_type, exam_date, full_score, raw_score, entered_by_role, entered_by_account_id, source_type)
                   VALUES ($1, $2, 'aya', 'midterm', (now() AT TIME ZONE 'Asia/Shanghai')::date - 3, 120, 92.5, 'self', $3, 'self')`, [ID.rec, ID.x1, ID.a1]);
    await replica(`INSERT INTO insight.guardian_goal (id, student_id, guardian_account_id, version, focus_subjects, target_scores, note)
                   VALUES ($1, $2, $3, 1, '{aya}', '[{"subject": "aya", "full_score": 120, "target_score": 105}]', '合成说明：只给作者看')`,
                  [ID.goal, ID.x1, ID.g1]);
  }
  bff = new pg.Pool({ user: 'bff_app', max: 5 });
  const shell = { config: { ...loadShellConfig(), externalTemplates: ALLOWED } };
  on = await startShell(bff, su, { log: (l) => logs.push(l), shell, familyGoals: { enabled: true }, monthlyPlan: { enabled: true, client: fake } });
  off = await startShell(bff, su, { log: () => {}, shell });
  bare = await startShell(bff, su, { log: () => {}, shell, monthlyPlan: { enabled: true } });  // 开关开、没配洞察服务
  // gR 拿到会话之后解除绑定（旧会话照样不能读）
  await on.sessionFor(ID.gR);
  await su.query(`UPDATE platform.guardian_student_link SET status = 'revoked', revoked_at = now() WHERE guardian_account_id = $1`, [ID.gR]);
});

after(async () => {
  if (!HAS_DB) return;
  await on.close();
  await off.close();
  await bare.close();
  await bff.end();
  await su.end();
});

const gp = (s: string) => `/students/${s}/monthly-plan`;
const sp = (s: string) => `/students/${s}/my-plan`;
const plans = async (s: string) => Number((await su.query('SELECT count(*) AS n FROM insight.monthly_plan WHERE student_id = $1', [s])).rows[0].n);

test('开关关闭：两条路由都不注册，与表外路径同一个 404（家长、学生、未登录都一样）', { skip: !HAS_DB }, async () => {
  const g = await off.sessionFor(ID.g1);
  const s = await off.sessionFor(ID.a1);
  const unknown = await callAs(off.base, 'GET', `/students/${ID.x1}/no-such-route`, g);
  for (const [who, sess] of [['家长', g], ['学生', s], ['未登录', null]] as const) {
    for (const p of [gp(ID.x1), sp(ID.x1)]) {
      const r = await callAs(off.base, 'GET', p, sess);
      assert.equal(r.status, 404, `${who} ${p}`);
      assert.equal(r.text, unknown.text, `${who} ${p} 与表外路径逐字节相同`);
    }
  }
});

test('学生端本月没有快照：plan = null，不触发生成；家长会话调学生端 403', { skip: !HAS_DB }, async () => {
  const before = generated.length;
  const s = await on.sessionFor(ID.a1);
  const r = await callAs(on.base, 'GET', sp(ID.x1), s);
  assert.equal(r.status, 200, r.text);
  assert.deepEqual(r.body, { student_id: ID.x1, month: month.slice(0, 7), plan: null });
  assert.equal(generated.length, before, '学生端不触发生成');
  assert.equal(await plans(ID.x1), 0);
  const g = await callAs(on.base, 'GET', sp(ID.x1), await on.sessionFor(ID.g1));
  assert.equal(g.status, 403);
  assert.equal(g.text, P.FORBIDDEN);
});

test('学生会话、别家、已解除绑定、不存在：家长端同一个 404，生成接口一次也没被调', { skip: !HAS_DB }, async () => {
  const g1 = await on.sessionFor(ID.g1);
  const missing = await callAs(on.base, 'GET', gp(ID.missing), g1);
  assert.equal(missing.status, 404);
  assert.equal(missing.text, P.NOT_FOUND);
  const before = generated.length;
  const cases: [string, Awaited<ReturnType<typeof callAs>>][] = [
    ['学生本人', await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.a1))],
    ['兄妹的学生会话', await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.a2))],
    ['另一户家长', await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.gO))],
    ['已解除绑定的家长（旧会话）', await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.gR))],
    ['非 UUID', await callAs(on.base, 'GET', '/students/not-a-uuid/monthly-plan', g1)],
    ['学生会话 + 别家', await callAs(on.base, 'GET', gp(ID.xO), await on.sessionFor(ID.a1))],
  ];
  for (const [what, r] of cases) {
    assert.equal(r.status, 404, what);
    assert.equal(r.text, missing.text, `${what}：与不存在逐字节相同`);
  }
  assert.equal(generated.length, before, '越权请求没有到达洞察服务');
  assert.equal(await plans(ID.x1), 0);
  // 学生会话在 handler 入口之前就被拒（server.ts 的 guardianOnly404）：访问日志没有 route_group
  const studentLines = logs.filter((l) => l['account_kind'] === 'student' && String(l['route']).endsWith('/monthly-plan'));
  assert.ok(studentLines.length >= 3);
  assert.ok(studentLines.every((l) => l['route_group'] === undefined));
  // 学生端：兄妹、另一户学生、不存在同一个 404
  const sMissing = await callAs(on.base, 'GET', sp(ID.missing), await on.sessionFor(ID.a2));
  assert.equal(sMissing.text, P.NOT_FOUND);
  assert.equal((await callAs(on.base, 'GET', sp(ID.x1), await on.sessionFor(ID.a2))).text, sMissing.text);
  assert.equal((await callAs(on.base, 'GET', sp(ID.x1), await on.sessionFor(ID.aO))).text, sMissing.text);
});

test('家长打开即生成、同月只生成一次：渲染出学习计划、注意事项与分差区间；每条关于学生的句子带 claim_id；白名单外的判断不出', { skip: !HAS_DB }, async () => {
  const g1 = await on.sessionFor(ID.g1);
  const before = generated.length;
  const r = await callAs(on.base, 'GET', gp(ID.x1), g1);
  assert.equal(r.status, 200, r.text);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.deepEqual(generated.slice(before), [{ student: ID.x1, month: month.slice(0, 7), actor: ID.g1 }], '生成带的是本人家长账号与本月');
  assert.equal(await plans(ID.x1), 1);
  assert.deepEqual(Object.keys(r.body).sort(), ['entitled', 'month', 'plan', 'student_id']);
  assert.equal(r.body.entitled, true);
  assert.equal(r.body.month, month.slice(0, 7));
  const p = r.body.plan;
  // c1、c2（家长可见）出；c3 的模板不在对外白名单：不出
  assert.deepEqual(p.student_plan.priority_nodes, [
    { claim_id: ID.c1, subject: 'aya', text: `{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜${NODES[0]}｜apply` },
    { claim_id: ID.c2, subject: 'aya', text: `{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜${NODES[1]}｜apply` },
  ]);
  assert.equal(p.student_plan.weekly_rhythm.text, '{{TODO_COPY:monthly_plan.student.weekly_rhythm}}｜5｜30');
  assert.deepEqual(p.student_plan.retests.dates, [day(1), day(12)]);
  assert.deepEqual(p.guardian_notes.map((n: { text: string }) => n.text), [
    '{{TODO_COPY:monthly_plan.guardian.quiet_time_for_retests}}｜2｜5｜30', '{{TODO_COPY:monthly_plan.guardian.weekly_rhythm_support}}｜5｜30']);
  assert.equal(p.review.last_month, null);
  assert.deepEqual(p.review.score_gaps.map((x: Record<string, unknown>) => [x['claim_id'], x['subject'], x['gap_band'], x['text']]),
                   [[ID.cE, 'aya', { reached: false, low: 10, high: 15 }, '{{TODO_COPY:monthly_plan.review.score_gap}}｜aya｜10–15']]);
  // 不出成绩、目标分、精确分差、诉求与系数、先后编号、模板键
  for (const k of ['92.5', '105', '12.5', 'goal_id', 'exam_record_id', 'focus', 'position', 'template_key', 'copy_key', 'plan_basis', 'input_refs', '合成说明']) {
    assert.ok(!r.text.includes(k), k);
  }
  const line = logs.filter((l) => l['route'] === 'GET /students/{student_id}/monthly-plan' && l['status'] === 200).at(-1)!;
  assert.equal(line['generate'], 'ready');
  assert.equal(line['dropped_statements'], '1');
  assert.equal(line['lexicon_hits'], undefined, '丢掉的那一条是白名单外的判断，不是命中词表');
  assert.ok(!JSON.stringify(line).includes('TODO_COPY'), '日志不记计划内容');
  // 同月第二次：已有快照，不再调洞察服务，响应逐字节相同
  const again = await callAs(on.base, 'GET', gp(ID.x1), g1);
  assert.equal(again.text, r.text);
  assert.equal(generated.length, before + 1, '同月只生成一次');
  assert.equal(await plans(ID.x1), 1);
});

test('第二位家长读同一份快照：分差只给作者家长（区间），学习计划与注意事项相同', { skip: !HAS_DB }, async () => {
  const r1 = await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.g1));
  const before = generated.length;
  const r2 = await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.g2));
  assert.equal(r2.status, 200, r2.text);
  assert.equal(generated.length, before);
  assert.deepEqual(r2.body.plan.student_plan, r1.body.plan.student_plan);
  assert.deepEqual(r2.body.plan.guardian_notes, r1.body.plan.guardian_notes);
  assert.deepEqual(r2.body.plan.review.score_gaps, [], '分差只给写了这份诉求的家长');
});

test('学生端只有学习计划：没有注意事项、回顾、分差、诉求与系数；只对家长可见的判断不出', { skip: !HAS_DB }, async () => {
  const before = generated.length;
  const r = await callAs(on.base, 'GET', sp(ID.x1), await on.sessionFor(ID.a1));
  assert.equal(r.status, 200, r.text);
  assert.deepEqual(Object.keys(r.body).sort(), ['month', 'plan', 'student_id']);
  assert.deepEqual(Object.keys(r.body.plan).sort(), ['generated_at', 'student_plan']);
  assert.deepEqual(r.body.plan.student_plan.priority_nodes,
                   [{ claim_id: ID.c1, subject: 'aya', text: `{{TODO_COPY:monthly_plan.student.priority_node}}｜aya｜${NODES[0]}｜apply` }]);
  for (const k of ['"guardian_notes"', '"review"', '"score_gaps"', '"gap_band"', '"goal_id"', '"focus_factor"', '"entitled"', '"last_month"',
                   'monthly_plan.guardian.', 'monthly_plan.review.', ID.c2, ID.cE, '合成说明']) {
    assert.ok(!r.text.includes(k), k);
  }
  assert.equal(generated.length, before);
});

test('没有权益（洞察服务统一的“不可用”回答）：entitled = false、plan = null，不带别的；5 分钟负缓存内不再问洞察服务', { skip: !HAS_DB }, async () => {
  const g1 = await on.sessionFor(ID.g1);
  const before = generated.length;
  const r = await callAs(on.base, 'GET', gp(ID.x2), g1);
  assert.equal(r.status, 200, r.text);
  assert.deepEqual(r.body, { student_id: ID.x2, month: month.slice(0, 7), entitled: false, plan: null });
  assert.equal(generated.length, before + 1);
  assert.equal(await plans(ID.x2), 0);
  // 负缓存（键：学生、家长、月份）：同一家长再打开，回答相同、不再调洞察服务；另一位家长有自己的键，照常问一次
  const again = await callAs(on.base, 'GET', gp(ID.x2), g1);
  assert.equal(again.text, r.text);
  assert.equal(generated.length, before + 1, '负缓存内不再问');
  const cached = logs.filter((l) => l['route'] === 'GET /students/{student_id}/monthly-plan' && l['generate'] === 'not_available_cached');
  assert.ok(cached.length >= 1);
  // 负缓存过期（注入 0 毫秒）后照常再问
  const s = await startShell(bff, su, { log: () => {}, shell: { config: { ...loadShellConfig(), externalTemplates: ALLOWED } },
                                        monthlyPlan: { enabled: true, client: fake, negativeTtlMs: 0 } });
  try {
    const g = await s.sessionFor(ID.g1);
    await callAs(s.base, 'GET', gp(ID.x2), g);
    await callAs(s.base, 'GET', gp(ID.x2), g);
    assert.equal(generated.length, before + 3, '负缓存过期后每次都问');
  } finally {
    await s.close();
  }
});

test('生成桶（按家长账号，每小时 20 次）：用完后按不可用处理，不再调洞察服务；命中负缓存不计生成桶', { skip: !HAS_DB }, async () => {
  const bucket = on.config.rate_limits['monthly_plan_generate.account.hour']!;
  assert.deepEqual(loadAuthConfig().rate_limits['monthly_plan_generate.account.hour'], { window_seconds: 3600, limit: 20 });
  const saved = bucket.limit;
  bucket.limit = 1;
  try {
    const gL = await on.sessionFor(ID.gL);
    const before = generated.length;
    const r1 = await callAs(on.base, 'GET', gp(ID.x3), gL);   // 第一次：调洞察服务（不可用 → 503）
    assert.equal(r1.status, 503);
    assert.equal(generated.length, before + 1);
    const r2 = await callAs(on.base, 'GET', gp(ID.x3), gL);   // 第二次：桶用完，不调洞察服务
    assert.equal(r2.status, 503);
    assert.equal(r2.text, P.TRUST_UNAVAILABLE);
    assert.equal(generated.length, before + 1, '桶用完后没有调洞察服务');
    assert.equal(logs.filter((l) => l['generate'] === 'rate_limited').length >= 1, true);
    // 已有快照的孩子不需要生成：照常读（不经生成桶）
    const ok = await callAs(on.base, 'GET', gp(ID.x1), await on.sessionFor(ID.g1));
    assert.equal(ok.status, 200);
  } finally {
    bucket.limit = saved;
  }
});

test('生成中途解除绑定：二次读取不通过，回与“不存在”相同的 404', { skip: !HAS_DB }, async () => {
  const gU = await on.sessionFor(ID.gU);
  const missing = await callAs(on.base, 'GET', gp(ID.missing), gU);
  const before = generated.length;
  const r = await callAs(on.base, 'GET', gp(ID.x3), gU);
  assert.equal(generated.length, before + 1, '生成前绑定有效，调了洞察服务');
  assert.equal(r.status, 404, r.text);
  assert.equal(r.text, missing.text);
});

test('本月按上海时区的月份：UTC 10 月 31 日 17:00 已是上海 11 月 1 日（与会话时区无关）', { skip: !HAS_DB }, async () => {
  const { monthExpr } = await import('../src/http/monthly-plan-routes.ts');
  const c = await bff.connect();
  try {
    for (const tz of ['UTC', 'America/New_York', 'Asia/Shanghai']) {
      await c.query(`SET TIME ZONE '${tz}'`);
      const at = (ts: string) => c.query<{ m: string }>(`SELECT ${monthExpr(`'${ts}'::timestamptz`)} AS m`).then((x) => x.rows[0]!.m);
      assert.equal(await at('2026-10-31T17:00:00Z'), '2026-11-01', tz);
      assert.equal(await at('2026-10-31T15:59:59Z'), '2026-10-01', tz);
    }
  } finally {
    await c.query('RESET TIME ZONE');
    c.release();
  }
});

test('洞察服务不可用或没配：503 UNAVAILABLE，不写行', { skip: !HAS_DB }, async () => {
  const r = await callAs(on.base, 'GET', gp(ID.x3), await on.sessionFor(ID.g1));
  assert.equal(r.status, 503);
  assert.equal(r.text, P.TRUST_UNAVAILABLE);
  const b = await callAs(bare.base, 'GET', gp(ID.x3), await bare.sessionFor(ID.g1));
  assert.equal(b.status, 503);
  assert.equal(b.text, P.TRUST_UNAVAILABLE);
  assert.equal(await plans(ID.x3), 0);
  // 没配洞察服务也不影响已有快照的读取
  const ok = await callAs(bare.base, 'GET', gp(ID.x1), await bare.sessionFor(ID.g1));
  assert.equal(ok.status, 200, ok.text);
  assert.equal(ok.body.entitled, true);
});

test('模板版本与 BFF 读到的模板库不一致、模板库或词表读不到：整份按不可用处理（503）', { skip: !HAS_DB }, async () => {
  const { loadTemplates, cleanWith } = await import('../src/family/monthly-plan.ts');
  const { noPromiseLexicon } = await import('../src/trust/lexicon.ts');
  const real = loadTemplates('test', cleanWith(lexicon(), noPromiseLexicon()));
  const variants: [string, NonNullable<Parameters<typeof startShell>[2]>['monthlyPlan']][] = [
    ['模板版本不一致', { enabled: true, client: fake, templates: (): TemplateLibrary => ({ ...real, version: 'syn-tpl-9.9.9' }) }],
    ['模板库读不到', { enabled: true, client: fake, templates: () => { throw new Error('missing'); } }],
    ['名次 / 概率 / 承诺词表读不到', { enabled: true, client: fake, noPromise: () => { throw new Error('missing'); } }],
    ['禁用推断词表读不到', { enabled: true, client: fake, lexicon: () => { throw new Error('missing'); } }],
  ];
  for (const [what, monthlyPlan] of variants) {
    const s = await startShell(bff, su, { log: () => {}, shell: { config: { ...loadShellConfig(), externalTemplates: ALLOWED } }, monthlyPlan });
    try {
      const r = await callAs(s.base, 'GET', gp(ID.x1), await s.sessionFor(ID.g1));
      assert.equal(r.status, 503, what);
      assert.equal(r.text, P.TRUST_UNAVAILABLE, what);
      const st = await callAs(s.base, 'GET', sp(ID.x1), await s.sessionFor(ID.a1));
      // 已知缺口（known gap）：学生视图（0054 insight.monthly_plan_student_view）不返回 template_version，学生端核不了版本号，
      // 版本不一致时学生端照样出——这不是期望行为，待迁移给学生视图补上 template_version 后改为 503（见下方 known gap 用例）
      if (what !== '模板版本不一致') assert.equal(st.status, 503, `学生端 ${what}`);
    } finally {
      await s.close();
    }
  }
});

test('成品句子命中词表：那一条不出，日志只记条数（不记内容）', { skip: !HAS_DB }, async () => {
  // 一张只认“周节奏”占位的合成词表冒充“名次 / 概率 / 承诺”词表：模拟签字文案或变量值带出命中的表述
  const fakeNoPromise = () => parseLexicon(['categories:', '  - id: syn_hit', '    patterns:', '      - "weekly_rhythm"', '    forbidden_examples: []',
                                            'allowed_examples: []'].join('\n'), 1);
  const lines: Record<string, unknown>[] = [];
  const { loadTemplates, cleanWith } = await import('../src/family/monthly-plan.ts');
  const { noPromiseLexicon } = await import('../src/trust/lexicon.ts');
  const real = loadTemplates('test', cleanWith(lexicon(), noPromiseLexicon()));  // 模板库本身照常按两张真词表加载
  const s = await startShell(bff, su, { log: (l) => lines.push(l), shell: { config: { ...loadShellConfig(), externalTemplates: ALLOWED } },
                                        monthlyPlan: { enabled: true, client: fake, noPromise: fakeNoPromise, templates: () => real } });
  try {
    const r = await callAs(s.base, 'GET', gp(ID.x1), await s.sessionFor(ID.g1));
    assert.equal(r.status, 200, r.text);
    assert.equal(r.body.plan.student_plan.weekly_rhythm, null, '命中的每周节奏不出');
    assert.equal(r.body.plan.guardian_notes.length, 1, '命中的注意事项不出');
    assert.equal(r.body.plan.student_plan.priority_nodes.length, 2, '其余照常');
    const line = lines.find((l) => l['route'] === 'GET /students/{student_id}/monthly-plan')!;
    assert.equal(line['dropped_statements'], '3');
    assert.equal(line['lexicon_hits'], '2', '命中词表的两条（每周节奏、一条注意事项）另记；白名单外的那条不算');
    assert.ok(!JSON.stringify(lines).includes('weekly_rhythm'));
  } finally {
    await s.close();
  }
});

test('known gap：学生视图不带模板版本——家长端按版本整份不可用，学生端暂时只能按模板键核对（待迁移补学生视图 template_version）', { skip: !HAS_DB }, async () => {
  // 已知缺口，不是期望行为：另一个学生的快照用别的模板版本写，家长端 503；学生端因为 0054 的学生视图不返回 template_version
  // 核不了版本，目前照样 200。迁移给学生视图补上 template_version 之后，学生端这里应改为 503（届时改本用例的断言）
  const x = randomUUID();
  const a = randomUUID();
  await su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [a]);
  await su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month)
                  VALUES ($1, $2, $3, 8, 'nanjing', 'bubian-2024', '2013-06-01')`, [x, a, ID.h1]);
  await su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`, [ID.g1, x]);
  await writeSnapshot(x, 'syn-tpl-0.0.9');
  const g = await callAs(on.base, 'GET', gp(x), await on.sessionFor(ID.g1));
  assert.equal(g.status, 503, '家长端：模板版本对不上');
  const st = await callAs(on.base, 'GET', sp(x), await on.sessionFor(a));
  assert.equal(st.status, 200, `known gap（当前行为）：${st.text}`);
  assert.deepEqual(st.body.plan.student_plan.priority_nodes, [], '判断属于另一个学生：视图读时再核不出');
});
