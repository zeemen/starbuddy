// 今日计划跨科编排 BFF 侧集成测试（PLT-11，specs/today-plan-orchestration-plt11/spec.md 范围第 2 条与验收）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，走真实 RLS 与真实会话（shell-harness.ts）；
// 学习服务的今日计划与跨科编排、洞察服务用按接口形状返回的假实现。全部合成数据（规则 8）：地图节点、判断、学生都是占位。
// 覆盖：开关关闭时 today 响应与现状逐字段一致、不调跨科接口；开关打开时学生本人另带 ordered（节点名、条目引用、学科码），
// 推荐理由只沿用按科任务已决定的那一条（不另取陈述）；家长会话不带 ordered；学习服务不可用时 ordered 为 null；
// 响应没有掌握、置信度、证据 ID、理由编码；本人核对与限流同现有 today。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { after, before, test } from 'node:test';
import { loadShellConfig, type ShellConfig } from '../src/shell/config.ts';
import type { ExplainRequest, InsightClient, RenderedStatement } from '../src/shell/insight-client.ts';
import type { PlanClient, PlanSection, PlanSubject } from '../src/shell/plan-client.ts';
import type { OrderedList, TodayOrchClient, TodayResult } from '../src/shell/today-orch.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

// ---------------------------------------------------------------- 响应按 openapi/shell.v0.yaml 的 today 200 schema 校验
// 不给 BFF 引入依赖：借用 adapters/aya 已锁版本的 ajv（2020-12）、ajv-formats 与 yaml（同 star-map-v1.integration.test.ts）；没装时不校验并在输出里说明。
const validateToday: ((v: unknown) => string | null) | null = (() => {
  if (!existsSync(new URL('../../../adapters/aya/node_modules/ajv/package.json', import.meta.url))) return null;
  const req = createRequire(new URL('../../../adapters/aya/package.json', import.meta.url));
  const Ajv2020 = req('ajv/dist/2020').default;
  const addFormats = req('ajv-formats').default ?? req('ajv-formats');
  const YAML = req('yaml');
  const doc = YAML.parse(readFileSync(new URL('../openapi/shell.v0.yaml', import.meta.url), 'utf8'));
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  ajv.addSchema({ ...doc, $id: 'shell.v0' });
  const fn = ajv.compile({ $ref: 'shell.v0#/paths/~1bff~1v0~1students~1%7Bstudent_id%7D~1today/get/responses/200/content/application~1json/schema' });
  return (v: unknown) => (fn(v) ? null : ajv.errorsText(fn.errors));
})();
if (!validateToday) console.log('# today-orch：adapters/aya 没装依赖，跳过响应 schema 校验');

const MAP = '5c000000-0000-4000-8000-0000000000b1';
const TPL = 'syn.math.today_orch_node';
// 内容类理由的编码（high_exam_weight）按现有机制以 {kind: content, code} 给前端，不在这里
const FORBIDDEN = ['p_mastery', 'confidence"', 'evidence_ids', 'evidence_id', 'learner_context', 'rationale_code', 'retest_scheduled',
                   'weak_mastery', 'source_unavailable', 'budget_minutes', 'sources'];

class FakePlan implements PlanClient {
  calls = 0;
  sections: (subjects: PlanSubject[]) => PlanSection[] = (subjects) => subjects.map((s) => ({ subject: s, status: 'unavailable', tasks: [] }));
  async plan(_student: string, subjects: PlanSubject[]) {
    this.calls += 1;
    return this.sections(subjects);
  }
}
/** 假的 /internal/v1/today：附带的各科计划与 FakePlan 同一份数据（不计入 plan.calls），跨科列表由用例给；down = 学习服务不可用 */
class FakeOrch implements TodayOrchClient {
  calls: { student: string; subjects: PlanSubject[] }[] = [];
  reply: OrderedList | null = null;
  down = false;
  async today(student: string, subjects: PlanSubject[]): Promise<TodayResult | null> {
    this.calls.push({ student, subjects });
    return this.down ? null : { sections: plan.sections(subjects), ordered: this.reply };
  }
}
class FakeInsight implements InsightClient {
  calls: ExplainRequest[] = [];
  async explain(req: ExplainRequest): Promise<RenderedStatement | null> {
    this.calls.push(req);
    return { claim_id: req.claimId, template_key: TPL, template_version: 1, status: 'established', display: 'solid',
             text: `（合成模板句 ${req.claimId.slice(-4)}）`, confidence_band: 'moderate' };
  }
}

let h: Harness;
const plan = new FakePlan();
const orch = new FakeOrch();
const insight = new FakeInsight();
const cfg: ShellConfig = { ...loadShellConfig(), externalTemplates: new Set([`${TPL}@1`]),
  rationaleTemplates: new Map([['retest_scheduled', new Set([`${TPL}@1`])]]) };
