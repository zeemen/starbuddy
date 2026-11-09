// 数学自适应诊断学生端接口的集成测试（SIE-02a 设计要点 5，验收第 2、3 条）：一次性 Postgres（真库）+ 学习服务进程
// （合成题面，只在 test 环境）+ 阿鸦适配器进程（判分只来自验证器）+ 本进程里的 BFF。
// 覆盖：只认学生本人（家长、其他学生、未登录一律拒绝，错误码同 2-L）；开始预留、结束结算、放弃释放；幂等重放不重复写证据、
// 不重复扣额度；暂停续做（不另占额度）；探针写入并在作答后关联；前端拿到的视图不含答案、题号、节点、对错。
// 全部合成数据（规则 8）。合成学生的答案取自阿鸦内容包（验证器自己的数据）。
// 4-H H2：挂在统一壳路由表上（auth = student），身份只来自真实会话（tests/shell-harness.ts）；
// H3：宵禁判断改由计量只读函数 meter_guard_status（0030）给出，测试只推进数据库的计量时钟，断言不变。
import assert from 'node:assert/strict';
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createServer as netServer, type AddressInfo } from 'node:net';
import { after, before, describe, test } from 'node:test';
import { httpDiagnosisLearning, type DiagnosisLearning } from '../src/diagnosis/learning-client.ts';
import { fetchAs, startShell, type ShellHarness } from './shell-harness.ts';
import { counters, createFamily, HAS_DB, ledgerConsistent, reservationsByStudent, setupHarness, type Harness } from './harness.ts';

const ROOT = new URL('../../../', import.meta.url);
const LEARNING = new URL('services/learning/', ROOT).pathname;
const PY = `${LEARNING}.venv/bin/python`;
const AYA = new URL('adapters/aya/', ROOT).pathname;
const READY = HAS_DB && existsSync(PY) && existsSync(`${AYA}node_modules/ajv`);
const SERVICE_CRED = `synthetic-bff-to-learning-${randomUUID()}`;
const SESSION_CRED = `synthetic-bff-to-learning-session-${randomUUID()}`;  // PLT-24：诊断只认会话令牌
const AYA_CRED = `synthetic-learning-to-aya-${randomUUID()}`;

const FIXTURE = JSON.parse(readFileSync(new URL('services/learning/learning/diagnosis/fixtures/quad-synthetic.json', ROOT), 'utf8')) as
  { items: { item_id: string; node_id: string; stem: string }[] };
const PACK = new Map((JSON.parse(readFileSync(`${AYA}content/pack.json`, 'utf8')) as {
  items: { id: string; format: string; answer: string[]; options?: { key: string }[] }[] }).items.map((i) => [i.id, i]));
