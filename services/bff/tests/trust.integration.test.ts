// SIE-03 PR③ 信任界面 BFF 接口的集成测试（specs/sie-03-dispute-reconcile/spec.md 范围 3、验收“导出”“删除”；5-H spec 验收）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，走真实 RLS、真实会话与 0032–0035 的库函数；
// 洞察服务的 insight.dispute 与预测 / 对账卡读取用按契约返回的假实现。全部合成数据（规则 8）。
// 覆盖：开关关闭时路由不注册（与表外同一个 404）；从路由表生成的授权矩阵（未登录 401、账号类型 403、缺 CSRF / 错 Origin 403、
// 另一户与不存在同一个 404）；异议（one_off_slip 只学生、判断不可见 404、fact_ref 不外传、幂等键按账号派生、只看自己提出的）；
// 考试录入；预测与对账卡只显示可见判断的句子（无 claim_id 的丢弃并记数）；导出（只绑定家长、一次性兑换、no-store 附件）；
// 删除（会话内二次确认、冷静期、撤回、状态与回执、监护操作留痕）；限流桶都在配置里；
// 周报（冲刺 5-H，0036）：学生读学生版、家长读家长版，每句库内再核判断，删掉或降为不可见的判断对应句子消失。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';
import { loadAuthConfig } from '../src/http/config.ts';
import { P } from '../src/http/problems.ts';
import type { Route } from '../src/http/routes.ts';
import { TRUST_ROUTES } from '../src/http/trust-routes.ts';
import { loadShellConfig, type ShellConfig } from '../src/shell/config.ts';
import type { DisputeClient, DisputeOutcome, DisputeRequest, ExamViewClient, ExamViewRequest } from '../src/trust/clients.ts';
import { MemoryExportStore } from '../src/trust/exports.ts';
import { TRUST_FEATURES } from '../src/trust/features.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const TPL = 'syn.trust.node_mastery';      // 对外白名单里
const TPL_HIDDEN = 'syn.trust.not_listed'; // 已审核但不在白名单里
const MISSING = '5e000000-0000-4000-8000-0000000000ff';

let h: Harness;
let shell: ShellHarness;  // 开关全开
let off: ShellHarness;    // 缺省（开关全关）
const logs: Record<string, unknown>[] = [];
let offset = 0;  // 注入时钟的偏移（删除确认的近期登录窗口用）
const store = new MemoryExportStore();

// ---------------------------------------------------------------- 假洞察服务
class FakeDispute implements DisputeClient {
  calls: DisputeRequest[] = [];
  next: DisputeOutcome | null = null;
  async dispute(req: DisputeRequest): Promise<DisputeOutcome> {
    this.calls.push(req);
    if (this.next) return this.next;
    // 契约形状里带 fact_ref / 验证题引用的结果，经 pickDispute 后应只剩三个字段；这里直接给 BFF 看到的形状
    return { kind: 'ok', value: { dispute_id: randomUUID(), next_step: req.kind === 'i_know' ? 'verification_scheduled' : req.kind === 'data_error'
      ? 'open_fact_for_correction' : 'observe_followups', verify_by: req.kind === 'i_know' ? '2026-10-10T08:00:00.000Z' : null } };
  }
}
class FakeExams implements ExamViewClient {
  calls: ExamViewRequest[] = [];
  prediction_: unknown = null;
  card: unknown = null;
  async prediction(req: ExamViewRequest) { this.calls.push(req); return this.prediction_; }
  async reconciliation(req: ExamViewRequest) { this.calls.push(req); return this.card; }
}
const disputes = new FakeDispute();
const exams = new FakeExams();
const cfg: ShellConfig = { ...loadShellConfig(), externalTemplates: new Set([`${TPL}@1`]) };

// ---------------------------------------------------------------- 合成家庭
interface Kid { id: string; account: string }
interface Fam { guardian: string; household: string; kid: Kid }
let F: Fam;          // 主家庭
let O: Fam;          // 另一户
let coGuardian = ''; // F 孩子的第二位有效绑定家长
let unlinked = '';   // 没有绑定任何孩子的家长

async function family(): Promise<Fam> {
  const guardian = randomUUID();
  const household = randomUUID();
  const account = randomUUID();
  const id = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'student')`, [guardian, account]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, guardian]);
  await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 8, 'nanjing')`,
                   [id, account, household]);
  await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`,
                   [guardian, id]);
  return { guardian, household, kid: { id, account } };
}

/** 合成判断：复制模式直接写行（0010 的触发器只允许洞察服务写入；只在一次性测试库里造数据） */
async function claim(kid: Kid, o: { status?: string; visibility?: string[]; template?: string; expired?: boolean } = {}): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                  source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
       VALUES ($1, $2, 'aya', 'node', 'MATH.TRU.T1.A', $3, 1, 0.7, 'inferred', $4::insight.claim_status, 'created', $5, $6,
               'mastery_estimate', gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], $7, $7)`,
      [id, kid.id, o.template ?? TPL, o.status ?? 'established', o.visibility ?? ['agent', 'student', 'guardian'],
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

/** 合成异议行（同上，复制模式；只造 open 状态的 i_know） */
async function disputeRow(kid: Kid, claimId: string, raisedBy: string, role: 'student' | 'guardian'): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(`INSERT INTO insight.dispute (id, student_id, claim_id, raised_by, raised_by_role, source_type, kind, status)
                   VALUES ($1, $2, $3, $4, $5, $6::insight.source_type, 'i_know', 'open')`,
                  [id, kid.id, claimId, raisedBy, role, role === 'student' ? 'self' : 'guardian']);
    await c.query('COMMIT');
  } finally {
    c.release(true);
  }
  return id;
}

const relink = (guardian: string, studentId: string) => h.su.query(
  `INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`, [guardian, studentId]);
