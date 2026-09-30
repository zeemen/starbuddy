// 统一壳 H4 读路由与家长写路由的集成测试（specs/unified-shell-sprint4/spec.md 第 5、6、8、9、10、12 节与对应验收）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，走真实 RLS 与真实会话（shell-harness.ts）；
// 计量时钟可注入（harness.ts 的 bfftest.clock）；学习服务与洞察服务用按契约返回的假实现（spec“依赖与输入”）。
// 全部合成数据（规则 8）：地图节点、判断、学生都是占位。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { after, before, test } from 'node:test';
import type pg from 'pg';
import { activeRoutes, ROUTES } from '../src/http/routes.ts';
import { loadShellConfig, type ShellConfig } from '../src/shell/config.ts';
import type { ExplainRequest, InsightClient, RenderedStatement } from '../src/shell/insight-client.ts';
import type { PlanClient, PlanSection, PlanSubject } from '../src/shell/plan-client.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const CONFIG = 'shelltest-plan@1';
const MAP = '5c000000-0000-4000-8000-0000000000a1';
const TPL = 'syn.math.node_mastery';
const N = { a: 'MATH.SHT.T1.A', b: 'MATH.SHT.T1.B', c: 'MATH.SHT.T1.C', d: 'MATH.SHT.T1.D', e: 'MATH.SHT.T1.E', f: 'MATH.SHT.T1.F' };
const FORBIDDEN = ['p_mastery', 'confidence":', 'evidence_ids', 'learner_context', 'evidence_chain', 'skill_nodes'];

let h: Harness;
let shell: ShellHarness;
const logs: Record<string, unknown>[] = [];
let clock = Date.now();

// ---------------------------------------------------------------- 假学习服务与假洞察服务
class FakePlan implements PlanClient {
  calls: { student: string; subjects: PlanSubject[] }[] = [];
  down = false;
  sections: (subjects: PlanSubject[]) => PlanSection[] = (subjects) => subjects.map((s) => ({ subject: s, status: 'unavailable', tasks: [] }));
  async plan(student: string, subjects: PlanSubject[]) {
    this.calls.push({ student, subjects });
    return this.down ? null : this.sections(subjects);
  }
}
class FakeInsight implements InsightClient {
  calls: ExplainRequest[] = [];
  silent = new Set<string>();  // 这些判断“超时”（返回 null）
  slow = new Map<string, number>();  // 这些判断延迟多少毫秒才回
  templateOf = new Map<string, string>();
  inFlight = 0;
  maxInFlight = 0;
  async explain(req: ExplainRequest): Promise<RenderedStatement | null> {
    this.calls.push(req);
    this.inFlight += 1;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    try {
      await new Promise((r) => setTimeout(r, this.slow.get(req.claimId) ?? 20));
    } finally {
      this.inFlight -= 1;
    }
    if (this.silent.has(req.claimId)) return null;
    return { claim_id: req.claimId, template_key: this.templateOf.get(req.claimId) ?? TPL, template_version: 1, status: 'established', display: 'solid',
             text: `（合成模板句 ${req.claimId.slice(-4)}）`, confidence_band: 'moderate' };
  }
}
const plan = new FakePlan();
const insight = new FakeInsight();
const TPL2 = 'syn.math.node_transfer';
// 白名单里有两个合成模板；理由编码只登记了 TPL（TPL2 的判断不作为这些编码的理由，审查 M3）
const cfg: ShellConfig = { ...loadShellConfig(), externalTemplates: new Set([`${TPL}@1`, `${TPL2}@1`]),
  // weak_mastery 另登记了白名单外的模板（加载器会拒绝这种配置；这里直接注入，证明白名单是独立的一道关）
  rationaleTemplates: new Map([['retest_scheduled', new Set([`${TPL}@1`])], ['weak_mastery', new Set([`${TPL}@1`, 'syn.math.not_listed@1'])]]) };

// ---------------------------------------------------------------- 合成家庭
interface Kid { id: string; account: string }
interface Fam { guardian: string; household: string; kids: Record<string, Kid> }
let F: Fam;      // 主家庭：T 试用、P 试点基本档、Q 试点专业档、R 留存、X 没有订阅
let O: Fam;      // 另一户
let period: { start: number; end: number };

async function family(kids: string[]): Promise<Fam> {
  const guardian = randomUUID();
  const household = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [guardian]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, guardian]);
  const out: Record<string, Kid> = {};
  for (const k of kids) {
    const account = randomUUID();
    const id = randomUUID();
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [account]);
    await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 9, 'nanjing')`,
                     [id, account, household]);
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`,
                     [guardian, id]);
    out[k] = { id, account };
  }
  return { guardian, household, kids: out };
}

