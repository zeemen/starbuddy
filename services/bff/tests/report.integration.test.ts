// SIE-02b 第二步 PR 2：首份报告（GET …/first-report，开关 report）与单条解释（GET …/claims/{claim_id}/explain，开关 why）的 BFF 集成测试
// （specs/sie-02b-claims-first-report/spec.md“第二步补充”第 3 条；lessons 第二步 PR 1：BFF 只转发 result）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，真实会话、真实 RLS；洞察服务用按契约返回的假实现（计数）。
// 覆盖：开关关闭不注册；先按档案授权（学生本人 / 有效绑定家长；兄妹、另一户、未绑定、撤销绑定、不存在同一个 404，越权请求到不了洞察服务）；
// 只转发 result（响应里没有信封外层的 confidence、evidence_ids、source_version、model_version，证据链没有 evidence_id）；
// 洞察服务 404 → 报告 null / 解释 404，不可用 → 503；结果形状、受众、禁用推断、库内再核（可见范围、状态、模板、白名单、到期）、
// 节键与模板类别任何一处不合整份不出；报告的 policy_version 记访问日志；解释按受众裁剪（学生无置信度分档、待验证无文字）；
// 按会话限流。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, test } from 'node:test';
import { P } from '../src/http/problems.ts';
import { loadShellConfig, type ShellConfig } from '../src/shell/config.ts';
import type { Who } from '../src/trust/clients.ts';
import type { ExplainWho, InsightReadClient, ReadOutcome } from '../src/trust/report.ts';
import { HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const TPL = 'syn.report.node_mastery';       // 对外白名单里
const TPL_T = 'syn.report.node_transfer';    // 对外白名单里
const TPL_HIDDEN = 'syn.report.not_listed';  // 已审核但不在白名单里
const MISSING = '5c000000-0000-4000-8000-0000000000ff';
const E1 = '5c000000-0000-4000-8000-000000000301';
const OUTER = ['confidence', 'evidence_ids', 'evidence_id', 'source_version', 'model_version', 'human_review_required', 'review_reasons', 'contract_version'];

let h: Harness;
let shell: ShellHarness;   // report、why 打开，配假洞察服务
let off: ShellHarness;     // 开关全关
let bare: ShellHarness;    // 开关打开，没配洞察服务
const logs: Record<string, unknown>[] = [];

// ---------------------------------------------------------------- 假洞察服务（计数）
class FakeRead implements InsightReadClient {
  reports: Who[] = [];
  explains: ExplainWho[] = [];
  report_: (who: Who) => ReadOutcome = () => ({ kind: 'not_found' });
  explain_: (who: ExplainWho) => ReadOutcome = () => ({ kind: 'not_found' });
  async report(who: Who) { this.reports.push(who); return this.report_(who); }
  async explain(who: ExplainWho) { this.explains.push(who); return this.explain_(who); }
}
const read = new FakeRead();
const cfg: ShellConfig = { ...loadShellConfig(), externalTemplates: new Set([`${TPL}@1`, `${TPL_T}@1`]) };
const ENV = { source_version: 'algo:sie-report@0.1.0', human_review_required: false };
const ok = (result: unknown, envelope = ENV): ReadOutcome => ({ kind: 'ok', result, envelope });

// ---------------------------------------------------------------- 合成家庭与判断
interface Kid { id: string; account: string }
let F: { guardian: string; household: string; kid: Kid; sibling: Kid };
let O: { guardian: string; kid: Kid };
let revoked = '';   // 曾绑定 F.kid，拿到会话后撤销
let unlinked = '';  // 没有绑定任何孩子的家长
const C: Record<string, string> = {};

async function kid(household: string): Promise<Kid> {
  const account = randomUUID();
  const id = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [account]);
  await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 8, 'nanjing')`,
                   [id, account, household]);
  return { id, account };
}
const link = (g: string, s: string) => h.su.query(
  `INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`, [g, s]);

/** 合成判断：复制模式直接写行（0010 的触发器只允许洞察服务写入；只在一次性测试库里造数据） */
async function claim(k: Kid, o: { status?: string; visibility?: string[]; template?: string; expired?: boolean; target?: 'node' | 'exam' } = {}): Promise<string> {
  const id = randomUUID();
  const c = await h.su.connect();
  try {
    await c.query('BEGIN');
    await c.query('SET LOCAL session_replication_role = replica');
    await c.query(
      `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                  source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
       VALUES ($1, $2, 'aya', $8, 'MATH.SYN.R1', $3, 1, 0.7, 'inferred', $4::insight.claim_status, 'created', $5, $6,
               'mastery_estimate', gen_random_uuid(), 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], $7, $7)`,
      [id, k.id, o.template ?? TPL, o.status ?? 'established', o.visibility ?? ['agent', 'student', 'guardian'],
       new Date(Date.now() + (o.expired ? -3600_000 : 30 * 86400_000)), new Date(Date.now() - (o.expired ? 7200_000 : 0)), o.target ?? 'node']);
    await c.query('COMMIT');
  } catch (e) {
    await c.query('ROLLBACK').catch(() => undefined);
    throw e;
  } finally {
    c.release(true);
  }
  return id;
}

/** 按契约的报告 result：claims = [判断 ID, 状态, 模板]；家长版每句带置信度 */
function report(audience: 'student' | 'guardian', claims: [string, 'hypothesis' | 'established', string?][], over: Record<string, unknown> = {}) {
  const sentences = claims.map(([id, status, tpl]) => ({
    claim_id: id, template_key: tpl ?? TPL, template_version: 1, status, display: 'solid', text: `（合成）模板句 ${id.slice(0, 8)}`,
    ...(status === 'hypothesis' ? { label: 'preliminary' } : {}), ...(audience === 'guardian' ? { confidence_band: 'moderate' } : {}),
  }));
  return { format: 'sie-first-report/0.1', audience, policy_version: 'syn-0.1.0', notice: '【占位·待人审】首份报告顶部提示语',
           title: '【占位·待人审】首份报告标题', sections: [{ key: 'node_mastery', connective: '【占位衔接·甲】', sentences }],
           claim_ids: claims.map(([id]) => id), ...over };
}
function explanation(claimId: string, status: string, over: Record<string, unknown> = {}) {
  return {
    statement: { claim_id: claimId, template_key: TPL, template_version: 1, status, display: status === 'to_verify' ? 'dashed' : 'solid',
                 text: '（合成）模板句', confidence_band: 'moderate' },
    evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T16:00:00+08:00', source_type: 'objective', evidence_level: 'A', node_id: 'MATH.SYN.R1' }],
    confidence_band: 'moderate', expires_at: '2026-11-09T00:00:00+08:00', ...over,
  };
}
const keysDeep = (v: unknown): string[] => (Array.isArray(v) ? v.flatMap(keysDeep)
  : v && typeof v === 'object' ? Object.entries(v).flatMap(([k, x]) => [k, ...keysDeep(x)]) : []);
const sess = (account: string) => shell.sessionFor(account);
const get = async (account: string, path: string, on: ShellHarness = shell) => callAs(on.base, 'GET', path, await on.sessionFor(account));
const same = (a: { status: number; text: string }, b: { status: number; text: string }, what: string) => {
  assert.equal(a.status, b.status, what);
  assert.equal(a.text, b.text, what);
};
const lastLog = () => logs.at(-1) ?? {};

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    SELECT k, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now()
                      FROM unnest($1::text[]) k ON CONFLICT DO NOTHING`, [[TPL, TPL_T, TPL_HIDDEN]]);
  const guardian = randomUUID();
  const household = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [guardian]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, guardian]);
  F = { guardian, household, kid: await kid(household), sibling: await kid(household) };
  await link(guardian, F.kid.id);
  await link(guardian, F.sibling.id);
  const og = randomUUID();
  const oh = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [og]);
  await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [oh, og]);
  O = { guardian: og, kid: await kid(oh) };
  await link(og, O.kid.id);
  revoked = randomUUID();
  unlinked = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'guardian')`, [revoked, unlinked]);
  await link(revoked, F.kid.id);
  C['est'] = await claim(F.kid);
  C['hyp'] = await claim(F.kid, { status: 'hypothesis' });
  C['tra'] = await claim(F.kid, { template: TPL_T });
  C['ver'] = await claim(F.kid, { status: 'to_verify' });
  C['agent'] = await claim(F.kid, { visibility: ['agent'] });
  C['studentOnly'] = await claim(F.kid, { visibility: ['agent', 'student'] });
  C['hidden'] = await claim(F.kid, { template: TPL_HIDDEN });
  C['expired'] = await claim(F.kid, { expired: true });
  C['refuted'] = await claim(F.kid, { status: 'refuted' });
  C['exam'] = await claim(F.kid, { target: 'exam' });
  C['sib'] = await claim(F.sibling);
  C['other'] = await claim(O.kid);
  shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l), shell: { config: cfg },
                                          trust: { features: new Set(['report', 'why'] as const), read } });
  off = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { read } });
  bare = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { features: new Set(['report', 'why'] as const) } });
  // 撤销绑定的家长：先拿到会话再撤销（旧会话不能继续读）
  await sess(revoked);
  await h.su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1`, [revoked]);
});