const sess = (account: string) => shell.sessionFor(account);
const get = async (account: string | null, path: string) => callAs(shell.base, 'GET', path, account ? await sess(account) : null);
const post = async (account: string, path: string, body?: unknown) => callAs(shell.base, 'POST', path, await sess(account), body);
const same = (a: { status: number; text: string }, b: { status: number; text: string }, what: string) => {
  assert.equal(a.status, b.status, what);
  assert.equal(a.text, b.text, what);
};
/** 上海时区今天起 n 天后的日期 */
const dayPlus = (n: number) => new Date(Date.now() + 8 * 3600_000 + n * 86400_000).toISOString().slice(0, 10);

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    VALUES ($1, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now()),
                           ($2, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now())
                    ON CONFLICT DO NOTHING`, [TPL, TPL_HIDDEN]);
  F = await family();
  O = await family();
  coGuardian = randomUUID();
  unlinked = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian')`, [coGuardian, unlinked]);
  await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`,
                   [coGuardian, F.kid.id]);
  shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l), shell: { config: cfg }, now: () => new Date(Date.now() + offset),
                                          trust: { features: new Set(TRUST_FEATURES), dispute: disputes, exams, exports: store } });
  off = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg } });
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await off.close();
  await h.close();
});

// ---------------------------------------------------------------- 开关与路由表

/** 按路由模板生成路径：student_id 取给定学生，其他参数取不存在的 UUID */
const pathFor = (r: Route, studentId: string) => r.path.replace('{student_id}', studentId).replace(/\{[a-z_]+\}/g, MISSING);
/** 能通过请求体校验的合成请求体（授权矩阵要测到库里的授权，而不是停在 422） */
const bodyFor = (r: Route): unknown => {
  if (!r.body) return undefined;
  if (r.path.endsWith('/disputes')) return { kind: 'data_error', idempotency_key: 'matrix-key-0000000001' };
  if (r.path.endsWith('/exams')) return { subject: 'aya', exam_type: 'monthly', exam_date: dayPlus(10), full_score: 100, scope_node_ids: ['MATH.TRU.T1.A'] };
  if (r.path.endsWith('/score')) return { raw_score: 90 };
  if (r.path.endsWith('/download')) return { download_ref: `dl_${'0'.repeat(64)}` };
  if (r.path.endsWith('/confirm')) return { confirm_token: '0'.repeat(64) };
  throw new Error(`no body for ${r.path}`);
};

test('开关关闭（缺省）：信任界面路由一律不注册，与表外路径逐字节相同的 404（有会话也一样）', { skip: !HAS_DB }, async () => {
  const g = await off.sessionFor(F.guardian);
  const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', g);
  assert.equal(outside.status, 404);
  for (const r of TRUST_ROUTES) {
    for (const who of [null, g]) {
      const x = await callAs(off.base, r.method, pathFor(r, F.kid.id), who, bodyFor(r));
      same(x, outside, `${r.method} ${r.path}`);
    }
  }
  // 开关只打开一块：别的块仍不注册
  const partial = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { features: new Set(['dispute'] as const) } });
  try {
    const pg = await partial.sessionFor(F.guardian);
    assert.equal((await callAs(partial.base, 'GET', `/students/${F.kid.id}/disputes`, pg)).status, 200);
    same(await callAs(partial.base, 'GET', `/students/${F.kid.id}/erasure`, pg), outside, 'data_rights 未打开');
    same(await callAs(partial.base, 'GET', `/students/${F.kid.id}/exams/${MISSING}/prediction`, pg), outside, 'reconcile 未打开');
  } finally {
    await partial.close();
  }
});

test('授权矩阵（从路由表生成）：未登录 401；学生会话调家长路由 403；缺 CSRF、错 Origin 403；另一户、未绑定与不存在同一个 404', { skip: !HAS_DB }, async () => {
  const st = await sess(F.kid.account);
  const other = await sess(O.guardian);
  const none = await sess(unlinked);
  for (const r of TRUST_ROUTES) {
    const path = pathFor(r, F.kid.id);
    const body = bodyFor(r);
    const anon = await callAs(shell.base, r.method, path, null, body);
    assert.equal(anon.status, 401, `未登录 ${r.method} ${r.path}`);
    assert.equal(anon.text, P.UNAUTHENTICATED);
    if (r.auth === 'guardian') {
      const x = await callAs(shell.base, r.method, path, st, body);
      assert.equal(x.status, 403, `学生会话 ${r.method} ${r.path}`);
      assert.equal(x.text, P.FORBIDDEN);
    }
    if (r.method !== 'GET') {
      const g = await sess(F.guardian);
      const who = r.auth === 'guardian' ? g : st;
      const noCsrf = await callAs(shell.base, r.method, path, who, body, { csrf: null });
      assert.equal(noCsrf.text, P.CSRF, `缺 CSRF ${r.method} ${r.path}`);
      same(await callAs(shell.base, r.method, path, who, body, { origin: 'http://evil.test' }), noCsrf, `错 Origin ${r.method} ${r.path}`);
      same(await callAs(shell.base, r.method, path, who, body, { csrf: other.csrf }), noCsrf, `别的会话的 CSRF ${r.method} ${r.path}`);
    }
    // 另一户家长、没有绑定的家长访问 F 的孩子，与访问不存在的学生：同一个 404
    const missing = await callAs(shell.base, r.method, pathFor(r, MISSING), other, body);
    assert.equal(missing.status, 404, `不存在 ${r.method} ${r.path} ${missing.text}`);
    assert.equal(missing.text, P.NOT_FOUND);
    same(await callAs(shell.base, r.method, path, other, body), missing, `另一户 ${r.method} ${r.path}`);
    same(await callAs(shell.base, r.method, path, none, body), missing, `未绑定 ${r.method} ${r.path}`);
    // 学生会话访问另一户的孩子
    const stOther = await callAs(shell.base, r.method, pathFor(r, O.kid.id), st, body);
    assert.equal(stOther.status, r.auth === 'guardian' ? 403 : 404, `学生访问另一户 ${r.method} ${r.path}`);
    // 非 UUID 参数：与不存在同一个 404
    same(await callAs(shell.base, r.method, r.path.replace('{student_id}', 'not-a-uuid').replace(/\{[a-z_]+\}/g, MISSING), other, body), missing,
         `非 UUID ${r.method} ${r.path}`);
  }
  assert.equal(disputes.calls.length, 0, '没有一次越权请求到达洞察服务');
  assert.equal(exams.calls.length, 0);
  // 矩阵从路由表生成，所以另外写死“谁能用”：导出与删除只给家长，其余学生本人与家长都能用
  for (const r of TRUST_ROUTES) assert.equal(r.auth, r.feature === 'data_rights' ? 'guardian' : 'session', `${r.method} ${r.path}`);
  for (const p of ['exports', 'erasure']) {
    const x = await callAs(shell.base, 'POST', `/students/${F.kid.id}/${p}`, st);
    assert.equal(x.text, P.FORBIDDEN, `学生本人也不能${p}`);
  }
});

test('限流桶都在 config/auth.json；改变状态的路由都有限流；超了是统一的 429', { skip: !HAS_DB }, async () => {
  const c = loadAuthConfig();
  for (const r of TRUST_ROUTES) {
    for (const l of r.rateLimits) assert.ok(c.rate_limits[l.bucket], `${r.path} 的限流桶 ${l.bucket}`);
    if (r.method !== 'GET') assert.ok(r.rateLimits.length > 0, `${r.method} ${r.path} 有限流`);
  }
  const bucket = 'erasure.account.hour';
  const saved = shell.config.rate_limits[bucket]!;
  const fresh = randomUUID();  // 还没有用过这个桶的家长账号
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [fresh]);
  const g = await sess(fresh);
  shell.config.rate_limits[bucket] = { window_seconds: 3600, limit: 1 };
  try {
    assert.equal((await post(fresh, `/students/${MISSING}/erasure`)).status, 404);
    const x = await callAs(shell.base, 'POST', `/students/${MISSING}/erasure`, g);
    assert.equal(x.status, 429);
    assert.equal(x.text, P.TOO_MANY);
  } finally {
    shell.config.rate_limits[bucket] = saved;
  }
});

// ---------------------------------------------------------------- 异议

test('异议：学生与家长可提；one_off_slip 只学生本人（家长 403，不到洞察服务）；响应只有编码，不含 fact_ref 与验证题引用', { skip: !HAS_DB }, async () => {
  const c1 = await claim(F.kid);
  disputes.calls.length = 0;
  const a = await post(F.kid.account, `/students/${F.kid.id}/claims/${c1}/disputes`, { kind: 'i_know', idempotency_key: 'student-key-000000001' });
  assert.equal(a.status, 200, a.text);
  assert.deepEqual(Object.keys(a.body).sort(), ['dispute_id', 'kind', 'next_step', 'verify_by']);
  assert.equal(a.body.next_step, 'verification_scheduled');
  assert.equal(a.headers.get('cache-control'), 'no-store');
  const call = disputes.calls[0]!;
  assert.deepEqual({ ...call, idempotencyKey: '' }, { studentRef: F.kid.id, audience: 'student', actorRef: F.kid.account, claimId: c1, kind: 'i_know',
                                                      idempotencyKey: '' });
  // 幂等键按账号派生：不把原键转给洞察服务；同一账号同一个键稳定，别的账号同一个键不同
  assert.match(call.idempotencyKey, /^bff\.[0-9a-f]{48}$/);
  assert.ok(!call.idempotencyKey.includes('student-key'));
  await post(F.kid.account, `/students/${F.kid.id}/claims/${c1}/disputes`, { kind: 'i_know', idempotency_key: 'student-key-000000001' });
  assert.equal(disputes.calls[1]!.idempotencyKey, call.idempotencyKey);
  const g = await post(F.guardian, `/students/${F.kid.id}/claims/${c1}/disputes`, { kind: 'data_error', idempotency_key: 'student-key-000000001' });
  assert.equal(g.status, 200);
  assert.equal(g.body.next_step, 'open_fact_for_correction');
  assert.ok(!('fact_ref' in g.body));
  const gc = disputes.calls[2]!;
  assert.equal(gc.audience, 'guardian');
  assert.match(String(gc.linkId), /^[0-9a-f-]{36}$/, '家长带绑定关系');
  assert.notEqual(gc.idempotencyKey, call.idempotencyKey);
  // 家长提“那次只是失误”：403，洞察服务没有收到
  const before = disputes.calls.length;
  const slip = await post(F.guardian, `/students/${F.kid.id}/claims/${c1}/disputes`, { kind: 'one_off_slip', idempotency_key: 'guardian-slip-00000001' });
  assert.equal(slip.status, 403);
  assert.equal(slip.text, P.FORBIDDEN);
  assert.equal(disputes.calls.length, before);
  const own = await post(F.kid.account, `/students/${F.kid.id}/claims/${c1}/disputes`, { kind: 'one_off_slip', idempotency_key: 'student-slip-00000001' });
  assert.equal(own.status, 200);
  assert.equal(own.body.next_step, 'observe_followups');
  // 类型不在三类里（包括“粗心”）、多余的键、键格式不对：422
  for (const body of [{ kind: 'careless', idempotency_key: 'student-key-000000009' }, { kind: 'i_know', idempotency_key: 'short' },
                      { kind: 'i_know', idempotency_key: 'student-key-000000009', account_id: O.guardian }]) {
    assert.equal((await post(F.kid.account, `/students/${F.kid.id}/claims/${c1}/disputes`, body)).status, 422, JSON.stringify(body));
  }
});

test('异议：判断不可见（另一个学生的、对受众不可见、已推翻、已过期、不存在）一律同一个 404，不到洞察服务', { skip: !HAS_DB }, async () => {
  const others = await claim(O.kid);
  const agentOnly = await claim(F.kid, { visibility: ['agent'] });
  const guardianOnly = await claim(F.kid, { visibility: ['agent', 'guardian'] });
  const refuted = await claim(F.kid, { status: 'refuted' });
  const expired = await claim(F.kid, { expired: true });
  const before = disputes.calls.length;
  const missing = await post(F.kid.account, `/students/${F.kid.id}/claims/${MISSING}/disputes`, { kind: 'i_know', idempotency_key: 'invisible-000000000' });
  assert.equal(missing.status, 404);
  for (const c of [others, agentOnly, guardianOnly, refuted, expired]) {
    same(await post(F.kid.account, `/students/${F.kid.id}/claims/${c}/disputes`, { kind: 'i_know', idempotency_key: 'invisible-000000000' }), missing, c);
  }
  // 家长能看的（guardian 可见）家长可以提
  assert.equal((await post(F.guardian, `/students/${F.kid.id}/claims/${guardianOnly}/disputes`, { kind: 'data_error', idempotency_key: 'visible-g-000000000' })).status, 200);
  assert.equal(disputes.calls.length, before + 1);
});

test('异议：洞察服务的结果映射（404 / 409 / 429 / 不可用）；没有配洞察服务时 503', { skip: !HAS_DB }, async () => {
  const c1 = await claim(F.kid);
  const path = `/students/${F.kid.id}/claims/${c1}/disputes`;
  const cases: [DisputeOutcome, string][] = [[{ kind: 'not_found' }, P.NOT_FOUND], [{ kind: 'conflict' }, P.STATE_CONFLICT],
    [{ kind: 'too_many' }, P.WRITE_CAP], [{ kind: 'unavailable' }, P.TRUST_UNAVAILABLE]];
  try {
    for (const [out, want] of cases) {
      disputes.next = out;
      assert.equal((await post(F.kid.account, path, { kind: 'i_know', idempotency_key: 'mapping-key-00000001' })).text, want, out.kind);
    }
  } finally {
    disputes.next = null;
  }
  const bare = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { features: new Set(['dispute'] as const) } });
  try {
    const x = await callAs(bare.base, 'POST', path, await bare.sessionFor(F.kid.account), { kind: 'i_know', idempotency_key: 'mapping-key-00000002' });
    assert.equal(x.status, 503);
    assert.equal(x.text, P.TRUST_UNAVAILABLE);
  } finally {
    await bare.close();
  }
});

test('异议列表：学生、家长各只看自己提出的；只有编码列，没有事实、作答、验证与复核引用', { skip: !HAS_DB }, async () => {
  const c1 = await claim(F.kid);
  const c2 = await claim(F.kid);
  const dS = await disputeRow(F.kid, c1, F.kid.account, 'student');
  const dG = await disputeRow(F.kid, c2, F.guardian, 'guardian');
  const dC = await disputeRow(F.kid, c1, coGuardian, 'guardian');
  const s = await get(F.kid.account, `/students/${F.kid.id}/disputes`);
  assert.equal(s.status, 200);
  const ids = (r: typeof s) => r.body.disputes.map((d: { dispute_id: string }) => d.dispute_id);
  assert.ok(ids(s).includes(dS) && !ids(s).includes(dG) && !ids(s).includes(dC));
  const g = await get(F.guardian, `/students/${F.kid.id}/disputes`);
  assert.ok(ids(g).includes(dG) && !ids(g).includes(dS) && !ids(g).includes(dC));
  assert.ok(ids(await get(coGuardian, `/students/${F.kid.id}/disputes`)).includes(dC));
  for (const d of [...s.body.disputes, ...g.body.disputes]) {
    assert.deepEqual(Object.keys(d).sort(), ['claim_id', 'created_at', 'dispute_id', 'kind', 'resolved_at', 'result_code', 'status']);
  }
  assert.ok(!/fact|attempt|verification|review|raised_by/.test(s.text + g.text));
  // 解除绑定后读不到（404，与不存在相同）
  await h.su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1 AND student_id = $2 AND status = 'active'`, [coGuardian, F.kid.id]);
  try {
    same(await get(coGuardian, `/students/${F.kid.id}/disputes`), await get(coGuardian, `/students/${MISSING}/disputes`), '解除绑定');
  } finally {
    await relink(coGuardian, F.kid.id);  // 绑定只能解除不能恢复：重新绑定一条
  }
});

