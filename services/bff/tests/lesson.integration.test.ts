// 讲评课会话学生端路由与按节计量的集成测试（specs/stage-lesson-session ③b-4b）：一次性 Postgres（真库：真实会话、RLS、
// platform.is_self_student、platform.meter_reserve / meter_settle / meter_release、platform.meter_guard_status）+ 本进程里的 BFF +
// 可编程的假学习服务（按 services/learning/openapi/lesson-internal.v0.yaml 的响应形状；学习服务本身在 services/learning/tests/test_lesson_session.py）。
// 覆盖：开关关 404、没配学习服务 503；只认学生本人（兄妹、另一户、不存在 404，家长 403，越权不到学习服务、不预留）；
// 计量：打开只预留一次 lesson_whiteboard、下发即结算、确定拒绝即释放、结果未知不结算不释放、同一个键重放不重扣、换内容 409、
// 基础档 402、额度用尽 402；课内提问不扣次（预留与额度桶不变）、换学生或伪造票据 422 与过期 410 都不调学习服务、宵禁 402；
// 响应白名单（不转会话 ID、usage_id、学习服务票据、第几问）；日志不记学生 ID、票据、讲解与回答。
// 全部合成数据（规则 8）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import { P } from '../src/http/problems.ts';
import type { AskBody, AskRequest, LessonLearning, LessonOutcome, OpenBody, OpenRequest } from '../src/lesson/learning-client.ts';
import { usageIdFor } from '../src/metering/uuid.ts';
import { counters, createFamily, HAS_DB, ledgerConsistent, setupHarness, type Harness } from './harness.ts';
import { callAs, startShell, type ShellHarness } from './shell-harness.ts';

const DSL = { dsl_version: '0.3.0', title: '（合成）讲评课', canvas: { width: 960, height: 440 }, roles: ['teacher'], narrated: true,
              steps: [{ id: 's1', say: 'l1', actions: [{ type: 'speaker', role: 'teacher' }] }] };
const SCRIPT = { dsl_version: '0.3.0', lines: [{ id: 'l1', role: 'teacher', text: '（合成台词）先看这一行。' }] };
const ANSWER = '（合成答疑）因为要凑成完全平方。';
const QUESTION = { cursor: 1, anchor: { kind: 'step', id: 's1' }, intent: 'why_step' };