after(async () => {
  if (!HAS_DB) return;
  await shell.close();
  await off.close();
  await bare.close();
  await h.close();
});

const R = (s: string) => `/students/${s}/first-report`;
const X = (s: string, c: string) => `/students/${s}/claims/${c}/explain`;

test('报告：开关关闭（缺省）两条路由不注册，与表外路径逐字节相同的 404；只开 report 时解释仍不注册，反之亦然', { skip: !HAS_DB }, async () => {
  const outside = await get(F.guardian, '/bff/v0/definitely-not-a-route', off);
  assert.equal(outside.status, 404);
  same(await get(F.guardian, R(F.kid.id), off), outside, 'first-report 关');
  same(await get(F.guardian, X(F.kid.id, C['est']!), off), outside, 'explain 关');
  for (const [only, offPath] of [['report', X(F.kid.id, C['est']!)], ['why', R(F.kid.id)]] as const) {
    const partial = await startShell(h.bff, h.su, { log: () => undefined, shell: { config: cfg }, trust: { features: new Set([only]), read } });
    try {
      same(await get(F.guardian, offPath, partial), await get(F.guardian, '/bff/v0/definitely-not-a-route', partial), `只开 ${only}`);
    } finally {
      await partial.close();
    }
  }
  assert.equal(read.reports.length + read.explains.length, 0);
});

