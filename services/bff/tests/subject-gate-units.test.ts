// 分学科并发闸门的纯函数与启动检查（PLT-16 第一步，specs/subject-concurrency-plt16/spec.md 范围第 1、5 条）：
// 并发上限、先到先得的有界队列、排队超时、队列满直接拒、每生每科上限（不进公共队列）、请求方断开即离队、名额在异常路径也释放且只释放一次、
// 已计量的重试绕过闸门、学科之间互不影响、配置的上下界与开关、非本地环境须声明单实例。
// 不连库；时钟与定时器用可手动推进的假实现（结果不依赖机器快慢）。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { subjectBusy as diagnosisBusy, withRetry as diagnosisRetry } from '../src/diagnosis/service.ts';
import {
  busyHeaders, CONCURRENCY_CONFIG_PATH, createSubjectGate, decideSubjectGate, GATE_SUBJECTS, loadConcurrencyConfig, parseSubjectGateFeature,
  queueBucket, withSubjectSlot, type ConcurrencyConfig, type GateClock, type GateResult, type SubjectGate, type SubjectLimits,
} from '../src/gate/subject-gate.ts';
import { P } from '../src/http/problems.ts';
import { SUBJECTS, subjectBusy as practiceBusy } from '../src/practice/service.ts';

/** 手动推进的时钟：advance(ms) 依次触发到期的定时器 */
function fakeClock(): GateClock & { advance: (ms: number) => void; pending: () => number } {
  let t = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    now: () => t,
    setTimer: (fn, ms) => { seq += 1; timers.set(seq, { at: t + ms, fn }); return seq; },
    clearTimer: (id) => { timers.delete(id as number); },
    advance: (ms) => {
      const end = t + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!due) break;
        timers.delete(due[0]);
        t = due[1].at;
        due[1].fn();
      }
      t = end;
    },
    pending: () => timers.size,
  };
}

const lim = (o: Partial<SubjectLimits> = {}): SubjectLimits =>
  ({ maxInflight: 2, maxQueue: 2, queueTimeoutMs: 100, retryAfterSec: 3, perStudentMax: 100, ...o });
const cfg = (aya: Partial<SubjectLimits> = {}, rest: Partial<SubjectLimits> = {}): ConcurrencyConfig =>
  ({ subjects: { aya: lim(aya), zhixing: lim(rest), wenli: lim(rest) } });
/** 每次一个新的合成学生（除非指定） */
const acq = (g: SubjectGate, subject = 'aya', student: string = randomUUID(), signal?: AbortSignal) =>
  g.acquire(subject, { student, ...(signal ? { signal } : {}) });
/** 让已经 resolve 的 promise 回调跑完 */
const tick = () => new Promise<void>((r) => setImmediate(r));
const ok = (g: GateResult): Extract<GateResult, { ok: true }> => { assert.ok(g.ok, JSON.stringify(g)); return g as Extract<GateResult, { ok: true }>; };

test('并发上限：前 max_inflight 个立即放行（admitted），之后的排队；占用数不超过上限', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 2, maxQueue: 5 }), fakeClock());
  const a = ok(await acq(gate));
  const b = ok(await acq(gate));
  assert.deepEqual([a.result, b.result, a.waitedMs], ['admitted', 'admitted', 0]);
  let third: GateResult | null = null;
  void acq(gate).then((g) => { third = g; });
  await tick();
  assert.equal(third, null, '第三个在排队，没有放行');
  assert.deepEqual([gate.stats().aya.inflight, gate.stats().aya.waiting], [2, 1]);
  a.release();
  await tick();
  assert.equal(ok(third!).result, 'queued');
  assert.deepEqual([gate.stats().aya.inflight, gate.stats().aya.waiting], [2, 0], '名额直接交给队首，占用数不变');
});

test('先到先得：释放一个名额只放行队首，按到达顺序', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 3 }), fakeClock());
  const first = ok(await acq(gate));
  const order: number[] = [];
  const held: Extract<GateResult, { ok: true }>[] = [];
  for (const i of [1, 2, 3]) void acq(gate).then((g) => { order.push(i); held.push(ok(g)); });
  await tick();
  assert.deepEqual(order, []);
  first.release();
  await tick();
  assert.deepEqual(order, [1], '一次释放只放行一个');
  held[0]!.release();
  await tick();
  held[1]!.release();
  await tick();
  assert.deepEqual(order, [1, 2, 3]);
  held[2]!.release();
  assert.deepEqual([gate.stats().aya.inflight, gate.stats().aya.waiting], [0, 0]);
});

