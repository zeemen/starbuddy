// H5 星图 v1 集成测试（specs/star-map-v1-h5/spec.md 验收；统一壳 spec 第 400 行“星图 7.2”与“陈述—判断一致性”的星图部分）。
// 一次性 Postgres（db/tests/pg-ephemeral.sh），BFF 登录角色只继承 starbuddy_bff，真实会话、真实 RLS（can_read_student、claim_front_read）。
// 学习服务 star-map/nodes 与洞察服务 insight.explain 用计数的假实现：假学习服务按库里当前已发布数学地图的全部知识节点作答
// （与真实接口同一口径：active、level = node），本文件的合成节点给定星态与依据，其余节点 unseen。
// 夹具（全部合成，规则 8）：
//   LIT  亮（transfer）且有匹配的知识掌握判断 → 画亮并带 claim_id        RET  保持（retain）且有匹配判断 → 亮 + 外圈
//   NOC  亮但没有判断 → 中性                                           STL  判断的 basis_id 是旧记录 → 中性
//   VER  待验证判断（依据匹配）→ 虚线开关关中性、开虚线 + 下一次验证日期   TRO  只有迁移判断 → 中性（迁移只进摘要位）
//   UNL  模板在“知识掌握”表里但不在对外白名单 → 中性                    STU / GRD  只对学生 / 只对家长可见的判断
//   MTH  方法节点（不参与掌握计算，unseen）→ 中性
// 依赖 --test-concurrency=1（make bff-test 与 tests/mutations.sh 都这样跑）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { after, before, test } from 'node:test';
import { lexicon } from '../src/trust/lexicon.ts';
import { P } from '../src/http/problems.ts';
import { loadShellConfig, type ShellConfig } from '../src/shell/config.ts';
import type { ExplainRequest, InsightClient, RenderedStatement } from '../src/shell/insight-client.ts';
import type { PlanClient } from '../src/shell/plan-client.ts';
import type { StarMapNodesClient, StarMapSubject, StarNode, StarNodesOutcome } from '../src/star-map/nodes-client.ts';
import { createFamily, HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type Sess, type ShellHarness } from './shell-harness.ts';

const M = 'syn.math.node_mastery';
const T = 'syn.math.node_transfer';
const X = 'syn.math.not_listed';
const cfg: ShellConfig = (() => {
  const base = loadShellConfig();
  return { ...base, externalTemplates: new Set([`${M}@1`, `${T}@1`]),
           // X 登记在“知识掌握”表里却不在对外白名单：只靠白名单检查挡住（变异“白名单不检查”）
           starMap: { timeoutMs: 3000, masteryTemplates: new Set([`${M}@1`, `${X}@1`]), transferTemplates: new Set([`${T}@1`]) },
           explain: { ...base.explain, timeoutMs: 500, pageDeadlineMs: 1000 } };
})();

const D = 'MATH.SMV';
const T1 = `${D}.T1`;
const T2 = `${D}.T2`;
const N = { LIT: `${T1}.LIT`, RET: `${T1}.RET`, NOC: `${T1}.NOC`, STL: `${T1}.STL`, VER: `${T2}.VER`, TRO: `${T2}.TRO`, UNL: `${T2}.UNL`,
            STU: `${T2}.STU`, GRD: `${T2}.GRD`, MTH: `${T2}.MTH` };
const BASIS = Object.fromEntries(Object.keys(N).map((k) => [k, randomUUID()])) as Record<keyof typeof N, string>;
const STATE: Record<keyof typeof N, StarNode['state']> = { LIT: 'transfer', RET: 'retain', NOC: 'transfer', STL: 'transfer', VER: 'stable',
  TRO: 'transfer', UNL: 'transfer', STU: 'transfer', GRD: 'transfer', MTH: 'unseen' };
const OLD_BASIS = randomUUID();

let h: Harness;
let on: ShellHarness;      // starmap_v1
let dashed: ShellHarness;  // starmap_v1 + starmap_dashed
let off: ShellHarness;     // 开关全关（缺省）
const logs: Record<string, unknown>[] = [];
const A = { gF: '', gO: '', gR: '', S: '', aS: '', S2: '', aS2: '', T: '', aT: '' };
const C: Record<string, string> = {};  // 判断 ID
let mapVersion = '';

// ---------------------------------------------------------------- 假学习服务与洞察服务（计数）