const BY_STEM = new Map(FIXTURE.items.map((i) => [i.stem, i]));
const Q = 'MATH.SYN.QUAD.';
// 合成学生：会到“图象”为止（卡在顶点式）
const MASTERY = new Set(['MATH.SYN.BASIC.COORD', 'MATH.SYN.EXPR.COMPLETE_SQ', 'MATH.SYN.EQ.QUAD_SOLVE', `${Q}CONCEPT`, `${Q}GRAPH_BASIC`]);

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
  let shell: ShellHarness;
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
      await h.su.query(execFileSync(process.execPath, ['--no-warnings', '../../tools/content-register/aya.ts'], { cwd: AYA, encoding: 'utf8',
                                                                                                         maxBuffer: 16 << 20 }));
    }
    const ayaPort = await freePort();
    procs.push(spawn(process.execPath, ['--no-warnings', 'src/main.ts'], { cwd: AYA, stdio: 'ignore',
      // 插件契约 1.0.0 已签字（specs/contract-v1.0-signoff）：阿鸦适配器不再需要候选版开关
      env: { ...process.env, AYA_ADAPTER_SERVICE_TOKEN: AYA_CRED, PORT: String(ayaPort) } }));
    await waitFor(`http://127.0.0.1:${ayaPort}/plugin/v0/capabilities`, { authorization: `Bearer ${AYA_CRED}` }, (s) => s === 200);
    const lPort = await freePort();
    learningUrl = `http://127.0.0.1:${lPort}`;
    procs.push(spawn(PY, ['-m', 'learning'], { cwd: LEARNING, stdio: 'ignore', env: {
      PATH: process.env['PATH'] ?? '/usr/bin:/bin', PORT: String(lPort), LEARNING_ENV: 'test',
      LEARNING_DATABASE_URL: `postgresql://learning_app@/${process.env['PGDATABASE'] ?? 'starbuddy_test'}?host=${process.env['PGHOST']}`,
      LEARNING_SERVICE_TOKEN: SERVICE_CRED, LEARNING_SESSION_TOKEN: SESSION_CRED, LEARNING_ADAPTER_AYA_URL: `http://127.0.0.1:${ayaPort}`, LEARNING_ADAPTER_AYA_TOKEN: AYA_CRED,
      // 诊断作答走 learning-internal v1 写入路径（2026-10-20 签字，LT-04 删除了开关）：学习服务按 BFF 在开始时建的 diagnosis_full 预留
      // （会话的 usage_id 与预留键、方法 diagnose）核对计量，预留中或已结算才放行
      LEARNING_DIAGNOSIS_PRESENTER: 'synthetic' } }));
    await waitFor(`${learningUrl}/internal/v1/diagnosis/sessions/${randomUUID()}`,
                  { authorization: `Bearer ${SESSION_CRED}`, 'x-student-ref': randomUUID() }, (s) => s === 404);
    learning.real = httpDiagnosisLearning({ baseUrl: learningUrl, token: SESSION_CRED });
    // 学习服务客户端可被单个测试临时换掉（模拟不可用），其余时候走真实进程
    const proxy: DiagnosisLearning = {
      start: (...a) => (learning.override ?? learning.real!).start(...a),
      current: (...a) => (learning.override ?? learning.real!).current(...a),
      probe: (...a) => (learning.override ?? learning.real!).probe(...a),
      answer: (...a) => (learning.override ?? learning.real!).answer(...a),
      pause: (...a) => (learning.override ?? learning.real!).pause(...a),
      abandon: (...a) => (learning.override ?? learning.real!).abandon(...a),
    };
    shell = await startShell(h.bff, h.su, { log: (l) => logs.push(l),
      diagnosis: { learning: proxy, retryDelaysMs: [20, 50], note: (e) => notes.push(e) } });
    base = shell.base;
  });

  after(async () => {
    await shell?.close();
    for (const p of procs) p.kill('SIGTERM');
    await h?.close();
  });

  const url = (student: string, rest = '') => `${base}/bff/v0/students/${student}/diagnosis/sessions${rest}`;
  const call = async (method: 'GET' | 'POST', u: string, account?: string, body?: unknown) => {
    const r = await fetchAs(shell, method, u, account, body);
    const text = await r.text();
    return { status: r.status, body: text ? JSON.parse(text) as Record<string, any> : {}, replayed: r.headers.get('idempotent-replayed'),
             raw: text };
  };
  const startBody = (key = randomUUID()) => ({ subject: 'aya', idempotency_key: key });
  /** 把这一步的出题时间（与会话的续做时间）往前挪：模拟学生确实在这道题上花了这么久（学习服务按墙钟给用时封顶，S1）。只在测试库 */
  const backdate = async (sid: string, seq: number, seconds: number) => {
    const c = await h.su.connect();
    try {
      await c.query('BEGIN');
      await c.query('SET LOCAL session_replication_role = replica');
      const { rows } = await c.query(`UPDATE platform.diagnosis_step SET created_at = created_at - make_interval(secs => $3)
                                       WHERE session_id = $1 AND seq = $2 AND status = 'presented' RETURNING created_at`, [sid, seq, seconds]);
      if (rows[0]) await c.query('UPDATE platform.diagnosis_session SET resumed_at = least(resumed_at, $2) WHERE id = $1', [sid, rows[0].created_at]);
      await c.query('COMMIT');
    } finally {
      c.release();
    }
  };
  const evidenceCount = async (student: string) =>
    Number((await h.su.query('SELECT count(*) FROM platform.evidence_event WHERE student_id = $1', [student])).rows[0].count);

  const pausedAt: number[] = [];
  /** 合成学生做题：每题前探针（可选）、按掌握图作答；pauseAt：这一步前暂停再续做 */
  async function play(stu: { id: string; account: string }, sid: string, opts: { pauseAt?: number; replayEach?: boolean } = {}) {
    for (let guard = 0; guard < 60; guard++) {
      const v = await call('GET', url(stu.id, `/${sid}`), stu.account);
      assert.equal(v.status, 200, v.raw);
      // 前端视图只有题面、序号、题型与进度：没有题号、节点、答案、选题理由、对错、额度占用
      assert.deepEqual(Object.keys(v.body).sort(), ['elapsed_sec', 'item_count', 'item_limit', 'process_tasks', 'session_id', 'status', 'step',
                                                   'time_budget_sec']);
      assert.ok(!/item_id|node|answer|reason|verdict|usage|Q-FUN|Q-SPR|MATH\./.test(v.raw), v.raw);
      if (v.body['status'] === 'finished') return v.body;
      const step = v.body['step'];
      assert.deepEqual(Object.keys(step).sort(), ['item', 'kind', 'probe_recorded', 'seq', 'step_elapsed_sec']);
      if (opts.pauseAt === step.seq) {
        pausedAt.push(step.seq);
        await backdate(sid, step.seq, 60);
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
    // 会话已结束（取题不再经过宵禁关卡，H3 的 meter_guard_status 也不再替它挡）：其他学生仍然 403，守住的是会话归属核对本身
    assert.equal((await call('GET', url(s1.id, `/${sid}`), s2.account)).status, 403);
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

  // ---------- 审查处理：计量结算（安全审查 B1、S10）、宵禁（B2） ----------
  /** 取当前题并作答一道（每题前跳过探针）；返回作答后的会话状态 */
  async function answerOne(stu: { id: string; account: string }, sid: string, key = randomUUID()) {
    const v = await call('GET', url(stu.id, `/${sid}`), stu.account);
    assert.equal(v.status, 200, v.raw);
    const step = v.body['step'];
    const body: Record<string, unknown> = { seq: step.seq, answer: answerFor(step.item.stem).answer, elapsed_sec: 30, idempotency_key: key };
    if (step.kind !== 'adaptive') body['work'] = '合成过程：先列式再求解。';
    const a = await call('POST', url(stu.id, `/${sid}/answer`), stu.account, body);
    assert.equal(a.status, 200, a.raw);
    return a.body['status'] as string;
  }
  const expireDue = async () => { await h.scheduler.query('SELECT platform.meter_expire_due(50)'); };

  test('计量：第一道题判分成功即结算；之后预留过期（计量时钟推进超过 10 分钟）照样扣一次，做完不重复扣', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const before = await counters(h, stu.id, 'diagnosis_full');
    const sid = (await call('POST', url(stu.id), stu.account, startBody())).body['session_id'] as string;
    assert.deepEqual((await reservationsByStudent(h, stu.id)).map((x) => x.state), ['reserved']);
    await answerOne(stu, sid);
    assert.deepEqual((await reservationsByStudent(h, stu.id)).map((x) => x.state), ['settled']);
    await h.advance(15 * 60_000);  // 预留有效期 10 分钟（0008）：已结算的不受影响
    await expireDue();
    try {
      let status = 'active';
      for (let i = 0; i < 40 && status !== 'finished'; i++) status = await answerOne(stu, sid);
      assert.equal(status, 'finished');
    } finally {
      await h.setClock(h.clockMs() - 15 * 60_000);
    }
    const end = await counters(h, stu.id, 'diagnosis_full');
    assert.equal(end.settled, before.settled + 1);
    assert.equal(end.reserved, before.reserved);
    assert.deepEqual((await reservationsByStudent(h, stu.id)).map((x) => x.state), ['settled']);
    assert.ok(await ledgerConsistent(h));
  });

  test('届别中途被改（迁移 0049）：续做 409 带 session_id 与 reason；“重新开始”放弃即使答过题也退回额度，重放不重复退', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [a, b] = fam.students as [{ id: string; account: string }, { id: string; account: string }];
    // 合成学生建档九年级（届别按当前时刻推算）；客服把届别改晚一届：会话建立时刻的年级变成八年级
    const shift = (s: string) => h.su.query(
      `UPDATE platform.student_profile SET exam_cohort = exam_cohort + 1, exam_cohort_source = 'staff_correction' WHERE id = $1`, [s]);
    // a：答过题（已结算）
    const baseA = await counters(h, a.id, 'diagnosis_full');
    const sidA = (await call('POST', url(a.id), a.account, startBody())).body['session_id'] as string;
    await answerOne(a, sidA);
    assert.equal((await counters(h, a.id, 'diagnosis_full')).settled, baseA.settled + 1);
    await shift(a.id);
    const stuck = await call('POST', url(a.id), a.account, startBody());
    assert.equal(stuck.status, 409, stuck.raw);
    assert.deepEqual([stuck.body['code'], stuck.body['reason'], stuck.body['session_id']], ['SESSION_INCONSISTENT', 'grade_changed', sidA]);
    // 别的学生拿不到这个会话 ID：同一路径下仍是 403
    assert.equal((await call('POST', url(a.id), b.account, startBody())).status, 403);
    // 放弃在学习服务已生效，但 BFF 拿不到结果（结果未知）：这一次没退成（退回审查 S5）
    learning.override = { ...learning.real!, abandon: async (...x) => { await learning.real!.abandon(...x); return { kind: 'unknown', reason: 'lost' }; } };
    try {
      assert.equal((await call('POST', url(a.id, `/${sidA}/abandon`), a.account, { idempotency_key: randomUUID() })).status, 503);
    } finally {
      learning.override = null;
    }
    assert.equal((await counters(h, a.id, 'diagnosis_full')).granted, baseA.granted, '结果未知时没有退');
    // 前端再取当前题：视图带 abandon_reason = grade_changed，BFF 补调退回
    assert.equal((await call('GET', url(a.id, `/${sidA}`), a.account)).body['status'], 'abandoned');
    const afterA = await counters(h, a.id, 'diagnosis_full');
    assert.deepEqual(afterA, { granted: baseA.granted + 1, reserved: baseA.reserved, settled: baseA.settled + 1 },
                     '答过题也退回 1 次（加回额度桶）：取当前题时补调');
    // 再放弃（重放）、再取当前题：幂等，不重复退
    const again2 = await call('POST', url(a.id, `/${sidA}/abandon`), a.account, { idempotency_key: randomUUID() });
    assert.equal(again2.status, 200, again2.raw);
    assert.equal(again2.replayed, 'true');
    assert.equal((await call('GET', url(a.id, `/${sidA}`), a.account)).body['status'], 'abandoned');
    assert.deepEqual(await counters(h, a.id, 'diagnosis_full'), afterA);
    assert.ok(notes.includes('refunded') && notes.includes('refund_replayed'), notes.join(','));
    // 重新开始：新届别下是八年级，合成夹具没有八年级目标 → 422 NO_TARGETS（不再卡在旧会话上），新预留释放
    const again = await call('POST', url(a.id), a.account, startBody());
    assert.equal(again.status, 422, again.raw);
    assert.equal(again.body['code'], 'NO_TARGETS');
    assert.deepEqual(await counters(h, a.id, 'diagnosis_full'), afterA);
    // b：还没答题（预留未结算）就被改届别：放弃时释放
    const baseB = await counters(h, b.id, 'diagnosis_full');
    const sidB = (await call('POST', url(b.id), b.account, startBody())).body['session_id'] as string;
    await shift(b.id);
    const stuckB = await call('POST', url(b.id), b.account, startBody());
    assert.equal(stuckB.body['session_id'], sidB, stuckB.raw);
    assert.equal((await call('POST', url(b.id, `/${sidB}/abandon`), b.account, { idempotency_key: randomUUID() })).status, 200);
    assert.deepEqual((await reservationsByStudent(h, b.id)).map((x) => x.state), ['released']);
    assert.deepEqual(await counters(h, b.id, 'diagnosis_full'), baseB);
    assert.ok(await ledgerConsistent(h));
  });

  test('计量：答过题再放弃不退额度；没答题就放弃退额度', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [a, b] = fam.students as [{ id: string; account: string }, { id: string; account: string }];
    const baseA = await counters(h, a.id, 'diagnosis_full');
    const sidA = (await call('POST', url(a.id), a.account, startBody())).body['session_id'] as string;
    await answerOne(a, sidA);
    for (const replay of ['false', 'true']) {
      const x = await call('POST', url(a.id, `/${sidA}/abandon`), a.account, { idempotency_key: randomUUID() });
      assert.equal(x.status, 200);
      assert.equal(x.replayed, replay);
    }
    // 放弃后再取当前题（前端刷新）：仍按已结算处理，不释放
    assert.equal((await call('GET', url(a.id, `/${sidA}`), a.account)).body['status'], 'abandoned');
    assert.deepEqual((await reservationsByStudent(h, a.id)).map((x) => x.state), ['settled']);
    assert.equal((await counters(h, a.id, 'diagnosis_full')).settled, baseA.settled + 1);
    const baseB = await counters(h, b.id, 'diagnosis_full');
    const sidB = (await call('POST', url(b.id), b.account, startBody())).body['session_id'] as string;
    assert.equal((await call('POST', url(b.id, `/${sidB}/abandon`), b.account, { idempotency_key: randomUUID() })).status, 200);
    assert.deepEqual((await reservationsByStudent(h, b.id)).map((x) => x.state), ['released']);
    assert.deepEqual(await counters(h, b.id, 'diagnosis_full'), baseB);
    assert.ok(await ledgerConsistent(h));
  });

  test('宵禁判断来自 meter_guard_status（H3）：家长把宵禁提前到 21:00 后，20:59 能开始、21:30 拒绝；与函数返回一致', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const asAccount = async <T>(acct: string, sql: string, args: unknown[]): Promise<T> => {
      const c = await h.bff.connect();
      try {
        await c.query('BEGIN');
        await c.query("SELECT set_config('app.account_id', $1, true)", [acct]);
        const { rows } = await c.query(sql, args);
        await c.query('COMMIT');
        return rows[0].r as T;
      } finally {
        c.release();
      }
    };
    const set = await asAccount<{ outcome: string }>(fam.guardian, 'SELECT platform.set_time_guard($1, NULL, $2::time, $3::time) AS r',
                                                   [stu.id, '21:00', '07:00']);
    assert.equal(set.outcome, 'saved');
    const guard = () => asAccount<{ in_curfew: boolean; curfew_start: string }>(stu.account, 'SELECT platform.meter_guard_status($1) AS r', [stu.id]);
    const day = h.clockMs();
    try {
      await h.setClock(Date.parse('2026-10-15T12:59:00Z'));  // 北京时间 20:59：缺省与家长设定都不在宵禁
      assert.equal((await guard()).in_curfew, false);
      const ok = await call('POST', url(stu.id), stu.account, startBody());
      assert.equal(ok.status, 201);
      const sid = ok.body['session_id'] as string;
      await h.setClock(Date.parse('2026-10-15T13:30:00Z'));  // 北京时间 21:30：只在家长收紧后的宵禁里（缺省 22:00 才开始）
      const g = await guard();
      assert.deepEqual([g.in_curfew, g.curfew_start], [true, '21:00']);
      const blocked = await call('GET', url(stu.id, `/${sid}`), stu.account);
      assert.equal(blocked.status, 402);
      assert.equal(blocked.body['reason'], 'curfew');
      await call('POST', url(stu.id, `/${sid}/abandon`), stu.account, { idempotency_key: randomUUID() });
    } finally {
      await h.setClock(day);
    }
  });

  test('BFF 源码里没有宵禁、单日上限、已用分钟的计算（只调 meter_guard_status，H3）', () => {
    const src = readFileSync(new URL('../src/diagnosis/service.ts', import.meta.url), 'utf8');
    assert.ok(src.includes('platform.meter_guard_status'));
    for (const banned of ['AT TIME ZONE', 'student_time_guard', 'curfew_start', "time '22:00'", 'Asia/Shanghai']) {
      assert.ok(!src.includes(banned), banned);
    }
  });

  test('宵禁：续做、取题、探针、作答一律 402 curfew，并把进行中的会话置为暂停；白天续做同一个会话，不另占额度', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const sid = (await call('POST', url(stu.id), stu.account, startBody())).body['session_id'] as string;
    const v = await call('GET', url(stu.id, `/${sid}`), stu.account);
    const seq = v.body['step'].seq as number;
    const day = h.clockMs();
    const status = async () => (await h.su.query('SELECT status FROM platform.diagnosis_session WHERE id = $1', [sid])).rows[0].status;
    await h.setClock(Date.parse('2026-10-15T15:30:00Z'));  // 北京时间 23:30
    try {
      const probe = await call('POST', url(stu.id, `/${sid}/probe`), stu.account,
                               { seq, prediction: 'yes', asked_at: new Date().toISOString(), idempotency_key: randomUUID() });
      assert.equal(probe.status, 402);
      assert.equal(probe.body['reason'], 'curfew');
      assert.equal(await status(), 'paused');
      // 暂停中：取题不挡（前端显示暂停页），作答、续做挡
      assert.equal((await call('GET', url(stu.id, `/${sid}`), stu.account)).body['status'], 'paused');
      const ans = await call('POST', url(stu.id, `/${sid}/answer`), stu.account,
                             { seq, answer: answerFor(v.body['step'].item.stem).answer, elapsed_sec: 30, idempotency_key: randomUUID() });
      assert.equal(ans.status, 402);
      const resume = await call('POST', url(stu.id), stu.account, startBody());
      assert.equal(resume.status, 402);
      assert.equal(resume.body['reason'], 'curfew');
      assert.equal(await status(), 'paused');
      assert.equal(Number((await h.su.query('SELECT count(*) FROM insight.confidence_probe WHERE student_id = $1', [stu.id])).rows[0].count), 0);
      assert.equal(Number((await h.su.query('SELECT count(*) FROM platform.attempt WHERE student_id = $1', [stu.id])).rows[0].count), 0);
    } finally {
      await h.setClock(day);
    }
    // 白天：续做同一个会话；然后在进行中遇到宵禁，取题即暂停
    const resumed = await call('POST', url(stu.id), stu.account, startBody());
    assert.equal(resumed.status, 200);
    assert.equal(resumed.body['session_id'], sid);
    assert.equal(await status(), 'active');
    await h.setClock(Date.parse('2026-10-15T21:00:00Z'));  // 北京时间 05:00（次日），仍在缺省宵禁里
    try {
      const g = await call('GET', url(stu.id, `/${sid}`), stu.account);
      assert.equal(g.status, 402);
      assert.equal(g.body['reason'], 'curfew');
      assert.equal(await status(), 'paused');
    } finally {
      await h.setClock(day);
    }
    assert.equal((await reservationsByStudent(h, stu.id)).length, 1);
    assert.ok(notes.includes('curfew_paused'));
    await call('POST', url(stu.id, `/${sid}/abandon`), stu.account, { idempotency_key: randomUUID() });
  });

  test('探针：同一步并发提交只写一条探针（派生键），后到的也记到同一条上', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const sid = (await call('POST', url(stu.id), stu.account, startBody())).body['session_id'] as string;
    const seq = (await call('GET', url(stu.id, `/${sid}`), stu.account)).body['step'].seq as number;
    const rs = await Promise.all(['yes', 'no', 'unsure'].map((prediction) => call('POST', url(stu.id, `/${sid}/probe`), stu.account,
      { seq, prediction, asked_at: new Date().toISOString(), idempotency_key: randomUUID() })));
    assert.deepEqual(rs.map((r) => r.status), [200, 200, 200]);
    const probes = await h.su.query('SELECT id FROM insight.confidence_probe WHERE student_id = $1', [stu.id]);
    assert.equal(probes.rowCount, 1);
    const step = await h.su.query('SELECT probe_id FROM platform.diagnosis_step WHERE session_id = $1 AND seq = $2', [sid, seq]);
    assert.equal(step.rows[0].probe_id, probes.rows[0].id);
    await call('POST', url(stu.id, `/${sid}/abandon`), stu.account, { idempotency_key: randomUUID() });
  });

  test('探针：写好探针后没记到步骤上（学习服务不可用），重新提交（新的请求键、别的预测）不留孤儿行，记的是第一条', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const stu = fam.students[0]!;
    const sid = (await call('POST', url(stu.id), stu.account, startBody())).body['session_id'] as string;
    const seq = (await call('GET', url(stu.id, `/${sid}`), stu.account)).body['step'].seq as number;
    learning.override = { ...learning.real!, probe: async () => ({ kind: 'unknown', reason: '503 BUSY' }) };
    try {
      const r1 = await call('POST', url(stu.id, `/${sid}/probe`), stu.account,
                            { seq, prediction: 'yes', asked_at: new Date().toISOString(), idempotency_key: randomUUID() });
      assert.equal(r1.status, 503);
    } finally {
      learning.override = null;
    }
    const r2 = await call('POST', url(stu.id, `/${sid}/probe`), stu.account,
                          { seq, prediction: 'no', asked_at: new Date().toISOString(), idempotency_key: randomUUID() });
    assert.equal(r2.status, 200, r2.raw);
    const probes = await h.su.query('SELECT id, prediction FROM insight.confidence_probe WHERE student_id = $1', [stu.id]);
    assert.deepEqual(probes.rows.map((r) => r.prediction), ['yes']);
    const step = await h.su.query('SELECT probe_id FROM platform.diagnosis_step WHERE session_id = $1 AND seq = $2', [sid, seq]);
    assert.equal(step.rows[0].probe_id, probes.rows[0].id);
    await call('POST', url(stu.id, `/${sid}/abandon`), stu.account, { idempotency_key: randomUUID() });
  });
});