async function subscribe(f: Fam, kid: Kid, source: 'trial' | 'pilot_virtual', tier: 'basic' | 'pro', ended = false): Promise<void> {
  const { rows } = await h.su.query<{ id: string }>(
    `INSERT INTO platform.subscription (household_id, student_id, tier, config_version, source, period_start, period_end)
     VALUES ($1, $2, $3::platform.plan_tier, $4, $5, $6, $7) RETURNING id`,
    [f.household, kid.id, tier, CONFIG, source, new Date(period.start), new Date(period.end)]);
  await h.su.query('SELECT platform.meter_grant_period($1)', [rows[0]!.id]);
  if (ended) await h.su.query(`UPDATE platform.subscription SET status = 'ended' WHERE id = $1`, [rows[0]!.id]);
}

/** 合成判断：复制模式直接写行（0010 的触发器只允许洞察服务写入；这里只在一次性测试库里造数据） */
async function claim(kid: Kid, node: string, o: { status?: string; visibility?: string[]; template?: string; expired?: boolean } = {}): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                  source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
       VALUES ($1, $2, 'aya', 'node', $3, $4, 1, 0.7, 'inferred', $5::insight.claim_status, 'created', $6, $7,
               'mastery_estimate', gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], $8, $8)`,
      [id, kid.id, node, o.template ?? TPL, o.status ?? 'established', o.visibility ?? ['agent', 'student', 'guardian'],
       new Date(Date.now() + (o.expired ? -3600_000 : 30 * 86400_000)), new Date(Date.now() - (o.expired ? 7200_000 : 0))]);
    await c.query('COMMIT');
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    throw e;
  } finally {
    c.release(true);
  }
  return id;
}

const sess = (account: string) => shell.sessionFor(account);
const get = async (account: string | null, path: string) => callAs(shell.base, 'GET', path, account ? await sess(account) : null);
const post = async (account: string, path: string, body?: unknown) => callAs(shell.base, 'POST', path, await sess(account), body);
const put = async (account: string, path: string, body?: unknown) => callAs(shell.base, 'PUT', path, await sess(account), body);
const shanghai = (isoLocal: string) => Date.parse(`${isoLocal}+08:00`);

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  clock = Date.now();
  await h.setClock(clock);
  // 新规则的额度配置（照抄 0015 的草稿 v12-pilot@1：基本档核心任选 1 科、选修 0；专业档核心全开、选修 4）
  if ((await h.su.query('SELECT 1 FROM platform.plan_config WHERE version = $1', [CONFIG])).rowCount === 0) {
    await h.su.query(`INSERT INTO platform.plan_config (version, note_code) VALUES ($1, 'shell_integration_test')`, [CONFIG]);
    await h.su.query(`INSERT INTO platform.plan_allowance (config_version, tier, resource_type, quantity, fair_use)
                      SELECT $1, tier, resource_type, quantity, fair_use FROM platform.plan_allowance WHERE config_version = 'v12-pilot@1'`, [CONFIG]);
    await h.su.query(`INSERT INTO platform.plan_feature (config_version, tier, feature_code, value_code)
                      SELECT $1, tier, feature_code, value_code FROM platform.plan_feature WHERE config_version = 'v12-pilot@1'`, [CONFIG]);
    await h.su.query(`UPDATE platform.plan_config SET status = 'published', published_at = now() WHERE version = $1`, [CONFIG]);
  }
  // 已发布的合成数学地图（节点名给今日计划的标题用）与白名单里的合成模板
  if ((await h.su.query('SELECT 1 FROM kge.map_version WHERE id = $1', [MAP])).rowCount === 0
      && (await h.su.query(`SELECT 1 FROM kge.map_version WHERE subject = 'aya' AND status = 'published'`)).rowCount === 0) {
    const c = await h.su.connect();
    try {
      await c.query('BEGIN');
      await c.query('SET LOCAL session_replication_role = replica');
      await c.query(`INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES ($1, 'aya', 2022, '0.0.7', 'published', now())`, [MAP]);
      await c.query(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
                       ($1, 'MATH.SHT', 'domain', NULL, NULL, '（合成）领域'), ($1, 'MATH.SHT.T1', 'topic', 'MATH.SHT', NULL, '（合成）主题')`, [MAP]);
      for (const [k, id] of Object.entries(N)) {
        await c.query(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES ($1, $2, 'node', 'MATH.SHT.T1', 'kp', $3)`,
                      [MAP, id, `（合成）节点 ${k.toUpperCase()}`]);
      }
      await c.query('COMMIT');
    } finally {
      c.release(true);
    }
  }
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    VALUES ($1, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now()),
                           ('syn.math.node_transfer', 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now()),
                           ('syn.math.not_listed', 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now())
                    ON CONFLICT DO NOTHING`, [TPL]);
  period = { start: clock - 86400_000, end: clock - 86400_000 + 14 * 86400_000 };
  F = await family(['T', 'P', 'Q', 'R', 'X']);
  O = await family(['Y']);
  await subscribe(F, F.kids['T']!, 'trial', 'basic');
  await subscribe(F, F.kids['P']!, 'pilot_virtual', 'basic');
  await subscribe(F, F.kids['Q']!, 'pilot_virtual', 'pro');
  await subscribe(F, F.kids['R']!, 'trial', 'basic', true);
  await subscribe(O, O.kids['Y']!, 'pilot_virtual', 'pro');
  shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l), now: () => new Date(clock), shell: { config: cfg, plan, insight } });
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await h.close();
});