// ---------------------------------------------------------------- 考试录入、预测与对账卡

test('考试录入：学生、家长都能录安排（同一场复用）；按安排录成绩；取值不合要求 422；成绩行不混进建档的考试安排', { skip: !HAS_DB }, async () => {
  const plan = { subject: 'aya', exam_type: 'midterm', exam_date: dayPlus(20), full_score: 120, scope_node_ids: ['MATH.TRU.T1.A', 'MATH.TRU.T1.B'] };
  const a = await post(F.kid.account, `/students/${F.kid.id}/exams`, plan);
  assert.equal(a.status, 201, a.text);
  const b = await post(F.guardian, `/students/${F.kid.id}/exams`, plan);
  assert.equal(b.body.exam_id, a.body.exam_id, '同一场考试只有一条当前安排');
  const { rows } = await h.su.query(`SELECT entered_by_role, source_type::text AS s, raw_score FROM insight.exam_record WHERE id = $1`, [a.body.exam_id]);
  assert.deepEqual(rows[0], { entered_by_role: 'self', s: 'self', raw_score: null });
  const sc = await post(F.guardian, `/students/${F.kid.id}/exams/${a.body.exam_id}/score`, { raw_score: 96 });
  assert.equal(sc.status, 201);
  const r2 = await h.su.query(`SELECT plan_record_id, entered_by_role FROM insight.exam_record WHERE id = $1`, [sc.body.score_id]);
  assert.deepEqual(r2.rows[0], { plan_record_id: a.body.exam_id, entered_by_role: 'guardian' });
  const bad = [
    { ...plan, exam_date: dayPlus(-1) }, { ...plan, exam_date: dayPlus(400) }, { ...plan, full_score: 301 }, { ...plan, full_score: 99.5 },
    { ...plan, exam_type: 'mock' }, { ...plan, scope_node_ids: [] }, { ...plan, scope_node_ids: ['not a node'] }, { ...plan, subject: 'nope' },
    { ...plan, extra: 1 },
  ];
  for (const x of bad) assert.equal((await post(F.kid.account, `/students/${F.kid.id}/exams`, x)).status, 422, JSON.stringify(x));
  assert.equal((await post(F.kid.account, `/students/${F.kid.id}/exams/${a.body.exam_id}/score`, { raw_score: 121 })).status, 422, '超过满分');
  assert.equal((await post(F.kid.account, `/students/${F.kid.id}/exams/${MISSING}/score`, { raw_score: 1 })).status, 422, '安排不存在');
  // 另一户孩子的安排 ID 挂在自己孩子路径下：库函数按学生核对，不认
  const oPlan = await post(O.guardian, `/students/${O.kid.id}/exams`, plan);
  assert.equal((await post(F.guardian, `/students/${F.kid.id}/exams/${oPlan.body.exam_id}/score`, { raw_score: 1 })).status, 422);
});