let base: ShellHarness;           // 没有跨科编排（开关上线前的样子）
let off: ShellHarness;            // 开关关（配了客户端也不调）
let on: ShellHarness;             // 开关开
let N: string[] = [];             // 已发布数学地图里的两个节点
const names = new Map<string, string>();
let kid: { id: string; account: string };
let sib: { id: string; account: string };
let guardian = '';
let other = '';

async function family(): Promise<{ guardian: string; kids: { id: string; account: string }[] }> {
  const g = randomUUID();
  const household = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [g]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, g]);
  const kids = [];
  for (let i = 0; i < 2; i++) {
    const account = randomUUID();
    const id = randomUUID();
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [account]);
    await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 9, 'nanjing')`,
                     [id, account, household]);
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`,
                     [g, id]);
    await h.su.query(`INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES ($1, 'aya', 'active'), ($1, 'zhixing', 'active')`, [id]);
    kids.push({ id, account });
  }
  return { guardian: g, kids };
}

/** 合成判断：复制模式直接写行（0010 的触发器只允许洞察服务写入；这里只在一次性测试库里造数据） */
async function claim(student: string, node: string): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                  source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
       VALUES ($1, $2, 'aya', 'node', $3, $4, 1, 0.7, 'inferred', 'established', 'created', ARRAY['agent', 'student', 'guardian'], now() + interval '30 days',
               'mastery_estimate', gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], now(), now())`, [id, student, node, TPL]);
    await c.query('COMMIT');
  } finally {
    c.release(true);
  }
  return id;
}

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  // 已发布的数学地图（节点名给标题用）：库里已有就用它的两个节点，没有就建一份合成地图
  let { rows } = await h.su.query<{ id: string; name: string }>(
    `SELECT n.id, n.name FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
      WHERE v.subject = 'aya' AND v.status = 'published' AND n.status = 'active' AND n.level = 'node' ORDER BY n.id LIMIT 2`);
  if (rows.length < 2 && (await h.su.query(`SELECT 1 FROM kge.map_version WHERE subject = 'aya' AND status = 'published'`)).rowCount === 0) {
    const c = await h.su.connect();
    try {
      await c.query('BEGIN');
      await c.query('SET LOCAL session_replication_role = replica');
      await c.query(`INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES ($1, 'aya', 2022, '0.0.8', 'published', now())`, [MAP]);
      await c.query(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
                       ($1, 'MATH.TOR', 'domain', NULL, NULL, '（合成）领域'), ($1, 'MATH.TOR.T1', 'topic', 'MATH.TOR', NULL, '（合成）主题'),
                       ($1, 'MATH.TOR.T1.A', 'node', 'MATH.TOR.T1', 'kp', '（合成）节点 A'), ($1, 'MATH.TOR.T1.B', 'node', 'MATH.TOR.T1', 'kp', '（合成）节点 B')`, [MAP]);
      await c.query('COMMIT');
    } finally {
      c.release(true);
    }
    ({ rows } = await h.su.query<{ id: string; name: string }>(
      `SELECT id, name FROM kge.knowledge_node WHERE map_version_id = $1 AND level = 'node' ORDER BY id`, [MAP]));
  }
  assert.equal(rows.length, 2, '需要已发布数学地图里的两个节点');
  N = rows.map((r) => r.id);
  for (const r of rows) names.set(r.id, r.name);
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    VALUES ($1, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now())
                    ON CONFLICT DO NOTHING`, [TPL]);
  const f = await family();
  guardian = f.guardian;
  [kid, sib] = f.kids as [typeof kid, typeof sib];
  other = (await family()).guardian;
  const shell = (orchOpt?: { enabled: boolean; client?: TodayOrchClient }) =>
    startShell(h.bff, h.su, { shell: { config: cfg, plan, insight, ...(orchOpt ? { orch: orchOpt } : {}) } });
  base = await shell();
  off = await shell({ enabled: false, client: orch });
  on = await shell({ enabled: true, client: orch });
});

after(async () => {
  if (!HAS_DB) return;
  await Promise.all([base.close(), off.close(), on.close()]);
  await h.close();
});

const get = async (s: ShellHarness, account: string, student = kid.id) => callAs(s.base, 'GET', `/students/${student}/today`, await s.sessionFor(account));

function setPlan(): void {
  plan.sections = (subjects) => subjects.map((s) => s === 'aya' ? { subject: s, status: 'ok' as const, tasks: [
    { kind: 'retest', target_node_ids: [N[0]!], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' },
    { kind: 'practice', target_node_ids: [N[1]!], priority: 0.7, estimated_minutes: 12, rationale_code: 'high_exam_weight' },
    { kind: 'lesson', target_node_ids: [], priority: 0.3, estimated_minutes: 5, rationale_code: 'weak_mastery' },
  ] } : { subject: s, status: 'unavailable' as const, tasks: [] });
}

test('开关关：today 响应与开关上线前逐字段一致（学生、家长都一样），不调跨科接口', { skip: !HAS_DB }, async () => {
  setPlan();
  orch.calls = [];
  for (const account of [kid.account, guardian]) {
    const a = await get(base, account);
    const b = await get(off, account);
    assert.equal(a.status, 200, a.text);
    assert.deepEqual(b.body, a.body);
    assert.deepEqual(Object.keys(b.body).sort(), ['student_id', 'subjects']);
    assert.equal(b.headers.get('cache-control'), a.headers.get('cache-control'));
    if (validateToday) assert.equal(validateToday(b.body), null, b.text.slice(0, 200));
  }
  assert.deepEqual(orch.calls, []);
});

test('响应按 shell.v0.yaml 校验：校验器本身能拦下多字段与不合格的 ordered', { skip: !HAS_DB || !validateToday }, async () => {
  const v = validateToday!;
  const okBody = { student_id: kid.id, subjects: [], ordered: { items: [], truncated_count: 0, partial: false } };
  assert.equal(v(okBody), null);
  assert.equal(v({ student_id: kid.id, subjects: [] }), null);
  assert.notEqual(v({ ...okBody, ordered: { ...okBody.ordered, sources: [] } }), null);
  assert.notEqual(v({ ...okBody, ordered: { items: [{ subject: 'math', kind: 'retest', node_id: null, node_name: null, estimated_minutes: 5,
                                                      source: 'retest', entry_id: randomUUID(), rationale: null, rationale_code: 'x' }],
                                           truncated_count: 0, partial: false } }), null);
  assert.notEqual(v({ ...okBody, extra: 1 }), null);
});

test('开关开（学生本人）：sections 不变，另带 ordered：学科码、节点名、条目引用、来源；推荐理由只沿用按科任务那一条', { skip: !HAS_DB }, async () => {
  setPlan();
  const c0 = await claim(kid.id, N[0]!);
  const e1 = randomUUID();
  const e2 = randomUUID();
  orch.reply = { truncated_count: 3, partial: true, items: [
    { subject: 'aya', kind: 'retest', node_id: N[0]!, estimated_minutes: 5, source: 'retest', entry_id: e1, rationale_code: null },
    { subject: 'zhixing', kind: 'verify', node_id: null, estimated_minutes: 5, source: 'retest', entry_id: e2, rationale_code: null },
    { subject: 'aya', kind: 'practice', node_id: N[1]!, estimated_minutes: 8, source: 'practice', entry_id: e2, rationale_code: null },
    { subject: 'aya', kind: 'practice', node_id: N[1]!, estimated_minutes: 12, source: 'plan', entry_id: null, rationale_code: 'high_exam_weight' },
    { subject: 'aya', kind: 'retest', node_id: N[0]!, estimated_minutes: 10, source: 'plan', entry_id: null, rationale_code: 'retest_scheduled' },
    { subject: 'aya', kind: 'lesson', node_id: null, estimated_minutes: 5, source: 'plan', entry_id: null, rationale_code: 'weak_mastery' },
    // 按科任务里没有的组合（例如两次调用之间计划变了）：理由一律不显示
    { subject: 'aya', kind: 'review', node_id: N[0]!, estimated_minutes: 5, source: 'plan', entry_id: null, rationale_code: 'retest_scheduled' },
  ] };
  orch.calls = [];
  insight.calls = [];
  const ref = await get(base, kid.account);
  const explainedBefore = insight.calls.length;
  insight.calls = [];
  const planCalls = plan.calls;
  const r = await get(on, kid.account);
  assert.equal(r.status, 200, r.text);
  // 每页只排一次计划：开关开时不另调 /plan，按科 sections 用 /today 附带的计划，与开关关时对同一数据逐字段一致
  assert.equal(plan.calls, planCalls, '开关开时不调 /plan');
  assert.deepEqual(r.body.subjects, ref.body.subjects, 'sections 与开关关时一致');
  if (validateToday) assert.equal(validateToday(r.body), null, `响应不符合 shell.v0.yaml：${r.text.slice(0, 200)}`);
  assert.deepEqual(Object.keys(r.body).sort(), ['ordered', 'student_id', 'subjects']);
  assert.equal(insight.calls.length, explainedBefore, '跨科列表不另取陈述');
  assert.deepEqual(orch.calls, [{ student: kid.id, subjects: ['aya', 'zhixing'] }]);
  const o = r.body.ordered;
  assert.deepEqual([o.truncated_count, o.partial], [3, true]);
  const claimR = { kind: 'claim', claim_id: c0, text: `（合成模板句 ${c0.slice(-4)}）`, confidence_band: 'moderate' };
  assert.deepEqual(o.items, [
    { subject: 'math', kind: 'retest', node_id: N[0], node_name: names.get(N[0]!), estimated_minutes: 5, source: 'retest', entry_id: e1, rationale: null },
    { subject: 'politics', kind: 'verify', node_id: null, node_name: null, estimated_minutes: 5, source: 'retest', entry_id: e2, rationale: null },
    { subject: 'math', kind: 'practice', node_id: N[1], node_name: names.get(N[1]!), estimated_minutes: 8, source: 'practice', entry_id: e2, rationale: null },
    { subject: 'math', kind: 'practice', node_id: N[1], node_name: names.get(N[1]!), estimated_minutes: 12, source: 'plan', entry_id: null,
      rationale: { kind: 'content', code: 'high_exam_weight' } },
    { subject: 'math', kind: 'retest', node_id: N[0], node_name: names.get(N[0]!), estimated_minutes: 10, source: 'plan', entry_id: null, rationale: claimR },
    { subject: 'math', kind: 'lesson', node_id: null, node_name: null, estimated_minutes: 5, source: 'plan', entry_id: null, rationale: null },
    { subject: 'math', kind: 'review', node_id: N[0], node_name: names.get(N[0]!), estimated_minutes: 5, source: 'plan', entry_id: null, rationale: null },
  ]);
  // 按科任务里同一条的理由与跨科列表一致
  assert.deepEqual(r.body.subjects[0].tasks[0].rationale, claimR);
  for (const k of FORBIDDEN) assert.ok(!r.text.includes(k), k);
});

test('开关开：家长会话不带 ordered、不调跨科接口；学习服务不可用时 ordered 为 null；本人核对同现有 today', { skip: !HAS_DB }, async () => {
  setPlan();
  orch.calls = [];
  const g = await get(on, guardian);
  assert.deepEqual(g.body, (await get(base, guardian)).body);
  assert.ok(!('ordered' in g.body));
  assert.deepEqual(orch.calls, []);
  orch.reply = null;
  const r = await get(on, kid.account);
  assert.equal(r.status, 200);
  assert.equal(r.body.ordered, null);
  assert.equal(r.body.subjects[0].status, 'ok');
  if (validateToday) assert.equal(validateToday(r.body), null, r.text.slice(0, 200));
  // 学习服务整体不可用：每科“今天暂无安排”、ordered 为 null（与 /plan 不可用时同一表现），也不退回去调 /plan
  orch.down = true;
  const planCalls = plan.calls;
  const d = await get(on, kid.account);
  orch.down = false;
  assert.deepEqual(d.body, { student_id: kid.id, subjects: [{ subject: 'math', status: 'unavailable', tasks: [] },
                                                            { subject: 'politics', status: 'unavailable', tasks: [] }], ordered: null });
  assert.equal(plan.calls, planCalls);
  orch.calls = [];
  assert.equal((await get(on, sib.account, kid.id)).status, 404);       // 兄妹
  assert.equal((await get(on, other, kid.id)).status, 404);              // 另一户家长
  assert.equal((await callAs(on.base, 'GET', `/students/${kid.id}/today`, null)).status, 401);
  assert.deepEqual(orch.calls, []);
});

test('开关开：没有在读学科时 ordered 为空列表、不调学习服务', { skip: !HAS_DB }, async () => {
  await h.su.query(`UPDATE platform.subject_enrollment SET status = 'paused', paused_at = now() WHERE student_id = $1`, [sib.id]);
  orch.calls = [];
  plan.calls = 0;
  const r = await get(on, sib.account, sib.id);
  assert.equal(r.status, 200, r.text);
  assert.deepEqual(r.body, { student_id: sib.id, subjects: [], ordered: { items: [], truncated_count: 0, partial: false } });
  assert.deepEqual([orch.calls.length, plan.calls], [0, 0]);
});

test('开关开：限流同现有 today（today.session.minute，超了统一 429）', { skip: !HAS_DB }, async () => {
  const rule = on.config.rate_limits['today.session.minute']!;
  const old = rule.limit;
  rule.limit = 2;
  try {
    const codes = [];
    for (let i = 0; i < 4; i++) codes.push((await get(on, kid.account)).status);
    assert.ok(codes.includes(429), codes.join(','));
  } finally {
    rule.limit = old;
  }
});