// ---------------------------------------------------------------- 响应按 openapi/star-map.v1.yaml 的 schema 校验
// 不给 BFF 引入依赖：借用 adapters/aya 已锁版本的 ajv（2020-12）、ajv-formats 与 yaml（契约测试同一套）；没装时不校验并在输出里说明。
const AYA_PKG = new URL('../../../adapters/aya/package.json', import.meta.url);
const validateV1: ((v: unknown) => string | null) | null = (() => {
  if (!existsSync(new URL('../../../adapters/aya/node_modules/ajv/package.json', import.meta.url))) return null;
  const req = createRequire(AYA_PKG);
  const Ajv2020 = req('ajv/dist/2020').default;
  const addFormats = req('ajv-formats').default ?? req('ajv-formats');
  const YAML = req('yaml');
  const doc = YAML.parse(readFileSync(new URL('../openapi/star-map.v1.yaml', import.meta.url), 'utf8'));
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  ajv.addSchema({ ...doc, $id: 'star-map.v1' });
  const fn = ajv.compile({ $ref: 'star-map.v1#/components/schemas/StarMapV1' });
  return (v: unknown) => (fn(v) ? null : ajv.errorsText(fn.errors));
})();
if (!validateV1) console.log('# star-map-v1：adapters/aya 没装依赖，跳过响应 schema 校验');

type Mode = 'ok' | 'no_map' | 'mastery_unavailable' | 'version_mismatch' | 'extra_node' | 'missing_node';
let mode: Mode = 'ok';
const nodeCalls: { student: string; subject: string }[] = [];
const learning: StarMapNodesClient = {
  async nodes(student: string, subject: StarMapSubject): Promise<StarNodesOutcome> {
    nodeCalls.push({ student, subject });
    if (mode === 'no_map') return { kind: 'no_map' };
    if (mode === 'mastery_unavailable') return { kind: 'mastery_unavailable' };
    const { rows } = await h.su.query<{ id: string; version: string }>(
      `SELECT n.id, v.version FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
        WHERE v.subject = $1::platform.subject AND v.status = 'published' AND n.status = 'active' AND n.level = 'node' ORDER BY n.id`, [subject]);
    const mine = new Map(Object.entries(N).map(([k, id]) => [id, k as keyof typeof N]));
    // 只有 S 有掌握记录；别的学生（兄妹）全部 unseen
    const nodes = rows.map((r): StarNode => {
      const k = mine.get(r.id);
      if (!k || student !== A.S || STATE[k] === 'unseen') return { node_id: r.id, state: 'unseen', star_lit: false, basis_id: null };
      return { node_id: r.id, state: STATE[k], star_lit: STATE[k] === 'transfer' || STATE[k] === 'retain', basis_id: BASIS[k] };
    });
    if (mode === 'extra_node') nodes.push({ node_id: `${T1}.ZZZ`, state: 'unseen', star_lit: false, basis_id: null });
    const out = mode === 'missing_node' ? nodes.filter((n) => n.node_id !== N.NOC) : nodes;
    return { kind: 'ok', mapVersion: mode === 'version_mismatch' ? '9.9.9' : rows[0]!.version, nodes: out };
  },
};
const planCalls: string[] = [];
const plan: PlanClient = { async plan(s) { planCalls.push(s); return null; } };
const explained: string[] = [];
let explainDelayMs = 0;
/** 为 true 时假洞察服务返回命中禁用推断词表的句子（取词表自带的反例，不在这里另写） */
let forbiddenText = false;
const claimMeta = new Map<string, { key: string; status: RenderedStatement['status'] }>();
const insight: InsightClient = {
  async explain(req: ExplainRequest): Promise<RenderedStatement | null> {
    explained.push(req.claimId);
    if (explainDelayMs > 0) await new Promise((r) => setTimeout(r, explainDelayMs));
    const m = claimMeta.get(req.claimId);
    if (!m) return null;
    return { claim_id: req.claimId, template_key: m.key, template_version: 1, status: m.status, display: m.status === 'to_verify' ? 'dashed' : 'solid',
             text: forbiddenText ? lexicon().categories[0]!.forbiddenExamples[0]! : `（合成陈述 ${m.key.split('.').pop()}）`, confidence_band: 'moderate' };
  },
};

// ---------------------------------------------------------------- 合成数据