test('预测：只有预测所引判断对当前受众可见、在白名单里才显示区间；冻结标记；不可见的判断（只给 agent）不显示', { skip: !HAS_DB }, async () => {
  const visible = await claim(F.kid);
  const agentOnly = await claim(F.kid, { visibility: ['agent'] });
  const exam = randomUUID();
  const base = { prediction_id: randomUUID(), low: 88, high: 104, interval_level: 0.8, frozen_at: '2026-01-05T00:00:00+08:00', basis: { node_count: 2, evidence_count: 1 } };
  exams.prediction_ = { ...base, claim_id: visible };
  const s = await get(F.kid.account, `/students/${F.kid.id}/exams/${exam}/prediction`);
  assert.equal(s.status, 200);
  assert.deepEqual(s.body.prediction, { prediction_id: base.prediction_id, claim_id: visible, low: 88, high: 104, interval_level: 0.8,
                                        frozen_at: '2026-01-04T16:00:00.000Z', frozen: true });
  assert.ok(!('basis' in s.body.prediction));
  const call = exams.calls.at(-1)!;
  assert.deepEqual(call, { studentRef: F.kid.id, audience: 'student', actorRef: F.kid.account, examRecordId: exam });
  exams.prediction_ = { ...base, claim_id: agentOnly };
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${exam}/prediction`)).body.prediction, null);
  exams.prediction_ = { ...base, claim_id: visible, frozen_at: '2099-01-05T00:00:00+08:00' };
  assert.equal((await get(F.guardian, `/students/${F.kid.id}/exams/${exam}/prediction`)).body.prediction.frozen, false);
  assert.ok(exams.calls.at(-1)!.linkId, '家长带绑定关系');
  exams.prediction_ = null;
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${exam}/prediction`)).body.prediction, null);
});