test('报告：学生本人得学生版、家长得家长版；只转发 result（没有信封外层的置信度、证据 ID、版本）；转给洞察服务的身份来自会话', { skip: !HAS_DB }, async () => {
  read.reports.length = 0;
  read.report_ = (who) => ok(report(who.audience, [[C['est']!, 'established'], [C['hyp']!, 'hypothesis']]));
  const st = await get(F.kid.account, R(F.kid.id));
  assert.equal(st.status, 200, st.text);
  assert.deepEqual(Object.keys(st.body).sort(), ['report', 'student_id']);
  assert.deepEqual(st.body.report, report('student', [[C['est']!, 'established'], [C['hyp']!, 'hypothesis']]));
  assert.equal(st.headers.get('cache-control'), 'no-store');
  for (const k of keysDeep(st.body)) assert.ok(!OUTER.includes(k), `学生版响应里不该有 ${k}`);
  assert.ok(!st.text.includes('algo:sie-report'), '依据版本不给前端');
  assert.deepEqual(read.reports[0], { studentRef: F.kid.id, audience: 'student', actorRef: F.kid.account });
  assert.equal(lastLog()['source_version'], 'algo:sie-report@0.1.0', '依据版本只记访问日志');
  assert.equal(lastLog()['policy_version'], 'syn-0.1.0', '策略版本记访问日志');

  const g = await get(F.guardian, R(F.kid.id));
  assert.equal(g.status, 200, g.text);
  assert.equal(g.body.report.audience, 'guardian');
  assert.ok(g.body.report.sections[0].sentences.every((s: { confidence_band?: string }) => s.confidence_band === 'moderate'), '家长版每句带置信度分档');
  for (const k of keysDeep(g.body)) assert.ok(!OUTER.includes(k), `家长版响应里不该有 ${k}`);
  const { rows: [{ id: linkId }] } = await h.su.query(
    `SELECT id FROM platform.guardian_student_link WHERE guardian_account_id = $1 AND student_id = $2 AND status = 'active'`, [F.guardian, F.kid.id]);
  assert.deepEqual(read.reports[1], { studentRef: F.kid.id, audience: 'guardian', actorRef: F.guardian, linkId });
  // 家长读兄妹（自己的另一个孩子）是有效绑定，可以读
  assert.equal((await get(F.guardian, R(F.sibling.id))).status, 200);
});

