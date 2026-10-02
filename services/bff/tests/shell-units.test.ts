// 统一壳 H4 的纯函数单测：学习服务计划响应只挑契约字段、洞察服务陈述形状核对、explain 请求体、连续使用提醒规则、
// 试用天数的上海自然日、显示规则配置的校验。
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { restEnabled, shanghaiDaysBetween } from '../src/http/shell-routes.ts';
import { loadShellConfig } from '../src/shell/config.ts';
import { explainBody, pickStatement } from '../src/shell/insight-client.ts';
import { pickSections } from '../src/shell/plan-client.ts';
import { MemoryRestStore, restState } from '../src/shell/rest.ts';

const task = { kind: 'retest', target_node_ids: ['MATH.SYN.T1.N1'], priority: 0.9, estimated_minutes: 10, rationale_code: 'retest_scheduled' };

test('计划响应：只挑契约字段；多给的 p_mastery、confidence、evidence_ids、learner_context 不进 BFF', () => {
  const body = { subjects: [
    { subject: 'aya', status: 'ok', learner_context: [{}], tasks: [{ ...task, p_mastery: 0.3, confidence: 0.9, evidence_ids: ['x'] }] },
    { subject: 'wenli', status: 'unavailable', tasks: [] },
  ] };
  const out = pickSections(body, ['aya', 'wenli', 'zhixing'])!;
  assert.deepEqual(out, [
    { subject: 'aya', status: 'ok', tasks: [task] },
    { subject: 'wenli', status: 'unavailable', tasks: [] },
    { subject: 'zhixing', status: 'unavailable', tasks: [] },  // 学习服务没回的学科按不可用
  ]);
  assert.ok(!JSON.stringify(out).includes('p_mastery'));
});

test('计划响应：任何一条不合契约，整科按不可用（不猜）', () => {
  for (const bad of [{ ...task, kind: 'homework' }, { ...task, priority: 2 }, { ...task, target_node_ids: ['aya:K-1'] },
                     { ...task, rationale_code: '该复习了' }, { ...task, estimated_minutes: 0 }]) {
    assert.deepEqual(pickSections({ subjects: [{ subject: 'aya', status: 'ok', tasks: [task, bad] }] }, ['aya']),
                     [{ subject: 'aya', status: 'unavailable', tasks: [] }], JSON.stringify(bad));
  }
  assert.equal(pickSections(null, ['aya']), null);
  assert.equal(pickSections({ subjects: 'x' }, ['aya']), null);
});

const stmt = { claim_id: '00000000-0000-4000-8000-00000000f001', template_key: 'syn.math.node_mastery', template_version: 1,
               status: 'established', display: 'solid', text: '（合成模板句）', confidence_band: 'moderate' };

test('洞察服务陈述：按 RenderedStatement 形状核对；待验证必须虚线；多余字段不带出', () => {
  assert.deepEqual(pickStatement({ ...stmt, extra: 'x' }), stmt);
  for (const bad of [{ ...stmt, claim_id: 'nope' }, { ...stmt, status: 'refuted' }, { ...stmt, status: 'to_verify' },
                     { ...stmt, display: 'dashed' }, { ...stmt, text: '' }, { ...stmt, text: 'x'.repeat(301) },
                     { ...stmt, confidence_band: 'high' }, { ...stmt, template_version: 0 }]) {
    assert.equal(pickStatement(bad), null, JSON.stringify(bad));
  }
});

test('explain 请求体：caller = bff、带 actor；家长带 link_id，学生不带', () => {
  const s = explainBody({ studentRef: 'S', audience: 'student', actorRef: 'A', claimId: 'C' }, 'R') as any;
  assert.deepEqual(s.context, { request_id: 'R', contract_version: '0.9.0', caller: 'bff', data_class: 'L2', student_ref: 'S', audience: 'student',
                                actor: { role: 'student', actor_ref: 'A' } });
  assert.equal(s.claim_id, 'C');
  const g = explainBody({ studentRef: 'S', audience: 'guardian', actorRef: 'G', linkId: 'L', claimId: 'C' }, 'R') as any;
  assert.deepEqual(g.context.actor, { role: 'guardian', actor_ref: 'G', link_id: 'L' });
});

test('连续使用提醒：满阈值才提醒；停够建议休息分钟数从头计；时钟倒退从头计', () => {
  const rule = { continuousMinutes: 40, breakMinutes: 10 };
  const M = 60_000;
  let mark = restState(undefined, 0, rule).mark;
  for (let m = 9; m <= 36; m += 9) mark = restState(mark, m * M, rule).mark;
  assert.equal(restState(mark, 39 * M, rule).view.show, false);
  assert.equal(restState(mark, 40 * M, rule).view.show, true);
  assert.equal(restState(mark, 46 * M, rule).view.continuous_minutes, 0);  // 距上次 10 分钟：休息过
  assert.equal(restState(mark, -1, rule).view.continuous_minutes, 0);
  const store = new MemoryRestStore(3600_000, 2);
  store.touch('a', 0, rule);
  store.touch('b', 0, rule);
  store.touch('c', 0, rule);  // 超过容量：丢最早的
  assert.equal(store.touch('a', 9 * M, rule).continuous_minutes, 0);
  assert.equal(store.reset('c', 5 * M, rule).continuous_minutes, 0);
});

