// 处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts）：默认值钉住；真实库上（db/tests/pg-ephemeral.sh 起的一次性库）
// 慢语句被库端取消、池满时取连接按时失败、事务闲置被库端断开；数据库超时之后分学科闸门的名额照常归还。
// 真实库上的用例把时限缩短到几百毫秒（requestPoolConfig 的 overrides），只验机制，不等 10 秒。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { DB_TIMEOUTS, requestPoolConfig } from '../src/http/db-pool.ts';
import { createSubjectGate, GATE_SUBJECTS, loadConcurrencyConfig, withSubjectSlot } from '../src/gate/subject-gate.ts';
import { meteringFingerprint } from '../src/metering/fingerprint.ts';
import { withAccount } from '../src/metering/db.ts';
import { reserve } from '../src/metering/meter.ts';
import { counters, createFamily, HAS_DB, key, ledgerConsistent, reservationsByStudent, setupHarness, type Harness } from './harness.ts';

const pools: pg.Pool[] = [];
const pool = (max: number, o: Parameters<typeof requestPoolConfig>[2]) => {
  const p = new pg.Pool(requestPoolConfig(undefined, max, o));
  p.on('error', () => {});  // 库端断开空闲连接时池会发 error 事件：测试里吞掉
  pools.push(p);
  return p;
};
let h: Harness | undefined;
after(async () => { await Promise.all(pools.map((p) => p.end().catch(() => {}))); await h?.close(); });