test('队列满直接拒（full），带配置的 retry-after；max_queue = 0 时不排队', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 1, retryAfterSec: 7 }), fakeClock());
  ok(await acq(gate));
  void acq(gate);
  await tick();
  const r = await acq(gate);
  assert.deepEqual(r, { ok: false, reason: 'full', waitedMs: 0, retryAfterSec: 7 });
  assert.equal(gate.stats().aya.rejected_full, 1);
  const noQueue = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 0 }), fakeClock());
  ok(await acq(noQueue));
  assert.equal((await acq(noQueue)).ok, false);
  assert.equal(noQueue.stats().aya.waiting, 0);
});

test('排队超时：等满 queue_timeout_ms 仍没有名额按被拒（timeout），离开队列；超时之后的释放不会把名额交给它', async () => {
  const clock = fakeClock();
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 2, queueTimeoutMs: 100, retryAfterSec: 4 }), clock);
  const a = ok(await acq(gate));
  let late: GateResult | null = null;
  void acq(gate).then((g) => { late = g; });
  await tick();
  clock.advance(99);
  await tick();
  assert.equal(late, null, '没到时限还在排队');
  clock.advance(1);
  await tick();
  assert.deepEqual(late, { ok: false, reason: 'timeout', waitedMs: 100, retryAfterSec: 4 });
  assert.deepEqual([gate.stats().aya.waiting, gate.stats().aya.rejected_timeout], [0, 1]);
  a.release();
  assert.deepEqual([gate.stats().aya.inflight, gate.stats().aya.waiting], [0, 0], '超时离队的请求不占名额');
  const b = ok(await acq(gate));
  let got: GateResult | null = null;
  void acq(gate).then((g) => { got = g; });
  await tick();
  clock.advance(30);
  b.release();
  await tick();
  const g = ok(got!);
  assert.deepEqual([g.result, g.waitedMs], ['queued', 30]);
  assert.equal(clock.pending(), 0, '放行后排队定时器已取消');
  clock.advance(500);
  assert.equal(gate.stats().aya.rejected_timeout, 1);
  g.release();
});

test('每生每科上限：同一学生“在处理 + 排队”到 per_student_max 后直接拒（per_student），不进公共队列；别的学生照常；释放、超时离队后计数回落', async () => {
  const clock = fakeClock();
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 5, perStudentMax: 2, retryAfterSec: 6 }), clock);
  const s = randomUUID();
  const a = ok(await acq(gate, 'aya', s));
  let queued: GateResult | null = null;
  void acq(gate, 'aya', s).then((g) => { queued = g; });
  await tick();
  assert.equal(gate.stats().aya.waiting, 1, '本学生第二个在排队（排队也算在内）');
  const third = await acq(gate, 'aya', s);
  assert.deepEqual(third, { ok: false, reason: 'per_student', waitedMs: 0, retryAfterSec: 6 });
  assert.equal(gate.stats().aya.waiting, 1, '超额的不进公共队列');
  assert.equal(gate.stats().aya.rejected_per_student, 1);
  let other: GateResult | null = null;
  void acq(gate, 'aya', randomUUID()).then((g) => { other = g; });
  await tick();
  assert.equal(gate.stats().aya.waiting, 2, '别的学生照常排队');
  assert.equal(ok(await acq(gate, 'zhixing', s)).result, 'admitted', '同一学生在别的科不受影响');
  a.release();
  await tick();
  const q = ok(queued!);
  assert.equal(q.result, 'queued');
  clock.advance(100);  // 别的学生排队超时
  await tick();
  assert.equal(other!.ok, false);
  q.release();
  const again = ok(await acq(gate, 'aya', s));
  assert.equal(again.result, 'admitted', '释放后本学生又能入闸');
  again.release();
  // 排队超时离队后本学生的计数也回落
  const blocker = ok(await acq(gate));
  const t = randomUUID();
  void acq(gate, 'aya', t);
  void acq(gate, 'aya', t);
  await tick();
  assert.equal((await acq(gate, 'aya', t)).ok, false);
  clock.advance(100);
  await tick();
  blocker.release();
  assert.equal(ok(await acq(gate, 'aya', t)).result, 'admitted', '超时离队之后计数回落');
});

