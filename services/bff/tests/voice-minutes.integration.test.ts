// 语音分钟计量辅助的库上测试（冲刺 4-I 第二部分）：一次性 Postgres + 全部合成数据（规则 8），沿用 gate0 的夹具与计量时钟。
// 只调用现有计量函数（meter_reserve / meter_settle / meter_release / meter_expire_due），不改任何函数、额度或配置；
// 宵禁与单日上限用家长经 set_time_guard 设定（与生产路径相同）。每个场景之后断言两个对账视图为零行。
import assert from 'node:assert/strict';
import { after, before, describe, test } from 'node:test';
import { runExpiry, runRenewal } from '../src/metering/jobs.ts';
import { meteringFingerprint } from '../src/metering/fingerprint.ts';
import { reserve } from '../src/metering/meter.ts';
import { usageIdFor } from '../src/metering/uuid.ts';
import {
  MAX_ESTIMATED_MINUTES, releaseVoiceMinutes, reserveVoiceMinutes, SETTLE_MARGIN_MS, settleVoiceMinutes, VOICE_RESERVATION_TTL_MS,
  withVoiceMinutes, type VoiceGrant, type VoiceMeteringEvent, type VoiceMeterDeps, type VoiceReservation, type VoiceSessionOutcome,
} from '../src/voice/minutes.ts';
import { counters, createFamily, HAS_DB, key, ledgerConsistent, setupHarness, type Family, type Harness } from './harness.ts';

const MIN = 60_000;
const HOUR = 60 * MIN;