test('默认值：取连接 3 秒、语句 10 秒（客户端兜底 12 秒）、事务闲置 15 秒；main.ts 用这份配置建池', () => {
  assert.deepEqual(DB_TIMEOUTS, { connectionTimeoutMillis: 3_000, statementTimeoutMs: 10_000, queryTimeoutMs: 12_000, idleInTransactionMs: 15_000 });
  const c = requestPoolConfig('postgresql://x@/y', 10);
  assert.equal(c.connectionTimeoutMillis, 3_000);
  assert.equal(c.statement_timeout, 10_000);
  assert.equal(c.query_timeout, 12_000);
  assert.equal(c.idle_in_transaction_session_timeout, 15_000);
  assert.ok(c.query_timeout! > c.statement_timeout!, '客户端兜底晚于库端取消（库端的错误更明确）');
  const main = readFileSync(fileURLToPath(new URL('../src/main.ts', import.meta.url)), 'utf8');
  assert.match(main, /new pg\.Pool\(requestPoolConfig\(url, 10\)\)/);
  assert.equal((main.match(/new pg\.Pool\(/g) ?? []).length, 1, 'BFF 只有这一个处理请求的池');
});

test('语句超时：慢语句被库端取消（57014），不一直挂着', { skip: !HAS_DB }, async () => {
  const p = pool(2, { statementTimeoutMs: 300, queryTimeoutMs: 2_000 });
  const t0 = Date.now();
  await assert.rejects(p.query('SELECT pg_sleep(3)'), (e: { code?: string }) => e.code === '57014');
  assert.ok(Date.now() - t0 < 1_500, `取消用了 ${Date.now() - t0} ms`);
  assert.equal((await p.query('SELECT 1 AS ok')).rows[0].ok, 1, '取消之后连接池照常可用');
});

test('取连接上限：池满时按时失败，不排队到底', { skip: !HAS_DB }, async () => {
  const p = pool(1, { connectionTimeoutMillis: 300 });
  const held = await p.connect();
  try {
    const t0 = Date.now();
    await assert.rejects(p.connect(), /timeout/i);
    assert.ok(Date.now() - t0 < 1_500, `等了 ${Date.now() - t0} ms`);
  } finally {
    held.release();
  }
  const again = await p.connect();
  again.release();
});

test('事务闲置：经 withAccount 的事务闲置超时，库端断开连接；进程不退出、连接被销毁、池照常可用（合并前安全审查第 1 条）', { skip: !HAS_DB }, async () => {
  // 不给借出的连接手动挂 error 监听：走生产路径（src/metering/db.ts 自己挂）。没有那个监听时，库端断开的 error 是未捕获异常，整个测试进程退出
  const p = new pg.Pool(requestPoolConfig(undefined, 2, { idleInTransactionMs: 300 }));
  pools.push(p);
  await assert.rejects(withAccount(p, 'syn-account', async (c) => {
    await new Promise((r) => setTimeout(r, 900));  // 事务开着、不发语句，超过闲置时限
    return c.query('SELECT 1');
  }));
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(p.totalCount, 0, '断开的连接已销毁，没还回池里');
  const ok = await withAccount(p, 'syn-account', async (c) => (await c.query('SELECT 1 AS ok')).rows[0].ok);
  assert.equal(ok, 1, '之后的请求照常');
});

test('withAccount 不给连接越挂越多的 error 监听：还回池里之前摘掉', { skip: !HAS_DB }, async () => {
  const p = new pg.Pool(requestPoolConfig(undefined, 1));
  pools.push(p);
  for (let i = 0; i < 20; i++) await withAccount(p, 'syn-account', async (c) => c.query('SELECT 1'));
  const c = await p.connect();
  try {
    assert.equal(c.listenerCount('error'), 0, '借出时只剩调用方自己的监听（这里没有）');
  } finally {
    c.release();
  }
});

test('main.ts 给连接池挂了 error 监听（空闲连接被库端断开时不让进程退出）', () => {
  const main = readFileSync(fileURLToPath(new URL('../src/main.ts', import.meta.url)), 'utf8');
  assert.match(main, /pool\.on\('error', \(e: Error & \{ code\?: unknown \}\) => \{\n\s+emit\('warn', \{ event: 'db_pool_idle_client_error'/);
});

test('数据库超时之后，分学科闸门的名额照常归还：同一科下一个请求立即拿到名额', { skip: !HAS_DB }, async () => {
  const base = loadConcurrencyConfig();
  const subject = GATE_SUBJECTS[0]!;
  const gate = createSubjectGate({ ...base, subjects: { ...base.subjects, [subject]: { ...base.subjects[subject], maxInflight: 1, maxQueue: 0 } } });
  const p = pool(2, { statementTimeoutMs: 300, queryTimeoutMs: 2_000 });
  const busy = () => 'busy' as const;
  await assert.rejects(withSubjectSlot(gate, { subject, student: 'syn-student-1' }, busy, async () => {
    await p.query('SELECT pg_sleep(3)');
    return 'done' as const;
  }), (e: { code?: string }) => e.code === '57014');
  assert.equal(gate.stats()[subject].inflight, 0, '超时后名额已归还');
  const next = await withSubjectSlot(gate, { subject, student: 'syn-student-2' }, busy, async () => 'ran' as const);
  assert.equal(next, 'ran', '同一科下一个请求没有被拒');
});

test('计量：预留做到一半语句超时，事务整体回滚（不留预留、额度不变）；放锁后同一幂等键重试只产生一笔预留', { skip: !HAS_DB }, async () => {
  h = await setupHarness();
  const fam = await createFamily(h, { source: 'trial' });  // 完整诊断 1 次
  const s = fam.students[0]!;
  const p = new pg.Pool({ ...requestPoolConfig(undefined, 2, { statementTimeoutMs: 300, queryTimeoutMs: 2_000 }), user: 'bff_app' });
  p.on('error', () => {});
  pools.push(p);
  const k = key();
  const input = { studentId: s.id, resourceType: 'diagnosis_full' as const, idempotencyKey: k,
                  requestFingerprint: meteringFingerprint('diagnose', { k }), quantity: 1, sourceMethod: 'diagnose' as const };
  // 另一个事务锁住这名学生的权益行（meter_reserve 要对它 FOR UPDATE）：预留只能排队，直到语句超时被取消
  const holder = await h.su.connect();
  try {
    await holder.query('BEGIN');
    await holder.query('SELECT 1 FROM platform.entitlement WHERE student_id = $1 FOR UPDATE', [s.id]);
    await assert.rejects(reserve(p, s.account, input), (e: { code?: string }) => e.code === '57014');
    await holder.query('ROLLBACK');
  } finally {
    holder.release();
  }
  assert.deepEqual(await reservationsByStudent(h, s.id), [], '超时的预留整体回滚，没有留下预留行');
  assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 1, reserved: 0, settled: 0 });
  const again = await reserve(p, s.account, input);
  assert.equal(again.outcome, 'reserved', '同一幂等键重试：第一次真正的预留');
  const once = await reserve(p, s.account, input);
  assert.equal(once.outcome, 'existing', '再重试按既有规则返回同一笔');
  assert.equal((await reservationsByStudent(h, s.id)).length, 1);
  assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 1, reserved: 1, settled: 0 });
  assert.ok(await ledgerConsistent(h));
});