test('请求方断开：排队中的立即离队（aborted），计数回落；已断开的请求不排队', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 2, perStudentMax: 1 }), fakeClock());
  const held = ok(await acq(gate));
  const s = randomUUID();
  const ac = new AbortController();
  let r: GateResult | null = null;
  void acq(gate, 'aya', s, ac.signal).then((g) => { r = g; });
  await tick();
  assert.equal(gate.stats().aya.waiting, 1);
  ac.abort();
  await tick();
  assert.equal(r!.ok, false);
  assert.equal((r as unknown as { reason: string }).reason, 'aborted');
  assert.deepEqual([gate.stats().aya.waiting, gate.stats().aya.rejected_aborted], [0, 1]);
  held.release();
  assert.equal(gate.stats().aya.inflight, 0, '断开离队的请求不会拿到名额');
  assert.equal(ok(await acq(gate, 'aya', s)).result, 'admitted', '断开离队后本学生计数回落');
  const gone = new AbortController();
  gone.abort();
  assert.equal(((await acq(gate, 'aya', randomUUID(), gone.signal)) as { reason?: string }).reason, 'aborted');
});

test('名额只释放一次：重复调用 release 不多空出位置', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 0 }), fakeClock());
  const a = ok(await acq(gate));
  a.release();
  a.release();
  const b = ok(await acq(gate));
  assert.equal((await acq(gate)).ok, false, '重复释放之后仍只有一个名额');
  b.release();
  assert.equal(gate.stats().aya.inflight, 0);
});

test('学科之间互不影响：一科占满、排满，别的科照常立即放行', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 0 }, { maxInflight: 1, maxQueue: 0 }), fakeClock());
  ok(await acq(gate));
  assert.equal((await acq(gate)).ok, false);
  assert.equal(ok(await acq(gate, 'zhixing')).result, 'admitted');
  assert.equal(ok(await acq(gate, 'wenli')).result, 'admitted');
  const s = gate.stats();
  assert.deepEqual([s.aya.inflight, s.zhixing.inflight, s.wenli.inflight, s.aya.rejected_full, s.zhixing.rejected_full], [1, 1, 1, 1, 0]);
  await assert.rejects(acq(gate, 'english'), /not configured/, '不认识的学科码不放行');
  for (const v of Object.values(SUBJECTS)) assert.ok((GATE_SUBJECTS as readonly string[]).includes(v), `再练、复测的学科码 ${v} 都有闸门配置`);
  assert.ok(JSON.stringify(s).length > 0 && !JSON.stringify(s).includes('-'), 'stats() 只有计数，没有学生 ID');
});

