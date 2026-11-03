// 分学科并发闸门的路由集成测试（PLT-16 第一步，specs/subject-concurrency-plt16/spec.md 范围第 2–5 条）：一次性 Postgres（真实会话、RLS、
// platform.meter_reserve / meter_practice 与 usage_reservation、practice_log）+ 本进程里的 BFF + 可暂停的假学习服务（诊断、再练、复测）。
// 覆盖：开关关时同样的并发不排队、不拒、日志没有闸门字段，响应与开关打开（未超限）时逐字节一致；
// 超限时 503 SUBJECT_BUSY 带 retry-after，且库里没有预留 / 计次行、学习服务没收到请求（诊断开始、再练作答、复测作答都在计量之前被拒）；
// 名额在上游失败（结果未知、确定失败、抛错）时被释放；各科独立；排队后放行与排队超时；访问日志只记学科码与闸门结果，不含学生 ID。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { DiagnosisLearning, DiagOutcome } from '../src/diagnosis/learning-client.ts';
import { createSubjectGate, type SubjectGate, type SubjectLimits } from '../src/gate/subject-gate.ts';
import { P } from '../src/http/problems.ts';
import { usageIdFor } from '../src/metering/uuid.ts';
import type { PracticeLearning, PracticeOutcome } from '../src/practice/learning-client.ts';
import type { RetestLearning } from '../src/retest/learning-client.ts';
import { createFamily, HAS_DB, reservationsByStudent, setupHarness, type Harness } from './harness.ts';
import { callAs, ORIGIN, startShell, type ShellHarness } from './shell-harness.ts';

type Out = PracticeOutcome<unknown>;
const ITEM = { format: 'single', stem: '（合成）1 + 1 = ?', options: [{ key: 'A', text: '1' }, { key: 'B', text: '2' }] };
const NODE = 'MATH.SYN.GATE.A';
const UNKNOWN: Out = { kind: 'unknown', reason: '503 UPSTREAM_UNAVAILABLE' };

/** 可暂停的一次调用：hold(op) 之后第一次 op 进入假学习服务时停住，entered 兑现；open() 放它继续 */
interface Latch { op: string; subject: string | null; entered: PromiseWithResolvers<void>; gate: PromiseWithResolvers<void> }

