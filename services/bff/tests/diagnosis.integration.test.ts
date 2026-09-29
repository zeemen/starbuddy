// 数学自适应诊断学生端接口的集成测试（SIE-02a 设计要点 5，验收第 2、3 条）：一次性 Postgres（真库）+ 学习服务进程
// （合成题面，只在 test 环境）+ 阿鸦适配器进程（判分只来自验证器）+ 本进程里的 BFF。
// 覆盖：只认学生本人（家长、其他学生、未登录一律拒绝，错误码同 2-L）；开始预留、结束结算、放弃释放；幂等重放不重复写证据、
// 不重复扣额度；暂停续做（不另占额度）；探针写入并在作答后关联；前端拿到的视图不含答案、题号、节点、对错。
// 全部合成数据（规则 8）。合成学生的答案取自阿鸦内容包（验证器自己的数据）。
import assert from 'node:assert/strict';
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import type { Server } from 'node:http';
import { createServer as netServer, type AddressInfo } from 'node:net';
import { after, before, describe, test } from 'node:test';
import { httpDiagnosisLearning, type DiagnosisLearning } from '../src/diagnosis/learning-client.ts';
import { createStarMapServer } from '../src/star-map/server.ts';
import { counters, createFamily, HAS_DB, ledgerConsistent, reservationsByStudent, setupHarness, type Harness } from './harness.ts';

const ROOT = new URL('../../../', import.meta.url);
const LEARNING = new URL('services/learning/', ROOT).pathname;
const PY = `${LEARNING}.venv/bin/python`;
const AYA = new URL('adapters/aya/', ROOT).pathname;
const READY = HAS_DB && existsSync(PY) && existsSync(`${AYA}node_modules/ajv`);
const SERVICE_CRED = `synthetic-bff-to-learning-${randomUUID()}`;
const AYA_CRED = `synthetic-learning-to-aya-${randomUUID()}`;

const FIXTURE = JSON.parse(readFileSync(new URL('services/learning/learning/diagnosis/fixtures/quad-synthetic.json', ROOT), 'utf8')) as
  { items: { item_id: string; node_id: string; stem: string }[] };
const PACK = new Map((JSON.parse(readFileSync(`${AYA}content/pack.json`, 'utf8')) as {
  items: { id: string; format: string; answer: string[]; options?: { key: string }[] }[] }).items.map((i) => [i.id, i]));
const BY_STEM = new Map(FIXTURE.items.map((i) => [i.stem, i]));
const Q = 'MATH.FUNC.QUAD.';
// 合成学生：会到“图象”为止（卡在顶点式）
const MASTERY = new Set(['MATH.FUNC.BASIC.COORD', 'MATH.ALG.EXPR.COMPLETE_SQ', 'MATH.ALG.EQ.QUAD_SOLVE', `${Q}CONCEPT`, `${Q}GRAPH_BASIC`]);

function answerFor(stem: string): { answer: unknown; itemId: string } {
  const f = BY_STEM.get(stem);
  assert.ok(f, 'stem must come from the synthetic fixture');
  const it = PACK.get(f.item_id)!;
  const correct = MASTERY.has(f.node_id);
  if (it.format === 'fill') return { itemId: f.item_id, answer: { text: correct ? it.answer[0] : '-999' } };
  return { itemId: f.item_id, answer: { choice: correct ? [...it.answer] : [it.options!.find((o) => !it.answer.includes(o.key))!.key] } };
}

const freePort = () => new Promise<number>((resolve) => {
  const s = netServer();
  s.listen(0, '127.0.0.1', () => { const p = (s.address() as AddressInfo).port; s.close(() => resolve(p)); });
});

async function waitFor(url: string, headers: Record<string, string>, ok: (s: number) => boolean): Promise<void> {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(url, { headers });
      await r.body?.cancel();
      if (ok(r.status)) return;
    } catch { /* 还没起来 */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`service did not start: ${url}`);
}

