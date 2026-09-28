// Gate 0 六类计量场景（spec 第 5 节、开发计划冲刺 2-E 验收）：真实的多连接 Postgres + 假学习服务。
// 每个场景之后断言两个对账视图为零行（计数 = 流水与预留）。
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import { runExpiry, runRenewal } from '../src/metering/jobs.ts';
import { meteredCall, reserve, settle, type MeterDeps } from '../src/metering/meter.ts';
import { meteringFingerprint } from '../src/metering/fingerprint.ts';
import { itemFingerprint } from '../src/metering/content-fingerprint.ts';
import { usageIdFor } from '../src/metering/uuid.ts';
import {
  CountingMetrics, FakeLearning, HAS_DB, REJECTED, SUCCESS, UNAVAILABLE, counters, createFamily, gradeBody, key,
  ledgerConsistent, reservationsByStudent, setupHarness, type Harness,
} from './harness.ts';

const MIN = 60_000;
const HOUR = 60 * MIN;

describe('Gate 0 计量场景', { skip: HAS_DB ? false : '没有 PGHOST（make bff-test 会起一次性 Postgres）' }, () => {
  let h: Harness;
  before(async () => { h = await setupHarness(); });
  after(async () => { await h?.close(); });

  function deps(learning: FakeLearning, metrics = new CountingMetrics()): MeterDeps {
    // 退避时“睡眠”就是推进计量时钟：数据库与 BFF 看到同一条时间轴
    return { pool: h.bff, learning: learning.call, replay: learning.replay, metrics, now: h.clockMs, sleep: (ms) => h.advance(ms) };
  }

  test('场景 1：并发扣同一额度——额度只剩 1，20 个连接同时预留，恰好 1 个成功', async () => {
    const fam = await createFamily(h, { source: 'trial' });  // 完整诊断 1 次
    const s = fam.students[0]!;
    const results = await Promise.all(Array.from({ length: 20 }, () => {
      const k = key();
      return reserve(h.bff, s.account, {
        studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: k,
        requestFingerprint: meteringFingerprint('diagnose', { k }), quantity: 1, sourceMethod: 'diagnose',
      });
    }));
    const outcomes = results.map((r) => r.outcome).sort();
    assert.equal(outcomes.filter((o) => o === 'reserved').length, 1);
    assert.equal(outcomes.filter((o) => o === 'insufficient').length, 19);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 1, reserved: 1, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 1（完整流程）：20 个并发请求走预留 → 学习服务 → 结算，只扣 1 次', async () => {
    const fam = await createFamily(h, { source: 'trial' });
    const s = fam.students[0]!;
    const fake = new FakeLearning();
    const results = await Promise.all(Array.from({ length: 20 }, () => meteredCall(deps(fake), {
      accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full',
      idempotencyKey: key(), body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] },
    })));
    assert.equal(results.filter((r) => r.status === 'ok').length, 1);
    assert.equal(results.filter((r) => r.status === 'insufficient').length, 19);
    assert.equal(fake.calls.length, 1, '额度不足的请求不调学习服务');
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 1, reserved: 0, settled: 1 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 2：预留提交后 BFF 断线——同 key 重试拿回同一笔预留，只扣一次', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const k = key();
    const body = gradeBody(s.id);
    const input = { accountId: s.account, studentId: s.id, resourceType: 'mistake_analysis' as const,
                    idempotencyKey: k, body, contentFingerprint: itemFingerprint('aya:synthetic-0301-07') };
    // 模拟：BFF 预留成功后进程退出，没来得及调学习服务
    const withCtx = structuredClone(body);
    withCtx['context'] = { ...(withCtx['context'] as object), idempotency_key: k, usage_id: usageIdFor(k) };
    const r = await reserve(h.bff, s.account, {
      studentId: s.id, resourceType: 'mistake_analysis', idempotencyKey: k, requestFingerprint: meteringFingerprint('grade', withCtx),
      quantity: 1, contentFingerprint: input.contentFingerprint, sourceMethod: 'grade',
    });
    assert.equal(r.outcome, 'reserved');
    // 前端用同一个 key 重试（request_id 变了也不影响）
    const fake = new FakeLearning();
    const again = await meteredCall(deps(fake), { ...input, body: gradeBody(s.id) });
    assert.equal(again.status, 'ok');
    assert.equal((await reservationsByStudent(h, s.id)).length, 1);
    assert.deepEqual(await counters(h, s.id, 'mistake_analysis'), { granted: 300, reserved: 0, settled: 1 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 2：预留事务提交前连接被杀——什么也没留下', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const c = await h.bff.connect();
    c.on('error', () => {});
    await c.query('BEGIN');
    await c.query("SELECT set_config('app.account_id', $1, true)", [s.account]);
    const { rows } = await c.query('SELECT pg_backend_pid() AS pid');
    const k = key();
    await c.query('SELECT platform.meter_reserve($1, $2, $3, $4, 1, NULL, $5)',
                  [s.id, 'diagnosis_full', k, meteringFingerprint('diagnose', { k }), 'diagnose']);
    await h.su.query('SELECT pg_terminate_backend($1)', [rows[0].pid]);
    await assert.rejects(c.query('COMMIT'));
    c.release(true);
    assert.equal((await reservationsByStudent(h, s.id)).length, 0);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 2：预留后客户端再也不回来——过期任务退回额度', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const k = key();
    await reserve(h.bff, s.account, { studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: k,
                                      requestFingerprint: meteringFingerprint('diagnose', { k }), quantity: 1, sourceMethod: 'diagnose' });
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 1, settled: 0 });
    await h.advance(11 * MIN);
    assert.ok(await runExpiry(h.scheduler) >= 1);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 3：同一个 key 并发 10 次——一笔预留、只扣一次；换请求体或换学生都是冲突', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [s, sibling] = [fam.students[0]!, fam.students[1]!];
    const k = key();
    const fake = new FakeLearning(async () => { await new Promise((r) => setTimeout(r, 20)); return SUCCESS; });
    const input = { accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full' as const,
                    idempotencyKey: k, body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] } };
    const results = await Promise.all(Array.from({ length: 10 }, () => meteredCall(deps(fake), input)));
    assert.ok(results.every((r) => r.status === 'ok'), JSON.stringify(results.map((r) => r.status)));
    assert.equal((await reservationsByStudent(h, s.id)).length, 1);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 1 });
    assert.ok(fake.calls.every((c) => c.usageId === usageIdFor(k)), '每次调学习服务都带同一个 usage_id');

    const changed = await meteredCall(deps(fake), { ...input, body: { context: { subject: 'aya', student_ref: s.id }, attempts: [1] } });
    assert.equal(changed.status, 'conflict');
    const other = await meteredCall(deps(fake), { ...input, accountId: sibling.account, studentId: sibling.id,
                                                   body: { context: { subject: 'aya', student_ref: sibling.id }, attempts: [] } });
    assert.equal(other.status, 'conflict');
    assert.deepEqual(await counters(h, sibling.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 4：结算前失败回滚——422 释放；503 后恢复只扣一次；一直 503 则不结算，到期释放', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const base = { accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full' as const,
                   body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] } };

    const rejected = await meteredCall(deps(new FakeLearning(() => REJECTED)), { ...base, idempotencyKey: key() });
    assert.equal(rejected.status, 'failed');
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });

    const metrics = new CountingMetrics();
    const flaky = new FakeLearning((attempt) => (attempt <= 2 ? UNAVAILABLE : SUCCESS));
    const k = key();
    const recovered = await meteredCall(deps(flaky, metrics), { ...base, idempotencyKey: k });
    assert.equal(recovered.status, 'ok');
    assert.equal(flaky.callsFor(k), 3, '结果未知时用同一个 key 重试');
    assert.equal(metrics.get('learning_retry'), 2);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 1 });

    const down = new FakeLearning(() => UNAVAILABLE);
    const k2 = key();
    const unknown = await meteredCall(deps(down, metrics), { ...base, idempotencyKey: k2 });
    assert.equal(unknown.status, 'unknown');
    assert.ok(down.callsFor(k2) >= 2);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 1, settled: 1 }, '结果未知：不回滚也不结算');
    await h.advance(11 * MIN);
    await runExpiry(h.scheduler);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 1 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 5：过期后迟到的成功不扣费；过期后同 key 重试只调重放接口', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const k = key();
    const metrics = new CountingMetrics();
    // 学习服务很慢：处理期间预留过期、被过期任务回收
    const slow = new FakeLearning(async () => {
      await h.advance(11 * MIN);
      await runExpiry(h.scheduler);
      return SUCCESS;
    });
    const input = { accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full' as const,
                    idempotencyKey: k, body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] } };
    const late = await meteredCall(deps(slow, metrics), input);
    assert.deepEqual([late.status, late.status === 'ok' && late.charged], ['ok', false]);
    assert.equal(metrics.get('late_success_after_expiry'), 1);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });

    const callsBefore = slow.calls.length;
    const replay = await meteredCall(deps(slow, metrics), input);
    assert.deepEqual([replay.status, replay.status === 'ok' && replay.replay, replay.status === 'ok' && replay.charged], ['ok', true, false]);
    assert.equal(metrics.get('replay_after_expiry'), 1);
    assert.equal(slow.calls.length, callsBefore, '过期后不再发原请求');
    assert.deepEqual(slow.replays.at(-1), { method: 'diagnose', key: k, studentRef: s.id, usageId: usageIdFor(k) },
                     '重放按原 key、学生、usage_id 查询');
    assert.equal((await reservationsByStudent(h, s.id)).length, 1, '过期后重试不重新预留');
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 5：过期后重放没有结果——key 被封存，晚到的原请求被拒，前端换新 key 重新预留并扣费', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const k = key();
    const metrics = new CountingMetrics();
    const input = { accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full' as const,
                    idempotencyKey: k, body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] } };
    const down = new FakeLearning(() => UNAVAILABLE);
    assert.equal((await meteredCall(deps(down, metrics), input)).status, 'unknown');
    await h.advance(11 * MIN);
    await runExpiry(h.scheduler);

    // 学习服务那边这个 key 没有成功记录：重放 404 并封存，BFF 不发原请求
    const learning = new FakeLearning();
    const r = await meteredCall(deps(learning, metrics), input);
    assert.equal(r.status, 'expired_retry_with_new_key');
    assert.equal(metrics.get('sealed_after_expiry'), 1);
    assert.equal(learning.calls.length, 0, '没有预留的执行为 0');
    assert.ok(learning.sealed.has(k));
    // 之前卡在路上的原请求此时才到学习服务：被封存拒绝
    const lateOriginal = await learning.call('diagnose', { context: { idempotency_key: k, usage_id: usageIdFor(k), student_ref: s.id } });
    assert.ok(lateOriginal.kind === 'failure' && lateOriginal.problem.code === 'IDEMPOTENCY_KEY_SEALED');
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });

    // 前端换新 key：重新预留、执行、扣费
    const k2 = key();
    const fresh = await meteredCall(deps(learning, metrics), { ...input, idempotencyKey: k2 });
    assert.deepEqual([fresh.status, fresh.status === 'ok' && fresh.charged], ['ok', true]);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 1 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 5：过期后重放参数对不上不封存；同 key 仍在执行时结果未知', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const k = key();
    const metrics = new CountingMetrics();
    const learning = new FakeLearning();
    // 另一个学生先用过这个 key（学习服务里记录的学生不同）
    await learning.call('grade', { context: { idempotency_key: k, usage_id: usageIdFor(k), student_ref: randomUUID() } });
    const r0 = await learning.replay('grade', k, s.id, usageIdFor(k));
    assert.equal(r0.kind, 'not_found');
    assert.ok(!learning.sealed.has(k), '参数对不上：不替别人封存');

    const input = { accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full' as const,
                    idempotencyKey: key(), body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] } };
    const down = new FakeLearning(() => UNAVAILABLE);
    await meteredCall(deps(down, metrics), input);
    await h.advance(11 * MIN);
    await runExpiry(h.scheduler);
    down.busy = true;
    const busy = await meteredCall(deps(down, metrics), input);
    assert.equal(busy.status, 'unknown', '重放 BUSY：结果未知，不换 key、不扣费');
    assert.ok(!down.sealed.has(input.idempotencyKey));
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
  });

  test('场景 5：过期任务与结算并发——到期的只会被过期、未到期的只会被结算，不死锁、不出错', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const make = async (n: number, tag: string) => {
      const ids: string[] = [];
      for (let i = 0; i < n; i++) {
        const k = key();
        const r = await reserve(h.bff, s.account, {
          studentId: s.id, resourceType: 'mistake_analysis', idempotencyKey: k, requestFingerprint: meteringFingerprint('grade', { k }),
          quantity: 1, contentFingerprint: itemFingerprint(`aya:synthetic-${tag}-${i}`), sourceMethod: 'grade',
        });
        assert.equal(r.outcome, 'reserved');
        ids.push(usageIdFor(k));
      }
      return ids;
    };
    const early = await make(15, 'early');
    await h.advance(5 * MIN);
    const late = await make(15, 'late');
    await h.advance(6 * MIN);  // early 已过 10 分钟的过期时间，late 还没有
    const [settles, ...expiries] = await Promise.all([
      Promise.all([...early, ...late].map((u) => settle(h.bff, s.account, u, 1))),
      runExpiry(h.scheduler, 3), runExpiry(h.scheduler, 3), runExpiry(h.scheduler, 3),
    ]);
    const settledIds = settles.filter((r) => r.outcome === 'settled').map((r) => r.usage_id).sort();
    assert.deepEqual(settledIds, [...late].sort(), '只有未到期的被结算');
    assert.ok(settles.filter((r) => r.outcome === 'conflict').every((r) => r.state === 'expired'));
    assert.equal(expiries.reduce((a, b) => a + b, 0), 15, '到期的 15 笔都由过期任务处理，且只处理一次');
    assert.deepEqual(await counters(h, s.id, 'mistake_analysis'), { granted: 300, reserved: 0, settled: 15 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 6：24 小时复看——首次扣 1、处理中复看 409、23 小时后扣 0、25 小时后扣 1；兄弟姐妹各扣各的', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [s, sibling] = [fam.students[0]!, fam.students[1]!];
    const cfp = itemFingerprint('aya:synthetic-0301-07');
    const call = (student: typeof s, learning: FakeLearning) => meteredCall(deps(learning), {
      accountId: student.account, studentId: student.id, resourceType: 'mistake_analysis',
      idempotencyKey: key(), body: gradeBody(student.id), contentFingerprint: cfp,
    });

    // 首次处理中（学习服务还没返回）时复看 → dedup_in_progress
    let inFlight: unknown;
    const first = new FakeLearning(async () => {
      inFlight = await call(s, new FakeLearning());
      return SUCCESS;
    });
    const r1 = await call(s, first);
    assert.equal(r1.status, 'ok');
    assert.deepEqual(inFlight, { status: 'dedup_in_progress' });
    assert.deepEqual(await counters(h, s.id, 'mistake_analysis'), { granted: 300, reserved: 0, settled: 1 });

    await h.advance(23 * HOUR);
    const r2 = await call(s, new FakeLearning());
    assert.deepEqual([r2.status, r2.status === 'ok' && r2.dedup, r2.status === 'ok' && r2.charged], ['ok', true, false]);
    assert.deepEqual(await counters(h, s.id, 'mistake_analysis'), { granted: 300, reserved: 0, settled: 1 });

    const r3 = await call(sibling, new FakeLearning());
    assert.deepEqual([r3.status, r3.status === 'ok' && r3.charged], ['ok', true], '兄弟姐妹看同一道题各扣各的');

    await h.advance(2 * HOUR);
    const r4 = await call(s, new FakeLearning());
    assert.deepEqual([r4.status, r4.status === 'ok' && r4.charged], ['ok', true]);
    assert.deepEqual(await counters(h, s.id, 'mistake_analysis'), { granted: 300, reserved: 0, settled: 2 });
    assert.ok(await ledgerConsistent(h));
  });

  test('场景 6：两个连接同时第一次看同一道题——只扣 1 次', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const cfp = itemFingerprint('aya:synthetic-0301-08');
    const results = await Promise.all(Array.from({ length: 5 }, () => {
      const k = key();
      return reserve(h.bff, s.account, { studentId: s.id, resourceType: 'mistake_analysis', idempotencyKey: k,
                                         requestFingerprint: meteringFingerprint('grade', { k }), quantity: 1, contentFingerprint: cfp,
                                         sourceMethod: 'grade' });
    }));
    const outcomes = results.map((r) => r.outcome);
    assert.equal(outcomes.filter((o) => o === 'reserved').length, 1);
    assert.equal(outcomes.filter((o) => o === 'dedup_in_progress').length, 4);
    assert.ok(await ledgerConsistent(h));
  });

  test('跨学生写入：请求体的 student_ref 必须是被扣费的学生', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual', children: 2 });
    const [s, sibling] = [fam.students[0]!, fam.students[1]!];
    const fake = new FakeLearning();
    await assert.rejects(meteredCall(deps(fake), {
      accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: key(),
      body: { context: { subject: 'aya', student_ref: sibling.id }, attempts: [] },
    }), TypeError);
    assert.equal(fake.calls.length, 0);
    assert.equal((await reservationsByStudent(h, s.id)).length, 0);
    const ok = await meteredCall(deps(fake), {
      accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: key(),
      body: { context: { subject: 'aya' }, attempts: [] },
    });
    assert.equal(ok.status, 'ok');
    assert.equal((fake.lastBody?.['context'] as { student_ref: string }).student_ref, s.id, '没给 student_ref 时由 BFF 补上被扣费的学生');
  });

  test('选科并发：基础会员同时选两科，只有一科成功', async () => {
    // 已上线学科在目录里是 beta，只对试点开放（0015）：用试点家庭，档位改成 basic（只能开 1 科）
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    await h.su.query(`UPDATE platform.subscription SET tier = 'basic' WHERE student_id = $1 AND status = 'active'`, [s.id]);
    const enroll = async (subject: string) => {
      const c = await h.bff.connect();
      try {
        await c.query('BEGIN');
        await c.query("SELECT set_config('app.account_id', $1, true)", [fam.guardian]);
        const { rows } = await c.query('SELECT platform.enroll_subject($1, $2) AS r', [s.id, subject]);
        await c.query('SELECT pg_sleep(0.05)');  // 让两个事务重叠
        await c.query('COMMIT');
        return rows[0].r.outcome as string;
      } finally {
        c.release();
      }
    };
    const outcomes = (await Promise.all([enroll('aya'), enroll('wenli')])).sort();
    assert.deepEqual(outcomes, ['enrolled', 'limit_reached']);
    const { rows } = await h.su.query(`SELECT count(*)::int AS n FROM platform.subject_enrollment WHERE student_id = $1 AND status = 'active'`, [s.id]);
    assert.equal(rows[0].n, 1);
  });

  test('续期：周期结束后定时任务按配置发放下一周期', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    await h.advance(31 * 24 * HOUR);
    assert.ok(await runRenewal(h.scheduler) >= 1);
    assert.deepEqual(await counters(h, s.id, 'diagnosis_full'), { granted: 6, reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  // 把计量时钟推到下一个上海时间 10:00（UTC 02:00），避开宵禁
  async function toDaytime(): Promise<void> {
    const d = new Date(h.clockMs());
    let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 2, 0, 0);
    if (t <= h.clockMs()) t += 24 * HOUR;
    await h.setClock(t);
  }
  async function asAccount<T>(account: string, sql: string, params: unknown[]): Promise<T> {
    const c = await h.bff.connect();
    try {
      await c.query('BEGIN');
      await c.query("SELECT set_config('app.account_id', $1, true)", [account]);
      const { rows } = await c.query(sql, params);
      await c.query('SELECT pg_sleep(0.02)');  // 让并发事务重叠
      await c.query('COMMIT');
      return rows[0].r as T;
    } finally {
      c.release();
    }
  }
  const reserveMinutes = (s: { id: string; account: string }, k: string, qty: number) => reserve(h.bff, s.account, {
    studentId: s.id, resourceType: 'ai_active_time', idempotencyKey: k,
    requestFingerprint: meteringFingerprint('grade', { k }), quantity: qty, sourceMethod: 'grade',
  });

  test('单日上限并发（0015）：20 笔各 30 分钟同时预留，上限 180 分钟，恰好 6 笔成功', async () => {
    await toDaytime();
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const results = await Promise.all(Array.from({ length: 20 }, () => reserveMinutes(s, key(), 30)));
    assert.equal(results.filter((r) => r.outcome === 'reserved').length, 6);
    assert.equal(results.filter((r) => r.outcome === 'daily_cap_reached').length, 14);
    assert.ok(await ledgerConsistent(h));
  });

  test('单日上限附近的同 key 并发重试（0015）：都拿到同一笔预留，不被上限拒绝', async () => {
    await toDaytime();
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    for (let i = 0; i < 5; i++) assert.equal((await reserveMinutes(s, key(), 30)).outcome, 'reserved');
    const k = key();
    const results = await Promise.all(Array.from({ length: 10 }, () => reserveMinutes(s, k, 30)));
    assert.deepEqual([...new Set(results.map((r) => r.outcome))].sort().filter((o) => o !== 'existing'), ['reserved']);
    assert.equal((await reservationsByStudent(h, s.id)).length, 6);
  });

  test('留存态自练并发（0015）：10 次同时自练，恰好 5 次放行', async () => {
    await toDaytime();
    const fam = await createFamily(h, { source: 'trial' });
    const s = fam.students[0]!;
    await h.advance(14 * 24 * HOUR + 1000);
    await runRenewal(h.scheduler);
    await toDaytime();
    const results = await Promise.all(Array.from({ length: 10 }, () =>
      asAccount<{ outcome: string }>(s.account, 'SELECT platform.meter_practice($1, $2) AS r', [s.id, key()])));
    assert.equal(results.filter((r) => r.outcome === 'allowed').length, 5);
    assert.equal(results.filter((r) => r.outcome === 'daily_limit_reached').length, 5);
  });

  test('试用领取并发（0015）：同一家庭两个孩子同时开试用，只有一个成功', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const guardian = fam.guardian;
    const kids = [randomUUID(), randomUUID()];
    const household = randomUUID();
    await h.su.query(`INSERT INTO platform.household (id, created_by_account) VALUES ($1, $2)`, [household, guardian]);
    for (const id of kids) {
      const account = randomUUID();
      await h.su.query(`INSERT INTO platform.account (id, kind) VALUES ($1, 'student')`, [account]);
      await h.su.query(`INSERT INTO platform.student_profile (id, account_id, household_id, grade, region) VALUES ($1, $2, $3, 9, 'nanjing')`,
                       [id, account, household]);
      await h.su.query(`INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via)
                        VALUES ($1, $2, 'household_creator')`, [guardian, id]);
    }
    const results = await Promise.all(kids.map((id) =>
      asAccount<{ outcome: string }>(guardian, 'SELECT platform.meter_open_subscription($1, $2) AS r', [id, 'trial'])));
    assert.deepEqual(results.map((r) => r.outcome).sort(), ['opened', 'trial_already_claimed']);
  });

  test('试用 14 天到期不续期；留存态的计量请求被拒，不调学习服务（0015）', async () => {
    const fam = await createFamily(h, { source: 'trial' });
    const s = fam.students[0]!;
    await h.advance(14 * 24 * HOUR + 1000);
    assert.ok(await runRenewal(h.scheduler) >= 1);
    const fake = new FakeLearning();
    const r = await meteredCall(deps(fake), {
      accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: key(),
      body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] },
    });
    assert.deepEqual(r, { status: 'retained_no_ai' });
    assert.equal(fake.calls.length, 0);
    assert.ok(await ledgerConsistent(h));
  });

  test('宵禁时段的计量请求被拒，不调学习服务（0015）', async () => {
    const fam = await createFamily(h, { source: 'pilot_virtual' });
    const s = fam.students[0]!;
    const start = h.clockMs();
    // 推进到上海时间 23:00（UTC 15:00）
    const d = new Date(start);
    const target = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 15, 0, 0) + (d.getUTCHours() >= 15 ? 24 * HOUR : 0);
    await h.setClock(target);
    const fake = new FakeLearning();
    const r = await meteredCall(deps(fake), {
      accountId: s.account, studentId: s.id, resourceType: 'diagnosis_full', idempotencyKey: key(),
      body: { context: { subject: 'aya', student_ref: s.id }, attempts: [] },
    });
    assert.deepEqual(r, { status: 'curfew' });
    assert.equal(fake.calls.length, 0);
    assert.equal((await reservationsByStudent(h, s.id)).length, 0);
  });
});