test('对账卡：每句带 claim_id，只留可见、成立或假设、白名单内、模板对得上的实线句子；其余丢弃并在日志记数；方向只是编码', { skip: !HAS_DB }, async () => {
  const ok = await claim(F.kid);
  const hidden = await claim(F.kid, { template: TPL_HIDDEN });
  const guardianOnly = await claim(F.kid, { visibility: ['agent', 'guardian'] });
  const others = await claim(O.kid);
  const st = (id: string | undefined, extra: Record<string, unknown> = {}) => ({ claim_id: id, template_key: TPL, template_version: 1,
    status: 'established', display: 'solid', text: `（合成模板句 ${String(id).slice(-4)}）`, confidence_band: 'moderate', ...extra });
  const predictionId = randomUUID();
  const predClaim = await claim(F.kid);
  exams.prediction_ = { prediction_id: predictionId, claim_id: predClaim, low: 88, high: 104, interval_level: 0.8, frozen_at: '2026-01-05T00:00:00+08:00',
                        basis: { node_count: 1, evidence_count: 1 } };
  exams.card = { prediction_id: predictionId, actual_score: 81, within_interval: false, deviation_nodes: ['MATH.TRU.T1.A'],
                 deviation_directions: ['diagnosis_optimistic'], adjustments: ['claims_to_verify'],
                 statements: [st(ok), st(undefined), { text: '（没有判断的句子）' }, st(hidden, { template_key: TPL_HIDDEN }), st(guardianOnly), st(others),
                              st(ok, { template_key: 'syn.trust.other' }), st(MISSING)] };
  const before = logs.length;
  const r = await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`);
  assert.equal(r.status, 200);
  const card = r.body.reconciliation;
  assert.deepEqual(card.statements, [{ claim_id: ok, text: `（合成模板句 ${ok.slice(-4)}）`, confidence_band: 'moderate' }]);
  assert.deepEqual(card.deviation_directions, ['diagnosis_optimistic']);
  assert.deepEqual(card.adjustments, ['claims_to_verify']);
  const line = logs.slice(before).find((l) => String(l['route']).endsWith('/reconciliation'));
  assert.equal(line?.['dropped_statements'], '7');
  // 家长能看 guardian 可见的那句
  const g = await get(F.guardian, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`);
  assert.deepEqual(g.body.reconciliation.statements.map((x: { claim_id: string }) => x.claim_id), [ok, guardianOnly]);
  // 卡片依据的预测所引判断对受众不可见、或预测对不上这张卡：整张卡不显示（安全审查 M3）
  const good = exams.card;
  exams.prediction_ = { ...(exams.prediction_ as object), claim_id: await claim(F.kid, { visibility: ['agent'] }) };
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`)).body.reconciliation, null);
  exams.prediction_ = { ...(exams.prediction_ as object), claim_id: predClaim, prediction_id: randomUUID() };
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`)).body.reconciliation, null);
  exams.prediction_ = null;
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`)).body.reconciliation, null);
  exams.prediction_ = { ...(exams.prediction_ ?? {}), prediction_id: predictionId, claim_id: predClaim, low: 88, high: 104, interval_level: 0.8,
                        frozen_at: '2026-01-05T00:00:00+08:00' };
  assert.notEqual((await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`)).body.reconciliation, null);
  // 契约外的方向编码：整张卡不显示
  exams.card = { ...(good as object), deviation_directions: ['careless'] };
  assert.equal((await get(F.kid.account, `/students/${F.kid.id}/exams/${randomUUID()}/reconciliation`)).body.reconciliation, null);
  exams.card = null;
  exams.prediction_ = null;
});

test('预测与对账卡：没有读取通道时 503（契约没有 BFF 可用的工具）', { skip: !HAS_DB }, async () => {
  const bare = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { features: new Set(['reconcile'] as const) } });
  try {
    const s = await bare.sessionFor(F.kid.account);
    for (const p of ['prediction', 'reconciliation']) {
      const x = await callAs(bare.base, 'GET', `/students/${F.kid.id}/exams/${MISSING}/${p}`, s);
      assert.equal(x.status, 503, p);
      assert.equal(x.text, P.TRUST_UNAVAILABLE);
    }
  } finally {
    await bare.close();
  }
});

// ---------------------------------------------------------------- 导出