// ---------------------------------------------------------------- 选科（第 5 节）

test('学科目录：只列适用年级、非 planned 的学科（英语不出现）；口径只做展示；beta 在非试点下不开放', { skip: !HAS_DB }, async () => {
  const t = await get(F.kids['T']!.account, `/students/${F.kids['T']!.id}/enrollments`);
  assert.equal(t.status, 200);
  assert.equal(t.headers.get('cache-control'), 'no-store');
  assert.deepEqual(t.body.subjects.map((s: any) => s.subject), ['math', 'chinese', 'politics']);
  assert.deepEqual(t.body.rules, { core_subjects: 'pick_one', elective_slots: '0', max_active_subjects: null });
  assert.ok(t.body.subjects.every((s: any) => s.open === false), '试用（非试点）下 beta 学科都不开放');
  const p = await get(F.guardian, `/students/${F.kids['P']!.id}/enrollments`);
  const open = Object.fromEntries(p.body.subjects.map((s: any) => [s.subject, s.open]));
  assert.deepEqual(open, { math: true, chinese: true, politics: false });  // 道法没有已发布内容版本
  assert.ok(!JSON.stringify(p.body).includes('english'));
});

test('选科 outcome 一一对应：enrolled / already_active / limit_reached（pick_one）/ paused / not_active / change_next_period', { skip: !HAS_DB }, async () => {
  const P = F.kids['P']!.id;
  const g = F.guardian;
  assert.deepEqual((await post(g, `/students/${P}/enrollments/math`)).body, { outcome: 'enrolled', slot: 'core', limit: 1 });
  assert.deepEqual((await post(g, `/students/${P}/enrollments/math`)).body, { outcome: 'already_active' });
  assert.deepEqual((await post(g, `/students/${P}/enrollments/chinese`)).body, { outcome: 'limit_reached', slot: 'core', limit: 1 });
  assert.deepEqual((await post(g, `/students/${P}/enrollments/math/pause`)).body, { outcome: 'paused' });
  assert.deepEqual((await post(g, `/students/${P}/enrollments/math/pause`)).body, { outcome: 'not_active' });
  assert.deepEqual((await post(g, `/students/${P}/enrollments/chinese`)).body, { outcome: 'change_next_period', slot: 'core' });
  // 学生端只读：看到的状态与家长一致
  const view = await get(F.kids['P']!.account, `/students/${P}/enrollments`);
  assert.equal(view.body.subjects.find((s: any) => s.subject === 'math').enrollment, 'paused');
});

test('选科 outcome：change_limit_reached、no_subscription、not_launched；英语与不认识的学科码 404', { skip: !HAS_DB }, async () => {
  const g = F.guardian;
  const Q = F.kids['Q']!.id;
  assert.equal((await post(g, `/students/${Q}/enrollments/math`)).body.outcome, 'enrolled');
  assert.equal((await post(g, `/students/${Q}/enrollments/chinese`)).body.outcome, 'enrolled');
  assert.deepEqual((await post(g, `/students/${Q}/enrollments/math/pause`)).body, { outcome: 'paused' });
  assert.deepEqual((await post(g, `/students/${Q}/enrollments/chinese/pause`)).body, { outcome: 'change_limit_reached' });
  assert.deepEqual((await post(g, `/students/${F.kids['X']!.id}/enrollments/math`)).body, { outcome: 'no_subscription' });
  assert.deepEqual((await post(g, `/students/${F.kids['T']!.id}/enrollments/math`)).body, { outcome: 'not_launched' });
  assert.deepEqual((await post(g, `/students/${Q}/enrollments/politics`)).body, { outcome: 'not_launched' });
  const ref = await post(g, `/students/${randomUUID()}/enrollments/math`);
  assert.equal(ref.status, 404);
  for (const path of [`/students/${Q}/enrollments/english`, `/students/${Q}/enrollments/history`, `/students/${Q}/enrollments/klingon`,
                      `/students/${O.kids['Y']!.id}/enrollments/math`]) {
    const r = await post(g, path);
    assert.equal(r.status, 404, path);
    assert.equal(r.text, ref.text, path);
  }
});