test('报告与解释：兄妹的学生会话、另一户家长与学生、没有绑定、撤销绑定（旧会话）、不存在、非 UUID 同一个 404，越权请求一次也到不了洞察服务', { skip: !HAS_DB }, async () => {
  read.reports.length = 0;
  read.explains.length = 0;
  read.report_ = (who) => ok(report(who.audience, [[C['est']!, 'established']]));
  read.explain_ = () => ok(explanation(C['est']!, 'established'));
  for (const path of [R(F.kid.id), X(F.kid.id, C['est']!)]) {
    const missing = await get(O.guardian, path.replace(F.kid.id, MISSING));
    assert.equal(missing.status, 404, path);
    assert.equal(missing.text, P.NOT_FOUND);
    same(await get(F.sibling.account, path), missing, `兄妹的学生会话 ${path}`);
    same(await get(O.guardian, path), missing, `另一户家长 ${path}`);
    same(await get(O.kid.account, path), missing, `另一户学生 ${path}`);
    same(await get(unlinked, path), missing, `没有绑定的家长 ${path}`);
    same(await get(revoked, path), missing, `撤销绑定的家长（旧会话）${path}`);
    same(await get(F.kid.account, path.replace(F.kid.id, MISSING)), missing, `学生会话 + 不存在 ${path}`);
    same(await get(O.guardian, path.replace(F.kid.id, 'not-a-uuid')), missing, `非 UUID ${path}`);
    const anon = await callAs(shell.base, 'GET', path, null);
    assert.equal(anon.status, 401);
  }
  assert.equal(read.reports.length + read.explains.length, 0, '越权请求没有到达洞察服务');
});

test('报告：洞察服务 404（无权、没有报告、检查不过）→ report null；不可用 → 503；没配洞察服务 → 503（越权仍先 404）', { skip: !HAS_DB }, async () => {
  read.report_ = () => ({ kind: 'not_found' });
  const nf = await get(F.kid.account, R(F.kid.id));
  assert.equal(nf.status, 200);
  assert.deepEqual(nf.body, { student_id: F.kid.id, report: null });
  read.report_ = () => ({ kind: 'unavailable' });
  const un = await get(F.kid.account, R(F.kid.id));
  assert.equal(un.status, 503);
  assert.equal(un.text, P.TRUST_UNAVAILABLE);
  read.report_ = () => { throw new Error('boom'); };
  assert.equal((await get(F.kid.account, R(F.kid.id))).status, 503, '客户端抛错当不可用');
  assert.equal((await get(F.kid.account, R(F.kid.id), bare)).text, P.TRUST_UNAVAILABLE);
  assert.equal((await get(O.guardian, R(F.kid.id), bare)).text, P.NOT_FOUND, '没配洞察服务时越权仍是 404');
});