test('导出：绑定家长建任务、拿一次性凭据；经 BFF 兑换一次（no-store 附件，不含凭据）；别人、用过、解除绑定后都 404', { skip: !HAS_DB }, async () => {
  const x = await post(F.guardian, `/students/${F.kid.id}/exports`);
  assert.equal(x.status, 201, x.text);
  assert.deepEqual(Object.keys(x.body).sort(), ['download_ref', 'expires_at', 'export_job_id', 'status']);
  assert.match(x.body.download_ref, /^dl_[0-9a-f]{64}$/);
  assert.ok(Date.parse(x.body.expires_at) - Date.now() <= 24 * 3600_000 + 60_000);
  const { rows } = await h.su.query(`SELECT requested_by, status FROM platform.export_job WHERE id = $1`, [x.body.export_job_id]);
  assert.deepEqual(rows[0], { requested_by: F.guardian, status: 'ready' }, '导出任务即审计记录');
  const dl = `/students/${F.kid.id}/exports/${x.body.export_job_id}/download`;
  // 别的家长（另一户、同一孩子的另一位家长）拿同一个凭据：404，且不作废
  assert.equal((await post(O.guardian, dl, { download_ref: x.body.download_ref })).status, 404);
  assert.equal((await post(coGuardian, dl, { download_ref: x.body.download_ref })).status, 404);
  assert.equal((await post(F.guardian, dl, { download_ref: `dl_${'f'.repeat(64)}` })).status, 404);
  assert.equal((await post(F.guardian, `/students/${F.kid.id}/exports/${MISSING}/download`, { download_ref: x.body.download_ref })).status, 404);
  const d = await post(F.guardian, dl, { download_ref: x.body.download_ref });
  assert.equal(d.status, 200, d.text);
  assert.equal(d.headers.get('cache-control'), 'no-store');
  assert.match(String(d.headers.get('content-disposition')), /^attachment; filename="starbuddy-export-[0-9a-f-]{36}\.json"$/);
  assert.equal(d.body.format, 'starbuddy-export@1');
  assert.equal(d.body.student_id, F.kid.id);
  assert.equal(d.body.export_job_id, x.body.export_job_id);
  assert.ok(d.body.topics && typeof d.body.topics === 'object');
  assert.ok(!d.text.includes(x.body.download_ref) && !('download_ref' in d.body), '下载内容不含凭据');
  assert.ok(!d.text.includes(O.kid.id), '不含别的学生');
  same(await post(F.guardian, dl, { download_ref: x.body.download_ref }), await post(F.guardian, `/students/${F.kid.id}/exports/${MISSING}/download`,
       { download_ref: x.body.download_ref }), '只能兑换一次');
  // 解除绑定：发起导出 404；解除前拿到的凭据兑换也 404
  const y = await post(coGuardian, `/students/${F.kid.id}/exports`);
  assert.equal(y.status, 201);
  await h.su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1 AND student_id = $2 AND status = 'active'`, [coGuardian, F.kid.id]);
  try {
    assert.equal((await post(coGuardian, `/students/${F.kid.id}/exports`)).status, 404);
    assert.equal((await post(coGuardian, `/students/${F.kid.id}/exports/${y.body.export_job_id}/download`, { download_ref: y.body.download_ref })).status, 404);
  } finally {
    await relink(coGuardian, F.kid.id);  // 绑定只能解除不能恢复：重新绑定一条
  }
  // 解除绑定时的兑换尝试清掉了这位家长的暂存：重新绑定后旧凭据也不能再用（安全审查 M1）
  assert.equal((await post(coGuardian, `/students/${F.kid.id}/exports/${y.body.export_job_id}/download`, { download_ref: y.body.download_ref })).status, 404);
  // 访问日志：导出与兑换记账号摘要与任务 ID（安全审查 M2），不记账号原文与凭据
  const lines = logs.filter((l) => l['guardian_op'] === 'export' || l['guardian_op'] === 'export_download');
  assert.ok(lines.length >= 3 && lines.every((l) => /^[0-9a-f]{16}$/.test(String(l['account'])) && l['account'] !== F.guardian));
  assert.ok(lines.some((l) => l['export_job'] === x.body.export_job_id));
});

// ---------------------------------------------------------------- 删除

test('删除：发起 → 会话内二次确认（换会话、错凭据 403）→ 冷静期 → 另一位家长撤回；状态与回执；监护操作留痕', { skip: !HAS_DB }, async () => {
  const r = await post(F.guardian, `/students/${F.kid.id}/erasure`);
  assert.equal(r.status, 201, r.text);
  assert.match(r.body.confirm_token, /^[0-9a-f]{64}$/);
  const again = await post(F.guardian, `/students/${F.kid.id}/erasure`);
  assert.equal(again.body.request_id, r.body.request_id, '未结束的请求只有一条');
  const confirm = `/students/${F.kid.id}/erasure/${r.body.request_id}/confirm`;
  // 错凭据、换了会话（同一家长重新登录）拿旧凭据：403
  assert.equal((await post(F.guardian, confirm, { confirm_token: '0'.repeat(64) })).text, P.CONFIRM_REQUIRED);
  shell.forget(F.guardian);
  assert.equal((await post(F.guardian, confirm, { confirm_token: r.body.confirm_token })).text, P.CONFIRM_REQUIRED, '换了会话');
  // 另一位家长不是发起人：即使拿到自己会话的凭据也确认不了（库函数只认发起人）→ 404
  const co = await post(coGuardian, `/students/${F.kid.id}/erasure`);
  assert.equal(co.body.request_id, r.body.request_id);
  assert.equal((await post(coGuardian, confirm, { confirm_token: co.body.confirm_token })).status, 404);
  // 本会话重新发起拿到新凭据；登录超过 10 分钟：403 要求重新登录（安全审查 H1）
  const fresh = await post(F.guardian, `/students/${F.kid.id}/erasure`);
  offset = 11 * 60_000;
  try {
    assert.equal((await post(F.guardian, confirm, { confirm_token: fresh.body.confirm_token })).text, P.REAUTH_REQUIRED);
  } finally {
    offset = 0;
  }
  const ok = await post(F.guardian, confirm, { confirm_token: fresh.body.confirm_token });
  assert.equal(ok.status, 200, ok.text);
  const until = Date.parse(ok.body.cooling_off_until);
  assert.ok(until - Date.now() > 6.9 * 86400_000 && until - Date.now() <= 7 * 86400_000 + 60_000, '冷静期 7 天');
  const list = await get(F.guardian, `/students/${F.kid.id}/erasure`);
  const row = list.body.requests.find((x: { request_id: string }) => x.request_id === r.body.request_id);
  assert.equal(row.status, 'pending');
  assert.equal(row.cooling_off_until, ok.body.cooling_off_until);
  assert.equal(row.receipt, null);
  assert.ok(!/requested_by|cancelled_by|[+]999/.test(list.text), '不含发起人、撤回人');
  // 冷静期内撤回：同一孩子的另一位有效绑定家长也能撤回；再撤回幂等
  const cancel = `/students/${F.kid.id}/erasure/${r.body.request_id}/cancel`;
  assert.equal((await post(O.guardian, cancel)).status, 404);
  const c = await post(coGuardian, cancel);
  assert.deepEqual(c.body, { request_id: r.body.request_id, status: 'cancelled' });
  assert.equal((await post(coGuardian, cancel)).status, 200);
  assert.equal((await post(F.guardian, confirm, { confirm_token: fresh.body.confirm_token })).text, P.STATE_CONFLICT, '已撤回的不能再确认');
  const after = await get(F.guardian, `/students/${F.kid.id}/erasure`);
  const row2 = after.body.requests.find((x: { request_id: string }) => x.request_id === r.body.request_id);
  assert.equal(row2.status, 'cancelled');
  assert.equal(row2.cancel_reason, 'guardian');
  const { rows } = await h.su.query(`SELECT requested_by, cancelled_by FROM platform.erasure_request WHERE id = $1`, [r.body.request_id]);
  assert.deepEqual(rows[0], { requested_by: F.guardian, cancelled_by: coGuardian }, '监护操作在库里留痕');
  // 访问日志只记操作编码，不记凭据
  const text = logs.map((l) => JSON.stringify(l)).join('\n');
  for (const op of ['erasure_request', 'erasure_confirm', 'erasure_cancel', 'export', 'export_download']) assert.ok(text.includes(`"guardian_op":"${op}"`), op);
  assert.ok(!text.includes(fresh.body.confirm_token) && !/dl_[0-9a-f]{64}/.test(text), '日志没有凭据');
  // 路径里的请求属于别的孩子：404
  const o = await post(O.guardian, `/students/${O.kid.id}/erasure`);
  assert.equal((await post(F.guardian, `/students/${F.kid.id}/erasure/${o.body.request_id}/cancel`)).status, 404);
});

test('禁用推断：BFF 不生成关于学生的文字——信任界面的源码除注释与启动配置错误外没有中文字符串（显示文案属 5-H 模板）', async () => {
  const { readFileSync, readdirSync } = await import('node:fs');
  const files = [new URL('../src/http/trust-routes.ts', import.meta.url),
                 ...readdirSync(new URL('../src/trust/', import.meta.url)).map((n) => new URL(`../src/trust/${n}`, import.meta.url))];
  for (const f of files) {
    const code = readFileSync(f, 'utf8').split('\n')
      .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l) && !/throw new Error\(/.test(l))  // 启动时的配置错误不是给学生、家长看的文字
      .map((l) => l.replace(/\/\/.*$/, '')).join('\n');
    const strings = code.match(/(['"`])(?:\\.|(?!\1).)*\1/g) ?? [];
    for (const s of strings) assert.ok(!/[一-鿿]/.test(s), `${f.pathname}: ${s}`);
  }
});

