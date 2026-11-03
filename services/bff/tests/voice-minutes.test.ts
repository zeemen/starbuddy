// 语音分钟计量辅助的单元测试（不连库）：向上取整、入参校验先于任何数据库调用、模块不碰网络与语音数据、没有挂到任何路由。
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { test } from 'node:test';
import type { Pool } from 'pg';
import {
  billableMinutes, MAX_ESTIMATED_MINUTES, MS_PER_MINUTE, releaseVoiceMinutes, reserveVoiceMinutes, SETTLE_MARGIN_MS, settleVoiceMinutes,
  VOICE_RESERVATION_TTL_MS, VOICE_RESOURCE, VOICE_SOURCE_METHOD, voiceFingerprint, withVoiceMinutes, type VoiceReservation,
} from '../src/voice/minutes.ts';
import { usageIdFor } from '../src/metering/uuid.ts';

// 碰到就失败的连接池：证明校验在任何数据库调用之前
const NO_DB = { connect: () => { throw new Error('database must not be touched'); } } as unknown as Pool;
const KEY = '0f8c7a52-3b1d-4e6f-9a10-2c3d4e5f6a7b';

test('按分钟向上取整：0 毫秒 0 分钟；59.x 秒 1 分钟；恰好整分钟不进位；多 1 毫秒进位', () => {
  const cases: [number, number][] = [
    [0, 0], [1, 1], [59_000, 1], [59_500, 1], [59_999, 1], [60_000, 1], [60_001, 2],
    [119_999, 2], [120_000, 2], [120_001, 3], [30 * MS_PER_MINUTE, 30], [30 * MS_PER_MINUTE + 1, 31],
  ];
  for (const [ms, want] of cases) assert.equal(billableMinutes(ms), want, `${ms} ms`);
});

test('时长只收非负整数毫秒', () => {
  for (const bad of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => billableMinutes(bad), TypeError, String(bad));
  }
});

test('指纹按预计分钟区分、同输入确定', () => {
  assert.equal(voiceFingerprint(5), voiceFingerprint(5));
  assert.notEqual(voiceFingerprint(5), voiceFingerprint(6));
  assert.match(voiceFingerprint(5), /^[0-9a-f]{64}$/);
  assert.equal(VOICE_RESOURCE, 'voice_realtime');
  assert.equal(VOICE_SOURCE_METHOD, 'teach');
});

test('预留：资源只能是 voice_realtime；key 必须是小写 UUIDv4；预计分钟必须是正整数——都在连库之前拒绝', async () => {
  const base = { accountId: KEY, studentId: KEY, idempotencyKey: KEY, estimatedMinutes: 5 };
  for (const resourceType of ['ai_active_time', 'lesson_whiteboard', 'upload', 'VOICE_REALTIME', '', 'x_<script>alert(1)</script>']) {
    // 固定文案，不回显调用方传入的资源名
    await assert.rejects(reserveVoiceMinutes(NO_DB, { ...base, resourceType }),
                         (e: unknown) => e instanceof TypeError && e.message === 'voice metering only reserves voice_realtime', resourceType);
  }
  await assert.rejects(reserveVoiceMinutes(NO_DB, { ...base, idempotencyKey: KEY.toUpperCase() }), TypeError);
  await assert.rejects(reserveVoiceMinutes(NO_DB, { ...base, idempotencyKey: 'not-a-key' }), TypeError);
  for (const estimatedMinutes of [0, -1, 1.5, Number.NaN]) {
    await assert.rejects(reserveVoiceMinutes(NO_DB, { ...base, estimatedMinutes }), TypeError, String(estimatedMinutes));
  }
  let ran = false;
  await assert.rejects(withVoiceMinutes({ pool: NO_DB }, { ...base, resourceType: 'ai_active_time' }, async () => {
    ran = true;
    return { kind: 'completed', durationMs: 0 };
  }), TypeError);
  assert.equal(ran, false, '被拒绝的请求不开会话');
});