test('报告：受众对不上、多出外层键、禁用推断、要人工复核、判断不可见 / 过期 / 状态对不上 / 模板不在白名单 / 不属于该学生——整份不出（null）并记原因', { skip: !HAS_DB }, async () => {
  const cases: [string, (who: Who) => ReadOutcome, string][] = [
    ['受众对不上', () => ok(report('guardian', [[C['est']!, 'established']])), 'invalid_shape'],
    // 句子形状合本次受众、只有 audience 字段标成另一受众：仍整份不出（受众核对本身，不靠置信度分档碰巧拒绝）
    ['只有 audience 字段对不上', (w) => ok(report(w.audience, [[C['est']!, 'established']], { audience: w.audience === 'student' ? 'guardian' : 'student' })),
     'invalid_shape'],
    ['result 多出 confidence', (w) => ok({ ...report(w.audience, [[C['est']!, 'established']]), confidence: 0.4 }), 'invalid_shape'],
    ['result 多出 evidence_ids', (w) => ok({ ...report(w.audience, [[C['est']!, 'established']]), evidence_ids: [E1] }), 'invalid_shape'],
    ['提示语命中禁用推断词表', (w) => ok(report(w.audience, [[C['est']!, 'established']], { notice: '（合成）孩子很聪明' })), 'forbidden_inference'],
    ['句子命中禁用推断词表', (w) => ok({ ...report(w.audience, [[C['est']!, 'established']]),
      sections: [{ key: 'node_mastery', connective: '甲', sentences: [{ ...report(w.audience, [[C['est']!, 'established']]).sections[0]!.sentences[0], text: '（合成）智商高' }] }] }),
     'forbidden_inference'],
    ['对当前受众不可见（只给 agent）', (w) => ok(report(w.audience, [[C['est']!, 'established'], [C['agent']!, 'established']])), 'claim_not_shown'],
    ['已过期', (w) => ok(report(w.audience, [[C['expired']!, 'established']])), 'claim_not_shown'],
    ['已推翻', (w) => ok(report(w.audience, [[C['refuted']!, 'established']])), 'claim_not_shown'],
    ['待验证混进报告（标成成立）', (w) => ok(report(w.audience, [[C['ver']!, 'established']])), 'claim_not_shown'],
    ['状态对不上（库里是假设）', (w) => ok(report(w.audience, [[C['hyp']!, 'established']])), 'claim_not_shown'],
    ['模板对不上', (w) => ok(report(w.audience, [[C['est']!, 'established', TPL_T]])), 'claim_not_shown'],
    ['模板不在对外白名单', (w) => ok(report(w.audience, [[C['hidden']!, 'established', TPL_HIDDEN]])), 'claim_not_shown'],
    ['不是知识节点的判断', (w) => ok(report(w.audience, [[C['exam']!, 'established']])), 'claim_not_shown'],
    ['兄妹的判断', (w) => ok(report(w.audience, [[C['est']!, 'established'], [C['sib']!, 'established']])), 'claim_not_shown'],
    ['另一户的判断', (w) => ok(report(w.audience, [[C['other']!, 'established']])), 'claim_not_shown'],
    ['迁移模板的句子放在掌握节', (w) => ok(report(w.audience, [[C['tra']!, 'established', TPL_T]])), 'section_template_mismatch'],
    ['掌握模板的句子放在迁移节', (w) => ok(report(w.audience, [[C['est']!, 'established']], {
      sections: [{ key: 'node_transfer', connective: '【占位衔接·乙】', sentences: report(w.audience, [[C['est']!, 'established']]).sections[0]!.sentences }] })),
     'section_template_mismatch'],
  ];
  for (const [what, fn, reason] of cases) {
    read.report_ = fn;
    const x = await get(F.kid.account, R(F.kid.id));
    assert.equal(x.status, 200, `${what}：${x.text}`);
    assert.deepEqual(x.body, { student_id: F.kid.id, report: null }, what);
    assert.equal(lastLog()['report'], reason, what);
  }
  // 家长看得到、学生看不到的判断：家长版能出，学生版不出
  read.report_ = (w) => ok(report(w.audience, [[C['est']!, 'established'], [C['studentOnly']!, 'established']]));
  assert.notEqual((await get(F.kid.account, R(F.kid.id))).body.report, null, '可见范围含 student');
  assert.equal((await get(F.guardian, R(F.kid.id))).body.report, null, '可见范围不含 guardian');
  // 信封要求人工复核：不出
  read.report_ = (w) => ok(report(w.audience, [[C['est']!, 'established']]), { source_version: 'algo:sie-report@0.1.0', human_review_required: true });
  const hr = await get(F.kid.account, R(F.kid.id));
  assert.equal(hr.body.report, null);
  assert.equal(lastLog()['human_review_required'], 'true');
  // 对照：两节、合格的报告能出
  read.report_ = (w) => ok({ ...report(w.audience, [[C['est']!, 'established'], [C['hyp']!, 'hypothesis']]),
    sections: [report(w.audience, [[C['est']!, 'established'], [C['hyp']!, 'hypothesis']]).sections[0],
               { key: 'node_transfer', connective: '【占位衔接·乙】', sentences: report(w.audience, [[C['tra']!, 'established', TPL_T]]).sections[0]!.sentences }],
    claim_ids: [C['est'], C['hyp'], C['tra']] });
  const good = await get(F.kid.account, R(F.kid.id));
  assert.equal(good.body.report.sections.length, 2, good.text);
});