test('删除已确认：未兑换的导出包作废、下载 404、不能新导出（409）；撤回后恢复；解除绑定的家长兑换时只清自己的（安全审查 M1）', { skip: !HAS_DB }, async () => {
  const kid = O.kid.id;
  const a = await post(O.guardian, `/students/${kid}/exports`);
  assert.equal(a.status, 201, a.text);
  const before = store.size;
  shell.forget(O.guardian);  // 重新登录：删除确认要求近期登录
  const r = await post(O.guardian, `/students/${kid}/erasure`);
  assert.equal((await post(O.guardian, `/students/${kid}/erasure/${r.body.request_id}/confirm`, { confirm_token: r.body.confirm_token })).status, 200);
  assert.equal(store.size, before - 1, '确认时清掉该学生的暂存');
  assert.equal((await post(O.guardian, `/students/${kid}/exports/${a.body.export_job_id}/download`, { download_ref: a.body.download_ref })).status, 404);
  // 另一个实例上确认的删除（本实例的暂存没被清）：兑换时查到已确认的删除请求，同样 404 并清掉
  const ref = `dl_${'7'.repeat(64)}`;
  assert.ok(store.put(ref, { jobId: a.body.export_job_id, studentId: kid, accountId: O.guardian, expiresAt: Date.now() + 600_000, body: '{}' }, Date.now()));
  assert.equal((await post(O.guardian, `/students/${kid}/exports/${a.body.export_job_id}/download`, { download_ref: ref })).status, 404);
  assert.equal(store.purgeStudent(kid), 0, '兑换时已清掉');
  const blocked = await post(O.guardian, `/students/${kid}/exports`);
  assert.equal(blocked.status, 409);
  assert.equal(blocked.text, P.STATE_CONFLICT);
  const { rows } = await h.su.query(`SELECT count(*)::int AS n FROM platform.export_job WHERE student_id = $1`, [kid]);
  assert.equal(rows[0].n, 1, '被拒的导出没有建任务');
  assert.equal((await post(O.guardian, `/students/${kid}/erasure/${r.body.request_id}/cancel`)).status, 200);
  assert.equal((await post(O.guardian, `/students/${kid}/exports`)).status, 201, '撤回后可以导出');
});

test('读路由限流：GET 异议列表、删除状态与周报按会话每分钟（trust_read.session.minute）', { skip: !HAS_DB }, async () => {
  const bucket = 'trust_read.session.minute';
  const saved = shell.config.rate_limits[bucket]!;
  shell.forget(F.guardian);
  shell.forget(F.kid.account);
  shell.forget(coGuardian);
  const g = await sess(F.guardian);
  const st = await sess(F.kid.account);
  const co = await sess(coGuardian);
  shell.config.rate_limits[bucket] = { window_seconds: 60, limit: 1 };
  try {
    for (const [who, path] of [[st, `/students/${F.kid.id}/disputes`], [g, `/students/${F.kid.id}/erasure`], [co, `/students/${F.kid.id}/weekly-report`]] as const) {
      assert.equal((await callAs(shell.base, 'GET', path, who)).status, 200, path);
      assert.equal((await callAs(shell.base, 'GET', path, who)).text, P.TOO_MANY, path);
    }
  } finally {
    shell.config.rate_limits[bucket] = saved;
  }
});

// ---------------------------------------------------------------- 周报（weekly，冲刺 5-H 跨科周报 v0）

/** 合成判断（周报用；可指定来源）：复制模式直接写行，只在一次性测试库里造数据 */
async function claimW(kid: Kid, o: { source?: string; template?: string; visibility?: string[]; status?: string } = {}): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence, source_type,
                                  status, change_reason, visibility, expires_at, evidence_ids)
       VALUES ($1, $2, 'aya', 'node', 'MATH.TRU.W1.A', $3, 1, 0.7, $4::insight.source_type, $6::insight.claim_status, 'created', $5,
               now() + interval '30 days', ARRAY[gen_random_uuid()])`,
      [id, kid.id, o.template ?? TPL, o.source ?? 'objective', o.visibility ?? ['agent', 'student', 'guardian'], o.status ?? 'established']);
    await c.query('COMMIT');
  } finally {
    c.release(true);
  }
  return id;
}
const wsent = (claimId: string, section: string, audience: 'student' | 'guardian', tpl = TPL) =>
  ({ claim_id: claimId, template_key: tpl, template_version: 1, section, text: `合成渲染句 ${section}`, ...(audience === 'guardian' ? { confidence_band: 'moderate' } : {}) });
const OBJECTIVE = { source_type: 'objective', usage_minutes: 95, plan_done: 4, plan_skipped: 1, subjects: [{ subject: 'aya', practice_minutes: 60, attempts: 18 }] };
/** 写一份周报（超级用户；replica = 绕过写入守卫，模拟“库里已有的不合规快照”，验证 BFF 的库内再核）。
 *  每句按库里判断当前的 updated_at 盖修订标记（与 0036 守卫同一做法；replica 下守卫不跑，这里照样盖上）；默认上一周（只收已结束的周） */
async function weeklyRow(kid: Kid, audience: 'student' | 'guardian', sentences: object[], o: { weeksAgo?: number; withheld?: boolean; replica?: boolean } = {}) {
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    if (o.replica) await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.weekly_report (student_id, week_start, week_end, audience, status, sentences, objective, withheld_reasons, algorithm_version, policy_version)
       SELECT $1, w, w + 6, $2, $3,
              (SELECT coalesce(jsonb_agg(x.s || jsonb_build_object('claim_revision', c.updated_at) ORDER BY x.o), '[]')
                 FROM jsonb_array_elements($4::jsonb) WITH ORDINALITY x(s, o) JOIN insight.claim c ON c.id = (x.s ->> 'claim_id')::uuid),
              $5::jsonb, $6::text[], 'sie-weekly@0.1.0', 'syn'
         FROM (SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date - 7 * $7::int AS w) x`,
      [kid.id, audience, o.withheld ? 'withheld' : 'generated', JSON.stringify(o.withheld ? [] : sentences), o.withheld ? null : JSON.stringify(OBJECTIVE),
       o.withheld ? ['forbidden_inference'] : [], o.weeksAgo ?? 1]);
    await c.query('COMMIT');
  } finally {
    c.release(true);
  }
}
const weeklyOf = (account: string, kid: Kid) => get(account, `/students/${kid.id}/weekly-report`);