test('withSubjectSlot：闸门为空（开关关）时直接执行、什么也不记；被拒时不执行；放行时成功、失败、抛错都释放', async () => {
  const notes: [string, string][] = [];
  const observe = (k: string, v: string) => { notes.push([k, v]); };
  const busy = (s: number) => `busy:${s}`;
  const req = { subject: 'aya', student: randomUUID(), observe };
  assert.equal(await withSubjectSlot(undefined, req, busy, async () => 'ran'), 'ran');
  assert.equal(notes.length, 0, '开关关时不记任何字段');

  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 0, retryAfterSec: 5 }), fakeClock());
  assert.equal(await withSubjectSlot(gate, req, busy, async () => 'ok'), 'ok');
  assert.deepEqual(notes, [['gate_subject', 'aya'], ['gate', 'admitted'], ['gate_queue_ms', '0']]);
  assert.equal(gate.stats().aya.inflight, 0, '成功后释放');
  await assert.rejects(withSubjectSlot(gate, req, busy, async () => { throw new Error('upstream'); }), /upstream/);
  assert.equal(gate.stats().aya.inflight, 0, '抛错也释放');
  assert.equal(await withSubjectSlot(gate, req, busy, async () => 'failure-reply'), 'failure-reply');
  assert.equal(gate.stats().aya.inflight, 0);

  const held = ok(await acq(gate));
  notes.length = 0;
  let ran = false;
  assert.equal(await withSubjectSlot(gate, req, busy, async () => { ran = true; return 'x'; }), 'busy:5');
  assert.equal(ran, false, '被拒时不执行（计量都在 fn 里，所以被拒的请求本次没有新增计量）');
  assert.deepEqual(notes, [['gate_subject', 'aya'], ['gate', 'rejected'], ['gate_queue_ms', '0'], ['gate_reason', 'full']]);
  // 已计量的重试绕过闸门：满了也执行，不占名额
  notes.length = 0;
  assert.equal(await withSubjectSlot(gate, { ...req, bypass: true }, busy, async () => 'replayed'), 'replayed');
  assert.deepEqual(notes, [['gate_subject', 'aya'], ['gate', 'bypassed']]);
  assert.equal(gate.stats().aya.inflight, 1, '绕过的请求不占名额');
  held.release();
  // 只记学科码与枚举值：不含任何 ID
  assert.ok(notes.every(([, v]) => /^[a-z0-9_]+$/.test(v)));
});

test('withSubjectSlot：入闸之后、执行之前请求方已断开 → 释放名额、不执行', async () => {
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 2 }), fakeClock());
  const held = ok(await acq(gate));
  const ac = new AbortController();
  const notes: string[] = [];
  let ran = false;
  const p = withSubjectSlot(gate, { subject: 'aya', student: randomUUID(), signal: ac.signal, observe: (k, v) => notes.push(`${k}=${v}`) },
                            (s) => `busy:${s}`, async () => { ran = true; return 'x'; });
  await tick();
  held.release();  // 名额交给它（queued）……
  ac.abort();      // ……但它拿到之前请求方已断开
  assert.match(await p, /^busy:/);
  assert.equal(ran, false);
  assert.ok(notes.includes('gate_reason=aborted'), notes.join(','));
  assert.equal(gate.stats().aya.inflight, 0, '断开后名额已释放');
});

test('withSubjectSlot：排队后放行记 queued 与分档；排队超时记 rejected / timeout', async () => {
  const clock = fakeClock();
  const gate = createSubjectGate(cfg({ maxInflight: 1, maxQueue: 2, queueTimeoutMs: 1000, retryAfterSec: 2 }), clock);
  const held = ok(await acq(gate));
  const notes: string[] = [];
  const req = { subject: 'aya', student: randomUUID(), observe: (k: string, v: string) => notes.push(`${k}=${v}`) };
  const p = withSubjectSlot(gate, req, (s) => `busy:${s}`, async () => 'done');
  await tick();
  clock.advance(250);
  held.release();
  assert.equal(await p, 'done');
  assert.deepEqual(notes, ['gate_subject=aya', 'gate=queued', 'gate_queue_ms=lt500']);
  const held2 = ok(await acq(gate));
  notes.length = 0;
  const q = withSubjectSlot(gate, req, (s) => `busy:${s}`, async () => 'never');
  await tick();
  clock.advance(1000);
  assert.equal(await q, 'busy:2');
  assert.deepEqual(notes, ['gate_subject=aya', 'gate=rejected', 'gate_queue_ms=lt2000', 'gate_reason=timeout']);
  held2.release();
});

test('被拒响应：诊断、再练（复测共用）的 SUBJECT_BUSY 与 problems.ts 逐字节相同，带 retry-after；排队毫秒分档', () => {
  const d = diagnosisBusy(3);
  assert.equal(JSON.stringify(d.body), P.SUBJECT_BUSY, '诊断的问题体（对象，路由里序列化）与 P.SUBJECT_BUSY 逐字节相同');
  assert.deepEqual([d.status, d.headers], [503, { 'retry-after': '3' }]);
  const p = practiceBusy(4);
  assert.deepEqual([p.status, p.body, p.headers], [503, P.SUBJECT_BUSY, { 'retry-after': '4' }]);
  assert.equal(JSON.parse(P.SUBJECT_BUSY).code, 'SUBJECT_BUSY');
  assert.deepEqual([0, 1, 99, 100, 499, 500, 1999, 2000, 60_000].map(queueBucket),
                   ['0', 'lt100', 'lt100', 'lt500', 'lt500', 'lt2000', 'lt2000', 'ge2000', 'ge2000']);
  assert.deepEqual(busyHeaders(3), { 'retry-after': '3' });
});