describe('分学科并发闸门（BFF 路由集成）', { skip: HAS_DB ? false : '没有 PGHOST' }, () => {
  let h: Harness;
  let on: ShellHarness;
  let off: ShellHarness;
  let gate: SubjectGate;
  const logsOn: Record<string, unknown>[] = [];
  const logsOff: Record<string, unknown>[] = [];
  const sent: { op: string; subject: string; student: string }[] = [];
  const latches: Latch[] = [];
  /** op 的结果覆盖（含 BFF 的重试在内一直生效，测试用完自己清掉） */
  const override = new Map<string, () => Out>();
  const clock = Date.now();

  const hold = (op: string, subject: string | null = null): Latch => {
    const l = { op, subject, entered: Promise.withResolvers<void>(), gate: Promise.withResolvers<void>() };
    latches.push(l);
    return l;
  };
  const maybeHold = async (op: string, subject: string): Promise<void> => {
    const i = latches.findIndex((l) => l.op === op && (l.subject === null || l.subject === subject));
    if (i < 0) return;
    const l = latches.splice(i, 1)[0]!;
    l.entered.resolve();
    await l.gate.promise;
  };
  const record = async (op: string, subject: string, student: string): Promise<Out | null> => {
    sent.push({ op, subject, student });
    await maybeHold(op, subject);
    const f = override.get(op);
    return f ? f() : null;
  };
  const ticketFor = (student: string, kind: string) =>
    `${kind}.${Buffer.from(JSON.stringify({ student, n: randomUUID() })).toString('base64url')}.c2ln`;

  const practice: PracticeLearning = {
    async queue(student, subject) {
      return (await record('practice.queue', subject, student)) ?? { kind: 'ok', status: 200, replayed: false, body: { subject, entries: [] } };
    },
    async next(student, subject, entry) {
      return (await record('practice.next', subject, student)) ?? { kind: 'ok', status: 200, replayed: false, body: {
        status: 'ready', entry_id: entry, round: 1, item_ref: 'aya:SYN-GATE-01', item: ITEM, ticket: ticketFor(student, 'pt1'),
        expires_at: new Date(clock + 3600_000).toISOString() } };
    },
    async answer(student, subject, body) {
      const o = await record('practice.answer', subject, student);
      if (o) return o;
      return { kind: 'ok', status: 200, replayed: false,
               body: { verdict: 'correct', entry: { entry_id: entryFor.get(`${student}:${subject}`), status: 'closed', round: 1, next_round_at: null },
                       echo: body.idempotency_key } };
    },
  };
  const retest: RetestLearning = {
    async queue(student, subject) {
      return (await record('retest.queue', subject, student)) ?? { kind: 'ok', status: 200, replayed: false, body: { subject, entries: [] } };
    },
    async next(student, subject, entry) {
      return (await record('retest.next', subject, student)) ?? { kind: 'ok', status: 200, replayed: false, body: {
        status: 'ready', entry_id: entry, kind: 'retest', item_ref: 'aya:SYN-GATE-02', item: ITEM, ticket: ticketFor(student, 'rt1'),
        expires_at: new Date(clock + 3600_000).toISOString() } };
    },
    async check(student, subject) {
      return (await record('retest.check', subject, student)) ?? { kind: 'ok', status: 200, replayed: false, body: { status: 'ok' } };
    },
    async answer(student, subject) {
      return (await record('retest.answer', subject, student)) ?? { kind: 'ok', status: 200, replayed: false,
        body: { verdict: 'correct', entry: { entry_id: entryFor.get(`${student}:${subject}`), status: 'done' } } };
    },
    async replay(student, subject) {
      return (await record('retest.replay', subject, student)) ?? { kind: 'failure', status: 404, problem: { code: 'REPLAY_NOT_FOUND' } };
    },
  };
  const diagUnknown = <T>(): DiagOutcome<T> => ({ kind: 'unknown', reason: '503 X' });
  const diagnosis: DiagnosisLearning = {
    async start(student, subject, usageId) {
      const o = await record('diagnosis.start', subject, student);
      if (o) return o as unknown as DiagOutcome<never>;
      return { kind: 'ok', status: 201, replayed: false, body: { session_id: randomUUID(), usage_id: usageId, status: 'active', created: true } };
    },
    async current(student, session) {
      await record('diagnosis.current', 'aya', student);
      return { kind: 'ok', status: 200, replayed: false, body: {
        session_id: session, status: 'active', state: 'synthetic', usage_id: randomUUID(), elapsed_sec: 0, time_budget_sec: 2100, item_count: 0,
        item_limit: 20, process_tasks: false,
        step: { seq: 1, kind: 'adaptive', subject: 'aya', item_id: 'SYN-GATE-03', item: ITEM as never, probe_recorded: true, step_elapsed_sec: 0 } } };
    },
    async probe(student) {
      await record('diagnosis.probe', 'aya', student);
      return diagUnknown();
    },
    async answer(student) {
      await record('diagnosis.answer', 'aya', student);
      return diagUnknown();
    },
    pause: async () => diagUnknown(),
    abandon: async () => diagUnknown(),
  };
  /** 每个（学生、学科）一个条目 ID（假学习服务的作答结果要对得上票据里的条目） */
  const entryFor = new Map<string, string>();
  const entry = (student: string, subject: string): string => {
    const k = `${student}:${subject}`;
    if (!entryFor.has(k)) entryFor.set(k, randomUUID());
    return entryFor.get(k)!;
  };

  const LIMITS: Record<'aya' | 'zhixing' | 'wenli', SubjectLimits> = {
    aya: { maxInflight: 1, maxQueue: 0, queueTimeoutMs: 1000, retryAfterSec: 7, perStudentMax: 2 },
    zhixing: { maxInflight: 1, maxQueue: 1, queueTimeoutMs: 1000, retryAfterSec: 3, perStudentMax: 2 },
    wenli: { maxInflight: 4, maxQueue: 4, queueTimeoutMs: 1000, retryAfterSec: 2, perStudentMax: 2 },
  };
  const S: { id: string; account: string }[] = [];
  const opts = (log: Record<string, unknown>[], withGate: boolean) => ({
    log: (l: Record<string, unknown>) => log.push(l), now: () => new Date(clock),
    practice: { enabled: true, learning: practice, retryDelaysMs: [1, 1] }, retest: { enabled: true, learning: retest, retryDelaysMs: [1, 1] },
    diagnosis: { learning: diagnosis, retryDelaysMs: [1, 1], sleep: async () => {} },
    ...(withGate ? { subjectGate: gate } : {}),
  });

  const practiceLog = async (student: string): Promise<number> =>
    (await h.su.query('SELECT count(*)::int AS n FROM platform.practice_log WHERE student_id = $1', [student])).rows[0].n;
  const nPath = (kind: 'practice' | 'retest', student: string, code: string) =>
    `/students/${student}/${kind}/${code}/entries/${entry(student, code === 'math' ? 'aya' : code === 'politics' ? 'zhixing' : 'wenli')}/next`;
  const aPath = (kind: 'practice' | 'retest', student: string, code: string) => `/students/${student}/${kind}/${code}/answer`;
  const DIAG = (student: string) => `/students/${student}/diagnosis/sessions`;

  async function ticket(shell: ShellHarness, kind: 'practice' | 'retest', s: { id: string; account: string }, code = 'math'): Promise<string> {
    const r = await callAs(shell.base, 'POST', nPath(kind, s.id, code), await shell.sessionFor(s.account));
    assert.equal(r.status, 200, r.text);
    return r.body.ticket as string;
  }
  const idle = () => {
    const st = gate.stats();
    assert.deepEqual([st.aya.inflight, st.aya.waiting, st.zhixing.inflight, st.zhixing.waiting, st.wenli.inflight, st.wenli.waiting],
                     [0, 0, 0, 0, 0, 0], `名额全部释放：${JSON.stringify(st)}`);
  };
  const busyOk = (r: { status: number; text: string; headers: Headers }, retryAfter: string, what: string) => {
    assert.equal(r.status, 503, `${what}：${r.text}`);
    assert.equal(r.text, P.SUBJECT_BUSY, `${what}：问题体与 problems.ts 逐字节相同`);
    assert.equal(JSON.parse(r.text).code, 'SUBJECT_BUSY', `${what}：与学习服务不可用（UPSTREAM_UNAVAILABLE）是两个问题码`);
    assert.equal(r.headers.get('retry-after'), retryAfter, what);
    assert.equal(r.headers.get('content-type'), 'application/problem+json');
  };

  before(async () => {
    h = await setupHarness();
    gate = createSubjectGate({ subjects: LIMITS });
    on = await startShell(h.bff, h.su, opts(logsOn, true));
    off = await startShell(h.bff, h.su, opts(logsOff, false));
    for (let i = 0; i < 3; i++) {
      const f = await createFamily(h, { source: 'pilot_virtual', children: 2 });
      S.push(...f.students);
    }
    // 会话逐个登录（同一家长并发登录会撞号码与验证码），两个服务各自一份
    for (const s of S) {
      await on.sessionFor(s.account);
      await off.sessionFor(s.account);
    }
  });

  after(async () => {
    for (const l of latches) l.gate.resolve();
    await on?.close();
    await off?.close();
    await h?.close();
  });

  test('开关关：同一科并发两个请求都照常处理（不排队、不拒）；访问日志没有闸门字段；固定响应与开关打开（未超限）时逐字节一致', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const l = hold('practice.next', 'aya');
    const first = callAs(off.base, 'POST', nPath('practice', a.id, 'math'), await off.sessionFor(a.account));
    await l.entered.promise;
    const second = await callAs(off.base, 'POST', nPath('practice', b.id, 'math'), await off.sessionFor(b.account));
    assert.equal(second.status, 200, '开关关时第二个请求不受第一个占用影响');
    l.gate.resolve();
    assert.equal((await first).status, 200);
    assert.ok(logsOff.length > 0 && logsOff.every((x) => Object.keys(x).every((k) => !k.startsWith('gate'))), '开关关时日志没有 gate_* 字段');
    // 确定的拒绝与固定响应：两个服务逐字节一致（开关打开且未超限时闸门不改变任何响应）
    const cases: [string, string, (s: { id: string }) => string, unknown][] = [
      ['POST', 'answer 票据伪造', (s) => aPath('practice', s.id, 'math'), { ticket: 'sbp1.' + 'A'.repeat(40), answer: { choice: ['A'] } }],
      ['POST', 'answer 请求体多键', (s) => aPath('practice', s.id, 'math'), { ticket: 'sbp1.x', answer: { choice: ['A'] }, usage_id: randomUUID() }],
      ['POST', '复测 answer 票据伪造', (s) => aPath('retest', s.id, 'math'), { ticket: 'sbr1.' + 'A'.repeat(40), answer: { choice: ['A'] } }],
      ['POST', '不认识的学科', (s) => `/students/${s.id}/practice/english/entries/${randomUUID()}/next`, undefined],
      ['POST', '诊断开始非阿鸦', (s) => DIAG(s.id), { subject: 'wenli', idempotency_key: randomUUID() }],
      ['POST', '诊断开始别人的路径', () => DIAG(S[2]!.id), { subject: 'aya', idempotency_key: randomUUID() }],
    ];
    for (const [m, what, path, body] of cases) {
      const x = await callAs(off.base, m, path(a), await off.sessionFor(a.account), body);
      const y = await callAs(on.base, m, path(a), await on.sessionFor(a.account), body);
      assert.deepEqual([y.status, y.text, y.headers.get('retry-after')], [x.status, x.text, null], what);
    }
    // 成功响应：同样的字段与响应头（票据是随机封装的，只比形状）
    const x = await callAs(off.base, 'POST', nPath('practice', a.id, 'math'), await off.sessionFor(a.account));
    const y = await callAs(on.base, 'POST', nPath('practice', a.id, 'math'), await on.sessionFor(a.account));
    assert.deepEqual([y.status, Object.keys(y.body).sort(), [...y.headers.keys()].filter((k) => k !== 'date').sort()],
                     [x.status, Object.keys(x.body).sort(), [...x.headers.keys()].filter((k) => k !== 'date').sort()]);
    idle();
  });

  test('诊断开始：超限 503 SUBJECT_BUSY 带 retry-after；被拒的请求没有预留、没到学习服务；占着的那个照常完成并释放名额', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const l = hold('diagnosis.start');
    const keyA = randomUUID();
    const first = callAs(on.base, 'POST', DIAG(a.id), await on.sessionFor(a.account), { subject: 'aya', idempotency_key: keyA });
    await l.entered.promise;
    const before = sent.length;
    const keyB = randomUUID();
    const r = await callAs(on.base, 'POST', DIAG(b.id), await on.sessionFor(b.account), { subject: 'aya', idempotency_key: keyB });
    busyOk(r, '7', '诊断开始');
    assert.equal(sent.length, before, '被拒的请求没到学习服务');
    assert.equal((await reservationsByStudent(h, b.id)).length, 0, '被拒的请求没有预留');
    const { rows } = await h.su.query('SELECT count(*)::int AS n FROM platform.usage_reservation WHERE usage_id = $1', [usageIdFor(keyB)]);
    assert.equal(rows[0].n, 0);
    // 同一科的再练也在这条线上（同一个学习服务学科码）
    busyOk(await callAs(on.base, 'POST', nPath('practice', b.id, 'math'), await on.sessionFor(b.account)), '7', '同科再练取题');
    busyOk(await callAs(on.base, 'POST', nPath('retest', b.id, 'math'), await on.sessionFor(b.account)), '7', '同科复测取题');
    // 别的科照常
    assert.equal((await callAs(on.base, 'POST', nPath('practice', b.id, 'politics'), await on.sessionFor(b.account))).status, 200, '各科独立');
    l.gate.resolve();
    const done = await first;
    assert.equal(done.status, 201, done.text);
    assert.equal((await reservationsByStudent(h, a.id)).length, 1, '占着名额的那个照常预留');
    idle();
  });

  test('诊断取当前题、探针、作答：同一科占满时 503 SUBJECT_BUSY、不到学习服务；暂停不经闸门；释放后照常', async () => {
    const [a, c] = [S[0]!, S[2]!];
    const { rows } = await h.su.query(
      `INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key)
       VALUES ($1, 'aya', 'gate-synthetic@1', '0.1.0', gen_random_uuid(), gen_random_uuid()) RETURNING id`, [c.id]);
    const sid = rows[0].id as string;
    const sc = await on.sessionFor(c.account);
    const l = hold('practice.next', 'aya');
    const first = callAs(on.base, 'POST', nPath('practice', a.id, 'math'), await on.sessionFor(a.account));
    await l.entered.promise;
    const before = sent.filter((s) => s.op.startsWith('diagnosis.')).length;
    busyOk(await callAs(on.base, 'GET', `${DIAG(c.id)}/${sid}`, sc), '7', '诊断取当前题');
    busyOk(await callAs(on.base, 'POST', `${DIAG(c.id)}/${sid}/answer`, sc,
                        { seq: 1, answer: { choice: ['B'] }, elapsed_sec: 5, idempotency_key: randomUUID() }), '7', '诊断作答');
    busyOk(await callAs(on.base, 'POST', `${DIAG(c.id)}/${sid}/probe`, sc,
                        { seq: 1, prediction: 'yes', asked_at: new Date().toISOString(), idempotency_key: randomUUID() }), '7', '诊断探针');
    assert.equal(sent.filter((s) => s.op.startsWith('diagnosis.')).length, before, '被拒的请求没到学习服务');
    const paused = await callAs(on.base, 'POST', `${DIAG(c.id)}/${sid}/pause`, sc, { idempotency_key: randomUUID() });
    assert.equal(paused.body.code, 'UPSTREAM_UNAVAILABLE', '暂停不经闸门（假学习服务回结果未知）');
    l.gate.resolve();
    assert.equal((await first).status, 200);
    const cur = await callAs(on.base, 'GET', `${DIAG(c.id)}/${sid}`, sc);
    assert.equal(cur.status, 200, cur.text);
    idle();
  });

  test('再练作答：宵禁之后、计次之前被拒——超限的作答不计次、不调学习服务；占着名额的作答已计次照常完成', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const ta = await ticket(on, 'practice', a);
    const tb = await ticket(on, 'practice', b);
    const before = { a: await practiceLog(a.id), b: await practiceLog(b.id) };
    const l = hold('practice.answer', 'aya');
    const first = callAs(on.base, 'POST', aPath('practice', a.id, 'math'), await on.sessionFor(a.account), { ticket: ta, answer: { choice: ['B'] } });
    await l.entered.promise;
    assert.equal(await practiceLog(a.id), before.a + 1, '占着名额的作答已计次（计次在名额内）');
    const calls = sent.filter((s) => s.op === 'practice.answer').length;
    const r = await callAs(on.base, 'POST', aPath('practice', b.id, 'math'), await on.sessionFor(b.account), { ticket: tb, answer: { choice: ['B'] } });
    busyOk(r, '7', '再练作答');
    assert.equal(await practiceLog(b.id), before.b, '被拒的作答没有计次');
    assert.equal(sent.filter((s) => s.op === 'practice.answer').length, calls, '被拒的作答没到学习服务');
    l.gate.resolve();
    assert.equal((await first).status, 200);
    idle();
    // 名额释放后，同一张票据原样重试成功（被拒时什么都没发生）
    const again = await callAs(on.base, 'POST', aPath('practice', b.id, 'math'), await on.sessionFor(b.account), { ticket: tb, answer: { choice: ['B'] } });
    assert.equal(again.status, 200, again.text);
    assert.equal(await practiceLog(b.id), before.b + 1);
    idle();
  });

  test('复测作答：只读预检与计次之前被拒——超限的作答不预检、不计次；复测与再练同一科共用名额', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const tb = await ticket(on, 'retest', b);
    const ta = await ticket(on, 'practice', a);
    const before = await practiceLog(b.id);
    const l = hold('practice.answer', 'aya');
    const first = callAs(on.base, 'POST', aPath('practice', a.id, 'math'), await on.sessionFor(a.account), { ticket: ta, answer: { choice: ['A'] } });
    await l.entered.promise;
    const checks = sent.filter((s) => s.op === 'retest.check').length;
    const r = await callAs(on.base, 'POST', aPath('retest', b.id, 'math'), await on.sessionFor(b.account), { ticket: tb, answer: { choice: ['A'] } });
    busyOk(r, '7', '复测作答');
    assert.equal(sent.filter((s) => s.op === 'retest.check').length, checks, '被拒的作答没有预检');
    assert.equal(await practiceLog(b.id), before, '被拒的作答没有计次');
    l.gate.resolve();
    assert.equal((await first).status, 200);
    const again = await callAs(on.base, 'POST', aPath('retest', b.id, 'math'), await on.sessionFor(b.account), { ticket: tb, answer: { choice: ['A'] } });
    assert.equal(again.status, 200, again.text);
    idle();
  });

  test('上游失败也释放名额：结果未知（重试后仍未知）、确定失败、学习服务客户端抛错', async () => {
    const a = S[0]!;
    override.set('practice.next', () => UNKNOWN);
    const u = await callAs(on.base, 'POST', nPath('practice', a.id, 'math'), await on.sessionFor(a.account));
    override.clear();
    assert.equal(u.status, 503);
    assert.equal(u.body.code, 'UPSTREAM_UNAVAILABLE', '学习服务不可用与闸门被拒是两个问题码');
    assert.equal(u.headers.get('retry-after'), null);
    idle();
    override.set('retest.next', () => ({ kind: 'failure', status: 409, problem: { code: 'RETEST_NOT_DUE' } }));
    const nd = await callAs(on.base, 'POST', nPath('retest', a.id, 'math'), await on.sessionFor(a.account));
    override.clear();
    assert.equal(nd.status, 409);
    idle();
    const t = await ticket(on, 'practice', a);
    override.set('practice.answer', () => { throw new Error('synthetic client crash'); });
    const crash = await callAs(on.base, 'POST', aPath('practice', a.id, 'math'), await on.sessionFor(a.account), { ticket: t, answer: { choice: ['A'] } });
    override.clear();
    assert.equal(crash.status, 500);
    idle();
    override.set('diagnosis.start', () => UNKNOWN);
    const d = await callAs(on.base, 'POST', DIAG(S[3]!.id), await on.sessionFor(S[3]!.account), { subject: 'aya', idempotency_key: randomUUID() });
    override.clear();
    assert.equal(d.status, 503);
    assert.equal(d.body.code, 'UPSTREAM_UNAVAILABLE');
    idle();
    // 释放之后照常放行
    assert.equal((await callAs(on.base, 'POST', nPath('practice', a.id, 'math'), await on.sessionFor(a.account))).status, 200);
    idle();
  });

  test('有界排队：名额占满时排队（先到先得），放行后记 queued；排队超时按被拒；队列满直接拒', async () => {
    const [a, b, c] = [S[0]!, S[1]!, S[2]!];
    const mark = logsOn.length;
    const l = hold('practice.next', 'zhixing');
    const first = callAs(on.base, 'POST', nPath('practice', a.id, 'politics'), await on.sessionFor(a.account));
    await l.entered.promise;
    const queued = callAs(on.base, 'POST', nPath('practice', b.id, 'politics'), await on.sessionFor(b.account));
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(gate.stats().zhixing.waiting, 1, '第二个在排队');
    // 队列（长度 1）已满：第三个直接拒
    busyOk(await callAs(on.base, 'POST', nPath('practice', c.id, 'politics'), await on.sessionFor(c.account)), '3', '队列满');
    l.gate.resolve();
    assert.equal((await first).status, 200);
    assert.equal((await queued).status, 200, '排队的在名额释放后放行');
    const mine = logsOn.slice(mark).filter((x) => x['gate_subject'] === 'zhixing');
    assert.deepEqual(mine.map((x) => x['gate']).sort(), ['admitted', 'queued', 'rejected']);
    assert.equal(mine.find((x) => x['gate'] === 'rejected')!['gate_reason'], 'full');
    assert.match(String(mine.find((x) => x['gate'] === 'queued')!['gate_queue_ms']), /^(lt100|lt500|lt2000)$/);
    idle();
    // 排队超时（1000 ms）：占着的一直不放，排队的按被拒
    const l2 = hold('practice.next', 'zhixing');
    const slow = callAs(on.base, 'POST', nPath('practice', a.id, 'politics'), await on.sessionFor(a.account));
    await l2.entered.promise;
    const mark2 = logsOn.length;
    busyOk(await callAs(on.base, 'POST', nPath('practice', b.id, 'politics'), await on.sessionFor(b.account)), '3', '排队超时');
    const t = logsOn.slice(mark2).find((x) => x['gate_subject'] === 'zhixing')!;
    assert.deepEqual([t['gate'], t['gate_reason'], t['gate_queue_ms']], ['rejected', 'timeout', 'lt2000']);
    l2.gate.resolve();
    assert.equal((await slow).status, 200);
    idle();
  });

  test('访问日志：闸门只记学科码、结果、排队分档与被拒原因；任何字段都不含学生 ID 与账号', async () => {
    const gated = logsOn.filter((x) => x['gate_subject'] !== undefined);
    assert.ok(gated.length >= 5);
    for (const x of gated) {
      assert.ok(['aya', 'zhixing', 'wenli'].includes(String(x['gate_subject'])));
      assert.ok(['admitted', 'queued', 'rejected', 'bypassed'].includes(String(x['gate'])));
      if (x['gate'] === 'bypassed') assert.equal(x['gate_queue_ms'], undefined, '绕过闸门的不排队');
      else assert.match(String(x['gate_queue_ms']), /^(0|lt100|lt500|lt2000|ge2000)$/);
      if (x['gate'] === 'rejected') assert.ok(['full', 'timeout', 'per_student', 'aborted'].includes(String(x['gate_reason'])));
      else assert.equal(x['gate_reason'], undefined);
    }
    const text = JSON.stringify(logsOn);
    for (const s of S) {
      assert.ok(!text.includes(s.id), '日志不含学生 ID 原文');
      assert.ok(!text.includes(s.account), '日志不含账号');
    }
    // 列表、暂停不经闸门
    const a = S[0]!;
    const mark = logsOn.length;
    assert.equal((await callAs(on.base, 'GET', `/students/${a.id}/practice/math`, await on.sessionFor(a.account))).status, 200);
    assert.equal(logsOn.slice(mark).some((x) => x['gate_subject'] !== undefined), false, '列表不经闸门');
    const st = gate.stats();
    assert.ok(st.aya.rejected_full >= 4 && st.zhixing.queued >= 1 && st.zhixing.rejected_timeout >= 1 && st.zhixing.rejected_full >= 1,
              `按学科累加计数：${JSON.stringify(st)}`);
    assert.ok(!JSON.stringify(st).includes(S[0]!.id), 'stats() 不含学生 ID');
  });

  test('每生每科上限：同一学生在一科“在处理 + 排队”到上限后 503 SUBJECT_BUSY，不进公共队列；别的学生照常', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const l1 = hold('practice.next', 'wenli');
    const l2 = hold('practice.next', 'wenli');
    const first = callAs(on.base, 'POST', nPath('practice', a.id, 'chinese'), await on.sessionFor(a.account));
    await l1.entered.promise;
    const second = callAs(on.base, 'POST', nPath('practice', a.id, 'chinese'), await on.sessionFor(a.account));
    await l2.entered.promise;
    const mark = logsOn.length;
    busyOk(await callAs(on.base, 'POST', nPath('practice', a.id, 'chinese'), await on.sessionFor(a.account)), '2', '本学生第三个');
    assert.equal(logsOn.slice(mark).find((x) => x['gate_subject'] === 'wenli')!['gate_reason'], 'per_student');
    assert.equal(gate.stats().wenli.waiting, 0, '超额的不进公共队列');
    assert.equal((await callAs(on.base, 'POST', nPath('practice', b.id, 'chinese'), await on.sessionFor(b.account))).status, 200, '别的学生照常');
    l1.gate.resolve();
    l2.gate.resolve();
    assert.deepEqual([(await first).status, (await second).status], [200, 200]);
    idle();
    assert.equal((await callAs(on.base, 'POST', nPath('practice', a.id, 'chinese'), await on.sessionFor(a.account))).status, 200, '释放后本学生照常');
    idle();
  });

  test('已计量的重试绕过闸门：再练同票据已计次、复测已计次、诊断开始同幂等键已预留——同一科占满时仍照常处理，不再计次、不另预留', async () => {
    const [a, b, d] = [S[0]!, S[1]!, S[4]!];
    // 先各做一次“已计量但结果未知”
    const tp = await ticket(on, 'practice', a);
    const tr = await ticket(on, 'retest', a);
    override.set('practice.answer', () => UNKNOWN);
    override.set('retest.answer', () => UNKNOWN);
    override.set('diagnosis.start', () => UNKNOWN);
    const before = await practiceLog(a.id);
    assert.equal((await callAs(on.base, 'POST', aPath('practice', a.id, 'math'), await on.sessionFor(a.account), { ticket: tp, answer: { choice: ['A'] } })).status, 503);
    assert.equal((await callAs(on.base, 'POST', aPath('retest', a.id, 'math'), await on.sessionFor(a.account), { ticket: tr, answer: { choice: ['A'] } })).status, 503);
    const key = randomUUID();
    assert.equal((await callAs(on.base, 'POST', DIAG(d.id), await on.sessionFor(d.account), { subject: 'aya', idempotency_key: key })).status, 503);
    override.clear();
    assert.equal(await practiceLog(a.id), before + 2, '两次作答都已计次');
    assert.equal((await reservationsByStudent(h, d.id)).length, 1, '诊断开始已预留');
    idle();
    // 占满数学这一科
    const l = hold('practice.next', 'aya');
    const blocker = callAs(on.base, 'POST', nPath('practice', b.id, 'math'), await on.sessionFor(b.account));
    await l.entered.promise;
    const mark = logsOn.length;
    const rp = await callAs(on.base, 'POST', aPath('practice', a.id, 'math'), await on.sessionFor(a.account), { ticket: tp, answer: { choice: ['A'] } });
    assert.equal(rp.status, 200, `再练同票据重试不被闸门拒：${rp.text}`);
    const rr = await callAs(on.base, 'POST', aPath('retest', a.id, 'math'), await on.sessionFor(a.account), { ticket: tr, answer: { choice: ['A'] } });
    assert.equal(rr.status, 200, `复测已计次重试不被闸门拒：${rr.text}`);
    const rd = await callAs(on.base, 'POST', DIAG(d.id), await on.sessionFor(d.account), { subject: 'aya', idempotency_key: key });
    assert.ok(rd.status === 200 || rd.status === 201, `诊断开始同幂等键重放不被闸门拒：${rd.text}`);
    assert.equal(await practiceLog(a.id), before + 2, '重试不再计次');
    assert.equal((await reservationsByStudent(h, d.id)).length, 1, '重放不另预留');
    const mine = logsOn.slice(mark).filter((x) => x['gate_subject'] === 'aya');
    assert.deepEqual(mine.map((x) => x['gate']), ['bypassed', 'bypassed', 'bypassed']);
    // 对照：新的作答（没计过次）仍被拒
    const fresh = await ticket(on, 'practice', S[2]!).catch(() => null);
    assert.equal(fresh, null, '数学占满时新取题也被拒（ticket 取不到）');
    l.gate.resolve();
    assert.equal((await blocker).status, 200);
    idle();
  });

  test('请求方断开：排队中的请求立即离队，不占名额、不到学习服务', async () => {
    const [a, b] = [S[0]!, S[1]!];
    const l = hold('practice.next', 'zhixing');
    const first = callAs(on.base, 'POST', nPath('practice', a.id, 'politics'), await on.sessionFor(a.account));
    await l.entered.promise;
    const sb = await on.sessionFor(b.account);
    const ac = new AbortController();
    const calls = sent.length;
    const gone = fetch(`${on.base}/bff/v0${nPath('practice', b.id, 'politics')}`, {
      method: 'POST', signal: ac.signal, headers: { 'x-sb-session': sb.token, 'x-csrf-token': sb.csrf, origin: ORIGIN } }).catch(() => 'aborted');
    for (let i = 0; i < 50 && gate.stats().zhixing.waiting === 0; i++) await new Promise((r) => setTimeout(r, 10));
    assert.equal(gate.stats().zhixing.waiting, 1, '第二个在排队');
    ac.abort();
    assert.equal(await gone, 'aborted');
    for (let i = 0; i < 50 && gate.stats().zhixing.waiting === 1; i++) await new Promise((r) => setTimeout(r, 10));
    assert.equal(gate.stats().zhixing.waiting, 0, '断开即离队（不等排队超时）');
    assert.ok(gate.stats().zhixing.rejected_aborted >= 1);
    l.gate.resolve();
    assert.equal((await first).status, 200);
    assert.equal(sent.length, calls + 0, '断开的请求没到学习服务（占着的那个在断开前已经计入）');
    idle();
  });
});