test('周报：学生读学生版“成长手记”、家长读家长版；每句带 claim_id；客观栏来源 objective；no-store；只取最近一周', { skip: !HAS_DB }, async () => {
  const W = await family();
  const c1 = await claimW(W.kid);
  const c2 = await claimW(W.kid, { status: 'hypothesis' });
  await weeklyRow(W.kid, 'student', [wsent(c1, 'lit', 'student'), { ...wsent(c2, 'working_on', 'student'), label: 'preliminary' }]);
  await weeklyRow(W.kid, 'guardian', [wsent(c1, 'progress', 'guardian'), { ...wsent(c2, 'stuck', 'guardian'), label: 'preliminary' }]);
  await weeklyRow(W.kid, 'student', [wsent(c1, 'working_on', 'student')], { weeksAgo: 2 });
  const s = await weeklyOf(W.kid.account, W.kid);
  assert.equal(s.status, 200, s.text);
  assert.equal(s.headers.get('cache-control'), 'no-store');
  const r = s.body.report;
  assert.deepEqual(Object.keys(r).sort(), ['generated_at', 'objective', 'sections', 'week_end', 'week_start']);
  assert.deepEqual(r.sections.map((x: any) => x.key), ['lit', 'working_on'], '最近一周（本周）的学生版');
  assert.deepEqual(r.sections.flatMap((x: any) => x.sentences.map((y: any) => y.claim_id)), [c1, c2]);
  assert.deepEqual(r.sections[1].sentences[0], { claim_id: c2, text: '合成渲染句 working_on', source_type: 'objective', label: 'preliminary' },
                   '学生版不带置信度分档，不转模板键与修订标记；“初步判断”按判断当前状态推导，随句带来源');
  assert.equal(r.sections[0].sentences[0].label, undefined, '成立的判断不标“初步判断”');
  assert.deepEqual(r.objective, OBJECTIVE);
  const g = await weeklyOf(W.guardian, W.kid);
  assert.deepEqual(g.body.report.sections.map((x: any) => x.key), ['progress', 'stuck']);
  assert.ok(g.body.report.sections.every((x: any) => x.sentences.every((y: any) => y.confidence_band === 'moderate')));
  // 另一户、没有绑定的家长：与不存在同一个 404（授权矩阵另有全表覆盖）
  assert.equal((await weeklyOf(O.guardian, W.kid)).status, 404);
  assert.equal((await weeklyOf(unlinked, W.kid)).status, 404);
  // 还没有周报的孩子：report 为 null
  assert.deepEqual((await weeklyOf(O.kid.account, O.kid)).body, { student_id: O.kid.id, report: null });
});

test('周报：删掉或降为不可见任一判断、过期、白名单外、模板对不上，对应句子消失并记数；一句不剩时 report 为 null', { skip: !HAS_DB }, async () => {
  const W = await family();
  const [a, b, c, d, e] = [await claimW(W.kid), await claimW(W.kid), await claimW(W.kid), await claimW(W.kid, { template: TPL_HIDDEN }), await claimW(W.kid)];
  await weeklyRow(W.kid, 'student', [wsent(a, 'lit', 'student'), wsent(b, 'working_on', 'student'), wsent(c, 'working_on', 'student'),
                                     wsent(d, 'working_on', 'student', TPL_HIDDEN), wsent(e, 'working_on', 'student')]);
  const ids = async () => ((await weeklyOf(W.kid.account, W.kid)).body.report?.sections ?? [])
    .flatMap((x: any) => x.sentences.map((y: any) => y.claim_id)).sort();
  assert.deepEqual(await ids(), [a, b, c, e].sort(), '白名单外的判断即使可见也不展示');
  const su = async (sql: string, p: unknown[]) => {
    const k = await h.su.connect();
    try {
      await k.query('BEGIN');
      await k.query('SET LOCAL session_replication_role = replica');
      await k.query(sql, p);
      await k.query('COMMIT');
    } finally {
      k.release(true);
    }
  };
  await su(`UPDATE insight.claim SET visibility = '{agent,guardian}' WHERE id = $1`, [b]);          // 降为学生不可见
  await su(`UPDATE insight.claim SET status = 'withdrawn' WHERE id = $1`, [c]);                     // 撤回
  await su(`UPDATE insight.claim SET created_at = now() - interval '2 days', expires_at = now() - interval '1 minute' WHERE id = $1`, [e]); // 过期
  logs.length = 0;
  assert.deepEqual(await ids(), [a]);
  assert.ok(logs.some((l) => l['dropped_statements'] === '4'), JSON.stringify(logs.slice(-3)));
  await su(`UPDATE insight.claim SET template_version = 2 WHERE id = $1`, [a]);                    // 模板对不上
  assert.deepEqual((await weeklyOf(W.kid.account, W.kid)).body, { student_id: W.kid.id, report: null });
});

test('周报：学生版不显示家长观察来源的判断（库内再核，即使快照里有）；不出的周报谁都读不到', { skip: !HAS_DB }, async () => {
  const W = await family();
  const own = await claimW(W.kid);
  const fromGuardian = await claimW(W.kid, { source: 'guardian' });
  await weeklyRow(W.kid, 'student', [wsent(own, 'working_on', 'student'), wsent(fromGuardian, 'working_on', 'student')], { replica: true });
  await weeklyRow(W.kid, 'guardian', [wsent(fromGuardian, 'stuck', 'guardian')]);
  const s = await weeklyOf(W.kid.account, W.kid);
  assert.deepEqual(s.body.report.sections.flatMap((x: any) => x.sentences.map((y: any) => y.claim_id)), [own]);
  assert.deepEqual((await weeklyOf(W.guardian, W.kid)).body.report.sections[0].sentences.map((y: any) => [y.claim_id, y.source_type]),
                   [[fromGuardian, 'guardian']], '家长版可以有家长观察来源的判断，随句标来源');
  const V = await family();
  const v1 = await claimW(V.kid);
  await weeklyRow(V.kid, 'student', [], { withheld: true });
  await weeklyRow(V.kid, 'guardian', [wsent(v1, 'stuck', 'guardian')], { withheld: true });
  assert.deepEqual((await weeklyOf(V.kid.account, V.kid)).body, { student_id: V.kid.id, report: null });
  assert.deepEqual((await weeklyOf(V.guardian, V.kid)).body, { student_id: V.kid.id, report: null });
});

test('周报：生成之后被修订过的判断（修订标记对不上）句子丢弃；命中禁用推断词表的句子丢弃（安全审查 F1）', { skip: !HAS_DB }, async () => {
  const W = await family();
  const [a, b, c] = [await claimW(W.kid), await claimW(W.kid), await claimW(W.kid)];
  await weeklyRow(W.kid, 'student', [wsent(a, 'lit', 'student'), wsent(b, 'working_on', 'student'),
                                     { ...wsent(c, 'working_on', 'student'), text: '合成渲染句：这孩子有点懒惰' }]);
  const ids = async () => ((await weeklyOf(W.kid.account, W.kid)).body.report?.sections ?? [])
    .flatMap((x: any) => x.sentences.map((y: any) => y.claim_id)).sort();
  logs.length = 0;
  assert.deepEqual(await ids(), [a, b].sort(), '命中禁用推断词表的句子不显示');
  assert.ok(logs.some((l) => l['dropped_statements'] === '1'));
  // 正常修订（守卫照常写 updated_at 与历史）：判断在生成之后变了，快照里的句子不再显示
  await h.su.query(`UPDATE insight.claim SET confidence = 0.9, change_reason = 'revised' WHERE id = $1`, [b]);
  assert.deepEqual(await ids(), [a]);
});