test('解释：学生与家长读得到（待验证是虚线、不带文字）；学生不带置信度分档；只转发 result 的子集——证据链没有 evidence_id，没有信封外层键',{ skip: !HAS_DB }, async () => {
  read.explains.length = 0;
  read.explain_ = (w) => ok(explanation(w.claimId, w.claimId === C['ver'] ? 'to_verify' : w.claimId === C['hyp'] ? 'hypothesis' : 'established'));
  for (const [account, audience] of [[F.kid.account, 'student'], [F.guardian, 'guardian']] as const) {
    for (const c of ['est', 'hyp', 'ver']) {
      const x = await get(account, X(F.kid.id, C[c]!));
      assert.equal(x.status, 200, `${audience} ${c}：${x.text}`);
      // 按受众裁剪：学生没有置信度分档（顶层与陈述内）；家长两处都有
      assert.deepEqual(Object.keys(x.body).sort(), audience === 'student'
        ? ['audience', 'evidence_chain', 'expires_at', 'statement', 'student_id']
        : ['audience', 'confidence_band', 'evidence_chain', 'expires_at', 'statement', 'student_id'], `${audience} ${c}`);
      assert.equal(x.body.audience, audience);
      assert.equal(x.body.statement.claim_id, C[c]);
      assert.equal(x.body.statement.display, c === 'ver' ? 'dashed' : 'solid');
      // 待验证（虚线）没有陈述文字；假设与成立有
      const stKeys = ['claim_id', 'display', 'status', 'template_key', 'template_version',
                      ...(c === 'ver' ? [] : ['text']), ...(audience === 'guardian' ? ['confidence_band'] : [])].sort();
      assert.deepEqual(Object.keys(x.body.statement).sort(), stKeys, `${audience} ${c} 陈述的键`);
      if (c === 'ver') assert.ok(!x.text.includes('（合成）模板句'), '待验证的结论文字不出 BFF');
      if (audience === 'student') assert.ok(!x.text.includes('confidence_band') && !x.text.includes('moderate'), '学生解释里没有置信度分档');
      else assert.equal(x.body.confidence_band, 'moderate');
      assert.deepEqual(x.body.evidence_chain, [{ occurred_at: '2026-10-09T08:00:00.000Z', source_type: 'objective', evidence_level: 'A', node_id: 'MATH.SYN.R1' }]);
      for (const k of keysDeep(x.body)) assert.ok(!OUTER.includes(k), `${audience} 解释里不该有 ${k}`);
      assert.ok(!x.text.includes(E1), '证据 ID 不给前端');
    }
  }
  assert.equal(read.explains.length, 6);
  assert.equal(read.explains[0]!.claimId, C['est']);
  assert.equal(read.explains[3]!.audience, 'guardian');
});