describe('讲评课会话（BFF 学生端路由与按节计量）', { skip: HAS_DB ? false : '没有 PGHOST' }, () => {
  let h: Harness;
  let shell: ShellHarness;
  let off: ShellHarness;
  let bare: ShellHarness;
  let clock = Date.now();
  const logs: Record<string, unknown>[] = [];
  const notes: string[] = [];
  const opens: OpenRequest[] = [];
  const asks: AskRequest[] = [];
  /** 假学习服务的会话：预留键 → 会话 ID（同键重放同一个会话，与学习服务一致） */
  const sessions = new Map<string, string>();
  let openImpl: ((b: OpenRequest) => LessonOutcome<OpenBody>) | null = null;
  let askImpl: ((b: AskRequest) => LessonOutcome<AskBody>) | null = null;
  const F = { g: '', S: '', aS: '', S2: '', aS2: '' };
  const O = { g: '', T: '', aT: '' };
  const B = { g: '', S: '', aS: '' };  // 基础档（trial）：讲评课 0 次

  const learningTicket = (student: string, session: string) =>
    `lt1.${Buffer.from(JSON.stringify({ student, session, lesson_id: 'syn.lesson.a' })).toString('base64url')}.c2lnbmF0dXJl`;

  const fake: LessonLearning = {
    async open(b) {
      opens.push({ ...b });
      if (openImpl) return openImpl(b);
      const replayed = sessions.has(b.idempotency_key);
      if (!replayed) sessions.set(b.idempotency_key, randomUUID());
      const sid = sessions.get(b.idempotency_key)!;
      return { kind: 'ok', status: replayed ? 200 : 201, replayed, body: {
        session_id: sid, usage_id: b.usage_id, dsl_version: '0.3.0', dsl: DSL, script: SCRIPT, ticket: learningTicket(b.student_ref, sid),
        expires_at: new Date(clock + 80 * 60_000).toISOString(), question_limit: 20, question_count: 0 } };
    },
    async ask(b) {
      asks.push({ ...b });
      if (askImpl) return askImpl(b);
      return { kind: 'ok', status: 200, replayed: false, body: { outcome: 'answered', text: ANSWER, question_seq: 1, questions_left: 19 } };
    },
  };

  const sess = (account: string) => shell.sessionFor(account);
  const oPath = (student: string) => `/students/${student}/lessons/open`;
  const qPath = (student: string) => `/students/${student}/lessons/ask`;
  const openBody = (over: Record<string, unknown> = {}) => ({
    subject: 'aya', lesson_ref: { lesson_id: 'syn.lesson.a', lesson_version: '0.1.0' }, skill_node: 'aya:K-QF-0301', item_ref: 'aya:SYN-1',
    idempotency_key: randomUUID(), ...over });
  const reservations = async (student: string) => (await h.su.query(
    `SELECT state::text, resource_type, reserved_qty::int, settled_qty::int, source_method FROM platform.usage_reservation WHERE student_id = $1
      ORDER BY reserved_at`, [student])).rows as { state: string; resource_type: string; reserved_qty: number; settled_qty: number | null;
                                                    source_method: string }[];

  async function opened(account: string, student: string): Promise<Record<string, any>> {
    const r = await callAs(shell.base, 'POST', oPath(student), await sess(account), openBody());
    assert.equal(r.status, 200, r.text);
    return r.body;
  }

  before(async () => {
    h = await setupHarness();
    const common = { log: (l: Record<string, unknown>) => logs.push(l), now: () => new Date(clock) };
    shell = await startShell(h.bff, h.su, { ...common, lesson: { enabled: true, learning: fake, retryDelaysMs: [1, 1], note: (e) => notes.push(e) } });
    off = await startShell(h.bff, h.su, common);
    bare = await startShell(h.bff, h.su, { ...common, lesson: { enabled: true } });
    const f = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    Object.assign(F, { g: f.guardian, S: f.students[0]!.id, aS: f.students[0]!.account, S2: f.students[1]!.id, aS2: f.students[1]!.account });
    const o = await createFamily(h, { source: 'pilot_virtual' });
    Object.assign(O, { g: o.guardian, T: o.students[0]!.id, aT: o.students[0]!.account });
    const b = await createFamily(h, { source: 'trial' });
    Object.assign(B, { g: b.guardian, S: b.students[0]!.id, aS: b.students[0]!.account });
  });

  after(async () => {
    await shell?.close();
    await off?.close();
    await bare?.close();
    await h?.close();
  });

  test('开关关闭：两条路由不注册，与表外路径逐字节相同的 404；开关打开但没配学习服务：503 LESSON_UNAVAILABLE', async () => {
    const st = await off.sessionFor(F.aS);
    const outside = await callAs(off.base, 'GET', '/bff/v0/definitely-not-a-route', st);
    for (const [p, b] of [[oPath(F.S), openBody()], [qPath(F.S), { ticket: 'sbl1.AAAA', question: QUESTION, idempotency_key: randomUUID() }]] as const) {
      const x = await callAs(off.base, 'POST', p, st, b);
      assert.equal(x.status, 404, p);
      assert.equal(x.text, outside.text);
      assert.equal(x.text, P.NOT_FOUND);
      const y = await callAs(bare.base, 'POST', p, await bare.sessionFor(F.aS), b);
      assert.equal(y.status, 503, y.text);
      assert.equal(y.body.code, 'LESSON_UNAVAILABLE');
    }
    assert.equal(opens.length + asks.length, 0);
    assert.deepEqual(await reservations(F.S), []);
  });

  test('打开：预留一次 lesson_whiteboard（teach），下发即结算；只转讲解、台词稿、不透明票据、过期时间与提问余量', async () => {
    const before = await counters(h, F.S, 'lesson_whiteboard');
    const body = openBody();
    const r = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.headers.get('idempotent-replayed'), 'false');
    assert.deepEqual(Object.keys(r.body).sort(), ['dsl', 'dsl_version', 'expires_at', 'questions_left', 'script', 'ticket']);
    assert.deepEqual(r.body.dsl, DSL);
    assert.deepEqual(r.body.script, SCRIPT);
    assert.equal(r.body.questions_left, 20);
    assert.match(r.body.ticket, /^sbl1\./);
    for (const banned of ['lt1.', F.S, 'session_id', 'usage_id', sessions.get(body.idempotency_key)!]) assert.ok(!r.text.includes(banned), banned);
    // 转给学习服务的：学生来自路径（已核对本人），usage_id 由幂等键派生，幂等键原样；不带 answer_submitted
    const sent = opens.at(-1)!;
    assert.deepEqual(sent, { student_ref: F.S, subject: 'aya', lesson_ref: body.lesson_ref, skill_node: body.skill_node, item_ref: body.item_ref,
                             usage_id: usageIdFor(body.idempotency_key), idempotency_key: body.idempotency_key });
    const after = await counters(h, F.S, 'lesson_whiteboard');
    assert.equal(after.settled, before.settled + 1);
    assert.equal(after.reserved, before.reserved);
    const rows = (await reservations(F.S)).filter((x) => x.resource_type === 'lesson_whiteboard');
    assert.deepEqual(rows.at(-1), { state: 'settled', resource_type: 'lesson_whiteboard', reserved_qty: 1, settled_qty: 1, source_method: 'teach' });
    assert.ok(await ledgerConsistent(h));
  });

  test('同一个幂等键重放：不重扣（仍一笔、已结算），响应标为重放；同一个键换一节课 409 IDEMPOTENCY_KEY_REUSED、不调学习服务', async () => {
    const body = openBody();
    const a = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
    const mid = await counters(h, F.S, 'lesson_whiteboard');
    const b = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
    assert.equal(a.status, 200);
    assert.equal(b.status, 200);
    assert.equal(b.headers.get('idempotent-replayed'), 'true');
    assert.deepEqual(await counters(h, F.S, 'lesson_whiteboard'), mid, '重放不再扣次');
    const n = opens.length;
    const c = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS),
                           { ...body, lesson_ref: { lesson_id: 'syn.lesson.other', lesson_version: '0.1.0' } });
    assert.equal(c.status, 409, c.text);
    assert.equal(c.body.code, 'IDEMPOTENCY_KEY_REUSED');
    assert.equal(opens.length, n);
  });

  test('学习服务确定拒绝（这节课打不开）：释放预留，422 LESSON_NOT_AVAILABLE；同一个键再打开 409 KEY_FAILED_PREVIOUSLY', async () => {
    openImpl = () => ({ kind: 'failure', status: 422, problem: { code: 'LESSON_NOT_AVAILABLE' } });
    try {
      const before = await counters(h, F.S, 'lesson_whiteboard');
      const body = openBody();
      const r = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
      assert.equal(r.status, 422, r.text);
      assert.equal(r.body.code, 'LESSON_NOT_AVAILABLE');
      assert.deepEqual(await counters(h, F.S, 'lesson_whiteboard'), before, '释放后额度桶不变');
      assert.equal((await h.su.query(`SELECT state::text FROM platform.usage_reservation WHERE usage_id = $1`, [usageIdFor(body.idempotency_key)]))
        .rows[0].state, 'released');
      const again = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
      assert.equal(again.status, 409);
      assert.equal(again.body.code, 'KEY_FAILED_PREVIOUSLY');
    } finally {
      openImpl = null;
    }
  });

  test('结果未知：不结算、不释放（预留留着），503；同一个键重试成功后结算一次', async () => {
    openImpl = () => ({ kind: 'unknown', reason: '503 UPSTREAM_UNAVAILABLE' });
    const body = openBody();
    try {
      const r = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
      assert.equal(r.status, 503, r.text);
      assert.equal(r.body.code, 'UPSTREAM_UNAVAILABLE');
      assert.equal((await h.su.query(`SELECT state::text FROM platform.usage_reservation WHERE usage_id = $1`, [usageIdFor(body.idempotency_key)]))
        .rows[0].state, 'reserved');
      openImpl = null;
      const ok = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.aS), body);
      assert.equal(ok.status, 200, ok.text);
      assert.equal((await h.su.query(`SELECT state::text, settled_qty::int AS q FROM platform.usage_reservation WHERE usage_id = $1`,
                                     [usageIdFor(body.idempotency_key)])).rows[0].q, 1);
    } finally {
      openImpl = null;
    }
  });

  test('基础档（讲评课 0 次）402 QUOTA_UNAVAILABLE，不调学习服务；请求体不合格 422 INVALID，不预留', async () => {
    const n = opens.length;
    const r = await callAs(shell.base, 'POST', oPath(B.S), await sess(B.aS), openBody());
    assert.equal(r.status, 402, r.text);
    assert.equal(r.body.code, 'QUOTA_UNAVAILABLE');
    assert.equal(opens.length, n);
    for (const bad of [openBody({ usage_id: randomUUID() }), openBody({ subject: 'politics' }), openBody({ answer_submitted: true }),
                       openBody({ idempotency_key: 'k' }), openBody({ skill_node: 'zhixing:K-1' })]) {
      const x = await callAs(shell.base, 'POST', oPath(F.S2), await sess(F.aS2), bad);
      assert.equal(x.status, 422, JSON.stringify(bad));
      assert.equal(x.text, P.INVALID);
    }
    assert.deepEqual(await reservations(F.S2), []);
    assert.equal(opens.length, n);
  });

  test('只认学生本人：兄妹、另一户、不存在 404（不调学习服务、不预留）；家长会话 403', async () => {
    const n = opens.length + asks.length;
    for (const [acct, student] of [[F.aS2, F.S], [O.aT, F.S], [F.aS, randomUUID()]] as const) {
      const r = await callAs(shell.base, 'POST', oPath(student), await sess(acct), openBody());
      assert.equal(r.status, 404, r.text);
      assert.equal(r.text, P.NOT_FOUND);
      const q = await callAs(shell.base, 'POST', qPath(student), await sess(acct), { ticket: 'sbl1.AAAA', question: QUESTION, idempotency_key: randomUUID() });
      assert.equal(q.status, 404);
    }
    const g = await callAs(shell.base, 'POST', oPath(F.S), await sess(F.g), openBody());
    assert.equal(g.status, 403, g.text);
    assert.equal(opens.length + asks.length, n);
    assert.deepEqual(await reservations(O.T), []);
  });

  test('课内提问：不扣次（预留与额度桶不变）；转学习服务的票据是学习服务自己的、学生来自路径；回答只转结果、文字与余量', async () => {
    const o = await opened(F.aS, F.S);
    const before = await counters(h, F.S, 'lesson_whiteboard');
    const rowsBefore = await reservations(F.S);
    const key = randomUUID();
    const r = await callAs(shell.base, 'POST', qPath(F.S), await sess(F.aS), { ticket: o.ticket, question: QUESTION, idempotency_key: key });
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(r.body, { outcome: 'answered', text: ANSWER, questions_left: 19 });
    const sent = asks.at(-1)!;
    assert.equal(sent.student_ref, F.S);
    assert.match(sent.ticket, /^lt1\./);
    assert.deepEqual(sent.question, QUESTION);
    assert.equal(sent.idempotency_key, key);
    assert.deepEqual(await counters(h, F.S, 'lesson_whiteboard'), before, '提问不扣次');
    assert.deepEqual(await reservations(F.S), rowsBefore, '提问不预留');
    // 不答（没有答疑、到上限）照样 200
    askImpl = () => ({ kind: 'ok', status: 200, replayed: false, body: { outcome: 'no_answer', reason: 'limit', question_seq: null, questions_left: 0 } });
    try {
      const x = await callAs(shell.base, 'POST', qPath(F.S), await sess(F.aS), { ticket: o.ticket, question: QUESTION, idempotency_key: randomUUID() });
      assert.deepEqual(x.body, { outcome: 'no_answer', reason: 'limit', questions_left: 0 });
    } finally {
      askImpl = null;
    }
  });

  test('票据：换学生（兄妹）、伪造、再练票据 422 TICKET_INVALID；过期 410；都不调学习服务；不合格的提问 422 INVALID', async () => {
    const o = await opened(F.aS, F.S);
    const n = asks.length;
    const cases: [string, string, Record<string, unknown>, number, string][] = [
      [F.aS2, F.S2, { ticket: o.ticket, question: QUESTION, idempotency_key: randomUUID() }, 422, 'TICKET_INVALID'],
      [F.aS, F.S, { ticket: `sbl1.${'A'.repeat(64)}`, question: QUESTION, idempotency_key: randomUUID() }, 422, 'TICKET_INVALID'],
      [F.aS, F.S, { ticket: `sbp1.${'A'.repeat(64)}`, question: QUESTION, idempotency_key: randomUUID() }, 422, 'TICKET_INVALID'],
      [F.aS, F.S, { ticket: o.ticket, question: { ...QUESTION, intent: 'again' }, idempotency_key: randomUUID() }, 422, 'INVALID'],
      [F.aS, F.S, { ticket: o.ticket, question: { ...QUESTION, text: '（合成）自由文字' }, idempotency_key: randomUUID() }, 422, 'INVALID'],
    ];
    for (const [acct, student, body, status, code] of cases) {
      const r = await callAs(shell.base, 'POST', qPath(student), await sess(acct), body);
      assert.equal(r.status, status, `${code} ${r.text}`);
      assert.equal(r.body.code, code);
    }
    const saved = clock;
    clock += 81 * 60_000;
    try {
      const r = await callAs(shell.base, 'POST', qPath(F.S), await sess(F.aS), { ticket: o.ticket, question: QUESTION, idempotency_key: randomUUID() });
      assert.equal(r.status, 410, r.text);
      assert.equal(r.body.code, 'TICKET_EXPIRED');
    } finally {
      clock = saved;
    }
    assert.equal(asks.length, n, '本地拒绝都不调学习服务');
  });

  test('学习服务的拒绝映射：会话没下发或预留失效 409 LESSON_CLOSED；过期 410；不存在 404；结果未知 503', async () => {
    const o = await opened(F.aS, F.S);
    const cases: [LessonOutcome<AskBody>, number, string][] = [
      [{ kind: 'failure', status: 409, problem: { code: 'IDEMPOTENCY_KEY_SEALED' } }, 409, 'LESSON_CLOSED'],
      [{ kind: 'failure', status: 409, problem: { code: 'LESSON_NOT_OPEN' } }, 409, 'LESSON_CLOSED'],
      [{ kind: 'failure', status: 410, problem: { code: 'TICKET_EXPIRED' } }, 410, 'TICKET_EXPIRED'],
      [{ kind: 'failure', status: 404, problem: { code: 'SESSION_NOT_FOUND' } }, 404, 'NOT_FOUND'],
      [{ kind: 'failure', status: 422, problem: { code: 'QUESTION_NOT_ANSWERABLE' } }, 422, 'INVALID'],
      [{ kind: 'unknown', reason: '503' }, 503, 'UPSTREAM_UNAVAILABLE'],
    ];
    for (const [out, status, code] of cases) {
      askImpl = () => out;
      try {
        const r = await callAs(shell.base, 'POST', qPath(F.S), await sess(F.aS), { ticket: o.ticket, question: QUESTION, idempotency_key: randomUUID() });
        assert.equal(r.status, status, `${code} ${r.text}`);
        assert.equal(r.body.code, code);
      } finally {
        askImpl = null;
      }
    }
  });

  test('宵禁：提问 402 reason = curfew，不调学习服务', async () => {
    const o = await opened(F.aS, F.S);
    const n = asks.length;
    // 家长把宵禁设成覆盖现在的整天（与诊断、再练同一口径：platform.meter_guard_status）
    await h.su.query(`INSERT INTO platform.student_time_guard (student_id, daily_cap_minutes, curfew_start, curfew_end, updated_at, updated_by)
                      VALUES ($1, 600, '00:00', '23:59', now(), $2)
                      ON CONFLICT (student_id) DO UPDATE SET curfew_start = '00:00', curfew_end = '23:59'`, [F.S, F.g]);
    try {
      const r = await callAs(shell.base, 'POST', qPath(F.S), await sess(F.aS), { ticket: o.ticket, question: QUESTION, idempotency_key: randomUUID() });
      assert.equal(r.status, 402, r.text);
      assert.equal(r.body.reason, 'curfew');
      assert.equal(asks.length, n);
    } finally {
      await h.su.query(`DELETE FROM platform.student_time_guard WHERE student_id = $1`, [F.S]);
    }
  });

  test('日志：只有路由模板与学生 ID 的哈希；不记学生 ID、票据、讲解与回答', () => {
    const text = JSON.stringify(logs);
    assert.ok(logs.some((l) => String(l['route'] ?? '').includes('/lessons/')), '有讲评课路由的访问日志');
    for (const banned of [F.S, 'sbl1.', 'lt1.', '合成']) assert.ok(!text.includes(banned), banned);
  });
});