describe('语音分钟计量（voice_realtime）', { skip: HAS_DB ? false : '没有 PGHOST（make bff-test 会起一次性 Postgres）' }, () => {
  let h: Harness;
  before(async () => { h = await setupHarness(); });
  after(async () => { await h?.close(); });

  const events: VoiceMeteringEvent[] = [];
  const deps = (): VoiceMeterDeps => ({ pool: h.bff, now: h.clockMs, onEvent: (e) => events.push(e) });

  // 合成会话：只返回一个时长，记录被开了几次、拿到的授权
  function fakeSession(outcome: VoiceSessionOutcome) {
    const grants: VoiceGrant[] = [];
    return { grants, run: async (g: VoiceGrant): Promise<VoiceSessionOutcome> => { grants.push(g); return outcome; } };
  }
  // 把计量时钟推到下一个上海时间 hh:mm（UTC = 上海 - 8）
  async function toShanghai(hh: number, mm = 0): Promise<void> {
    const d = new Date(h.clockMs());
    let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), (hh + 16) % 24, mm, 0);
    while (t <= h.clockMs()) t += 24 * HOUR;
    await h.setClock(t);
  }
  async function pilotKid(): Promise<{ fam: Family; s: Family['students'][number] }> {
    const fam = await createFamily(h, { source: 'pilot_virtual' });  // PRO：voice_realtime 按最新发布配置（240 或 300 分钟）
    return { fam, s: fam.students[0]! };
  }
  async function asAccount<T>(account: string, sql: string, params: unknown[]): Promise<T> {
    const c = await h.bff.connect();
    try {
      await c.query('BEGIN');
      await c.query("SELECT set_config('app.account_id', $1, true)", [account]);
      const { rows } = await c.query(sql, params);
      await c.query('COMMIT');
      return rows[0].r as T;
    } finally {
      c.release();
    }
  }
  async function rows(studentId: string) {
    const r = await h.su.query(`SELECT resource_type, source_method, state::text, reserved_qty::int, settled_qty::int
                                  FROM platform.usage_reservation WHERE student_id = $1 ORDER BY reserved_at, usage_id`, [studentId]);
    return r.rows as { resource_type: string; source_method: string; state: string; reserved_qty: number; settled_qty: number | null }[];
  }
  // 只看预留与结算：发放量取决于库里最新发布的额度配置（全量跑时别的测试可能发布了新配置），与本测试无关
  const voice = async (s: { id: string }) => {
    const c = await counters(h, s.id, 'voice_realtime');
    return { reserved: c.reserved, settled: c.settled };
  };

  test('库里的口径没变：voice_realtime 按分钟、向上取整、voice 池、单笔最多 30、宵禁适用、不是 BFF 专属', async () => {
    const { rows: [rt] } = await h.su.query(`SELECT unit, rounding, pool, max_per_reservation::int AS max, curfew_applies, bff_only,
                                                    extract(epoch FROM reservation_ttl)::int AS ttl
                                               FROM platform.resource_type WHERE code = 'voice_realtime'`);
    assert.deepEqual(rt, { unit: 'minute', rounding: 'ceil', pool: 'voice', max: 30, curfew_applies: true, bff_only: false, ttl: 1800 });
    // 辅助里的有效期常量与库一致；封顶值 = floor((有效期 − 结算余量) / 1 分钟) = 29，且不超过库的单笔上限
    assert.equal(VOICE_RESERVATION_TTL_MS, rt.ttl * 1000);
    assert.equal(MAX_ESTIMATED_MINUTES, Math.floor((rt.ttl * 1000 - SETTLE_MARGIN_MS) / MIN));
    assert.ok(MAX_ESTIMATED_MINUTES <= rt.max);
  });

  test('向上取整：0 秒、59.5 秒、59.999 秒、恰好 1 分钟、多 1 毫秒、恰好 2 分钟，以及超出预留按预留结算', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const cases: [number, number, number][] = [  // [实际毫秒, 应扣分钟, 超出预留没扣的分钟]，每笔预留 2 分钟
      [0, 0, 0], [59_500, 1, 0], [59_999, 1, 0], [60_000, 1, 0], [60_001, 2, 0], [120_000, 2, 0], [120_001, 2, 1], [600_000, 2, 8],
    ];
    let total = 0;
    for (const [ms, charged, unbilled] of cases) {
      const sess = fakeSession({ kind: 'completed', durationMs: ms });
      events.length = 0;
      const r = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 2 }, sess.run);
      assert.equal(r.status, 'completed', `${ms} ms`);
      assert.ok(r.status === 'completed');
      assert.deepEqual([r.chargedMinutes, r.unbilledMinutes, r.charged], [charged, unbilled, charged > 0], `${ms} ms`);
      assert.deepEqual(events, unbilled > 0 ? ['overrun_unbilled'] : [], `${ms} ms`);
      total += charged;
      assert.deepEqual(await voice(s), { reserved: 0, settled: total }, `${ms} ms`);
    }
    const byNum = (a: number, b: number) => a - b;  // 同一时刻的预留，行序不定
    assert.deepEqual((await rows(s.id)).map((x) => x.settled_qty!).sort(byNum), cases.map((c) => c[1]).sort(byNum));
    assert.ok(await ledgerConsistent(h));
  });

  test('预留 → 结算：预留期间占额度，结算后按实际分钟扣、预留归零；会话拿到的最长时长留出结算余量', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const k = key();
    const r = await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes: 10 });
    assert.ok(r.status === 'reserved' && !r.resumed && !r.capped);
    assert.equal(r.reservation.usageId, usageIdFor(k));
    assert.deepEqual(await voice(s), { reserved: 10, settled: 0 });
    await h.advance(7 * MIN);
    const st = await settleVoiceMinutes(h.bff, s.account, r.reservation, 6 * MIN + 1);
    assert.deepEqual(st, { status: 'settled', usageId: usageIdFor(k), billableMinutes: 7, chargedMinutes: 7, unbilledMinutes: 0, idempotent: false });
    assert.deepEqual(await voice(s), { reserved: 0, settled: 7 });

    // 整段流程：预计 30 分钟封顶为 29（有效期 30 分钟减结算余量 1 分钟），并报出 estimate_capped；会话最长 29 分钟
    const sess = fakeSession({ kind: 'completed', durationMs: 29 * MIN });
    events.length = 0;
    const full = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 30 }, sess.run);
    assert.deepEqual(sess.grants.map((g) => [g.reservedMinutes, g.maxDurationMs]), [[29, 30 * MIN - SETTLE_MARGIN_MS]]);
    assert.deepEqual(events, ['estimate_capped']);
    assert.ok(full.status === 'completed' && full.chargedMinutes === 29);
    const short = fakeSession({ kind: 'completed', durationMs: 1000 });
    await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 5 }, short.run);
    assert.equal(short.grants[0]!.maxDurationMs, 5 * MIN, '短预留以预留分钟为限');
    assert.deepEqual(await voice(s), { reserved: 0, settled: 7 + 29 + 1 });
    assert.ok(await ledgerConsistent(h));
  });

  test('失败回滚：会话确定失败整笔释放；直接释放也归零；结果未知不释放，过期任务回收，之后同 key 要换新 key', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const failed = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 8 },
                                          fakeSession({ kind: 'failed' }).run);
    assert.equal(failed.status, 'failed');
    assert.deepEqual(await voice(s), { reserved: 0, settled: 0 });

    const k = key();
    const r = await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes: 4 });
    assert.ok(r.status === 'reserved');
    assert.deepEqual(await releaseVoiceMinutes(h.bff, s.account, r.reservation), { status: 'released', usageId: usageIdFor(k), idempotent: false });
    assert.deepEqual(await releaseVoiceMinutes(h.bff, s.account, r.reservation), { status: 'released', usageId: usageIdFor(k), idempotent: true });
    // 释放之后再结算：不扣
    const late = await settleVoiceMinutes(h.bff, s.account, r.reservation, MIN);
    assert.equal(late.status, 'not_charged');
    assert.deepEqual(await voice(s), { reserved: 0, settled: 0 });

    const ku = key();
    events.length = 0;
    const unknown = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: ku, estimatedMinutes: 6 },
                                           fakeSession({ kind: 'unknown' }).run);
    assert.deepEqual([unknown, events], [{ status: 'unknown', usageId: usageIdFor(ku), cause: 'session_reported_unknown' },
                                         ['session_result_unknown']]);
    assert.deepEqual(await voice(s), { reserved: 6, settled: 0 }, '结果未知不释放');
    // 结果未知后同 key 重试：找回仍在预留中的那笔，不再开会话
    const retry = fakeSession({ kind: 'completed', durationMs: MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: ku, estimatedMinutes: 6 }, retry.run),
                     { status: 'in_progress', usageId: usageIdFor(ku) });
    assert.equal(retry.grants.length, 0, '结果未知后同 key 重试不再开会话');
    assert.deepEqual(await voice(s), { reserved: 6, settled: 0 });
    await h.advance(31 * MIN);
    assert.ok(await runExpiry(h.scheduler) >= 1);
    assert.deepEqual(await voice(s), { reserved: 0, settled: 0 }, '过期任务回收');
    const sess = fakeSession({ kind: 'completed', durationMs: MIN });
    const again = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: ku, estimatedMinutes: 6 }, sess.run);
    assert.deepEqual([again.status, sess.grants.length], ['expired_retry_with_new_key', 0]);
    assert.deepEqual((await rows(s.id)).map((x) => x.state).sort(), ['expired', 'released', 'released']);
    assert.ok(await ledgerConsistent(h));
  });

  test('预留过期后才结算：不扣（宁可少扣），并报出事件', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    events.length = 0;
    const r = await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 3 },
                                     async () => { await h.advance(31 * MIN); return { kind: 'completed', durationMs: 2 * MIN }; });
    assert.ok(r.status === 'completed');
    assert.deepEqual([r.charged, r.chargedMinutes, r.billableMinutes, events], [false, 0, 2, ['late_settle_not_charged']]);
    assert.deepEqual(await voice(s), { reserved: 3, settled: 0 }, '过期任务跑到之前仍算预留中');
    await runExpiry(h.scheduler);
    assert.deepEqual(await voice(s), { reserved: 0, settled: 0 });
    assert.ok(await ledgerConsistent(h));
  });

  test('幂等：同 key 重放不重复扣——预留、结算、释放、整段流程、并发预留', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const input = (k: string, estimatedMinutes = 5) => ({ accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes });

    // 整段流程：结算后同 key 重放不再开会话、不再扣
    const k1 = key();
    const first = fakeSession({ kind: 'completed', durationMs: 90_000 });
    const done = await withVoiceMinutes(deps(), input(k1), first.run);
    assert.ok(done.status === 'completed' && done.chargedMinutes === 2);
    const replay = fakeSession({ kind: 'completed', durationMs: 90_000 });
    assert.deepEqual(await withVoiceMinutes(deps(), input(k1), replay.run),
                     { status: 'completed_previously', usageId: usageIdFor(k1), settledMinutes: 2 });
    assert.equal(replay.grants.length, 0, '重放不再开会话');
    assert.deepEqual(await withVoiceMinutes(deps(), input(k1, 6), replay.run), { status: 'conflict' }, '同 key 换预计分钟是冲突');
    assert.deepEqual(await voice(s), { reserved: 0, settled: 2 });

    // 预留：同 key 再预留拿回同一笔（resumed），额度只占一次；重放结算幂等，换时长重放是冲突且不改账
    const k2 = key();
    const a = await reserveVoiceMinutes(h.bff, input(k2, 4));
    const b = await reserveVoiceMinutes(h.bff, input(k2, 4));
    assert.ok(a.status === 'reserved' && !a.resumed && b.status === 'reserved' && b.resumed);
    assert.deepEqual(b.reservation, a.reservation);
    assert.deepEqual(await voice(s), { reserved: 4, settled: 2 });
    const s1 = await settleVoiceMinutes(h.bff, s.account, a.reservation, 150_000);
    const s2 = await settleVoiceMinutes(h.bff, s.account, b.reservation, 150_000);
    assert.ok(s1.status === 'settled' && !s1.idempotent && s1.chargedMinutes === 3);
    assert.ok(s2.status === 'settled' && s2.idempotent && s2.chargedMinutes === 3);
    assert.deepEqual(await settleVoiceMinutes(h.bff, s.account, a.reservation, 30_000),
                     { status: 'conflict', usageId: usageIdFor(k2), state: 'settled', settledMinutes: 3 });
    assert.deepEqual(await releaseVoiceMinutes(h.bff, s.account, a.reservation), { status: 'conflict', usageId: usageIdFor(k2), state: 'settled' });
    assert.deepEqual(await reserveVoiceMinutes(h.bff, input(k2, 4)), { status: 'settled_previously', usageId: usageIdFor(k2), settledMinutes: 3 });
    assert.deepEqual(await voice(s), { reserved: 0, settled: 5 });

    // 同 key 并发重试：只有一笔预留，额度只占一次
    const k3 = key();
    const many = await Promise.all(Array.from({ length: 10 }, () => reserveVoiceMinutes(h.bff, input(k3, 7))));
    assert.ok(many.every((r) => r.status === 'reserved' && r.reservation.usageId === usageIdFor(k3)));
    assert.equal(many.filter((r) => r.status === 'reserved' && !r.resumed).length, 1);
    assert.deepEqual(await voice(s), { reserved: 7, settled: 5 });
    // 底层结算接口：同一笔预留的结算被重复提交（例如结算请求超时重试），按同一时长重放幂等，只扣一次。
    // 整段流程不会为同 key 开第二次会话（见下方并发用例），这里只验证结算本身的幂等
    const settles = await Promise.all(many.slice(0, 5).map((r) =>
      settleVoiceMinutes(h.bff, s.account, (r as { reservation: VoiceReservation }).reservation, 4 * MIN)));
    assert.equal(settles.filter((x) => x.status === 'settled' && !x.idempotent).length, 1);
    assert.deepEqual(await voice(s), { reserved: 0, settled: 9 });

    // 整段流程同 key 并发调用：只有一次拿到新预留并开会话，其余拿到 in_progress（或已结算后的 completed_previously），只扣一次
    const k5 = key();
    const conc = fakeSession({ kind: 'completed', durationMs: 2 * MIN });
    const results = await Promise.all(Array.from({ length: 8 }, () => withVoiceMinutes(deps(), input(k5, 3), conc.run)));
    assert.equal(conc.grants.length, 1, '同 key 并发只开一次会话');
    assert.equal(results.filter((x) => x.status === 'completed').length, 1);
    assert.ok(results.every((x) => ['completed', 'in_progress', 'completed_previously'].includes(x.status)), JSON.stringify(results));
    assert.deepEqual(await voice(s), { reserved: 0, settled: 11 });

    // 会话期间这笔已被别处按同样分钟数结算（例如结算请求重试先到）：本次结算是幂等重放，报 completed_previously，不报 charged
    const k6 = key();
    const twice = await withVoiceMinutes(deps(), input(k6, 3), async (g) => {
      await settleVoiceMinutes(h.bff, s.account, { usageId: g.usageId, idempotencyKey: g.idempotencyKey, reservedMinutes: g.reservedMinutes,
                                                   expiresAt: '' }, 2 * MIN);
      return { kind: 'completed', durationMs: 2 * MIN };
    });
    assert.deepEqual(twice, { status: 'completed_previously', usageId: usageIdFor(k6), settledMinutes: 2 });
    assert.deepEqual(await voice(s), { reserved: 0, settled: 13 });

    // 释放后同 key 重放：之前已失败，不再开会话
    const k4 = key();
    await withVoiceMinutes(deps(), input(k4), fakeSession({ kind: 'failed' }).run);
    const after4 = fakeSession({ kind: 'completed', durationMs: MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), input(k4), after4.run), { status: 'failed_previously', usageId: usageIdFor(k4) });
    assert.equal(after4.grants.length, 0);
    assert.equal((await rows(s.id)).length, 6);
    assert.ok(await ledgerConsistent(h));
  });

  test('结果未知：会话抛异常、结算抛异常、时长非法都不释放不结算，发 session_result_unknown 并返回 unknown；小数毫秒向上取整', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const input = (k: string, estimatedMinutes = 4) => ({ accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes });
    let held = 0;

    // 会话回调抛异常
    const k1 = key();
    events.length = 0;
    const threw = await withVoiceMinutes(deps(), input(k1), async () => { throw new Error('synthetic session crash'); });
    assert.deepEqual([threw, events], [{ status: 'unknown', usageId: usageIdFor(k1), cause: 'session_threw' }, ['session_result_unknown']]);
    held += 4;
    assert.deepEqual(await voice(s), { reserved: held, settled: 0 }, '会话抛异常不释放');

    // 结算抛异常（会话结束后连接断开：不知道库里是否已结算）
    const k2 = key();
    let broken = false;
    const flaky = { connect: () => (broken ? Promise.reject(new Error('synthetic connection loss')) : h.bff.connect()) } as unknown as VoiceMeterDeps['pool'];
    events.length = 0;
    const lost = await withVoiceMinutes({ ...deps(), pool: flaky }, input(k2), async () => { broken = true; return { kind: 'completed', durationMs: MIN }; });
    assert.deepEqual([lost, events], [{ status: 'unknown', usageId: usageIdFor(k2), cause: 'settle_threw' }, ['session_result_unknown']]);
    held += 4;
    assert.deepEqual(await voice(s), { reserved: held, settled: 0 }, '结算抛异常不释放、不结算');

    // 非法时长：NaN、负数、无穷、超出安全整数
    for (const bad of [Number.NaN, -1, -0.5, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER * 2]) {
      const k = key();
      events.length = 0;
      const r = await withVoiceMinutes(deps(), input(k), fakeSession({ kind: 'completed', durationMs: bad }).run);
      assert.deepEqual([r, events], [{ status: 'unknown', usageId: usageIdFor(k), cause: 'invalid_duration' }, ['session_result_unknown']], String(bad));
      held += 4;
      assert.deepEqual(await voice(s), { reserved: held, settled: 0 }, `${bad} 不释放、不结算`);
    }

    // 小数毫秒先向上取整成整数毫秒：59 999.2 → 60 000 → 1 分钟；60 000.5 → 60 001 → 2 分钟；0.3 → 1 毫秒 → 1 分钟
    let settled = 0;
    for (const [ms, want] of [[59_999.2, 1], [60_000.5, 2], [0.3, 1]] as const) {
      const r = await withVoiceMinutes(deps(), input(key()), fakeSession({ kind: 'completed', durationMs: ms }).run);
      assert.ok(r.status === 'completed' && r.charged && r.chargedMinutes === want, `${ms} ms`);
      settled += want;
    }
    assert.deepEqual(await voice(s), { reserved: held, settled });

    // 过期任务回收全部“结果未知”的预留
    await h.advance(31 * MIN);
    await runExpiry(h.scheduler);
    assert.deepEqual(await voice(s), { reserved: 0, settled });
    assert.ok(await ledgerConsistent(h));
  });

  test('预留到手时已来不及开会话（计时偏差）：报 no_time_left 并释放，不开会话', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const k = key();
    events.length = 0;
    const sess = fakeSession({ kind: 'completed', durationMs: MIN });
    // BFF 时钟比库里的计量时钟快 30 分钟：预留的有效期在 BFF 看来已不足结算余量
    const r = await withVoiceMinutes({ ...deps(), now: () => h.clockMs() + 30 * MIN },
                                     { accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes: 5 }, sess.run);
    assert.deepEqual([r, events, sess.grants.length], [{ status: 'failed', usageId: usageIdFor(k) }, ['no_time_left'], 0]);
    assert.deepEqual(await voice(s), { reserved: 0, settled: 0 }, 'no_time_left 时释放');
    assert.deepEqual((await rows(s.id)).map((x) => x.state), ['released']);
    // 快 25 分钟：剩余有效期 5 分钟，扣掉结算余量 1 分钟，会话最长 4 分钟（短于预留的 10 分钟）
    const skew = fakeSession({ kind: 'completed', durationMs: 4 * MIN });
    const r2 = await withVoiceMinutes({ ...deps(), now: () => h.clockMs() + 25 * MIN },
                                      { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 10 }, skew.run);
    assert.deepEqual(skew.grants.map((g) => [g.reservedMinutes, g.maxDurationMs]), [[10, 5 * MIN - SETTLE_MARGIN_MS]]);
    assert.ok(r2.status === 'completed' && r2.chargedMinutes === 4);
    assert.ok(await ledgerConsistent(h));
  });

  test('资源只能是 voice_realtime：来源方法 teach、不碰 ai_active_time 额度；别的资源名直接拒绝、不落库', async () => {
    await toShanghai(10);
    const { s } = await pilotKid();
    const aiBefore = await counters(h, s.id, 'ai_active_time');
    await assert.rejects(reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 3,
                                                      resourceType: 'ai_active_time' }), TypeError);
    await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 3, resourceType: 'voice_realtime' },
                           fakeSession({ kind: 'completed', durationMs: 2 * MIN }).run);
    assert.deepEqual((await rows(s.id)).map((x) => [x.resource_type, x.source_method, x.state, x.settled_qty]),
                     [['voice_realtime', 'teach', 'settled', 2]]);
    assert.deepEqual(await counters(h, s.id, 'ai_active_time'), aiBefore, '不同时扣 AI 互动时长（待人确认 #4 默认）');
    // 预计分钟先封顶到 29 再预留（原来 31 会被库的单笔上限 30 拒绝）：返回值标出 capped，库里只占 29
    const k = key();
    const big = await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes: 31 });
    assert.ok(big.status === 'reserved' && big.capped && !big.resumed && big.reservation.reservedMinutes === MAX_ESTIMATED_MINUTES);
    // 同 key 以封顶后的值重试是同一笔（指纹按封顶后的分钟算）
    const same = await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: k, estimatedMinutes: 29 });
    assert.ok(same.status === 'reserved' && same.resumed && !same.capped);
    assert.deepEqual(await voice(s), { reserved: 29, settled: 2 });
    await releaseVoiceMinutes(h.bff, s.account, big.reservation);
    assert.ok(await ledgerConsistent(h));
  });

  test('宵禁同样生效：宵禁内拒绝、不开会话、不落库；临近宵禁超出可用分钟返回 curfew_soon，缩短后可以预留', async () => {
    const { s } = await pilotKid();
    await toShanghai(23);
    const sess = fakeSession({ kind: 'completed', durationMs: MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 5 }, sess.run),
                     { status: 'curfew' });
    await toShanghai(5, 30);
    assert.deepEqual(await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 5 }),
                     { status: 'curfew' }, '凌晨仍在缺省宵禁 22:00–06:00 内');
    await toShanghai(21, 50);
    assert.deepEqual(await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 15 }, sess.run),
                     { status: 'curfew_soon', minutesUntilCurfew: 10 });
    assert.equal(sess.grants.length, 0, '被拒绝的请求不开会话');
    assert.equal((await rows(s.id)).length, 0);
    const ok = await reserveVoiceMinutes(h.bff, { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 10 });
    assert.equal(ok.status, 'reserved');
    // 21:50 预留 10 分钟、会话结果未知；21:55 同 key 重试：不得拿到会话（旧预留 22:20 才过期，重开会跨进 22:00 宵禁）
    const kc = key();
    const lost = fakeSession({ kind: 'unknown' });
    assert.equal((await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: kc, estimatedMinutes: 10 }, lost.run)).status,
                 'unknown');
    assert.equal(lost.grants.length, 1);
    await h.advance(5 * MIN);
    const late = fakeSession({ kind: 'completed', durationMs: 10 * MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: kc, estimatedMinutes: 10 }, late.run),
                     { status: 'in_progress', usageId: usageIdFor(kc) });
    assert.equal(late.grants.length, 0, '21:55 同 key 重试不开会话');
    // 家长收紧宵禁（21:00 起）同样生效
    const { s: t, fam } = await pilotKid();
    assert.equal((await asAccount<{ outcome: string }>(fam.guardian, 'SELECT platform.set_time_guard($1, NULL, $2::time, $3::time) AS r',
                                                     [t.id, '21:00', '07:00'])).outcome, 'saved');
    await toShanghai(21, 30);
    assert.deepEqual(await reserveVoiceMinutes(h.bff, { accountId: t.account, studentId: t.id, idempotencyKey: key(), estimatedMinutes: 5 }),
                     { status: 'curfew' });
    assert.ok(await ledgerConsistent(h));
  });

  test('单日上限同样生效：家长设 20 分钟，超出拒绝并给出今日剩余；与其他分钟类资源共用一个上限；结算后按实际分钟占用', async () => {
    await toShanghai(10);
    const { s, fam } = await pilotKid();
    assert.equal((await asAccount<{ outcome: string }>(fam.guardian, 'SELECT platform.set_time_guard($1, $2, NULL, NULL) AS r',
                                                     [s.id, 20])).outcome, 'saved');
    const input = (estimatedMinutes: number) => ({ accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes });
    const r = await reserveVoiceMinutes(h.bff, input(15));
    assert.ok(r.status === 'reserved');
    const sess = fakeSession({ kind: 'completed', durationMs: MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), input(10), sess.run), { status: 'daily_cap_reached', remainingToday: 5 });
    assert.equal(sess.grants.length, 0);
    // 结算 15 分钟的预留只用了 61 秒 → 占 2 分钟，今日还剩 18
    await settleVoiceMinutes(h.bff, s.account, r.reservation, 61_000);
    // 其他分钟类资源（AI 互动时长）同一个上限：先占 10 分钟，语音只剩 8
    const ai = await reserve(h.bff, s.account, { studentId: s.id, resourceType: 'ai_active_time', idempotencyKey: key(),
                                                 requestFingerprint: meteringFingerprint('grade', { synthetic: 'ai' }), quantity: 10,
                                                 sourceMethod: 'grade' });
    assert.equal(ai.outcome, 'reserved');
    assert.deepEqual(await reserveVoiceMinutes(h.bff, input(9)), { status: 'daily_cap_reached', remainingToday: 8 });
    assert.equal((await reserveVoiceMinutes(h.bff, input(8))).status, 'reserved');
    assert.deepEqual(await reserveVoiceMinutes(h.bff, input(1)), { status: 'daily_cap_reached', remainingToday: 0 });
    // 第二天重新计
    await toShanghai(10);
    assert.equal((await reserveVoiceMinutes(h.bff, input(20))).status, 'reserved');
    assert.ok(await ledgerConsistent(h));
  });

  test('留存态（试用到期）拒绝语音，不开会话', async () => {
    await toShanghai(10);
    const fam = await createFamily(h, { source: 'trial' });
    const s = fam.students[0]!;
    await h.advance(14 * 24 * HOUR + 1000);
    await runRenewal(h.scheduler);
    await toShanghai(10);
    const sess = fakeSession({ kind: 'completed', durationMs: MIN });
    assert.deepEqual(await withVoiceMinutes(deps(), { accountId: s.account, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 1 }, sess.run),
                     { status: 'retained_no_ai' });
    assert.equal(sess.grants.length, 0);
    assert.ok(await ledgerConsistent(h));
  });

  test('只认学生本人：家长账号不能替孩子预留语音分钟', async () => {
    await toShanghai(10);
    const { s, fam } = await pilotKid();
    await assert.rejects(reserveVoiceMinutes(h.bff, { accountId: fam.guardian, studentId: s.id, idempotencyKey: key(), estimatedMinutes: 1 }),
                         { code: '42501' });
    assert.equal((await rows(s.id)).length, 0);
  });
});