test('解释：自己孩子的路径带兄妹或另一户的判断、对当前受众不可见、不在白名单、过期、已推翻、不存在——同一个 404，到不了洞察服务', { skip: !HAS_DB }, async () => {
  read.explains.length = 0;
  read.explain_ = (w) => ok(explanation(w.claimId, 'established'));
  const missing = await get(F.kid.account, X(F.kid.id, MISSING));
  assert.equal(missing.text, P.NOT_FOUND);
  for (const c of ['sib', 'other', 'agent', 'hidden', 'expired', 'refuted']) {
    same(await get(F.kid.account, X(F.kid.id, C[c]!)), missing, `学生 ${c}`);
    same(await get(F.guardian, X(F.kid.id, C[c]!)), missing, `家长 ${c}`);
  }
  same(await get(F.guardian, X(F.kid.id, C['studentOnly']!)), missing, '可见范围不含 guardian');
  assert.equal(read.explains.length, 0);
  assert.equal((await get(F.kid.account, X(F.kid.id, C['studentOnly']!))).status, 200, '可见范围含 student');
});

test('解释：洞察服务 404 → 404；不可用、陈述模板或状态对不上、形状不合、要人工复核、命中禁用推断 → 503；没配洞察服务 → 503', { skip: !HAS_DB }, async () => {
  const path = X(F.kid.id, C['est']!);
  read.explain_ = () => ({ kind: 'not_found' });
  assert.equal((await get(F.kid.account, path)).text, P.NOT_FOUND);
  const bad: [string, (w: ExplainWho) => ReadOutcome][] = [
    ['不可用', () => ({ kind: 'unavailable' })],
    ['状态对不上', (w) => ok(explanation(w.claimId, 'hypothesis'))],
    ['模板对不上', (w) => ok({ ...explanation(w.claimId, 'established'), statement: { ...explanation(w.claimId, 'established').statement, template_key: TPL_T } })],
    ['不是所问的判断', () => ok(explanation(C['hyp']!, 'hypothesis'))],
    ['多出 confidence', (w) => ok({ ...explanation(w.claimId, 'established'), confidence: 0.7 })],
    ['证据条目带原文', (w) => ok(explanation(w.claimId, 'established', { evidence_chain: [{ evidence_id: E1, occurred_at: '2026-10-09T08:00:00Z', source_type: 'objective', utterance: '（合成）' }] }))],
    ['要人工复核', (w) => ({ kind: 'ok', result: explanation(w.claimId, 'established'), envelope: { source_version: 'x', human_review_required: true } })],
    ['命中禁用推断', (w) => ok({ ...explanation(w.claimId, 'established'), statement: { ...explanation(w.claimId, 'established').statement, text: '（合成）天赋好' } })],
  ];
  for (const [what, fn] of bad) {
    read.explain_ = fn;
    const x = await get(F.kid.account, path);
    assert.equal(x.status, 503, `${what}：${x.text}`);
    assert.equal(x.text, P.TRUST_UNAVAILABLE, what);
  }
  assert.equal((await get(F.kid.account, path, bare)).text, P.TRUST_UNAVAILABLE);
});

test('报告与解释：按会话限流（trust_read.session.minute），超了是统一的 429', { skip: !HAS_DB }, async () => {
  const bucket = 'trust_read.session.minute';
  const saved = shell.config.rate_limits[bucket]!;
  read.report_ = () => ({ kind: 'not_found' });
  shell.config.rate_limits[bucket] = { window_seconds: 60, limit: 1 };
  try {
    const fresh = randomUUID();
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [fresh]);
    await link(fresh, F.kid.id);
    assert.equal((await get(fresh, R(F.kid.id))).status, 200);
    const x = await get(fresh, X(F.kid.id, C['est']!));
    assert.equal(x.status, 429);
    assert.equal(x.text, P.TOO_MANY);
  } finally {
    shell.config.rate_limits[bucket] = saved;
  }
});