test('学生会话调选科、暂停、时长设置一律 403；另一户家长 404', { skip: !HAS_DB }, async () => {
  const kid = F.kids['Q']!;
  for (const [m, p] of [['POST', `/students/${kid.id}/enrollments/math`], ['POST', `/students/${kid.id}/enrollments/math/pause`],
                        ['PUT', `/students/${kid.id}/time-guard`]] as const) {
    const r = await callAs(shell.base, m, p, await sess(kid.account), m === 'PUT' ? { daily_cap_minutes: 10, curfew_start: null, curfew_end: null } : undefined);
    assert.equal(r.status, 403, p);
  }
  for (const p of ['enrollments', 'today', 'balance', 'time-guard']) {
    assert.equal((await get(O.guardian, `/students/${kid.id}/${p}`)).status, 404, p);
    assert.equal((await get(null, `/students/${kid.id}/${p}`)).status, 401, p);
  }
});

// ---------------------------------------------------------------- 今日计划与推荐理由（第 6、8 节）

test('今日计划：按科分组、按 priority 排序；推荐理由按判断显示；响应没有掌握概率、置信度、证据', { skip: !HAS_DB }, async () => {
  const kid = F.kids['Q']!;
  // Q 在读数学（上一条测试暂停了数学，这里重新开一门：Q 是专业档，核心全开）
  await h.su.query(`UPDATE platform.subject_enrollment SET status = 'active', paused_at = NULL WHERE student_id = $1`, [kid.id]);
  const cA = await claim(kid, N.a);                                        // 成立、可见、白名单内 → 显示
  await claim(kid, N.c, { status: 'refuted' });                            // 已推翻 → 不显示
  await claim(kid, N.d, { expired: true });                                // 已过期 → 不显示
  const cE = await claim(kid, N.e, { template: 'syn.math.not_listed' });  // 白名单外 → 不显示（即使编码映射里有它）
  insight.templateOf.set(cE, 'syn.math.not_listed');
  const cF = await claim(kid, N.f);                                        // 洞察服务超时 → 不显示
  insight.silent.add(cF);
  plan.sections = (subjects) => subjects.map((s) => s === 'aya' ? {
    subject: s, status: 'ok' as const, tasks: [
      { kind: 'practice', target_node_ids: [N.b], priority: 0.7, estimated_minutes: 20, rationale_code: 'high_exam_weight' },
      { kind: 'retest', target_node_ids: [N.a], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
      { kind: 'practice', target_node_ids: [N.b], priority: 0.65, estimated_minutes: 15, rationale_code: 'weak_mastery' },  // 没有判断
      { kind: 'practice', target_node_ids: [N.c], priority: 0.6, estimated_minutes: 15, rationale_code: 'weak_mastery' },
      { kind: 'practice', target_node_ids: [N.d], priority: 0.5, estimated_minutes: 15, rationale_code: 'weak_mastery' },
      { kind: 'practice', target_node_ids: [N.e], priority: 0.4, estimated_minutes: 15, rationale_code: 'weak_mastery' },
      { kind: 'practice', target_node_ids: [N.f], priority: 0.3, estimated_minutes: 15, rationale_code: 'weak_mastery' },
      { kind: 'practice', target_node_ids: [N.a], priority: 0.2, estimated_minutes: 15, rationale_code: 'mystery_code' },  // 映射表外
      { kind: 'lesson', target_node_ids: [], priority: 0.1, estimated_minutes: 5, rationale_code: 'weak_mastery' },
    ] } : { subject: s, status: 'unavailable' as const, tasks: [] });
  insight.calls = [];
  const r = await get(kid.account, `/students/${kid.id}/today`);
  assert.equal(r.status, 200, r.text);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.deepEqual(plan.calls.at(-1), { student: kid.id, subjects: ['aya', 'wenli'] });
  assert.deepEqual(r.body.subjects.map((s: any) => [s.subject, s.status]), [['math', 'ok'], ['chinese', 'unavailable']]);
  const tasks = r.body.subjects[0].tasks;
  assert.deepEqual(tasks.map((t: any) => t.priority), [0.9, 0.7, 0.65, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]);
  assert.deepEqual(tasks[0].rationale, { kind: 'claim', claim_id: cA, text: `（合成模板句 ${cA.slice(-4)}）`, confidence_band: 'moderate' });
  assert.deepEqual(tasks[1].rationale, { kind: 'content', code: 'high_exam_weight' });  // 内容类：固定短句，不带 claim_id
  for (const t of tasks.slice(2)) assert.equal(t.rationale, null, JSON.stringify(t));
  assert.equal(tasks[0].node_name, '（合成）节点 A');
  assert.deepEqual([tasks[8].node_id, tasks[8].node_name], [null, null]);  // 没有节点：中性标题由前端出
  const text = r.text;
  for (const k of FORBIDDEN) assert.ok(!text.includes(k), k);
  // 洞察服务只被问了可见、成立或假设、白名单内的判断；代表学生本人
  assert.deepEqual(insight.calls.map((c) => c.claimId).sort(), [cA, cF].sort());
  assert.ok(insight.calls.every((c) => c.audience === 'student' && c.actorRef === kid.account && c.linkId === undefined));
});

test('今日计划（家长）：只按 guardian 可见范围取判断，带绑定关系与置信度；学习服务不可用时每科“暂无安排”', { skip: !HAS_DB }, async () => {
  const kid = F.kids['Q']!;
  const hidden = await claim(kid, N.b, { visibility: ['agent', 'student'] });  // 家长不可见
  plan.sections = (subjects) => subjects.map((s) => ({ subject: s, status: 'ok' as const, tasks: s !== 'aya' ? [] : [
    { kind: 'retest', target_node_ids: [N.a], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
    { kind: 'retest', target_node_ids: [N.b], priority: 0.8, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
  ] }));
  insight.calls = [];
  const r = await get(F.guardian, `/students/${kid.id}/today`);
  const tasks = r.body.subjects[0].tasks;
  assert.equal(tasks[0].rationale.kind, 'claim');
  assert.equal(tasks[0].rationale.confidence_band, 'moderate');
  assert.equal(tasks[1].rationale, null);
  assert.ok(!insight.calls.some((c) => c.claimId === hidden));
  const { rows } = await h.su.query('SELECT id FROM platform.guardian_student_link WHERE student_id = $1 AND guardian_account_id = $2', [kid.id, F.guardian]);
  assert.ok(insight.calls.every((c) => c.audience === 'guardian' && c.actorRef === F.guardian && c.linkId === rows[0].id));
  plan.down = true;
  const down = await get(kid.account, `/students/${kid.id}/today`);
  assert.equal(down.status, 200);
  assert.ok(down.body.subjects.every((s: any) => s.status === 'unavailable' && s.tasks.length === 0));
  plan.down = false;
  // 兄妹、另一户、不存在：404
  assert.equal((await get(F.kids['P']!.account, `/students/${kid.id}/today`)).status, 404);
  assert.equal((await get(O.guardian, `/students/${kid.id}/today`)).status, 404);
});

test('今日计划：每页取陈述有上限（配置），超过的任务不显示理由', { skip: !HAS_DB }, async () => {
  const kid = F.kids['Q']!;
  const c = await claim(kid, N.b);
  plan.sections = (subjects) => subjects.map((s) => ({ subject: s, status: 'ok' as const, tasks: s !== 'aya' ? [] : [
    { kind: 'retest', target_node_ids: [N.a], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
    { kind: 'retest', target_node_ids: [N.b], priority: 0.8, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
  ] }));
  const old = cfg.explain.maxPerPage;
  (cfg.explain as { maxPerPage: number }).maxPerPage = 1;
  try {
    const r = await get(kid.account, `/students/${kid.id}/today`);
    assert.equal(r.body.subjects[0].tasks[0].rationale.kind, 'claim');
    assert.equal(r.body.subjects[0].tasks[1].rationale, null, c);
  } finally {
    (cfg.explain as { maxPerPage: number }).maxPerPage = old;
  }
});

test('今日计划：理由编码只认登记给它的模板（白名单内、未登记给这个编码的判断不显示；审查 M3）', { skip: !HAS_DB }, async () => {
  const kid = F.kids['X']!;
  await h.su.query(`INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES ($1, 'aya', 'active') ON CONFLICT DO NOTHING`, [kid.id]);
  const other = await claim(kid, N.a, { template: TPL2 });
  insight.templateOf.set(other, TPL2);
  plan.sections = (subjects) => subjects.map((s) => ({ subject: s, status: 'ok' as const, tasks: s !== 'aya' ? [] : [
    { kind: 'retest', target_node_ids: [N.a], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
  ] }));
  insight.calls = [];
  const r = await get(kid.account, `/students/${kid.id}/today`);
  assert.equal(r.body.subjects[0].tasks[0].rationale, null);
  assert.deepEqual(insight.calls, []);  // 不在映射里的判断连洞察服务都不问
  const mine = await claim(kid, N.a);
  const r2 = await get(kid.account, `/students/${kid.id}/today`);
  assert.equal(r2.body.subjects[0].tasks[0].rationale.claim_id, mine);
});

test('今日计划：陈述并行取（并发上限）、整页总时限内没回来的句子不显示', { skip: !HAS_DB }, async () => {
  const kid = F.kids['X']!;
  const ids = [];
  for (const n of [N.b, N.c, N.d, N.e, N.f]) ids.push(await claim(kid, n));
  plan.sections = (subjects) => subjects.map((s) => ({ subject: s, status: 'ok' as const, tasks: s !== 'aya' ? [] : [N.b, N.c, N.d, N.e, N.f].map((n, i) => (
    { kind: 'practice', target_node_ids: [n], priority: 0.9 - i / 10, estimated_minutes: 10, rationale_code: 'weak_mastery' })) }));
  insight.slow.set(ids[4]!, 2_000);  // 这一句超过整页时限
  const e = cfg.explain as { pageDeadlineMs: number; concurrency: number };
  const old = { ...e };
  e.pageDeadlineMs = 500;
  e.concurrency = 2;
  insight.maxInFlight = 0;
  try {
    const started = Date.now();
    const r = await get(kid.account, `/students/${kid.id}/today`);
    assert.ok(Date.now() - started < 1_500, '整页不等超时的那一句');
    const rs = r.body.subjects[0].tasks.map((t: any) => t.rationale?.claim_id ?? null);
    assert.deepEqual(rs, [ids[0], ids[1], ids[2], ids[3], null]);
    assert.ok(insight.maxInFlight <= 2 && insight.maxInFlight >= 2, `并发 ${insight.maxInFlight}`);
  } finally {
    Object.assign(e, old);
    insight.slow.clear();
  }
});

test('今日计划限流：每会话每分钟（today.session.minute，config/auth.json）；超了是统一的 429', { skip: !HAS_DB }, async () => {
  const kid = F.kids['X']!;
  const rule = shell.config.rate_limits['today.session.minute']!;
  const old = rule.limit;
  rule.limit = 2;
  try {
    const fresh = await callAs(shell.base, 'GET', '/me', await sess(kid.account));  // 同一会话
    assert.equal(fresh.status, 200);
    const codes = [];
    for (let i = 0; i < 4; i++) codes.push((await get(kid.account, `/students/${kid.id}/today`)).status);
    assert.ok(codes.includes(429), codes.join(','));
    const r = await get(kid.account, `/students/${kid.id}/today`);
    assert.equal(r.body.code, 'TOO_MANY_REQUESTS');
  } finally {
    rule.limit = old;
  }
});

test('选科口径：主订阅要求计量时钟落在周期内（试用周期过了、续期任务没跑时不显示口径）', { skip: !HAS_DB }, async () => {
  const T = F.kids['T']!;
  assert.notEqual((await get(T.account, `/students/${T.id}/enrollments`)).body.rules, null);
  await h.setClock(period.end + 3600_000);
  try {
    assert.equal((await get(T.account, `/students/${T.id}/enrollments`)).body.rules, null);
  } finally {
    await h.setClock(clock);
  }
});

// ---------------------------------------------------------------- 额度与时长（第 9 节）

async function guard(kid: Kid): Promise<any> {
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query("SELECT set_config('app.account_id', $1, true)", [kid.account]);
    const { rows } = await c.query('SELECT platform.meter_guard_status($1) AS g', [kid.id]);
    await c.query('COMMIT');
    return rows[0].g;
  } finally {
    c.release(true);
  }
}

test('额度与时长：数值等于 meter_guard_status 与 entitlement_balance；学生合并一行，家长有分来源明细', { skip: !HAS_DB }, async () => {
  const kid = F.kids['T']!;
  const s = await get(kid.account, `/students/${kid.id}/balance`);
  assert.equal(s.status, 200, s.text);
  const g = await guard(kid);
  for (const k of ['plan_daily_cap', 'daily_cap', 'minutes_used_today', 'minutes_remaining_today', 'curfew_start', 'curfew_end',
                   'in_curfew', 'minutes_until_curfew']) {
    assert.deepEqual(s.body.time[k], g[k], k);
  }
  assert.equal(s.body.state, 'trialing');
  const { rows } = await h.su.query<{ resource_type: string; total: string }>(
    `SELECT resource_type, sum(remaining) AS total FROM platform.entitlement_balance WHERE student_id = $1 GROUP BY resource_type`, [kid.id]);
  assert.ok(rows.length > 0);
  for (const r of rows) assert.equal(s.body.balances.find((b: any) => b.resource_type === r.resource_type).remaining, Number(r.total));
  assert.ok(s.body.balances.every((b: any) => !('sources' in b)));
  const p = await get(F.guardian, `/students/${kid.id}/balance`);
  assert.ok(p.body.balances.every((b: any) => Array.isArray(b.sources) && b.sources.length >= 1));
});

test('试用还剩 N 天（上海自然日）；留存态每天 5 次自练；宵禁 23:30 / 05:59 显示、06:00 不显示；距宵禁 20 分钟提示', { skip: !HAS_DB }, async () => {
  const T = F.kids['T']!;
  await h.setClock(period.start);
  assert.equal((await get(T.account, `/students/${T.id}/balance`)).body.trial_days_left, 14);
  await h.setClock(period.start + 13 * 86400_000);
  assert.equal((await get(T.account, `/students/${T.id}/balance`)).body.trial_days_left, 1);
  const R = F.kids['R']!;
  const r = await get(R.account, `/students/${R.id}/balance`);
  assert.deepEqual([r.body.state, r.body.practice_remaining_today, r.body.trial_days_left, r.body.practice_daily_limit],
                   ['retained', cfg.meterDefaults.retainedDailyPractice, null, cfg.meterDefaults.retainedDailyPractice]);
  const day = new Date(clock + 8 * 3600_000).toISOString().slice(0, 10);
  const at = async (hhmm: string) => {
    await h.setClock(shanghai(`${day}T${hhmm}:00`));
    return (await get(T.account, `/students/${T.id}/balance`)).body.time;
  };
  assert.equal((await at('23:30')).in_curfew, true);
  assert.equal((await at('05:59')).in_curfew, true);
  const six = await at('06:00');
  assert.deepEqual([six.in_curfew, six.curfew_near], [false, false]);
  const soon = await at('21:40');
  assert.deepEqual([soon.in_curfew, soon.minutes_until_curfew, soon.curfew_near], [false, 20, true]);
  await h.setClock(clock);
});

test('时长设置：只能调低、只能收紧；学生端下一次请求看到新值，看不到设置人', { skip: !HAS_DB }, async () => {
  const T = F.kids['T']!;
  const g = F.guardian;
  const before = await get(g, `/students/${T.id}/time-guard`);
  assert.equal(before.status, 200);
  const cap = before.body.plan_daily_cap;
  assert.deepEqual((await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: cap + 1, curfew_start: null, curfew_end: null })).body,
                   { outcome: 'cap_above_plan', plan_cap: cap });
  assert.equal((await h.su.query('SELECT count(*)::int AS n FROM platform.student_time_guard WHERE student_id = $1', [T.id])).rows[0].n, 0);
  assert.deepEqual((await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: 30, curfew_start: '23:00', curfew_end: '06:00' })).body,
                   { outcome: 'curfew_must_cover_default' });
  assert.equal((await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: 30, curfew_start: '21:00' })).status, 422);
  assert.equal((await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: 30, curfew_start: '21:00', curfew_end: '21:00' })).status, 422);
  assert.deepEqual((await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: 30, curfew_start: '21:00', curfew_end: '07:00' })).body,
                   { outcome: 'saved', daily_cap: 30 });
  assert.deepEqual(before.body.default_curfew, { start: cfg.meterDefaults.curfewStart, end: cfg.meterDefaults.curfewEnd });  // 单处配置
  const seen = await get(T.account, `/students/${T.id}/time-guard`);
  assert.deepEqual([seen.body.daily_cap_minutes, seen.body.effective_daily_cap, seen.body.curfew_start, seen.body.curfew_end, seen.body.custom_curfew],
                   [30, 30, '21:00', '07:00', true]);
  assert.ok(!/updated_by|updated_at/.test(seen.text));
  assert.equal((await get(T.account, `/students/${T.id}/balance`)).body.time.daily_cap, 30);
  // 单日上限调到 0：额度页显示“今天的陪练到这里”
  await put(g, `/students/${T.id}/time-guard`, { daily_cap_minutes: 0, curfew_start: null, curfew_end: null });
  const b = await get(T.account, `/students/${T.id}/balance`);
  assert.deepEqual([b.body.time.daily_cap, b.body.time.daily_cap_reached], [0, true]);
  // 监护操作留痕：访问日志有路由与结果，没有学生 ID 原文
  const line = logs.filter((l) => l['route'] === 'PUT /students/{student_id}/time-guard').at(-1)!;
  assert.equal(line['outcome'], 'saved');
  assert.ok(!JSON.stringify(logs).includes(T.id));
});