describe('数学自适应诊断（BFF 学生端）', { skip: READY ? false : '没有 PGHOST、学习服务 .venv 或阿鸦依赖' }, () => {
  let h: Harness;
  let server: Server;
  let base = '';
  let learningUrl = '';
  const procs: ChildProcess[] = [];
  const notes: string[] = [];
  const logs: Record<string, unknown>[] = [];
  const learning = { real: null as DiagnosisLearning | null, override: null as DiagnosisLearning | null };

  before(async () => {
    h = await setupHarness();
    await h.su.query(`DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'learning_app') THEN CREATE ROLE learning_app LOGIN IN ROLE starbuddy_learning; END IF;
    END $$;`);
    const registered = await h.su.query(`SELECT 1 FROM platform.item WHERE subject = 'aya' AND item_id = 'Q-FUN-U01-T-002'`);
    if (registered.rowCount === 0) {
      await h.su.query(execFileSync(process.execPath, ['--no-warnings', 'scripts/register-content.ts'], { cwd: AYA, encoding: 'utf8',
                                                                                                         maxBuffer: 16 << 20 }));
    }
    const ayaPort = await freePort();
    procs.push(spawn(process.execPath, ['--no-warnings', 'src/main.ts'], { cwd: AYA, stdio: 'ignore',
      env: { ...process.env, AYA_ADAPTER_SERVICE_TOKEN: AYA_CRED, PORT: String(ayaPort) } }));
    await waitFor(`http://127.0.0.1:${ayaPort}/plugin/v0/capabilities`, { authorization: `Bearer ${AYA_CRED}` }, (s) => s === 200);
    const lPort = await freePort();
    learningUrl = `http://127.0.0.1:${lPort}`;
    procs.push(spawn(PY, ['-m', 'learning'], { cwd: LEARNING, stdio: 'ignore', env: {
      PATH: process.env['PATH'] ?? '/usr/bin:/bin', PORT: String(lPort), LEARNING_ENV: 'test',
      LEARNING_DATABASE_URL: `postgresql://learning_app@/${process.env['PGDATABASE'] ?? 'starbuddy_test'}?host=${process.env['PGHOST']}`,
      LEARNING_SERVICE_TOKEN: SERVICE_CRED, LEARNING_ADAPTER_AYA_URL: `http://127.0.0.1:${ayaPort}`, LEARNING_ADAPTER_AYA_TOKEN: AYA_CRED,
      LEARNING_DIAGNOSIS_PRESENTER: 'synthetic' } }));
    await waitFor(`${learningUrl}/internal/v1/diagnosis/sessions/${randomUUID()}?student_ref=${randomUUID()}`,
                  { authorization: `Bearer ${SERVICE_CRED}` }, (s) => s === 404);
    learning.real = httpDiagnosisLearning({ baseUrl: learningUrl, token: SERVICE_CRED });
    // 学习服务客户端可被单个测试临时换掉（模拟不可用），其余时候走真实进程
    const proxy: DiagnosisLearning = {
      start: (...a) => (learning.override ?? learning.real!).start(...a),
      current: (...a) => (learning.override ?? learning.real!).current(...a),
      probe: (...a) => (learning.override ?? learning.real!).probe(...a),
      answer: (...a) => (learning.override ?? learning.real!).answer(...a),
      pause: (...a) => (learning.override ?? learning.real!).pause(...a),
      abandon: (...a) => (learning.override ?? learning.real!).abandon(...a),
    };
    server = createStarMapServer({ pool: h.bff, devAuth: true, log: (l) => logs.push(l),
      diagnosis: { learning: proxy, retryDelaysMs: [20, 50], note: (e) => notes.push(e) } });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  after(async () => {
    await new Promise<void>((r) => server?.close(() => r()));
    for (const p of procs) p.kill('SIGTERM');
    await h?.close();
  });

  const url = (student: string, rest = '') => `${base}/bff/v0/students/${student}/diagnosis/sessions${rest}`;
  const call = async (method: 'GET' | 'POST', u: string, account?: string, body?: unknown) => {
    const r = await fetch(u, { method, headers: { ...(account ? { 'x-dev-account-id': account } : {}),
                                                  ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
                               body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await r.text();
    return { status: r.status, body: text ? JSON.parse(text) as Record<string, any> : {}, replayed: r.headers.get('idempotent-replayed'),
             raw: text };
  };
  const startBody = (key = randomUUID()) => ({ subject: 'aya', idempotency_key: key });
  const evidenceCount = async (student: string) =>
    Number((await h.su.query('SELECT count(*) FROM platform.evidence_event WHERE student_id = $1', [student])).rows[0].count);

  const pausedAt: number[] = [];
  /** 合成学生做题：每题前探针（可选）、按掌握图作答；pauseAt：这一步前暂停再续做 */
  async function play(stu: { id: string; account: string }, sid: string, opts: { pauseAt?: number; replayEach?: boolean } = {}) {
    for (let guard = 0; guard < 60; guard++) {
      const v = await call('GET', url(stu.id, `/${sid}`), stu.account);
      assert.equal(v.status, 200, v.raw);
      // 前端视图只有题面、序号、题型与进度：没有题号、节点、答案、选题理由、对错、额度占用
      assert.deepEqual(Object.keys(v.body).sort(), ['elapsed_sec', 'item_count', 'item_limit', 'session_id', 'status', 'step', 'time_budget_sec']);
      assert.ok(!/item_id|node|answer|reason|verdict|usage|Q-FUN|Q-SPR|MATH\./.test(v.raw), v.raw);
      if (v.body['status'] === 'finished') return v.body;
      const step = v.body['step'];
      assert.deepEqual(Object.keys(step).sort(), ['item', 'kind', 'probe_recorded', 'seq', 'step_elapsed_sec']);
      if (opts.pauseAt === step.seq) {
        pausedAt.push(step.seq);
        const p = await call('POST', url(stu.id, `/${sid}/pause`), stu.account, { seq: step.seq, step_elapsed_sec: 9, idempotency_key: randomUUID() });
        assert.equal(p.status, 200);
        const blocked = await call('POST', url(stu.id, `/${sid}/answer`), stu.account,
                                   { seq: step.seq, answer: answerFor(step.item.stem).answer, elapsed_sec: 20, idempotency_key: randomUUID() });
        assert.equal(blocked.status, 409);
        assert.equal(blocked.body['code'], 'INVALID_STATE');
        const resumed = await call('POST', url(stu.id), stu.account, startBody());  // 续做：新的开始请求，同一个会话
        assert.equal(resumed.status, 200);
        assert.equal(resumed.body['session_id'], sid);
        const again = await call('GET', url(stu.id, `/${sid}`), stu.account);
        assert.equal(again.body['step'].seq, step.seq);
        assert.equal(again.body['step'].step_elapsed_sec, 9);
      }
      const probeBody = { seq: step.seq, prediction: 'unsure', asked_at: new Date().toISOString(), idempotency_key: randomUUID() };
      const pr = await call('POST', url(stu.id, `/${sid}/probe`), stu.account, probeBody);
      assert.equal(pr.status, 200, pr.raw);
      const { answer } = answerFor(step.item.stem);
      const body: Record<string, unknown> = { seq: step.seq, answer, elapsed_sec: step.kind === 'adaptive' ? 40 : 120, idempotency_key: randomUUID() };
      if (step.kind !== 'adaptive') body['work'] = `合成过程：第 ${step.seq} 步先列式再求解。`;
      const a = await call('POST', url(stu.id, `/${sid}/answer`), stu.account, body);
      assert.equal(a.status, 200, a.raw);
      assert.deepEqual(Object.keys(a.body), ['status']);  // 不回对错
      if (opts.replayEach) {
        const before = await evidenceCount(stu.id);
        const a2 = await call('POST', url(stu.id, `/${sid}/answer`), stu.account, { ...body, idempotency_key: randomUUID() });
        assert.equal(a2.status, 200);
        assert.equal(a2.replayed, 'true');
        assert.equal(await evidenceCount(stu.id), before);
      }
    }
    throw new Error('session did not finish');
  }

  test('只认学生本人：未登录 401，家长、其他学生、不存在一律 403；不预留、不建会话', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [s1, s2] = fam.students as [{ id: string; account: string }, { id: string; account: string }];
    assert.equal((await call('POST', url(s1.id), undefined, startBody())).status, 401);
    for (const who of [fam.guardian, s2.account]) {
      const r = await call('POST', url(s1.id), who, startBody());
      assert.equal(r.status, 403);
      assert.equal(r.body['code'], 'FORBIDDEN');
    }
    assert.equal((await call('POST', url(randomUUID()), s1.account, startBody())).status, 403);
    assert.equal((await reservationsByStudent(h, s1.id)).length, 0);
    const started = await call('POST', url(s1.id), s1.account, startBody());
    assert.equal(started.status, 201, started.raw);
    const sid = started.body['session_id'] as string;
    for (const who of [fam.guardian, s2.account]) {
      assert.equal((await call('GET', url(s1.id, `/${sid}`), who)).status, 403);
      assert.equal((await call('POST', url(s1.id, `/${sid}/pause`), who, { idempotency_key: randomUUID() })).status, 403);
      // 其他学生拿自己的路径访问 s1 的会话也不行
      assert.equal((await call('GET', url(s2.id, `/${sid}`), s2.account)).status, 403);
    }
    assert.equal((await call('GET', url(s1.id, `/${randomUUID()}`), s1.account)).status, 403);
    assert.equal((await call('GET', url(s1.id, `/${sid}`))).status, 401);
    // 请求体：未知键、非阿鸦学科、坏键一律 422，不回显
    for (const bad of [{ ...startBody(), extra: 1 }, { subject: 'wenli', idempotency_key: randomUUID() }, { subject: 'aya', idempotency_key: 'k' }]) {
      const r = await call('POST', url(s1.id), s1.account, bad);
      assert.equal(r.status, 422);
      assert.ok(!r.raw.includes('wenli'));
    }
    const abandoned = await call('POST', url(s1.id, `/${sid}/abandon`), s1.account, { idempotency_key: randomUUID() });
    assert.equal(abandoned.status, 200);
    // 日志只记学生 ID 的哈希，不记作答
    assert.ok(logs.every((l) => l['student'] === undefined || !String(l['student']).includes('-')));
  });

  test('完整会话：开始预留、结束结算一次；重放任一步不重复写证据、不重复扣额度；探针作答后关联', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const before = await counters(h, stu.id, 'diagnosis_full');
    const key = randomUUID();
    const r1 = await call('POST', url(stu.id), stu.account, startBody(key));
    assert.equal(r1.status, 201, r1.raw);
    assert.equal(r1.replayed, 'false');
    const r2 = await call('POST', url(stu.id), stu.account, startBody(key));
    assert.equal(r2.status, 200);
    assert.equal(r2.replayed, 'true');
    assert.equal(r2.body['session_id'], r1.body['session_id']);
    const mid = await counters(h, stu.id, 'diagnosis_full');
    assert.equal(mid.reserved, before.reserved + 1);
    const sid = r1.body['session_id'] as string;
    const done = await play(stu, sid, { replayEach: true });
    assert.equal(done['status'], 'finished');
    assert.equal(done['step'], null);
    assert.ok((done['item_count'] as number) <= 20 && (done['elapsed_sec'] as number) <= 2100);
    const end = await counters(h, stu.id, 'diagnosis_full');
    assert.equal(end.settled, before.settled + 1);
    assert.equal(end.reserved, before.reserved);
    // 结束后重放开始、再取当前题：不再扣
    assert.equal((await call('POST', url(stu.id), stu.account, startBody(key))).replayed, 'true');
    await call('GET', url(stu.id, `/${sid}`), stu.account);
    assert.deepEqual(await counters(h, stu.id, 'diagnosis_full'), end);
    assert.ok(await ledgerConsistent(h));
    // 探针：每道作答都有一条学生自述探针，并已关联作答（实际对错按作答 ID 去判分表取，探针表不存）
    const probes = await h.su.query(`SELECT count(*) FILTER (WHERE attempt_id IS NOT NULL)::int AS linked, count(*)::int AS n,
                                            bool_and(source_type = 'self') AS self FROM insight.confidence_probe WHERE student_id = $1`, [stu.id]);
    const steps = await h.su.query(`SELECT count(*)::int AS n FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id
                                     WHERE s.id = $1 AND st.status = 'answered'`, [sid]);
    assert.equal(probes.rows[0].n, steps.rows[0].n);
    assert.equal(probes.rows[0].linked, steps.rows[0].n);
    assert.equal(probes.rows[0].self, true);
    // 过程观察进复核队列；判分只来自验证器
    const q = await h.su.query(`SELECT count(*)::int AS n FROM platform.review_task WHERE student_id = $1 AND source_kind = 'diagnosis_step'`, [stu.id]);
    assert.equal(q.rows[0].n, 4);
    const g = await h.su.query(`SELECT bool_and(verifier_id = 'aya-symbolic-ts') AS v FROM platform.grade_result WHERE student_id = $1`, [stu.id]);
    assert.equal(g.rows[0].v, true);
    assert.ok(notes.includes('settled') && notes.includes('probe_linked'));
  });

  test('暂停、跨天续做：同一个会话，不另占额度；结果与一次做完相同', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const seqs: string[] = [];
    for (const [i, stu] of fam.students.entries()) {
      const r = await call('POST', url(stu.id), stu.account, startBody());
      const sid = r.body['session_id'] as string;
      await play(stu, sid, i === 1 ? { pauseAt: 4 } : {});
      const rows = await h.su.query(`SELECT kind, node_id, item_id, reason_code, status FROM platform.diagnosis_step WHERE session_id = $1 ORDER BY seq`, [sid]);
      seqs.push(JSON.stringify(rows.rows));
      const res = await reservationsByStudent(h, stu.id);
      assert.deepEqual(res.map((x) => x.state), ['settled']);  // 续做没有新预留
    }
    assert.equal(seqs[0], seqs[1]);
    assert.deepEqual(pausedAt, [4]);  // 确实在第 4 步暂停、续做过（第 3 步是无题可测，不出题）
  });

  test('放弃释放额度；学习服务确定拒绝时释放；结果未知时不释放，同一个键重试找回同一笔预留', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [a, b] = fam.students as [{ id: string; account: string }, { id: string; account: string }];
    const base0 = await counters(h, a.id, 'diagnosis_full');
    const r = await call('POST', url(a.id), a.account, startBody());
    const sid = r.body['session_id'] as string;
    for (const replay of ['false', 'true']) {
      const x = await call('POST', url(a.id, `/${sid}/abandon`), a.account, { idempotency_key: randomUUID() });
      assert.equal(x.status, 200);
      assert.equal(x.replayed, replay);
    }
    assert.deepEqual(await counters(h, a.id, 'diagnosis_full'), base0);
    assert.deepEqual((await reservationsByStudent(h, a.id)).map((x) => x.state), ['released']);
    // 学习服务不可用：503，预留留着（不释放、不结算）；恢复后同一个键重试：不再预留第二笔
    learning.override = {
      start: async () => ({ kind: 'unknown', reason: '503 BUSY' }), current: async () => ({ kind: 'unknown', reason: 'x' }),
      probe: async () => ({ kind: 'unknown', reason: 'x' }), answer: async () => ({ kind: 'unknown', reason: 'x' }),
      pause: async () => ({ kind: 'unknown', reason: 'x' }), abandon: async () => ({ kind: 'unknown', reason: 'x' }),
    };
    const key = randomUUID();
    try {
      const down = await call('POST', url(b.id), b.account, startBody(key));
      assert.equal(down.status, 503);
      assert.equal(down.body['code'], 'UPSTREAM_UNAVAILABLE');
    } finally {
      learning.override = null;
    }
    assert.deepEqual((await reservationsByStudent(h, b.id)).map((x) => x.state), ['reserved']);
    const up = await call('POST', url(b.id), b.account, startBody(key));
    assert.equal(up.status, 201, up.raw);
    assert.equal((await reservationsByStudent(h, b.id)).length, 1);
    // 学习服务确定拒绝（例如这个年级在试点地图里没有诊断目标）：释放，422
    learning.override = { ...learning.real!, start: async () => ({ kind: 'failure', status: 422, problem: { code: 'NO_TARGETS' } }) };
    let no;
    try {
      no = await call('POST', url(a.id), a.account, startBody());
    } finally {
      learning.override = null;
    }
    assert.equal(no.status, 422);
    assert.equal(no.body['code'], 'NO_TARGETS');
    assert.deepEqual((await reservationsByStudent(h, a.id)).map((x) => x.state).sort(), ['released', 'released']);
    assert.ok(await ledgerConsistent(h));
  });

  test('探针与作答的请求体：未知键、自由文本类型、过期步骤都拒绝；同一步只认第一次探针', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const sid = (await call('POST', url(stu.id), stu.account, startBody())).body['session_id'] as string;
    const v = await call('GET', url(stu.id, `/${sid}`), stu.account);
    const seq = v.body['step'].seq as number;
    const bad = [
      { seq, prediction: 'maybe', asked_at: new Date().toISOString(), idempotency_key: randomUUID() },
      { seq, prediction: 'yes', asked_at: 'yesterday', idempotency_key: randomUUID() },
      { seq, prediction: 'yes', asked_at: new Date().toISOString(), idempotency_key: randomUUID(), note: '合成' },
    ];
    for (const b of bad) assert.equal((await call('POST', url(stu.id, `/${sid}/probe`), stu.account, b)).status, 422);
    const stale = await call('POST', url(stu.id, `/${sid}/probe`), stu.account,
                             { seq: seq + 1, prediction: 'yes', asked_at: new Date().toISOString(), idempotency_key: randomUUID() });
    assert.equal(stale.status, 409);
    const pk = randomUUID();
    const asked = new Date().toISOString();
    const p1 = await call('POST', url(stu.id, `/${sid}/probe`), stu.account, { seq, prediction: 'yes', asked_at: asked, idempotency_key: pk });
    const p2 = await call('POST', url(stu.id, `/${sid}/probe`), stu.account, { seq, prediction: 'no', asked_at: asked, idempotency_key: randomUUID() });
    assert.equal(p1.status, 200);
    assert.equal(p2.status, 200);
    assert.equal(p2.replayed, 'true');
    assert.equal(Number((await h.su.query('SELECT count(*) FROM insight.confidence_probe WHERE student_id = $1', [stu.id])).rows[0].count), 1);
    for (const b of [{ seq, answer: { text: 'x', choice: ['A'] }, elapsed_sec: 5, idempotency_key: randomUUID() },
                     { seq, answer: { choice: ['a'] }, elapsed_sec: 5, idempotency_key: randomUUID() },
                     { seq, answer: { choice: ['A'] }, elapsed_sec: -1, idempotency_key: randomUUID() },
                     { seq, answer: { choice: ['A'] }, elapsed_sec: 5, idempotency_key: randomUUID(), work: 3 }]) {
      assert.equal((await call('POST', url(stu.id, `/${sid}/answer`), stu.account, b)).status, 422);
    }
    assert.equal(await evidenceCount(stu.id), 0);
    await call('POST', url(stu.id, `/${sid}/abandon`), stu.account, { idempotency_key: randomUUID() });
  });
});