test('预计分钟封顶：预留有效期 30 分钟减结算余量 1 分钟 = 29', () => {
  assert.equal(VOICE_RESERVATION_TTL_MS, 30 * MS_PER_MINUTE);
  assert.equal(MAX_ESTIMATED_MINUTES, Math.floor((VOICE_RESERVATION_TTL_MS - SETTLE_MARGIN_MS) / MS_PER_MINUTE));
  assert.equal(MAX_ESTIMATED_MINUTES, 29);
});

// 合成连接池：只回应 withAccount 的事务语句与一次 meter_reserve，返回给定的结果
function fakePool(reserveResult: Record<string, unknown>): Pool {
  const client = {
    query: async (sql: string) => (sql.includes('meter_reserve') ? { rows: [{ r: reserveResult }] } : { rows: [] }),
    release: () => {},
  };
  return { connect: async () => client } as unknown as Pool;
}

test('预留：库返回的 usage_id 与 BFF 由 key 派生的不同就报错，不当成预留成功、不开会话', async () => {
  const other = '1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d';
  const row = { outcome: 'reserved', usage_id: usageIdFor(other), state: 'reserved', reserved_qty: 5, expires_at: '2026-10-15T00:30:00Z' };
  const input = { accountId: KEY, studentId: KEY, idempotencyKey: KEY, estimatedMinutes: 5 };
  await assert.rejects(reserveVoiceMinutes(fakePool(row), input), /usage_id derived by the database does not match/);
  let ran = false;
  await assert.rejects(withVoiceMinutes({ pool: fakePool(row) }, input, async () => { ran = true; return { kind: 'completed', durationMs: 0 }; }),
                       /usage_id/);
  assert.equal(ran, false);
  // 对照：库返回同一个 usage_id 时正常预留
  const ok = await reserveVoiceMinutes(fakePool({ ...row, usage_id: usageIdFor(KEY) }), input);
  assert.equal(ok.status, 'reserved');
});

test('结算 / 释放：usage_id 必须由 idempotency_key 派生；时长与原因先校验', async () => {
  const good: VoiceReservation = { usageId: usageIdFor(KEY), idempotencyKey: KEY, reservedMinutes: 5, expiresAt: '2026-10-15T00:30:00Z' };
  const other = '1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d';
  await assert.rejects(settleVoiceMinutes(NO_DB, KEY, { ...good, usageId: usageIdFor(other) }, 1000), TypeError);
  await assert.rejects(releaseVoiceMinutes(NO_DB, KEY, { ...good, usageId: usageIdFor(other) }), TypeError);
  await assert.rejects(settleVoiceMinutes(NO_DB, KEY, good, -1), TypeError);
  await assert.rejects(settleVoiceMinutes(NO_DB, KEY, good, 1.5), TypeError);
  await assert.rejects(settleVoiceMinutes(NO_DB, KEY, { ...good, reservedMinutes: 0 }, 1000), TypeError);
  await assert.rejects(releaseVoiceMinutes(NO_DB, KEY, good, 'Not A Code'), TypeError);
});

test('模块只做计量：不导入网络、子进程、文件与音频相关模块，不读环境变量；没有挂到任何 HTTP 路由', () => {
  const src = readFileSync(new URL('../src/voice/minutes.ts', import.meta.url), 'utf8');
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports.sort(), ['../metering/canonical.ts', '../metering/db.ts', '../metering/meter.ts', '../metering/uuid.ts', 'pg']);
  const code = src.split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n').toLowerCase();  // 去掉注释，只查代码
  for (const banned of ['process.env', 'fetch(', 'node:http', 'node:net', 'node:fs', 'child_process', 'audio', 'wav', 'pcm', 'asr', 'tts',
                        'speech', 'transcript']) {
    assert.ok(!code.includes(banned), banned);
  }
  const httpDir = new URL('../src/http/', import.meta.url);
  for (const f of readdirSync(httpDir)) {
    assert.ok(!readFileSync(new URL(f, httpDir), 'utf8').includes('voice/minutes'), `${f} 不应引用语音计量`);
  }
  assert.ok(!readFileSync(new URL('../src/main.ts', import.meta.url), 'utf8').includes('voice/minutes'));
});