// ---------------------------------------------------------------- 连续使用提醒（第 10 节）

test('连续使用提醒：可注入时钟下连续 40 分钟出现；停 10 分钟或确认休息后从头计；家长端没有', { skip: !HAS_DB }, async () => {
  const P = F.kids['P']!;
  const path = `/students/${P.id}/rest-reminder`;
  const t0 = clock;
  const at = async (min: number) => { clock = t0 + min * 60_000; return (await get(P.account, path)).body; };
  assert.equal((await at(0)).show, false);
  for (const m of [5, 10, 15, 20, 25, 30, 35]) assert.equal((await at(m)).show, false, String(m));
  const v = await at(40);
  assert.deepEqual(v, { enabled: true, show: true, continuous_minutes: 40, threshold_minutes: 40, suggest_break_minutes: 10 });
  const ack = await post(P.account, `${path}/ack`);
  assert.deepEqual([ack.status, ack.body.show], [200, false]);
  assert.equal((await at(45)).show, false);
  assert.equal((await at(90)).continuous_minutes, 0);  // 停了 45 分钟：视为休息过
  assert.equal((await get(F.guardian, path)).status, 403);
  assert.equal((await get(F.kids['T']!.account, path)).status, 404);  // 学生只读得到自己
  clock = t0;
});