async function replica(sql: string, params: unknown[]): Promise<void> {
  const c = await h.su.connect();
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

async function claim(name: string, student: string, node: string, o: { key?: string; basis?: string; status?: string; visibility?: string[] } = {}) {
  const id = randomUUID();
  const key = o.key ?? M;
  const status = o.status ?? 'established';
  await replica(
    `INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                source_type, status, change_reason, visibility, expires_at, basis_kind, basis_id, model_version, evidence_ids, created_at, updated_at)
     VALUES ($1, $2, 'aya', 'node', $3, $4, 1, 0.7, 'inferred', $5::insight.claim_status, 'created', $6, now() + interval '30 days',
             'mastery_estimate', $7, 'algo:sie-claims@0.1.0', ARRAY[gen_random_uuid()], now(), now())`,
    [id, student, node, key, status, o.visibility ?? ['agent', 'student', 'guardian'], o.basis]);
  C[name] = id;
  claimMeta.set(id, { key, status: status as RenderedStatement['status'] });
}

const enroll = (student: string, subject: string, status = 'active') =>
  replica(`INSERT INTO platform.subject_enrollment (student_id, subject, status) VALUES ($1, $2::platform.subject, $3)
           ON CONFLICT (student_id, subject) DO UPDATE SET status = excluded.status`, [student, subject, status]);

before(async () => {
  if (!HAS_DB) return;
  h = await setupHarness();
  await h.su.query(`INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status, reviewed_by, reviewed_at)
                    SELECT k, 1, 'node', ARRAY['agent', 'student', 'guardian'], interval '60 days', '（合成模板）', 'approved', 'synthetic', now()
                      FROM unnest($1::text[]) k ON CONFLICT DO NOTHING`, [[M, T, X]]);
  // 已发布的合成数学地图（同一学科只能有一个已发布版本：库里已有就在它上面追加本文件的合成节点）
  let map = (await h.su.query<{ id: string; version: string }>(`SELECT id, version FROM kge.map_version WHERE subject = 'aya' AND status = 'published'`)).rows[0];
  if (!map) {
    map = { id: randomUUID(), version: '0.0.9' };
    await replica(`INSERT INTO kge.map_version (id, subject, standard_year, version, status, published_at) VALUES ($1, 'aya', 2022, $2, 'published', now())`,
                  [map.id, map.version]);
  }
  mapVersion = map.version;
  await replica(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES
                   ($1, $2, 'domain', NULL, NULL, '（合成）领域 SMV'), ($1, $3, 'topic', $2, NULL, '（合成）主题一'), ($1, $4, 'topic', $2, NULL, '（合成）主题二')
                 ON CONFLICT DO NOTHING`, [map.id, D, T1, T2]);
  for (const [k, id] of Object.entries(N)) {
    await replica(`INSERT INTO kge.knowledge_node (map_version_id, id, level, parent_id, kind, name) VALUES ($1, $2, 'node', $3, $4, $5) ON CONFLICT DO NOTHING`,
                  [map.id, id, id.split('.').slice(0, 3).join('.'), k === 'MTH' ? 'method' : 'kp', `（合成）节点 ${k}`]);
  }
  // 语文没有已发布地图（“地图建设中”）
  assert.equal((await h.su.query(`SELECT 1 FROM kge.map_version WHERE subject = 'wenli' AND status = 'published'`)).rowCount, 0);

  const F = await createFamily(h, { source: 'pilot_virtual', children: 2 });
  const O = await createFamily(h, { source: 'pilot_virtual' });
  Object.assign(A, { gF: F.guardian, S: F.students[0]!.id, aS: F.students[0]!.account, S2: F.students[1]!.id, aS2: F.students[1]!.account,
                     gO: O.guardian, T: O.students[0]!.id, aT: O.students[0]!.account });
  A.gR = randomUUID();
  await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian')`, [A.gR]);
  await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'invite')`, [A.gR, A.S]);
  // S：数学、语文、英语在读，道法已暂停；兄妹 S2 与另一户 T 也在读数学（越权读到它们时会调学习服务）
  await enroll(A.S, 'aya');
  await enroll(A.S, 'wenli');
  await enroll(A.S, 'echo');
  await enroll(A.S, 'zhixing', 'paused');
  await enroll(A.S2, 'aya');
  await enroll(A.T, 'aya');

  await claim('LIT', A.S, N.LIT, { basis: BASIS.LIT });
  await claim('RET', A.S, N.RET, { basis: BASIS.RET, status: 'hypothesis' });
  await claim('STL', A.S, N.STL, { basis: OLD_BASIS });
  await claim('VER', A.S, N.VER, { basis: BASIS.VER, status: 'to_verify' });
  await claim('TRO', A.S, N.TRO, { basis: BASIS.TRO, key: T });
  await claim('LIT_T', A.S, N.LIT, { basis: BASIS.LIT, key: T });  // 同一条已亮记录另挂迁移判断：只进摘要位
  await claim('UNL', A.S, N.UNL, { basis: BASIS.UNL, key: X });
  await claim('STU', A.S, N.STU, { basis: BASIS.STU, visibility: ['agent', 'student'] });
  await claim('GRD', A.S, N.GRD, { basis: BASIS.GRD, visibility: ['agent', 'guardian'] });
  await claim('T_LIT', A.T, N.LIT, { basis: BASIS.LIT });  // 另一户孩子的判断（依据与 S 的相同也不能串）
  // 待验证星的下一次验证任务（只给日期）
  await replica(`INSERT INTO platform.retest_task (student_id, subject, node_id, cognitive_level, reason, due_at)
                 VALUES ($1, 'aya', $2, 'apply', 'verify_in_platform', '2026-11-03T02:00:00Z'), ($1, 'aya', $2, 'understand', 'decay', '2026-11-05T02:00:00Z')`,
                [A.S, N.VER]);

  const deps = { log: (l: Record<string, unknown>) => logs.push(l), now: () => new Date('2026-10-20T00:00:00Z'), shell: { config: cfg, insight, plan } };
  on = await startShell(h.bff, h.su, { ...deps, starMap: { features: new Set(['starmap_v1']), nodes: learning } });
  dashed = await startShell(h.bff, h.su, { ...deps, starMap: { features: new Set(['starmap_v1', 'starmap_dashed']), nodes: learning } });
  off = await startShell(h.bff, h.su, deps);
  // 会话都在撤销绑定之前拿到
  for (const a of [A.gF, A.gO, A.gR, A.aS, A.aS2, A.aT]) await on.sessionFor(a);
  await h.su.query(`UPDATE platform.guardian_student_link SET status = 'revoked' WHERE guardian_account_id = $1`, [A.gR]);
});

after(async () => {
  if (!HAS_DB) return;
  await Promise.all([on.close(), dashed.close(), off.close()]);
  await h.close();
});

const sess = (a: string): Promise<Sess> => on.sessionFor(a);
const v1 = async (shell: ShellHarness, account: string | null, student = A.S) => {
  const r = await callAs(shell.base, 'GET', `/students/${student}/star-map/v1`, account ? await sess(account) : null);
  if (r.status === 200 && validateV1) assert.equal(validateV1(r.body), null, `响应不符合 star-map.v1.yaml：${r.text.slice(0, 200)}`);
  return r;
};
const starsOf = (body: any, subject = 'math'): Map<string, any> => {
  const sky = body.skies.find((s: any) => s.subject === subject);
  return new Map(sky.domains.flatMap((d: any) => d.topics.flatMap((t: any) => t.stars)).map((s: any) => [s.node_id, s]));
};
const NEUTRAL_KEYS = ['display', 'name', 'node_id'];
/** 响应里不得出现掌握数值、星态原值与依据 */
function noLeak(text: string, what: string) {
  for (const k of ['"state"', '"star_lit"', '"basis_id"', '"p_mastery"', '"confidence"', '"evidence_ids"', '"next_retest_at"']) {
    assert.ok(!text.includes(k), `${what}：响应里出现了 ${k}`);
  }
  for (const b of [...Object.values(BASIS), OLD_BASIS]) assert.ok(!text.includes(b), `${what}：basis_id 下发了`);
}

// ---------------------------------------------------------------- 7.2 节夹具

test('星图 v1 学生本人：亮且有匹配判断 → 画亮带 claim_id；保持层外圈；无判断、basis 不匹配、只有迁移判断、白名单外、家长可见的、方法节点 → 中性底星', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  const r = await v1(on, A.aS);
  assert.equal(r.status, 200, r.text);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  noLeak(r.text, '学生');
  const stars = starsOf(r.body);
  assert.deepEqual(stars.get(N.LIT), { node_id: N.LIT, name: '（合成）节点 LIT', display: 'lit', retained: false, claim_id: C['LIT'] });
  assert.deepEqual(stars.get(N.RET), { node_id: N.RET, name: '（合成）节点 RET', display: 'lit', retained: true, claim_id: C['RET'] });
  assert.deepEqual(stars.get(N.STU), { node_id: N.STU, name: '（合成）节点 STU', display: 'lit', retained: false, claim_id: C['STU'] });
  for (const k of ['NOC', 'STL', 'VER', 'TRO', 'UNL', 'GRD', 'MTH'] as const) {
    assert.deepEqual(Object.keys(stars.get(N[k])).sort(), NEUTRAL_KEYS, `${k} 中性星只有 node_id、name、display`);
    assert.equal(stars.get(N[k]).display, 'neutral', k);
  }
  // 中性底星的字段集合（全部星）：不出现 state、star_lit、basis_id、retained、claim_id
  for (const s of stars.values()) {
    if (s.display === 'neutral') assert.deepEqual(Object.keys(s).sort(), NEUTRAL_KEYS, s.node_id);
    else assert.deepEqual(Object.keys(s).sort(), ['claim_id', 'display', 'name', 'node_id', 'retained'], s.node_id);
  }
  // 星集合 = 库里当前已发布数学地图的全部知识节点（以学习服务返回为准），按领域、主题分组
  const { rows } = await h.su.query(`SELECT n.id FROM kge.knowledge_node n JOIN kge.map_version v ON v.id = n.map_version_id
    WHERE v.subject = 'aya' AND v.status = 'published' AND n.status = 'active' AND n.level = 'node' ORDER BY n.id`);
  assert.deepEqual([...stars.keys()].sort(), rows.map((x: { id: string }) => x.id));
  const math = r.body.skies.find((s: any) => s.subject === 'math');
  assert.equal(math.map_version, mapVersion);
  const smv = math.domains.find((d: any) => d.domain_id === D);
  assert.equal(smv.name, '（合成）领域 SMV');
  assert.deepEqual(smv.topics.map((t: any) => [t.topic_id, t.name, t.stars.map((s: any) => s.node_id)]), [
    [T1, '（合成）主题一', [N.LIT, N.NOC, N.RET, N.STL]],
    [T2, '（合成）主题二', [N.GRD, N.MTH, N.STU, N.TRO, N.UNL, N.VER]],
  ]);
  // 每颗非中性的星的 claim_id 都是该受众可见、成立或假设的判断
  for (const s of stars.values()) if (s.claim_id) assert.ok([C['LIT'], C['RET'], C['STU']].includes(s.claim_id), s.node_id);
});

test('星图 v1 范围：在读未发布 → 地图建设中；英语与暂停学科不出现；只为在读且有已发布地图的学科调学习服务；侧栏只有占位、无数据请求', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  const calls0 = nodeCalls.length;
  const plans0 = planCalls.length;
  const r = await v1(on, A.aS);
  assert.deepEqual(r.body.skies.map((s: any) => [s.subject, s.status]), [['math', 'ok'], ['chinese', 'building']]);
  assert.deepEqual(r.body.skies[1], { subject: 'chinese', status: 'building' });
  assert.ok(!/english|echo|politics|zhixing/.test(r.text), '英语、暂停的道法不出现');
  assert.deepEqual(nodeCalls.slice(calls0), [{ student: A.S, subject: 'aya' }], '只调数学一次（语文没有已发布地图不调，英语、暂停学科不调）');
  assert.deepEqual(r.body.skill_view, { status: 'coming_soon' });
  assert.equal(planCalls.length, plans0, '侧栏没有数据请求（不调计划等其他学习服务接口）');
  // 学习服务说 no_map（BFF 看到的版本刚被撤下）：同样“地图建设中”，不画星
  mode = 'no_map';
  const nm = await v1(on, A.aS);
  assert.deepEqual(nm.body.skies[0], { subject: 'math', status: 'building' });
  mode = 'ok';
});

test('星图 v1 星数说明与迁移摘要：只在有对应维度的判断时出现，文字只取 insight.explain；没有判断的学生不出现', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  explainDelayMs = 0;
  const ex0 = explained.length;
  const r = await v1(on, A.aS);
  const math = r.body.skies.find((s: any) => s.subject === 'math');
  assert.ok([C['LIT'], C['RET'], C['STU']].includes(math.summary.claim_id), '星数说明来自画了星的知识掌握判断');
  assert.match(math.summary.text, /node_mastery/);
  assert.equal(math.summary.confidence_band, 'moderate');
  assert.ok([C['TRO'], C['LIT_T']].includes(math.transfer_summary.claim_id), '迁移摘要来自依据对得上的知识迁移判断');
  assert.match(math.transfer_summary.text, /node_transfer/);
  const asked = explained.slice(ex0);
  assert.ok(asked.length >= 2 && asked.length <= cfg.explain.maxPerPage, `单页取陈述 ${asked.length} 次`);
  assert.ok(!asked.includes(C['STL']!) && !asked.includes(C['UNL']!) && !asked.includes(C['VER']!), '不匹配、白名单外、待验证的判断不取文字');
  // 兄妹 S2 本人：在读数学但没有任何判断 → 全部中性，没有星数说明与迁移摘要
  const s2 = await v1(on, A.aS2, A.S2);
  assert.equal(s2.status, 200);
  const sky = s2.body.skies.find((s: any) => s.subject === 'math');
  assert.ok(!('summary' in sky) && !('transfer_summary' in sky));
  assert.ok([...starsOf(s2.body).values()].every((s) => s.display === 'neutral'));
  noLeak(s2.text, '兄妹本人');
  // 洞察服务超时：星照画（判断在库里核过），文字不出现
  explainDelayMs = 1500;
  const slow = await v1(on, A.aS);
  explainDelayMs = 0;
  const slowSky = slow.body.skies.find((s: any) => s.subject === 'math');
  assert.ok(!('summary' in slowSky) && !('transfer_summary' in slowSky), '超时不显示文字');
  assert.equal(starsOf(slow.body).get(N.LIT).claim_id, C['LIT']);
  // 禁用推断词表（规则 12）：洞察服务给的句子命中词表 → 星数说明与迁移摘要都不出现，星照画
  forbiddenText = true;
  const bad = await v1(on, A.aS);
  forbiddenText = false;
  const badSky = bad.body.skies.find((s: any) => s.subject === 'math');
  assert.ok(!('summary' in badSky) && !('transfer_summary' in badSky), '命中禁用推断词表的句子不显示');
  assert.ok(!bad.text.includes(lexicon().categories[0]!.forbiddenExamples[0]!));
  assert.equal(starsOf(bad.body).get(N.LIT).claim_id, C['LIT']);
});

test('星图 v1 家长：只按 guardian 可见范围取判断；陈述带 confidence_band', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  const r = await v1(on, A.gF);
  assert.equal(r.status, 200, r.text);
  noLeak(r.text, '家长');
  const stars = starsOf(r.body);
  assert.equal(stars.get(N.GRD).claim_id, C['GRD'], '只对家长可见的判断画星');
  assert.deepEqual(Object.keys(stars.get(N.STU)).sort(), NEUTRAL_KEYS, '只对学生可见的判断对家长是中性');
  assert.equal(stars.get(N.LIT).claim_id, C['LIT']);
  const math = r.body.skies.find((s: any) => s.subject === 'math');
  assert.ok(math.summary && ['few_evidence', 'moderate', 'strong'].includes(math.summary.confidence_band), '家长端陈述带置信度说明');
  assert.ok([C['LIT'], C['RET'], C['GRD']].includes(math.summary.claim_id));
});

test('星图 v1 待验证：虚线开关关 → 中性；开 → 虚线 + claim_id + 下一次验证日期，不带结论文字', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  const ex0 = explained.length;
  const offR = await v1(on, A.aS);
  assert.deepEqual(Object.keys(starsOf(offR.body).get(N.VER)).sort(), NEUTRAL_KEYS);
  const r = await v1(dashed, A.aS);
  assert.equal(r.status, 200, r.text);
  noLeak(r.text, '虚线');
  assert.deepEqual(starsOf(r.body).get(N.VER), { node_id: N.VER, name: '（合成）节点 VER', display: 'dashed', claim_id: C['VER'], next_check: { due_on: '2026-11-03' } });
  assert.ok(!explained.slice(ex0).includes(C['VER']!), '虚线不取陈述文字');
  // 其余星与开关关时相同
  const a = starsOf(offR.body);
  const b = starsOf(r.body);
  for (const [id, s] of a) if (id !== N.VER) assert.deepEqual(b.get(id), s, id);
});

test('星图 v1 学习服务 409 MASTERY_UNAVAILABLE 或地图不一致：星全部中性，不下发任何星态、判断 ID 与说明', { skip: !HAS_DB }, async () => {
  for (const m of ['mastery_unavailable', 'version_mismatch', 'extra_node', 'missing_node'] as const) {
    mode = m;
    const r = await v1(dashed, A.aS);
    assert.equal(r.status, 200, `${m} ${r.text}`);
    noLeak(r.text, m);
    const math = r.body.skies.find((s: any) => s.subject === 'math');
    assert.equal(math.status, 'ok');
    assert.ok(!('summary' in math) && !('transfer_summary' in math), m);
    for (const s of starsOf(r.body).values()) assert.deepEqual(Object.keys(s).sort(), NEUTRAL_KEYS, `${m} ${s.node_id}`);
    assert.ok(!r.text.includes('claim_id'), m);
  }
  mode = 'ok';
});

// ---------------------------------------------------------------- 授权

test('星图 v1 授权：未登录 401；兄妹、另一户家长与孩子、撤销绑定、不存在一律同一个 404，且学习服务与洞察服务一次也没被调用', { skip: !HAS_DB }, async () => {
  mode = 'ok';
  const anon = await v1(on, null);
  assert.equal(anon.status, 401);
  assert.equal(anon.text, P.UNAUTHENTICATED);
  const calls0 = nodeCalls.length;
  const ex0 = explained.length;
  const missing = await v1(on, A.gF, '6e000000-0000-4000-8000-0000000000ff');
  assert.equal(missing.status, 404);
  assert.equal(missing.text, P.NOT_FOUND);
  for (const [who, account] of [['兄妹', A.aS2], ['另一户家长', A.gO], ['另一户孩子', A.aT], ['撤销绑定的家长', A.gR]] as const) {
    const r = await v1(on, account);
    assert.equal(r.status, 404, `${who} ${r.text}`);
    assert.equal(r.text, missing.text, who);
    assert.deepEqual(Object.fromEntries([...r.headers].filter(([k]) => k !== 'date')),
                     Object.fromEntries([...missing.headers].filter(([k]) => k !== 'date')), who);
  }
  assert.equal(nodeCalls.length, calls0, '越权请求没有到达学习服务');
  assert.equal(explained.length, ex0, '越权请求没有到达洞察服务');
  // 本人读：学习服务收到的学生只来自已校验的路径参数（查询参数、请求头里的别人一律不认）
  const own = await callAs(on.base, 'GET', `/students/${A.S}/star-map/v1?student_id=${A.T}&account_id=${A.gO}`, await sess(A.aS), undefined,
                           { headers: { 'x-student-id': A.T, 'x-account-id': A.gO } });
  assert.equal(own.status, 200);
  assert.deepEqual(nodeCalls.slice(calls0).map((c) => c.student), [A.S]);
  // 日志：student_id 只以 HMAC 记录；不记判断 ID、陈述文字、依据
  const text = JSON.stringify(logs);
  for (const s of [A.S, C['LIT']!, BASIS.LIT, '合成陈述']) assert.ok(!text.includes(s), `日志里出现了 ${s.slice(0, 8)}`);
});

// ---------------------------------------------------------------- 开关关闭

test('开关关闭（缺省）：v1 路由不注册，与表外路径同一个 404；v0 星图在开关开、关两种部署下逐字节相同', { skip: !HAS_DB }, async () => {
  const s = await sess(A.aS);
  const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', s);
  const r = await callAs(off.base, 'GET', `/students/${A.S}/star-map/v1`, s);
  assert.equal(r.status, 404);
  assert.equal(r.text, outside.text);
  const v0Off = await callAs(off.base, 'GET', `/students/${A.S}/star-map`, s);
  const v0On = await callAs(on.base, 'GET', `/students/${A.S}/star-map`, s);
  const v0Dashed = await callAs(dashed.base, 'GET', `/students/${A.S}/star-map`, s);
  assert.equal(v0Off.status, 200);
  assert.equal(v0On.text, v0Off.text);
  assert.equal(v0Dashed.text, v0Off.text);
  assert.deepEqual(Object.keys(v0Off.body).sort(), ['generated_at', 'skies', 'student_id'], 'v0 形状不变');
});