test('诊断学习服务调用的重试有整体总时限（与再练对齐）：结果一直未知时不会无限占着名额', async () => {
  let t = 0;
  let calls = 0;
  const notes: string[] = [];
  const out = await diagnosisRetry({ retryDelaysMs: [300, 1000, 2000], retryBudgetMs: 1000, monotonic: () => t,
                                     sleep: async (ms) => { t += ms; }, note: (e) => notes.push(e) },
                                   async () => { calls += 1; return { kind: 'unknown', reason: '503 X' }; });
  assert.equal(out.kind, 'unknown');
  assert.equal(calls, 2, '300 ms 后重试一次；再等 1000 ms 就超总时限，不再重试');
  assert.ok(notes.includes('learning_retry_budget_exhausted'));
  let n = 0;
  const unlimited = await diagnosisRetry({ retryDelaysMs: [1, 1], sleep: async () => {} }, async () => { n += 1; return { kind: 'unknown', reason: 'x' }; });
  assert.deepEqual([unlimited.kind, n], ['unknown', 3], '没到总时限照常重试完');
});

test('配置：仓库里的 config/concurrency.json 合格、三科齐全、有 _doc 且标“待人确认”、single_instance 缺省 false；上下界、缺科、多余学科与字段都拒绝', () => {
  const c = loadConcurrencyConfig();
  assert.deepEqual(Object.keys(c.subjects).sort(), [...GATE_SUBJECTS].sort());
  assert.equal(c.singleInstance, false);
  assert.ok(GATE_SUBJECTS.every((s) => c.subjects[s].perStudentMax === 2 && c.subjects[s].queueTimeoutMs <= 10_000));
  const raw = JSON.parse(readFileSync(CONCURRENCY_CONFIG_PATH, 'utf8')) as Record<string, any>;
  assert.match(String(raw['_doc']), /待人确认/);
  const dir = mkdtempSync(join(tmpdir(), 'starbuddy-gate-'));
  const good = { max_inflight: 4, max_queue: 8, queue_timeout_ms: 500, retry_after_s: 2, per_student_max: 2 };
  const write = (subjects: unknown, single: unknown = false): string => {
    const p = join(dir, `${randomBytes(4).toString('hex')}.json`);
    writeFileSync(p, JSON.stringify({ _doc: 'test', ...(single === 'omit' ? {} : { single_instance: single }), subjects }));
    return p;
  };
  const all = (o: Record<string, unknown> = {}) => ({ aya: { ...good, ...o }, zhixing: good, wenli: good });
  assert.equal(loadConcurrencyConfig(write(all())).subjects.aya.maxInflight, 4);
  assert.equal(loadConcurrencyConfig(write(all(), true)).singleInstance, true);
  for (const [bad, why] of [
    [all({ max_inflight: 0 }), /max_inflight/], [all({ max_inflight: 1001 }), /max_inflight/], [all({ max_queue: -1 }), /max_queue/],
    [all({ queue_timeout_ms: 5 }), /queue_timeout_ms/], [all({ queue_timeout_ms: 10_001 }), /queue_timeout_ms/], [all({ retry_after_s: 0 }), /retry_after_s/],
    [all({ per_student_max: 0 }), /per_student_max/], [all({ per_student_max: 101 }), /per_student_max/],
    [all({ per_student_max: undefined }), /per_student_max/],
    [all({ max_inflight: 2.5 }), /max_inflight/], [all({ extra: 1 }), /extra/], [{ aya: good, zhixing: good }, /wenli/],
    [{ ...all(), english: good }, /english/], [[], /subjects/],
  ] as [unknown, RegExp][]) {
    assert.throws(() => loadConcurrencyConfig(write(bad)), why);
  }
  assert.throws(() => loadConcurrencyConfig(write(all(), 'omit')), /single_instance/);
  assert.throws(() => loadConcurrencyConfig(write(all(), 'yes')), /single_instance/);
});