// ---------------------------------------------------------------- 信任界面预留与计量入口（第 12 节、第二轮 7.7）

const srcFiles = (dir: URL): string[] => readdirSync(dir).flatMap((n) => {
  const u = new URL(n, dir);
  return statSync(u).isDirectory() ? srcFiles(new URL(`${n}/`, dir)) : [u.pathname];
});

// SIE-03 PR③ 起，异议、考试、导出删除的路由挂在信任界面开关上（src/http/trust-routes.ts），开关缺省全关：
// 缺省注册的路由表（activeRoutes()）仍然没有这些路由；只有 src/trust/clients.ts 调 insight.dispute，其余工具源码里一律不调。
test('信任界面预留：开关关（缺省）时注册的路由没有“为什么”“我不同意”、对账卡、周报、导出删除；源码只在 trust/clients.ts 调 insight.dispute（trust-routes.ts 只读异议表）', () => {
  for (const r of activeRoutes()) assert.ok(!/why|explain|dispute|reconcile|prediction|weekly|export|erase|data-rights/.test(r.path), r.path);
  for (const r of ROUTES) assert.equal(r.feature, undefined, r.path);
  for (const f of srcFiles(new URL('../src/', import.meta.url))) {
    const text = readFileSync(f, 'utf8').replace(/\/\/.*$/gm, '');
    // trust/clients.ts 调异议工具；trust-routes.ts 按 0032 RLS 读异议表（FROM insight.dispute），不是调工具
    const banned = f.endsWith('/src/trust/clients.ts') ? /insight\.(reconcile|export|erase|record|predict)\b/
      : f.endsWith('/src/http/trust-routes.ts') ? /insight\.(reconcile|export|erase|record|predict)\b|(?<!FROM )insight\.dispute\b/
        : /insight\.(dispute|reconcile|export|erase|record|predict)\b/;
    assert.ok(!banned.test(text), f);
  }
});

test('计量入口：壳的路由不调用 reserve / settle / meteredCall / meter_practice；BFF 源码不写 subject_enrollment、不读 next_retest_at', () => {
  const shellSrc = readFileSync(new URL('../src/http/shell-routes.ts', import.meta.url), 'utf8').replace(/\/\/.*$/gm, '');
  assert.ok(!/\breserve\(|\bsettle\(|meteredCall|meter_practice|meter_reserve|meter_settle/.test(shellSrc));
  for (const f of srcFiles(new URL('../src/', import.meta.url))) {
    const text = readFileSync(f, 'utf8');
    assert.ok(!/(INSERT\s+INTO|UPDATE)\s+platform\.subject_enrollment/i.test(text), f);
    assert.ok(!/next_retest_at/.test(text.replace(/\/\/.*$/gm, '')), f);
  }
});