test('试用剩余天数按上海自然日：跨 UTC 零点仍是同一天', () => {
  assert.equal(shanghaiDaysBetween('2026-10-01T02:00:00Z', '2026-10-15T02:00:00Z'), 14);
  assert.equal(shanghaiDaysBetween('2026-10-01T15:59:00Z', '2026-10-01T16:01:00Z'), 1);   // 上海 23:59 → 00:01
  assert.equal(shanghaiDaysBetween('2026-10-01T16:01:00Z', '2026-10-02T15:00:00Z'), 0);   // 同一个上海日
});

test('显示规则配置：仓库里的白名单为空（人登记前不显示任何判断陈述）；格式错误拒绝加载', () => {
  const c = loadShellConfig();
  assert.equal(c.externalTemplates.size, 0);
  assert.ok(c.contentRationale.has('high_exam_weight') && c.studentRationale.has('retest_scheduled'));
  assert.equal(c.rest.continuousMinutes, 40);
  const dir = tmpdir();
  const base = JSON.parse(JSON.stringify({ rationale_codes: { content: ['a'], about_student: ['b'] }, external_claim_templates: ['x.y@1'],
    rationale_templates: { b: ['x.y@1'] }, star_map: { timeout_ms: 3000, mastery_templates: ['x.y@1'], transfer_templates: [] },
    explain: { max_per_page: 10, timeout_ms: 1500, concurrency: 4, page_deadline_ms: 3000 }, plan: { timeout_ms: 8000 },
    rest_reminder: { continuous_minutes: 40, break_minutes: 10, single_instance: false }, curfew_near_minutes: 30,
    meter_defaults: { curfew_start: '22:00', curfew_end: '06:00', retained_daily_practice: 5 } }));
  const bad = (mut: (x: any) => void) => {
    const x = JSON.parse(JSON.stringify(base));
    mut(x);
    const p = join(dir, `shell-${process.pid}-${Math.random()}.json`);
    writeFileSync(p, JSON.stringify(x));
    return () => loadShellConfig(p);
  };
  assert.doesNotThrow(bad(() => undefined));
  assert.throws(bad((x) => { x.rationale_codes.about_student = ['a']; }));
  assert.throws(bad((x) => { x.external_claim_templates = ['math.node']; }));
  assert.throws(bad((x) => { x.rest_reminder.break_minutes = 50; }));
  assert.throws(bad((x) => { x.explain.max_per_page = 50; }));
  assert.throws(bad((x) => { x.rationale_templates = { a: ['x.y@1'] }; }), '内容类编码不能登记模板');
  assert.throws(bad((x) => { x.rationale_templates = { b: ['z.w@1'] }; }), '登记的模板必须在白名单里');
  assert.throws(bad((x) => { x.meter_defaults.curfew_start = '22'; }));
  assert.throws(bad((x) => { delete x.rest_reminder.single_instance; }));
  // H5 星图 v1：两维模板表必须是白名单子集、互不相交；仓库里两张表为空（画像可见范围，由人登记）
  assert.throws(bad((x) => { delete x.star_map; }));
  assert.throws(bad((x) => { x.star_map.mastery_templates = ['z.w@1']; }), '知识掌握模板必须在白名单里');
  assert.throws(bad((x) => { x.star_map.transfer_templates = ['x.y@1']; }), '同一模板不能两维都算');
  assert.throws(bad((x) => { x.star_map.transfer_templates = ['z.w@1']; }), '知识迁移模板必须在白名单里');
  assert.throws(bad((x) => { x.star_map.timeout_ms = 60_000; }));
  assert.equal(c.starMap.masteryTemplates.size + c.starMap.transferTemplates.size, 0);
  assert.equal(c.rationaleTemplates.size, 0, '仓库里的编码—模板映射为空（画像可见范围，由人登记）');
  assert.equal(c.explain.concurrency, 4);
  assert.equal(c.explain.pageDeadlineMs, 3000);
});

test('计量缺省值只在 config/shell.json 一处，并与迁移里写死的值一致（meter_guard_status 不返回，改函数属必停）', () => {
  const d = loadShellConfig().meterDefaults;
  const m15 = readFileSync(new URL('../../../db/migrations/0015_platform__metering_v2.up.sql', import.meta.url), 'utf8');
  const m30 = readFileSync(new URL('../../../db/migrations/0030_platform__meter_guard_status.up.sql', import.meta.url), 'utf8');
  assert.ok(m15.includes(`time '${d.curfewStart}', time '${d.curfewEnd}'`), '默认宵禁与 0015 set_time_guard 一致');
  assert.ok(m30.includes(`greatest(${d.retainedDailyPractice} - v_practice, 0)`), '每天自练次数与 0030 一致');
  // BFF 路由里不写死这些值
  const src = readFileSync(new URL('../src/http/shell-routes.ts', import.meta.url), 'utf8').replace(/\/\/.*$/gm, '');
  assert.ok(!/'22:00'|'06:00'|\b5 次/.test(src));
});

test('连续使用提醒的进程内计时：dev / test 启用；其余环境只有声明单实例部署才启用', () => {
  const rule = { continuousMinutes: 40, breakMinutes: 10 };
  assert.equal(restEnabled('test', { ...rule, singleInstance: false }), true);
  assert.equal(restEnabled('dev', { ...rule, singleInstance: false }), true);
  for (const env of ['staging', 'pilot', 'production'] as const) {
    assert.equal(restEnabled(env, { ...rule, singleInstance: false }), false, env);
    assert.equal(restEnabled(env, { ...rule, singleInstance: true }), true, env);
  }
});