test('开关：只认 subject_gate；没设或空为关；不认识的取值拒绝。非本地环境须配置 single_instance: true 才建闸门（同 rest.singleInstance 先例）', () => {
  assert.equal(parseSubjectGateFeature('production', undefined), false);
  assert.equal(parseSubjectGateFeature('test', ' '), false);
  assert.equal(parseSubjectGateFeature('production', 'subject_gate'), true);
  assert.throws(() => parseSubjectGateFeature('test', '1'), /STARBUDDY_SUBJECT_GATE/);
  const base = { subjects: { aya: lim(), zhixing: lim(), wenli: lim() } };
  for (const env of ['production', 'pilot', 'staging'] as const) {
    const off = decideSubjectGate(env, { ...base, singleInstance: false });
    assert.deepEqual(off, { build: false, warning: { event: 'subject_gate_disabled', reason: 'in_process_state_requires_single_instance' } }, env);
    assert.equal(decideSubjectGate(env, base).build, false, `${env}：没写 single_instance 也不建`);
    assert.equal(decideSubjectGate(env, { ...base, singleInstance: true }).build, true, env);
  }
  for (const env of ['dev', 'test'] as const) {
    const d = decideSubjectGate(env, { ...base, singleInstance: false });
    assert.deepEqual(d, { build: true, warning: { event: 'subject_gate_in_process', reason: 'in_process_state_requires_single_instance' } }, env);
  }
});

// ---------------------------------------------------------------- 启动检查（子进程，连不上数据库才退出）

const MAIN = new URL('../src/main.ts', import.meta.url).pathname;
const hex = () => randomBytes(32).toString('hex');
function start(vars: Record<string, string>): { status: number | null; stderr: string } {
  const r = spawnSync(process.execPath, ['--no-warnings', MAIN], {
    env: { PATH: process.env['PATH'] ?? '', BFF_DATABASE_URL: 'postgresql://nobody@/none?host=/nonexistent', ...vars },
    encoding: 'utf8', timeout: 20_000,
  });
  return { status: r.status, stderr: r.stderr };
}

test('启动：开关关时不读闸门配置、没有告警；打开时告警进程内状态需单实例；配置不合格或取值不认识拒绝启动；换配置只在 dev / test', () => {
  const off = start({ STARBUDDY_ENV: 'test', BFF_CONCURRENCY_CONFIG: '/nonexistent/concurrency.json' });
  assert.doesNotMatch(off.stderr, /subject_gate_|concurrency/, '开关关时不读配置（坏路径也不影响启动检查）');
  const on = start({ STARBUDDY_ENV: 'test', STARBUDDY_SUBJECT_GATE: 'subject_gate' });
  assert.match(on.stderr, /"event":"subject_gate_in_process"/);
  assert.match(on.stderr, /in_process_state_requires_single_instance/);
  const bad = start({ STARBUDDY_ENV: 'test', STARBUDDY_SUBJECT_GATE: 'on' });
  assert.equal(bad.status, 2);
  assert.match(bad.stderr, /STARBUDDY_SUBJECT_GATE/);
  const missing = start({ STARBUDDY_ENV: 'test', STARBUDDY_SUBJECT_GATE: 'subject_gate', BFF_CONCURRENCY_CONFIG: '/nonexistent/concurrency.json' });
  assert.equal(missing.status, 2);
  assert.doesNotMatch(missing.stderr, /subject_gate_in_process/);
  for (const env of ['production', 'pilot', 'staging']) {
    const r = start({ STARBUDDY_ENV: env, STARBUDDY_SUBJECT_GATE: 'subject_gate', BFF_CONCURRENCY_CONFIG: '/tmp/x.json', BFF_LOG_HMAC_KEY: hex(),
                      BFF_AUTH_SECRET: hex(), BFF_TRUST_SECRET: hex(), BFF_SHELL_ORIGIN: 'https://shell.example.invalid' });
    assert.equal(r.status, 2, env);
    assert.match(r.stderr, /BFF_CONCURRENCY_CONFIG 只在 dev \/ test/, env);
  }
});
