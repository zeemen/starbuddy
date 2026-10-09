// 复测与验证题学生端路由与计量的集成测试（specs/retest-delivery-kge05）：一次性 Postgres（真实会话、RLS、platform.is_self_student、
// platform.meter_practice、platform.meter_guard_status 与 practice_log）+ 本进程里的 BFF + 可编程的假学习服务（按 retest-internal.v0.yaml 的响应形状）。
// 覆盖：开关关 404、开关开但没配学习服务 503；只认学生本人（兄妹、另一户、不存在 404，家长会话 403，越权不到学习服务、不计次）；
// 响应白名单（不转掌握、置信度、证据 ID、错因、任务原因、空原因编码、题目引用、学习服务票据）；票据不透明、绑定学生、与再练票据不通用；
// 计量：每张票据计一次（重试、并发不再计次），usage_id 由票据里的幂等键派生并与同一个键转给学习服务；本地可判的拒绝不计次；
// 列表与取题不计次；留存态每天 5 次与错题再练合计；宵禁（取题、作答 402 curfew；列表不挡）；日志不记学生 ID 与作答。全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import { P } from '../src/http/problems.ts';
import { isUuidV4, usageIdFor } from '../src/metering/uuid.ts';
import type { RetestLearning, RetestOutcome } from '../src/retest/learning-client.ts';
import { EXPIRY_MARGIN_MS } from '../src/practice/service.ts';
import { createFamily, HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const ITEM_REF = 'aya:SYN-RT-ITEM-01';
const NODE = 'MATH.SYN.RT.A';
const TTL_MS = 60 * 60_000;
type Outcome = RetestOutcome<unknown>;
interface Sent { op: 'queue' | 'next' | 'answer' | 'check' | 'replay'; student: string; subject: string; body?: Record<string, unknown>; entry?: string }

const learningTicket = (student: string) =>
  `rt1.${Buffer.from(JSON.stringify({ student, subject: 'aya', item_id: ITEM_REF, node_id: NODE, anchor: 'retest:x' })).toString('base64url')}.c2ln`;

describe('复测与验证题（BFF 学生端路由与计量）', { skip: HAS_DB ? false : '没有 PGHOST' }, () => {
  let h: Harness;
  let shell: ShellHarness;
  let off: ShellHarness;
  let bare: ShellHarness;
  let clock = Date.now();
  const logs: Record<string, unknown>[] = [];
  const notes: string[] = [];
  const sent: Sent[] = [];
  const entryOf = new Map<string, string>();
  const results = new Map<string, { answer: string; result: unknown }>();
  let answerImpl: ((student: string, body: Record<string, unknown>) => Outcome) | null = null;
  let nextImpl: ((student: string, entry: string) => Outcome) | null = null;
  let checkImpl: ((student: string) => Outcome) | null = null;
  const F = { g: '', S: '', aS: '', S2: '', aS2: '' };
  const O = { g: '', T: '', aT: '' };

  const fake: RetestLearning = {
    async queue(student, subject) {
      sent.push({ op: 'queue', student, subject });
      return { kind: 'ok', status: 200, replayed: false, body: { subject, entries: [
        { entry_id: entryOf.get(student) ?? randomUUID(), node_id: NODE, cognitive_level: 'apply', kind: 'retest', status: 'due', empty_reason: null,
          reason: 'scheduled', p_mastery: 0.31, confidence: 0.7, evidence_ids: [randomUUID()], error_category: 'aya:sign' },
        { entry_id: randomUUID(), node_id: NODE, cognitive_level: 'apply', kind: 'verify', status: 'unavailable', empty_reason: 'no_new_family' }] } };
    },
    async next(student, subject, entry) {
      sent.push({ op: 'next', student, subject, entry });
      if (nextImpl) return nextImpl(student, entry);
      if (entryOf.get(student) !== entry) return { kind: 'failure', status: 404, problem: { code: 'ENTRY_NOT_FOUND', retryable: false } };
      return { kind: 'ok', status: 200, replayed: false, body: {
        status: 'ready', entry_id: entry, kind: 'retest', item_ref: ITEM_REF, reason: 'scheduled',
        item: { format: 'single', stem: '（合成）1 + 1 = ?', options: [{ key: 'A', text: '1' }, { key: 'B', text: '2' }] },
        ticket: learningTicket(student), expires_at: new Date(clock + TTL_MS).toISOString() } };
    },
    async answer(student, subject, body) {
      sent.push({ op: 'answer', student, subject, body: { ...body } });
      if (answerImpl) return answerImpl(student, body as unknown as Record<string, unknown>);
      const key = body.idempotency_key;
      const answer = JSON.stringify(body.answer);
      const seen = results.get(key);
      if (seen && seen.answer !== answer) return { kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_CONFLICT', retryable: false } };
      if (!seen) {
        results.set(key, { answer, result: { verdict: 'correct', entry: { entry_id: entryOf.get(student), status: 'done' },
                                             p_mastery: 0.8, confidence: 0.9, evidence_ids: [randomUUID()], purpose: 'retest', attempt_id: randomUUID() } });
      }
      return { kind: 'ok', status: 200, body: results.get(key)!.result, replayed: seen !== undefined };
    },
    async check(student, subject, ticket) {
      sent.push({ op: 'check', student, subject, body: { ticket } });
      return checkImpl ? checkImpl(student) : { kind: 'ok', status: 200, replayed: false, body: { status: 'ok' } };
    },
    async replay(student, subject, body) {
      sent.push({ op: 'replay', student, subject, body: { ...body } });
      const seen = results.get(body.idempotency_key);
      return seen ? { kind: 'ok', status: 200, body: seen.result, replayed: true }
        : { kind: 'failure', status: 404, problem: { code: 'REPLAY_NOT_FOUND', retryable: false } };
    },
  };

  const practiceLog = async (student?: string): Promise<number> => (await h.su.query(
    `SELECT count(*)::int AS n FROM platform.practice_log WHERE $1::uuid IS NULL OR student_id = $1::uuid`, [student ?? null])).rows[0].n;
  const sess = (account: string) => shell.sessionFor(account);
  const qPath = (student: string, subject = 'math') => `/students/${student}/retest/${subject}`;
  const nPath = (student: string, entry: string, subject = 'math') => `/students/${student}/retest/${subject}/entries/${entry}/next`;
  const aPath = (student: string, subject = 'math') => `/students/${student}/retest/${subject}/answer`;
  const ops = (op: Sent['op']) => sent.filter((s) => s.op === op).length;

  async function ticketFor(account: string, student: string, subject = 'math'): Promise<string> {
    const r = await callAs(shell.base, 'POST', nPath(student, entryOf.get(student)!, subject), await sess(account));
    assert.equal(r.status, 200, r.text);
    return r.body.ticket as string;
  }

  async function family(source: 'trial' | 'pilot_virtual', children = 1): Promise<{ g: string; kids: { id: string; account: string }[] }> {
    const f = await createFamily(h, { source, children });
    for (const s of f.students) entryOf.set(s.id, randomUUID());
    return { g: f.guardian, kids: f.students };
  }

  before(async () => {
    h = await setupHarness();
    const common = { log: (l: Record<string, unknown>) => logs.push(l), now: () => new Date(clock) };
    shell = await startShell(h.bff, h.su, { ...common, retest: { enabled: true, learning: fake, retryDelaysMs: [1, 1], note: (e) => notes.push(e) } });
    off = await startShell(h.bff, h.su, { ...common, practice: { enabled: true } });   // 再练开、复测关：复测路由不注册
    bare = await startShell(h.bff, h.su, { ...common, retest: { enabled: true } });
    const f = await family('pilot_virtual', 2);
    Object.assign(F, { g: f.g, S: f.kids[0]!.id, aS: f.kids[0]!.account, S2: f.kids[1]!.id, aS2: f.kids[1]!.account });
    const o = await family('pilot_virtual');
    Object.assign(O, { g: o.g, T: o.kids[0]!.id, aT: o.kids[0]!.account });
  });

  after(async () => {
    await shell?.close();
    await off?.close();
    await bare?.close();
    await h?.close();
  });

  test('开关关闭：三条路由不注册，与表外路径逐字节相同的 404；开关打开但没配学习服务：503 RETEST_UNAVAILABLE', async () => {
    const st = await off.sessionFor(F.aS);
    const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', st);
    for (const [m, p, b] of [['GET', qPath(F.S), undefined], ['POST', nPath(F.S, entryOf.get(F.S)!), undefined],
                             ['POST', aPath(F.S), { ticket: 'sbr1.AAAA', answer: { text: '1' } }]] as const) {
      const x = await callAs(off.base, m, p, st, b);
      assert.equal(x.status, 404, `${m} ${p}`);
      assert.equal(x.text, outside.text);
      const y = await callAs(bare.base, m, p, await bare.sessionFor(F.aS), b);
      assert.equal(y.status, 503, y.text);
      assert.equal(y.body.code, 'RETEST_UNAVAILABLE');
    }
    assert.equal(sent.length, 0);
  });

  test('列表：只认学生本人；只转白名单字段（不转任务原因、空原因编码、掌握、置信度、证据 ID、错因）；学科码换成学习服务的；不计次', async () => {
    const before = await practiceLog();
    const r = await callAs(shell.base, 'GET', qPath(F.S), await sess(F.aS));
    assert.equal(r.status, 200, r.text);
    assert.equal(r.body.subject, 'math');
    assert.deepEqual(r.body.entries.map((e: Record<string, unknown>) => Object.keys(e).sort()),
                     [['cognitive_level', 'entry_id', 'kind', 'node_id', 'status'], ['cognitive_level', 'entry_id', 'kind', 'node_id', 'status']]);
    assert.deepEqual(r.body.entries.map((e: Record<string, unknown>) => [e['kind'], e['status']]), [['retest', 'due'], ['verify', 'unavailable']]);
    for (const leak of ['p_mastery', 'confidence', 'evidence', 'error', 'reason', 'scheduled', 'no_new_family']) assert.ok(!r.text.includes(leak), leak);
    assert.deepEqual(sent.at(-1), { op: 'queue', student: F.S, subject: 'aya' });
    assert.equal(await practiceLog(), before);
    assert.equal((await callAs(shell.base, 'GET', qPath(F.S, 'english'), await sess(F.aS))).text, P.NOT_FOUND);
  });

  test('授权：兄妹、另一户学生、不存在的学生同一个 404；家长会话 403；越权请求不到学习服务、不计次', async () => {
    const before = { sent: sent.length, log: await practiceLog() };
    const missing = randomUUID();
    const t = await ticketFor(F.aS, F.S);
    const sentAfterTicket = sent.length;
    for (const [who, student] of [[F.aS2, F.S], [O.aT, F.S], [F.aS, missing], [F.aS, O.T]] as const) {
      for (const [m, p, b] of [['GET', qPath(student), undefined], ['POST', nPath(student, entryOf.get(F.S)!), undefined],
                               ['POST', aPath(student), { ticket: t, answer: { choice: ['B'] } }]] as const) {
        const x = await callAs(shell.base, m, p, await sess(who), b);
        assert.equal(x.text, P.NOT_FOUND, `${m} ${p}`);
      }
    }
    for (const [m, p, b] of [['GET', qPath(F.S), undefined], ['POST', aPath(F.S), { ticket: t, answer: { choice: ['B'] } }]] as const) {
      const x = await callAs(shell.base, m, p, await sess(F.g), b);
      assert.equal(x.status, 403, x.text);
    }
    assert.equal(sent.length, sentAfterTicket, '越权请求一次也到不了学习服务');
    assert.equal(sentAfterTicket, before.sent + 1);
    assert.equal(await practiceLog(), before.log, '越权请求不计次');
    assert.ok(!notes.includes('curfew_forbidden'), '越权请求停在本人核对（没有走到宵禁查询）');
  });

  test('取题：前端拿到题面、类别与不透明 sbr1 票据；不含题目引用、学生 ID、学习服务票据、任务原因；不可用只回 status；不计次', async () => {
    const before = await practiceLog();
    const r = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), await sess(F.aS));
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(Object.keys(r.body).sort(), ['entry_id', 'expires_at', 'item', 'kind', 'status', 'ticket']);
    assert.match(r.body.ticket, /^sbr1\.[A-Za-z0-9_-]+$/);
    for (const leak of [ITEM_REF, F.S, 'rt1.', 'scheduled', 'item_ref']) assert.ok(!r.text.includes(leak), leak);
    nextImpl = () => ({ kind: 'ok', status: 200, replayed: false, body: { status: 'unavailable', empty_reason: 'no_new_family' } });
    try {
      const u = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), await sess(F.aS));
      assert.deepEqual(u.body, { status: 'unavailable' });
      nextImpl = () => ({ kind: 'failure', status: 409, problem: { code: 'RETEST_NOT_DUE', retryable: false } });
      const d = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), await sess(F.aS));
      assert.deepEqual([d.status, d.body.code], [409, 'RETEST_NOT_DUE']);
      nextImpl = () => ({ kind: 'ok', status: 200, replayed: false, body: { status: 'ready', entry_id: entryOf.get(F.S), kind: 'transfer_check' } });
      assert.equal((await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), await sess(F.aS))).body.code, 'UPSTREAM_UNAVAILABLE');
    } finally {
      nextImpl = null;
    }
    assert.equal(await practiceLog(), before);
  });

  test('作答计次：每张票据计一次（题库自练），usage_id 由票据里的幂等键派生、与同一个键转给学习服务；同票据重放、并发都不再计次；响应只有判分结论与条目状态', async () => {
    const st = await sess(F.aS);
    const t = await ticketFor(F.aS, F.S);
    const before = await practiceLog(F.S);
    const r = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(r.body, { verdict: 'correct', entry: { entry_id: entryOf.get(F.S), status: 'done' } });
    assert.equal(r.headers.get('idempotent-replayed'), 'false');
    assert.equal(await practiceLog(F.S), before + 1);
    const last = sent.filter((s) => s.op === 'answer').at(-1)!;
    const key = String(last.body!['idempotency_key']);
    assert.ok(isUuidV4(key));
    assert.equal(last.body!['usage_id'], usageIdFor(key));
    assert.equal(last.body!['ticket'], learningTicket(F.S));
    assert.deepEqual(Object.keys(last.body!).sort(), ['answer', 'idempotency_key', 'ticket', 'usage_id']);
    const again = await Promise.all([1, 2].map(() => callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } })));
    for (const a of again) {
      assert.equal(a.status, 200, a.text);
      assert.equal(a.headers.get('idempotent-replayed'), 'true');
    }
    assert.equal(await practiceLog(F.S), before + 1, '同一张票据重试、并发都不再计次');
    const changed = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['A'] } });
    assert.deepEqual([changed.status, changed.body.code], [409, 'TICKET_USED']);
    assert.equal(await practiceLog(F.S), before + 1);
    assert.ok(notes.includes('retest_counted') && notes.includes('retest_count_existing'));
  });

  test('本地可判的拒绝不计次、不调学习服务：伪造票据、再练票据、兄妹的票据、换学科、作答形状不合题型、票据已过期', async () => {
    const st = await sess(F.aS);
    const t = await ticketFor(F.aS, F.S);
    const sibling = await ticketFor(F.aS2, F.S2);
    const before = { log: await practiceLog(), answers: ops('answer') };
    const cases: [Record<string, unknown>, number, string][] = [
      [{ ticket: `sbr1.${'A'.repeat(64)}`, answer: { choice: ['B'] } }, 422, 'TICKET_INVALID'],
      [{ ticket: `sbp1.${t.slice(5)}`, answer: { choice: ['B'] } }, 422, 'INVALID'],                 // 再练前缀：请求体就不合格
      [{ ticket: sibling, answer: { choice: ['B'] } }, 422, 'TICKET_INVALID'],
      [{ ticket: t, answer: { choice: ['A', 'B'] } }, 422, 'INVALID_ANSWER'],
      [{ ticket: t, answer: { text: '2' } }, 422, 'INVALID_ANSWER'],
      [{ ticket: t, answer: { choice: ['B'] }, purpose: 'transfer_check' }, 422, 'INVALID'],
    ];
    for (const [body, status, code] of cases) {
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, body);
      assert.deepEqual([x.status, x.body.code], [status, code], JSON.stringify(body).slice(0, 80));
    }
    const sub = await callAs(shell.base, 'POST', aPath(F.S, 'politics'), st, { ticket: t, answer: { choice: ['B'] } });
    assert.deepEqual([sub.status, sub.body.code], [422, 'TICKET_INVALID']);
    const saved = clock;
    clock = Date.now() + TTL_MS - EXPIRY_MARGIN_MS + 1000;
    try {
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
      assert.deepEqual([x.status, x.body.code], [410, 'TICKET_EXPIRED']);
    } finally {
      clock = saved;
    }
    assert.equal(await practiceLog(), before.log);
    assert.equal(ops('answer'), before.answers);
  });

  test('学习服务拒绝的映射：RETEST_NOT_DUE 409、403 → 422 TICKET_INVALID、410、501 → 503 RETEST_UNAVAILABLE；计次之后才知道，已计一次', async () => {
    const st = await sess(F.aS);
    for (const [out, status, code] of [
      [{ kind: 'failure', status: 409, problem: { code: 'RETEST_NOT_DUE' } }, 409, 'RETEST_NOT_DUE'],
      [{ kind: 'failure', status: 403, problem: { code: 'TICKET_INVALID' } }, 422, 'TICKET_INVALID'],
      [{ kind: 'failure', status: 410, problem: { code: 'TICKET_EXPIRED' } }, 410, 'TICKET_EXPIRED'],
      [{ kind: 'unknown', reason: '501 RETEST_UNAVAILABLE' }, 503, 'RETEST_UNAVAILABLE'],
      [{ kind: 'unknown', reason: '503 BUSY' }, 503, 'UPSTREAM_UNAVAILABLE'],
    ] as [Outcome, number, string][]) {
      const t = await ticketFor(F.aS, F.S);
      const before = await practiceLog(F.S);
      answerImpl = () => out;
      try {
        const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
        assert.deepEqual([x.status, x.body.code], [status, code], x.text);
      } finally {
        answerImpl = null;
      }
      assert.equal(await practiceLog(F.S), before + 1);
    }
  });

  test('宵禁：取题、作答 402 curfew，不计次、不调学习服务；列表不挡；白天恢复', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);
    const day = h.clockMs();
    const before = { log: await practiceLog(), answers: ops('answer'), next: ops('next') };
    try {
      await h.setClock(Date.parse('2026-10-15T15:30:00Z'));  // 北京时间 23:30：缺省宵禁内
      const a = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
      assert.deepEqual([a.status, a.body.code, a.body.reason], [402, 'QUOTA_UNAVAILABLE', 'curfew']);
      const n = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), st);
      assert.deepEqual([n.status, n.body.reason], [402, 'curfew']);
      assert.equal(await practiceLog(), before.log);
      assert.equal(ops('answer'), before.answers);
      assert.equal(ops('next'), before.next);
      assert.equal((await callAs(shell.base, 'GET', qPath(F.S), st)).status, 200, '列表不挡');
    } finally {
      await h.setClock(day);
    }
    const ok = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
    assert.equal(ok.status, 200, ok.text);
  });

  test('额度：留存态每天 5 次（题库自练口径，复测作答也计入），第 6 次 402 daily_limit_reached 且不调学习服务；列表与取题不受限', async () => {
    const r = await family('trial');
    const kid = r.kids[0]!;
    const st = await sess(kid.account);
    await h.su.query(`UPDATE platform.subscription SET status = 'ended' WHERE student_id = $1`, [kid.id]);
    for (let i = 0; i < 5; i++) {
      const ok = await callAs(shell.base, 'POST', aPath(kid.id), st, { ticket: await ticketFor(kid.account, kid.id), answer: { choice: ['B'] } });
      assert.equal(ok.status, 200, `第 ${i + 1} 次：${ok.text}`);
    }
    const sixth = await ticketFor(kid.account, kid.id);
    const a1 = ops('answer');
    const x = await callAs(shell.base, 'POST', aPath(kid.id), st, { ticket: sixth, answer: { choice: ['B'] } });
    assert.deepEqual([x.status, x.body.code, x.body.reason], [402, 'QUOTA_UNAVAILABLE', 'daily_limit_reached']);
    assert.equal(ops('answer'), a1);
    assert.equal(await practiceLog(kid.id), 5);
    assert.equal((await callAs(shell.base, 'GET', qPath(kid.id), st)).status, 200);
  });

  test('计次前只读预检（审查意见 2）：条目已不到期、票据已用、学习服务判过期或结果未知时不计次、不调作答；已计次的票据重试不再预检', async () => {
    const st = await sess(F.aS);
    for (const [out, status, code] of [
      [{ kind: 'failure', status: 409, problem: { code: 'RETEST_NOT_DUE' } }, 409, 'RETEST_NOT_DUE'],
      [{ kind: 'failure', status: 409, problem: { code: 'TICKET_USED' } }, 409, 'TICKET_USED'],
      [{ kind: 'failure', status: 410, problem: { code: 'TICKET_EXPIRED' } }, 410, 'TICKET_EXPIRED'],
      [{ kind: 'unknown', reason: '503 BUSY' }, 503, 'UPSTREAM_UNAVAILABLE'],
    ] as [Outcome, number, string][]) {
      const t = await ticketFor(F.aS, F.S);
      const before = { log: await practiceLog(F.S), answers: ops('answer') };
      checkImpl = () => out;
      try {
        const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
        assert.deepEqual([x.status, x.body.code], [status, code], x.text);
      } finally {
        checkImpl = null;
      }
      assert.equal(await practiceLog(F.S), before.log, `${code}：预检不通过不计次`);
      assert.equal(ops('answer'), before.answers, `${code}：不调作答`);
    }
    assert.ok(notes.includes('retest_precheck_rejected'));
    // 已计次的票据（上一次结果未知）重试：不再预检，直接按幂等找回
    const t = await ticketFor(F.aS, F.S);
    answerImpl = () => ({ kind: 'unknown', reason: '503 BUSY' });
    try {
      assert.equal((await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } })).status, 503);
    } finally {
      answerImpl = null;
    }
    const checks = ops('check');
    const log = await practiceLog(F.S);
    const ok = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
    assert.equal(ok.status, 200, ok.text);
    assert.equal(ops('check'), checks, '已计次的键不再预检');
    assert.equal(await practiceLog(F.S), log, '不再计次');
  });

  test('宵禁中已计次的票据只找回原结果（不执行、不计次）；没计次的票据、找不到原结果仍回 402 curfew', async () => {
    const st = await sess(F.aS);
    const done = await ticketFor(F.aS, F.S);
    const first = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: done, answer: { choice: ['B'] } });
    assert.equal(first.status, 200, first.text);
    const lost = await ticketFor(F.aS, F.S);                        // 计过次但学习服务没有结果（结果未知后一直没找回）
    answerImpl = () => ({ kind: 'unknown', reason: '503 BUSY' });
    try {
      assert.equal((await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: lost, answer: { choice: ['B'] } })).status, 503);
    } finally {
      answerImpl = null;
    }
    const fresh = await ticketFor(F.aS, F.S);
    const day = h.clockMs();
    const before = { log: await practiceLog(), answers: ops('answer'), replays: ops('replay') };
    try {
      await h.setClock(Date.parse('2026-10-15T15:30:00Z'));
      const r = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: done, answer: { choice: ['B'] } });
      assert.equal(r.status, 200, r.text);
      assert.deepEqual(r.body, first.body);
      assert.equal(r.headers.get('idempotent-replayed'), 'true');
      const l = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: lost, answer: { choice: ['B'] } });
      assert.deepEqual([l.status, l.body.reason], [402, 'curfew'], '找不到原结果仍回宵禁');
      const f = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: fresh, answer: { choice: ['B'] } });
      assert.deepEqual([f.status, f.body.reason], [402, 'curfew'], '没计次的票据宵禁中不找回、不作答');
      assert.equal(ops('replay'), before.replays + 2, '只有已计次的两张票据去找回');
      assert.equal(ops('answer'), before.answers, '宵禁中一次作答也不执行');
      assert.equal(await practiceLog(), before.log, '宵禁中不计次');
    } finally {
      await h.setClock(day);
    }
    assert.ok(notes.includes('retest_curfew_replay'));
  });

  test('作答结果的条目与票据的条目不一致：整条不转（503 UPSTREAM_UNAVAILABLE）', async () => {
    const st = await sess(F.aS);
    const t = await ticketFor(F.aS, F.S);
    answerImpl = () => ({ kind: 'ok', status: 200, replayed: false, body: { verdict: 'correct', entry: { entry_id: randomUUID(), status: 'done' } } });
    try {
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t, answer: { choice: ['B'] } });
      assert.deepEqual([x.status, x.body.code], [503, 'UPSTREAM_UNAVAILABLE'], x.text);
    } finally {
      answerImpl = null;
    }
  });

  test('日志：只记路由模板、状态与学生 ID 的哈希，不记学生 ID 原文、票据、作答与题面', () => {
    const text = JSON.stringify(logs.filter((l) => String(l['route'] ?? '').includes('/retest/')));
    assert.ok(text.length > 100);
    for (const leak of [F.S, F.S2, O.T, 'sbr1.', 'rt1.', '（合成）', ITEM_REF, '"choice"']) assert.ok(!text.includes(leak), `日志不含 ${leak}`);
    assert.ok(notes.includes('ticket_expired_local'));
  });
});
