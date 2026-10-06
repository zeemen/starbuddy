// 错题再练学生端路由与计量的集成测试（specs/practice-loop-stage1 PR 3）：一次性 Postgres（真库：真实会话、RLS、
// platform.is_self_student、platform.meter_practice、platform.meter_guard_status 与 practice_log）+ 本进程里的 BFF + 可编程的假学习服务
// （按 practice-internal.v0.yaml 的响应形状；客户端的请求形状在 tests/practice-units.test.ts 对本机假 HTTP 服务核对）。
// 覆盖：开关关 404；只认学生本人（兄妹、另一户、不存在 404，家长会话 403，越权不到学习服务、不计次、停在本人核对）；
// 响应白名单（不转掌握、置信度、证据 ID、错因、题目引用、同族限定标记、学习服务票据）；票据不透明且绑定学生；
// 计量：幂等键在票据里（一张票据最多计一次：重试、并发、换作答都不再计次）、结果未知同一张票据重试只计一次、本地可判的拒绝不计次、
// 列表与取题不计次、留存态第 6 次 402、没有权益 402；宵禁（取题、作答 402 curfew，不计次、不调学习服务；列表不挡）；
// 学习服务 409 / 410 / 403 / 422 / 501 / 502 / 503 的映射（计次之后才知道的拒绝已计一次）；日志不记学生 ID 与作答。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import { P } from '../src/http/problems.ts';
import { isUuidV4, usageIdFor } from '../src/metering/uuid.ts';
import type { PracticeLearning, PracticeOutcome } from '../src/practice/learning-client.ts';
import { EXPIRY_MARGIN_MS } from '../src/practice/service.ts';
import { createFamily, HAS_DB, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const ITEM_REF = 'aya:SYN-PR-ITEM-01';
const NODE = 'MATH.SYN.PR.A';
const TTL_MS = 60 * 60_000;
type Outcome = PracticeOutcome<unknown>;
interface Sent { op: 'queue' | 'next' | 'answer'; student: string; subject: string; body?: Record<string, unknown>; entry?: string }

/** 学习服务票据：载荷里有学生内部 ID 与题目引用（与真实票据一样，前端不该看到） */
const learningTicket = (student: string) =>
  `pt1.${Buffer.from(JSON.stringify({ student, subject: 'aya', item_id: ITEM_REF, node_id: NODE, issued_at: new Date().toISOString() })).toString('base64url')}.c2lnbmF0dXJl`;

describe('错题再练（BFF 学生端路由与计量）', { skip: HAS_DB ? false : '没有 PGHOST' }, () => {
  let h: Harness;
  let shell: ShellHarness;
  let off: ShellHarness;
  let bare: ShellHarness;
  let clock = Date.now();
  const logs: Record<string, unknown>[] = [];
  const notes: string[] = [];
  const sent: Sent[] = [];
  /** 本测试的学生 → 它的条目（学习服务读时计算，假实现固定一条） */
  const entryOf = new Map<string, string>();
  /** 假学习服务的幂等记录：键 → （作答、结果）；同键不同作答 409 IDEMPOTENCY_CONFLICT（与学习服务一致） */
  const results = new Map<string, { answer: string; result: unknown }>();
  let format: 'single' | 'fill' = 'single';
  let nextImpl: ((student: string, entry: string) => Outcome) | null = null;
  let answerImpl: ((student: string, body: Record<string, unknown>) => Outcome) | null = null;
  const F = { g: '', S: '', aS: '', S2: '', aS2: '' };
  const O = { g: '', T: '', aT: '' };

  const fake: PracticeLearning = {
    async queue(student, subject) {
      sent.push({ op: 'queue', student, subject });
      return { kind: 'ok', status: 200, replayed: false, body: { subject, entries: [{
        entry_id: entryOf.get(student) ?? randomUUID(), node_id: NODE, cognitive_level: 'apply', source: 'wrong', status: 'due', round: 1, max_rounds: 3,
        source_item_ref: 'aya:SYN-PR-ITEM-00', next_round_at: null, retest_due: true, same_family_only: true, empty_reason: null,
        p_mastery: 0.31, confidence: 0.7, evidence_ids: [randomUUID()], error_category: 'aya:sign' }] } };
    },
    async next(student, subject, entry) {
      sent.push({ op: 'next', student, subject, entry });
      if (nextImpl) return nextImpl(student, entry);
      if (entryOf.get(student) !== entry) return { kind: 'failure', status: 404, problem: { code: 'ENTRY_NOT_FOUND', retryable: false } };
      return { kind: 'ok', status: 200, replayed: false, body: {
        status: 'ready', entry_id: entry, round: 1, item_ref: ITEM_REF,
        item: format === 'fill' ? { format: 'fill', stem: '（合成）x + 1 = 3，x = ?' }
          : { format: 'single', stem: '（合成）1 + 1 = ?', options: [{ key: 'A', text: '1' }, { key: 'B', text: '2' }, { key: 'C', text: '3' }] },
        ticket: learningTicket(student), expires_at: new Date(clock + TTL_MS).toISOString(), same_family_only: true } };
    },
    async answer(student, subject, body) {
      sent.push({ op: 'answer', student, subject, body: { ...body } });
      if (answerImpl) return answerImpl(student, body as unknown as Record<string, unknown>);
      const key = body.idempotency_key;
      const answer = JSON.stringify(body.answer);
      const seen = results.get(key);
      if (seen && seen.answer !== answer) return { kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_CONFLICT', retryable: false } };
      if (!seen) {
        results.set(key, { answer, result: { verdict: 'correct', entry: { entry_id: entryOf.get(student), status: 'closed', round: 1, next_round_at: null },
                                             p_mastery: 0.8, confidence: 0.9, evidence_ids: [randomUUID()], error_attribution: null, attempt_id: randomUUID() } });
      }
      return { kind: 'ok', status: 200, body: results.get(key)!.result, replayed: seen !== undefined };
    },
  };

  const practiceLog = async (student?: string): Promise<number> => (await h.su.query(
    `SELECT count(*)::int AS n FROM platform.practice_log WHERE $1::uuid IS NULL OR student_id = $1::uuid`, [student ?? null])).rows[0].n;
  const sess = (account: string) => shell.sessionFor(account);
  const qPath = (student: string, subject = 'math') => `/students/${student}/practice/${subject}`;
  const nPath = (student: string, entry: string, subject = 'math') => `/students/${student}/practice/${subject}/entries/${entry}/next`;
  const aPath = (student: string, subject = 'math') => `/students/${student}/practice/${subject}/answer`;
  const ops = (op: Sent['op']) => sent.filter((s) => s.op === op).length;
  const lastAnswerKey = () => String(sent.filter((s) => s.op === 'answer').at(-1)!.body!['idempotency_key']);

  /** 学生本人取一道题，返回下发给前端的票据 */
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
    shell = await startShell(h.bff, h.su, { ...common, practice: { enabled: true, learning: fake, retryDelaysMs: [1, 1], note: (e) => notes.push(e) } });
    off = await startShell(h.bff, h.su, common);
    bare = await startShell(h.bff, h.su, { ...common, practice: { enabled: true } });
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

  test('开关关闭：三条路由不注册，与表外路径逐字节相同的 404；开关打开但没配学习服务：503 PRACTICE_UNAVAILABLE', async () => {
    const st = await off.sessionFor(F.aS);
    const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', st);
    for (const [m, p, b] of [['GET', qPath(F.S), undefined], ['POST', nPath(F.S, entryOf.get(F.S)!), undefined],
                             ['POST', aPath(F.S), { ticket: 'sbp1.AAAA', answer: { text: '1' } }]] as const) {
      const x = await callAs(off.base, m, p, st, b);
      assert.equal(x.status, 404, `${m} ${p}`);
      assert.equal(x.text, outside.text);
      assert.equal(x.text, P.NOT_FOUND);
      const y = await callAs(bare.base, m, p, await bare.sessionFor(F.aS), b);
      assert.equal(y.status, 503, y.text);
      assert.equal(y.body.code, 'PRACTICE_UNAVAILABLE');
    }
    assert.equal(sent.length, 0);
  });

  test('列表：只认学生本人；只转白名单字段（不转原题引用、复测标记、同族限定标记、掌握、置信度、证据 ID、错因）；学科码换成学习服务的；不计次', async () => {
    const before = await practiceLog();
    const r = await callAs(shell.base, 'GET', qPath(F.S), await sess(F.aS));
    assert.equal(r.status, 200, r.text);
    assert.equal(r.body.subject, 'math');
    assert.deepEqual(Object.keys(r.body.entries[0]).sort(), ['cognitive_level', 'empty_reason', 'entry_id', 'max_rounds', 'next_round_at', 'node_id', 'round',
                                                             'source', 'status']);
    for (const banned of ['source_item_ref', 'SYN-PR-ITEM', 'retest_due', 'same_family', 'mastery', 'confidence', 'evidence', 'error', F.S]) {
      assert.ok(!r.text.includes(banned), `列表不含 ${banned}`);
    }
    const last = sent.at(-1)!;
    assert.deepEqual([last.op, last.student, last.subject], ['queue', F.S, 'aya']);
    await callAs(shell.base, 'GET', qPath(F.S, 'politics'), await sess(F.aS));
    assert.equal(sent.at(-1)!.subject, 'zhixing');
    const calls = sent.length;
    const en = await callAs(shell.base, 'GET', qPath(F.S, 'english'), await sess(F.aS));
    assert.equal(en.text, P.NOT_FOUND, '不认识的学科码与不存在同一个 404');
    assert.equal(sent.length, calls, '不认识的学科码不调学习服务');
    assert.equal(await practiceLog(), before, '列表不计次');
  });

  test('授权：兄妹、另一户学生、不存在的学生同一个 404；家长会话（含本户家长）403；越权请求停在本人核对：不查宵禁、不到学习服务、不计次', async () => {
    const aS2 = await sess(F.aS2);  // 逐个登录：同一家长并发登录会撞号码与验证码
    const aT = await sess(O.aT);
    const gF = await sess(F.g);
    const gO = await sess(O.g);
    const missing = randomUUID();
    const before = { calls: sent.length, log: await practiceLog(), notes: notes.length };
    const body = () => ({ ticket: 'sbp1.' + 'A'.repeat(40), answer: { text: '1' } });
    const routes = [['GET', (s: string) => qPath(s)], ['POST', (s: string) => nPath(s, entryOf.get(F.S)!)], ['POST', (s: string) => aPath(s)]] as const;
    for (const [m, p] of routes) {
      const b = p === routes[2][1] ? body() : undefined;
      const none = await callAs(shell.base, m, p(missing), aS2, b);
      assert.equal(none.status, 404, none.text);
      assert.equal(none.text, P.NOT_FOUND);
      for (const [who, s] of [['兄妹', aS2], ['另一户学生', aT]] as const) {
        const x = await callAs(shell.base, m, p(F.S), s, b);
        assert.equal(x.status, 404, `${who} ${m}`);
        assert.equal(x.text, none.text, `${who} ${m} 与不存在逐字节相同`);
      }
      for (const [who, g] of [['本户家长', gF], ['另一户家长', gO]] as const) {
        const x = await callAs(shell.base, m, p(F.S), g, b);
        assert.equal(x.status, 403, `${who} ${m}`);
        assert.equal(x.text, P.FORBIDDEN, `${who} ${m}：家长看不到再练（spec #10）`);
      }
    }
    assert.equal(sent.length, before.calls, '越权请求没有到达学习服务');
    assert.equal(await practiceLog(), before.log, '越权请求没有计次');
    assert.ok(!notes.slice(before.notes).includes('curfew_forbidden'), '越权请求停在本人核对（没有走到宵禁查询）');
  });

  test('取题：前端拿到题面与不透明票据；不含题目引用、学生 ID、学习服务票据、同族限定标记；没有候选回 unavailable；学习服务的拒绝按取题映射；不计次', async () => {
    const before = await practiceLog();
    const r = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), await sess(F.aS));
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(Object.keys(r.body).sort(), ['entry_id', 'expires_at', 'item', 'round', 'status', 'ticket']);
    assert.deepEqual(Object.keys(r.body.item).sort(), ['format', 'options', 'stem']);
    assert.match(r.body.ticket, /^sbp1\.[A-Za-z0-9_-]+$/);
    const decoded = Buffer.from(r.body.ticket.slice(5), 'base64url').toString('latin1');
    for (const leak of [ITEM_REF, 'SYN-PR-ITEM', NODE, F.S, 'pt1.', 'item_ref', 'same_family']) {
      assert.ok(!r.text.includes(leak) && !decoded.includes(leak), `取题响应与票据不含 ${leak}`);
    }
    const st = await sess(F.aS);
    const call = () => callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), st);
    try {
      nextImpl = () => ({ kind: 'ok', status: 200, replayed: false, body: { status: 'unavailable', empty_reason: 'no_admitted_candidate' } });
      assert.deepEqual((await call()).body, { status: 'unavailable', empty_reason: 'no_admitted_candidate' });
      nextImpl = () => ({ kind: 'failure', status: 409, problem: { code: 'PRACTICE_NOT_DUE', retryable: false } });
      const nd = await call();
      assert.deepEqual([nd.status, nd.body.code], [409, 'PRACTICE_NOT_DUE']);
      for (const c of ['TICKET_USED', 'IDEMPOTENCY_CONFLICT', 'SOMETHING_NEW']) {
        nextImpl = () => ({ kind: 'failure', status: 409, problem: { code: c, retryable: false } });
        const x = await call();
        assert.deepEqual([x.status, x.body.code], [503, 'PRACTICE_UNAVAILABLE'], `取题收到 409 ${c}`);
      }
      nextImpl = () => ({ kind: 'failure', status: 400, problem: { code: 'INVALID_REQUEST', retryable: false } });
      assert.equal((await call()).body.code, 'PRACTICE_UNAVAILABLE', '兜底是 503，不是 422');
      nextImpl = () => ({ kind: 'unknown', reason: '503 BUSY' });
      const n0 = ops('next');
      const busy = await call();
      assert.deepEqual([busy.status, busy.body.code], [503, 'UPSTREAM_UNAVAILABLE']);
      assert.equal(ops('next') - n0, 3, '结果未知时同一请求重试（共 3 次）');
      nextImpl = () => ({ kind: 'unknown', reason: '501 PRESENTER_UNAVAILABLE' });
      const n1 = ops('next');
      const det = await call();
      assert.deepEqual([det.status, det.body.code], [503, 'PRACTICE_UNAVAILABLE']);
      assert.equal(ops('next') - n1, 1, '确定性拒绝不重试');
    } finally {
      nextImpl = null;
    }
    const other = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(O.T)!), st);
    assert.equal(other.text, P.NOT_FOUND, '别人的条目与不存在同一个 404');
    assert.equal(await practiceLog(), before, '取题不计次');
  });

  test('作答计次：幂等键在票据里（BFF 生成的 UUIDv4），每张票据计一次；学习服务收到的 usage_id 由它派生、原票据原样转发；同一张票据重放、并发都不再计次；响应只有判分结论与条目状态', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);
    const before = await practiceLog(F.S);
    const r = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(r.body, { verdict: 'correct', entry: { entry_id: entryOf.get(F.S), status: 'closed', round: 1, next_round_at: null } });
    assert.equal(r.headers.get('idempotent-replayed'), 'false');
    assert.equal(await practiceLog(F.S), before + 1, '计一次');
    const got = sent.filter((s) => s.op === 'answer').at(-1)!;
    assert.deepEqual(Object.keys(got.body!).sort(), ['answer', 'idempotency_key', 'ticket', 'usage_id']);
    assert.deepEqual(got.body!['answer'], { choice: ['B'] });
    assert.equal(got.student, F.S);
    assert.equal(got.subject, 'aya');
    const key = String(got.body!['idempotency_key']);
    assert.ok(isUuidV4(key), 'BFF 生成的幂等键是小写 UUIDv4');
    assert.ok(!ticket.includes(key), '票据里的键前端读不到');
    assert.equal(got.body!['usage_id'], usageIdFor(key));
    assert.match(String(got.body!['ticket']), /^pt1\./, '转给学习服务的是它自己签发的票据');
    const { rows } = await h.su.query(`SELECT usage_id::text FROM platform.practice_log WHERE idempotency_key = $1`, [key]);
    assert.deepEqual(rows, [{ usage_id: usageIdFor(key) }], '库里记的 usage_id 与转给学习服务的一致');
    // 同一张票据重放
    const again = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
    assert.equal(again.status, 200);
    assert.equal(again.headers.get('idempotent-replayed'), 'true');
    assert.equal(again.text, r.text);
    assert.equal(lastAnswerKey(), key, '同一张票据同一个键');
    assert.equal(await practiceLog(F.S), before + 1, '同一张票据重放不再计次');
    // 另一张票据：新键，再计一次；同一张票据并发只计一次
    const t2 = await ticketFor(F.aS, F.S);
    const many = await Promise.all(Array.from({ length: 6 }, () => callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t2, answer: { choice: ['B'] } })));
    assert.ok(many.every((x) => x.status === 200), many.map((x) => x.status).join(','));
    assert.notEqual(lastAnswerKey(), key, '每张票据一个新键');
    assert.equal(await practiceLog(F.S), before + 2, '同一张票据并发只计一次');
    // 请求体里带幂等键、usage_id、数量、学生等：422，不计次
    for (const extra of [{ idempotency_key: randomUUID() }, { usage_id: randomUUID() }, { quantity: 0 }, { student_ref: F.S2 }, { purpose: 'retest' }]) {
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: await ticketFor(F.aS, F.S), answer: { choice: ['B'] }, ...extra });
      assert.equal(x.text, P.INVALID, JSON.stringify(extra));
    }
    assert.equal(await practiceLog(F.S), before + 2);
  });

  test('一张票据最多扣一次（审查 H2）：同一张票据换一个作答 → 学习服务 IDEMPOTENCY_CONFLICT → 409 TICKET_USED，不再计次', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);
    const before = await practiceLog(F.S);
    const first = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['A'] } });
    assert.equal(first.status, 200, first.text);
    const key = lastAnswerKey();
    for (const answer of [{ choice: ['B'] }, { choice: ['C'] }]) {
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer });
      assert.equal(x.status, 409, x.text);
      assert.equal(x.body.code, 'TICKET_USED');
      assert.equal(lastAnswerKey(), key, '换作答仍是票据里的同一个键');
    }
    assert.equal(await practiceLog(F.S), before + 1, '换了两次作答，仍只计一次');
    const { rows } = await h.su.query(`SELECT count(*)::int AS n FROM platform.practice_log WHERE idempotency_key = $1`, [key]);
    assert.equal(rows[0].n, 1);
  });

  test('结果未知：同一个键、同一个 usage_id 重试；仍未知回 503，已计一次；之后用同一张票据重试拿回结果，不再计次', async () => {
    const st = await sess(F.aS);
    const before = await practiceLog(F.S);
    let n = 0;
    answerImpl = (_s, body) => (++n < 3 ? { kind: 'unknown', reason: '503 BUSY' }
      : { kind: 'ok', status: 200, replayed: false, body: { verdict: 'incorrect', entry: { entry_id: entryOf.get(F.S), status: 'waiting', round: 1,
                                                                                          next_round_at: '2026-10-07T02:00:00Z' }, key: body['idempotency_key'] } });
    try {
      const r = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: await ticketFor(F.aS, F.S), answer: { choice: ['A'] } });
      assert.equal(r.status, 200, r.text);
      assert.deepEqual(r.body, { verdict: 'incorrect', entry: { entry_id: entryOf.get(F.S), status: 'waiting', round: 1, next_round_at: '2026-10-07T02:00:00Z' } });
      const tries = sent.filter((s) => s.op === 'answer').slice(-3);
      assert.equal(tries.length, 3);
      for (const t of tries) assert.deepEqual(t.body, tries[0]!.body, '重试用同一个请求体');
      assert.equal(await practiceLog(F.S), before + 1);
      // 一直未知：503，已计一次
      answerImpl = () => ({ kind: 'unknown', reason: '500 INTERNAL' });
      const t2 = await ticketFor(F.aS, F.S);
      const u = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t2, answer: { choice: ['A'] } });
      assert.equal(u.status, 503);
      assert.equal(u.body.code, 'UPSTREAM_UNAVAILABLE');
      assert.equal(await practiceLog(F.S), before + 2);
      answerImpl = null;
      const ok = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: t2, answer: { choice: ['A'] } });
      assert.equal(ok.status, 200, ok.text);
      assert.equal(await practiceLog(F.S), before + 2, '同一张票据重试不再计次');
    } finally {
      answerImpl = null;
    }
  });

  test('学习服务拒绝的映射：409 PRACTICE_NOT_DUE / TICKET_USED / IDEMPOTENCY_CONFLICT / KEY_SEALED、410、403、422、501、502 ADAPTER_RESULT_REJECTED、503；这些在计次之后才知道，已计一次（题库自练没有释放）', async () => {
    const st = await sess(F.aS);
    const cases: [Outcome, number, string, number][] = [
      [{ kind: 'failure', status: 409, problem: { code: 'PRACTICE_NOT_DUE', retryable: false } }, 409, 'PRACTICE_NOT_DUE', 1],
      [{ kind: 'failure', status: 409, problem: { code: 'TICKET_USED', retryable: false } }, 409, 'TICKET_USED', 1],
      [{ kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_CONFLICT', retryable: false } }, 409, 'TICKET_USED', 1],
      [{ kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_KEY_SEALED', retryable: false } }, 409, 'KEY_SEALED', 1],
      [{ kind: 'failure', status: 410, problem: { code: 'TICKET_EXPIRED', retryable: false } }, 410, 'TICKET_EXPIRED', 1],
      [{ kind: 'failure', status: 403, problem: { code: 'TICKET_INVALID', retryable: false } }, 422, 'TICKET_INVALID', 1],
      [{ kind: 'failure', status: 422, problem: { code: 'INVALID_ANSWER', retryable: false } }, 422, 'INVALID_ANSWER', 1],
      [{ kind: 'failure', status: 400, problem: { code: 'INVALID_REQUEST', retryable: false } }, 503, 'PRACTICE_UNAVAILABLE', 1],
      [{ kind: 'failure', status: 501, problem: { code: 'PRESENTER_UNAVAILABLE', retryable: false } }, 503, 'PRACTICE_UNAVAILABLE', 1],
      [{ kind: 'unknown', reason: '502 ADAPTER_RESULT_REJECTED' }, 503, 'PRACTICE_UNAVAILABLE', 1],
      [{ kind: 'unknown', reason: '503 UPSTREAM_UNAVAILABLE' }, 503, 'UPSTREAM_UNAVAILABLE', 3],
    ];
    try {
      for (const [out, status, code, calls] of cases) {
        const ticket = await ticketFor(F.aS, F.S);
        answerImpl = () => out;
        const before = { log: await practiceLog(F.S), answers: ops('answer') };
        const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['A'] } });
        assert.equal(x.status, status, `${code}：${x.text}`);
        assert.equal(x.body.code, code);
        assert.deepEqual(Object.keys(x.body).sort(), ['code', 'status', 'title', 'type'], '错误体只有错误码');
        assert.equal(await practiceLog(F.S), before.log + 1, `${code}：计次之后学习服务才拒绝，已计一次`);
        assert.equal(ops('answer') - before.answers, calls, `${JSON.stringify(out)}：调学习服务 ${calls} 次`);
        answerImpl = null;
      }
    } finally {
      answerImpl = null;
    }
  });

  test('本地可判的拒绝不计次、不调学习服务：伪造或篡改票据、兄妹的票据、换学科、作答形状不合题型、票据已过期', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);
    const siblingTicket = await ticketFor(F.aS2, F.S2);
    const before = { log: await practiceLog(), answers: ops('answer') };
    const post = (b: Record<string, unknown>, subject = 'math') => callAs(shell.base, 'POST', aPath(F.S, subject), st, b);
    const tampered = `${ticket.slice(0, 20)}${ticket[20] === 'A' ? 'B' : 'A'}${ticket.slice(21)}`;
    for (const [what, b, subject, code] of [
      ['伪造', { ticket: `sbp1.${'A'.repeat(80)}`, answer: { choice: ['A'] } }, 'math', 'TICKET_INVALID'],
      ['篡改', { ticket: tampered, answer: { choice: ['A'] } }, 'math', 'TICKET_INVALID'],
      ['兄妹的票据', { ticket: siblingTicket, answer: { choice: ['A'] } }, 'math', 'TICKET_INVALID'],
      ['换学科', { ticket, answer: { choice: ['A'] } }, 'politics', 'TICKET_INVALID'],
      ['单选给填空', { ticket, answer: { text: 'B' } }, 'math', 'INVALID_ANSWER'],
      ['单选多选', { ticket, answer: { choice: ['A', 'B'] } }, 'math', 'INVALID_ANSWER'],
      ['不存在的选项', { ticket, answer: { choice: ['E'] } }, 'math', 'INVALID_ANSWER'],
    ] as const) {
      const x = await post(b, subject);
      assert.equal(x.status, 422, `${what}：${x.text}`);
      assert.equal(x.body.code, code, what);
    }
    // 过期（含余量）：本地 410，不计次
    clock += TTL_MS - EXPIRY_MARGIN_MS + 1;
    try {
      const x = await post({ ticket, answer: { choice: ['A'] } });
      assert.equal(x.status, 410, x.text);
      assert.equal(x.body.code, 'TICKET_EXPIRED');
    } finally {
      clock -= TTL_MS - EXPIRY_MARGIN_MS + 1;
    }
    assert.equal(await practiceLog(), before.log, '本地拒绝不计次');
    assert.equal(ops('answer'), before.answers, '本地拒绝不调学习服务');
    // 兄妹本人用自己的票据：能用（票据绑定的是会话所认的学生）
    const own = await callAs(shell.base, 'POST', aPath(F.S2), await sess(F.aS2), { ticket: siblingTicket, answer: { choice: ['B'] } });
    assert.equal(own.status, 200, own.text);
  });

  test('票据过期后用已计过次的同一张票据重试：照样转给学习服务找回原结果，不再计次；没计过次的票据过期 410', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);
    const fresh = await ticketFor(F.aS, F.S);
    answerImpl = () => ({ kind: 'unknown', reason: '500 INTERNAL' });
    try {
      assert.equal((await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } })).status, 503);
    } finally {
      answerImpl = null;
    }
    const before = await practiceLog(F.S);
    clock += TTL_MS + 60_000;
    try {
      const r = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
      assert.equal(r.status, 200, r.text);
      assert.equal(await practiceLog(F.S), before, '不再计次');
      const x = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket: fresh, answer: { choice: ['B'] } });
      assert.equal(x.status, 410);
      assert.equal(await practiceLog(F.S), before);
    } finally {
      clock -= TTL_MS + 60_000;
    }
  });

  test('填空题：{text} 能交，{choice} 本地拒绝', async () => {
    format = 'fill';
    try {
      const st = await sess(F.aS);
      const ticket = await ticketFor(F.aS, F.S);
      assert.equal((await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['A'] } })).body.code, 'INVALID_ANSWER');
      assert.equal((await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { text: '2' } })).status, 200);
    } finally {
      format = 'single';
    }
  });

  test('宵禁（审查 B1，meter_guard_status 的 in_curfew）：取题、作答 402 curfew，不计次、不调学习服务；列表不挡；白天恢复', async () => {
    const st = await sess(F.aS);
    const ticket = await ticketFor(F.aS, F.S);  // 白天取的题
    const day = h.clockMs();
    const before = { log: await practiceLog(), answers: ops('answer'), next: ops('next') };
    try {
      await h.setClock(Date.parse('2026-10-15T15:30:00Z'));  // 北京时间 23:30：缺省宵禁内
      const a = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
      assert.equal(a.status, 402, a.text);
      assert.deepEqual({ code: a.body.code, reason: a.body.reason }, { code: 'QUOTA_UNAVAILABLE', reason: 'curfew' });
      const n = await callAs(shell.base, 'POST', nPath(F.S, entryOf.get(F.S)!), st);
      assert.equal(n.status, 402, n.text);
      assert.equal(n.body.reason, 'curfew');
      assert.equal(await practiceLog(), before.log, '宵禁内作答不计次');
      assert.equal(ops('answer'), before.answers, '宵禁内作答不调学习服务');
      assert.equal(ops('next'), before.next, '宵禁内取题不调学习服务');
      const q = await callAs(shell.base, 'GET', qPath(F.S), st);
      assert.equal(q.status, 200, '列表不挡');
    } finally {
      await h.setClock(day);
    }
    const ok = await callAs(shell.base, 'POST', aPath(F.S), st, { ticket, answer: { choice: ['B'] } });
    assert.equal(ok.status, 200, `白天同一张票据照常作答：${ok.text}`);
    assert.equal(await practiceLog(), before.log + 1);
  });

  test('额度：没有权益 402 no_entitlement；留存态每天 5 次，第 6 次 402 daily_limit_reached 且不调学习服务；付费与试点不限', async () => {
    // 没有任何订阅的学生（合成）：建档、绑定，不开通
    const g = randomUUID(), hh = randomUUID(), acct = randomUUID(), s = randomUUID();
    await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'guardian'), ($2, 'student')`, [g, acct]);
    await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [hh, g]);
    await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 8, 'nanjing')`, [s, acct, hh]);
    await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1, $2, 'household_creator')`, [g, s]);
    entryOf.set(s, randomUUID());
    const none = await ticketFor(acct, s);
    const a0 = ops('answer');
    const x = await callAs(shell.base, 'POST', aPath(s), await sess(acct), { ticket: none, answer: { choice: ['B'] } });
    assert.equal(x.status, 402, x.text);
    assert.deepEqual({ code: x.body.code, reason: x.body.reason }, { code: 'QUOTA_UNAVAILABLE', reason: 'no_entitlement' });
    assert.equal(ops('answer'), a0);
    // 留存态：试用结束（合成：订阅直接置为 ended）
    const r = await family('trial');
    const kid = r.kids[0]!;
    const st = await sess(kid.account);
    await h.su.query(`UPDATE platform.subscription SET status = 'ended' WHERE student_id = $1`, [kid.id]);
    for (let i = 0; i < 5; i++) {
      const ok = await callAs(shell.base, 'POST', aPath(kid.id), st, { ticket: await ticketFor(kid.account, kid.id), answer: { choice: ['B'] } });
      assert.equal(ok.status, 200, `第 ${i + 1} 次：${ok.text}`);
    }
    const sixthTicket = await ticketFor(kid.account, kid.id);
    const a1 = ops('answer');
    const sixth = await callAs(shell.base, 'POST', aPath(kid.id), st, { ticket: sixthTicket, answer: { choice: ['B'] } });
    assert.equal(sixth.status, 402, sixth.text);
    assert.deepEqual({ code: sixth.body.code, reason: sixth.body.reason }, { code: 'QUOTA_UNAVAILABLE', reason: 'daily_limit_reached' });
    assert.equal(ops('answer'), a1, '被拒的第 6 次不调学习服务');
    assert.equal(await practiceLog(kid.id), 5);
    // 列表与取题不受次数限制（不计次）
    assert.equal((await callAs(shell.base, 'GET', qPath(kid.id), st)).status, 200);
    assert.equal((await callAs(shell.base, 'POST', nPath(kid.id, entryOf.get(kid.id)!), st)).status, 200);
    // 试点（付费同口径）不限：S 已远超 5 次
    assert.ok(await practiceLog(F.S) > 5);
  });

  test('日志：只记路由模板、状态与学生 ID 的哈希，不记学生 ID 原文、票据、作答与题面', () => {
    const text = JSON.stringify(logs.filter((l) => String(l['route'] ?? '').includes('/practice/')));
    assert.ok(text.length > 100);
    for (const leak of [F.S, F.S2, O.T, 'sbp1.', 'pt1.', '（合成）', ITEM_REF, '"choice"']) assert.ok(!text.includes(leak), `日志不含 ${leak}`);
    assert.ok(notes.includes('practice_counted') && notes.includes('practice_count_existing') && notes.includes('ticket_expired_local'));
  });
});
