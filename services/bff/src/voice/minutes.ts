// 平台侧语音分钟计量辅助（冲刺 4-I 第二部分；specs/voice-blind-test-sprint4/spec.md 设计要点 3、待人确认 #4 按默认）。
// 接在尚不存在的语音会话之前：不建 HTTP 路由，不接任何 ASR / TTS 供应商，不碰任何语音数据——这里只有分钟数与幂等键。
//
// 口径（全部沿用迁移 0008 / 0009 / 0015，不改任何计量函数、额度、迁移或 RLS）：
//   - 资源只能是 voice_realtime（unit = minute、rounding = ceil、pool = voice），不同时扣 ai_active_time（待人确认 #4）；
//   - 来源方法固定 teach（0009 meter_method_allowed：voice_realtime 只认 teach / grade；语音会话是讲解对话，按 teach）；
//   - 按预计分钟预留：先封顶到 MAX_ESTIMATED_MINUTES（预留有效期减结算余量，29 分钟），封顶在返回值与事件里标出；
//     封顶后仍须满足库里的 max_per_reservation（30）；
//   - 按实际时长结算：毫秒按分钟向上取整（0 毫秒 → 0 分钟；59 999 毫秒 → 1；恰好 60 000 → 1；60 001 → 2）；
//     会话报回的时长先校验为有限非负数，再向上取整成整数毫秒；非法值按“结果未知”处理；
//     meter_settle 只接受 0..reserved_qty，实际超出预留时按预留分钟结算，多出的分钟不扣、单独报出（宁可少扣，同 meter.ts 场景 5）；
//   - 失败回滚：meter_release；结果未知（会话报未知、会话抛异常、时长非法、结算抛异常）不释放也不结算，留给过期任务（与 meteredCall 相同）；
//   - 宵禁、单日上限、留存态由 meter_reserve 判定（voice_realtime 的 curfew_applies 为真、unit 为 minute），这里不重算第二套口径；
//   - 必须带 idempotency_key（小写 UUIDv4）；usage_id 由它派生（uuidv5，与 0009 meter_usage_id 一致），预留结果里的 usage_id 必须相同。
// 同 key 重放：仍在预留中 → 整段流程不再开会话（in_progress：另一个请求正在用，或上次结果未知；
//   重开会按旧预留的有效期开出新会话，可能跨进宵禁）；已结算 → 不再开会话、不再扣；已释放 → 之前已失败；已过期 → 换新 key。
import type { Pool } from 'pg';
import { canonical, sha256Hex } from '../metering/canonical.ts';
import { withAccount } from '../metering/db.ts';
import { release, settle } from '../metering/meter.ts';
import { isUuidV4, usageIdFor } from '../metering/uuid.ts';

export const VOICE_RESOURCE = 'voice_realtime';
export const VOICE_SOURCE_METHOD = 'teach';
export const MS_PER_MINUTE = 60_000;
// 会话最长时长要给结算留出余量：预留过期后 meter_settle 不再结算（0009），整段会话就白送了
export const SETTLE_MARGIN_MS = 60_000;
// 与 0008 voice_realtime.reservation_ttl（30 分钟）一致；集成测试比对库里的值，库里改了这里要跟着改
export const VOICE_RESERVATION_TTL_MS = 30 * MS_PER_MINUTE;
// 单笔预留的分钟上限：预留超过“有效期减结算余量”的部分，会话也用不上（29）
export const MAX_ESTIMATED_MINUTES = Math.floor((VOICE_RESERVATION_TTL_MS - SETTLE_MARGIN_MS) / MS_PER_MINUTE);
const REASON = /^[a-z0-9_]{1,48}$/;  // 与 meter_release 的 reason 校验一致

/** 实际时长（毫秒，非负整数）→ 计费分钟：按分钟向上取整（0008 voice_realtime：unit = minute、rounding = ceil） */
export function billableMinutes(durationMs: number): number {
  if (!Number.isSafeInteger(durationMs) || durationMs < 0) {
    throw new TypeError('durationMs must be a non-negative integer number of milliseconds');
  }
  return Math.ceil(durationMs / MS_PER_MINUTE);
}

/** 预留的请求指纹：同一个 key 必须对应同一个预计分钟数（封顶后的值），否则库返回 conflict（meter_same_key） */
export function voiceFingerprint(estimatedMinutes: number): string {
  return sha256Hex(canonical({ method: VOICE_SOURCE_METHOD, resource: VOICE_RESOURCE, estimated_minutes: estimatedMinutes }));
}

export interface VoiceReserveInput {
  accountId: string;          // 已通过鉴权的当前账号（学生本人；meter_reserve 只认本人）
  studentId: string;
  idempotencyKey: string;     // 前端为每次开口动作生成的小写 UUIDv4，重试时原样复用
  estimatedMinutes: number;   // 预计分钟（正整数；超过 MAX_ESTIMATED_MINUTES 封顶）
  resourceType?: string;      // 只能缺省或为 voice_realtime：调用方不能借这个辅助扣别的资源
}

/**
 * 一笔仍在预留中的语音分钟：结算、释放都凭它，usage_id 与 idempotency_key 一起带着。
 * 只能留在服务端（由 reserveVoiceMinutes 返回、在同一进程里交给结算 / 释放），不可来自请求体或任何客户端输入：
 * reservedMinutes 决定结算封顶，客户端能填就能少扣。接 HTTP 路由前须补 H6 用例锁住（见 tests/route-matrix.integration.test.ts）。
 */
export interface VoiceReservation {
  usageId: string;
  idempotencyKey: string;
  reservedMinutes: number;
  expiresAt: string;
}

export type VoiceReserveResult =
  // capped：调用方的预计分钟超过 MAX_ESTIMATED_MINUTES，按封顶后的分钟预留
  | { status: 'reserved'; reservation: VoiceReservation; resumed: boolean; capped: boolean }
  | { status: 'settled_previously'; usageId: string; settledMinutes: number }
  | { status: 'released_previously'; usageId: string }
  | { status: 'expired'; usageId: string }
  | { status: 'insufficient'; remaining: number }
  | { status: 'daily_cap_reached'; remainingToday: number }
  | { status: 'curfew_soon'; minutesUntilCurfew: number }
  | { status: 'conflict' | 'no_entitlement' | 'retained_no_ai' | 'curfew' };

function checkKey(idempotencyKey: string): string {
  if (!isUuidV4(idempotencyKey)) throw new TypeError('idempotencyKey must be a lowercase UUIDv4');
  return usageIdFor(idempotencyKey);
}

function checkHandle(r: VoiceReservation): void {
  if (checkKey(r.idempotencyKey) !== r.usageId) throw new TypeError('usageId does not belong to idempotencyKey');
  if (!Number.isSafeInteger(r.reservedMinutes) || r.reservedMinutes < 1) throw new TypeError('reservedMinutes must be a positive integer');
}

export async function reserveVoiceMinutes(pool: Pool, input: VoiceReserveInput): Promise<VoiceReserveResult> {
  if (input.resourceType !== undefined && input.resourceType !== VOICE_RESOURCE) {
    throw new TypeError('voice metering only reserves voice_realtime');  // 固定文案：不回显调用方传入的值
  }
  const usageId = checkKey(input.idempotencyKey);
  if (!Number.isSafeInteger(input.estimatedMinutes) || input.estimatedMinutes < 1) {
    throw new TypeError('estimatedMinutes must be a positive integer');
  }
  const capped = input.estimatedMinutes > MAX_ESTIMATED_MINUTES;
  const minutes = Math.min(input.estimatedMinutes, MAX_ESTIMATED_MINUTES);
  const r = await withAccount(pool, input.accountId, async (c) => {
    const { rows } = await c.query('SELECT platform.meter_reserve($1, $2, $3, $4, $5, $6, $7) AS r',
      [input.studentId, VOICE_RESOURCE, input.idempotencyKey, voiceFingerprint(minutes), minutes,
       null, VOICE_SOURCE_METHOD]);
    return rows[0].r as Record<string, unknown>;
  });
  switch (r['outcome']) {
    case 'insufficient':
      return { status: 'insufficient', remaining: Number(r['remaining']) };
    case 'daily_cap_reached':
      return { status: 'daily_cap_reached', remainingToday: Number(r['remaining_today']) };
    case 'curfew_soon':
      return { status: 'curfew_soon', minutesUntilCurfew: Number(r['minutes_until_curfew']) };
    case 'conflict':
    case 'no_entitlement':
    case 'retained_no_ai':
    case 'curfew':
      return { status: r['outcome'] };
    case 'reserved':
    case 'existing':
      break;
    default:  // dedup / dedup_in_progress：voice_realtime 没有去重窗口，出现即说明配置变了
      throw new Error(`unexpected metering outcome for ${VOICE_RESOURCE}: ${String(r['outcome'])}`);
  }
  if (r['usage_id'] !== usageId) throw new Error('usage_id derived by the database does not match the BFF');
  const state = r['outcome'] === 'reserved' ? 'reserved' : String(r['state']);
  if (state === 'settled') return { status: 'settled_previously', usageId, settledMinutes: Number(r['settled_qty']) };
  if (state === 'released') return { status: 'released_previously', usageId };
  if (state === 'expired') return { status: 'expired', usageId };
  return {
    status: 'reserved',
    resumed: r['outcome'] === 'existing',
    capped,
    reservation: { usageId, idempotencyKey: input.idempotencyKey, reservedMinutes: Number(r['reserved_qty']),
                   expiresAt: String(r['expires_at']) },
  };
}

export type VoiceSettleResult =
  // chargedMinutes = min(billableMinutes, reservedMinutes)；unbilledMinutes = 超出预留、没有扣的分钟；
  // idempotent = 这笔之前已按同样分钟数结算过（本次没有再扣）
  | { status: 'settled'; usageId: string; billableMinutes: number; chargedMinutes: number; unbilledMinutes: number; idempotent: boolean }
  // 结算前预留已过期或已释放：不扣（宁可少扣）
  | { status: 'not_charged'; usageId: string; state: string; billableMinutes: number }
  // 同一笔之前已按另一个分钟数结算：不改
  | { status: 'conflict'; usageId: string; state: string; settledMinutes: number };

export async function settleVoiceMinutes(pool: Pool, accountId: string, reservation: VoiceReservation,
                                         actualDurationMs: number): Promise<VoiceSettleResult> {
  checkHandle(reservation);
  const billable = billableMinutes(actualDurationMs);
  const charged = Math.min(billable, reservation.reservedMinutes);
  const s = await settle(pool, accountId, reservation.usageId, charged);
  if (s.outcome === 'settled') {
    return { status: 'settled', usageId: reservation.usageId, billableMinutes: billable, chargedMinutes: charged,
             unbilledMinutes: billable - charged, idempotent: s.idempotent === true };
  }
  if (s.state === 'settled') {
    const prior = Number((s as unknown as Record<string, unknown>)['settled_qty']);  // meter_settle 在这种情况下带回已结算数
    return { status: 'conflict', usageId: reservation.usageId, state: s.state, settledMinutes: prior };
  }
  return { status: 'not_charged', usageId: reservation.usageId, state: s.state, billableMinutes: billable };
}

export type VoiceReleaseResult =
  | { status: 'released'; usageId: string; idempotent: boolean }
  | { status: 'conflict'; usageId: string; state: string };

export async function releaseVoiceMinutes(pool: Pool, accountId: string, reservation: VoiceReservation,
                                          reason = 'voice_session_failed'): Promise<VoiceReleaseResult> {
  checkHandle(reservation);
  if (!REASON.test(reason)) throw new TypeError('reason must be a lowercase code');
  const r = await release(pool, accountId, reservation.usageId, reason);
  if (r.outcome === 'released') return { status: 'released', usageId: reservation.usageId, idempotent: r.idempotent === true };
  return { status: 'conflict', usageId: reservation.usageId, state: r.state };
}

// ---------- 整段流程：预留 → 会话 → 结算或释放 ----------

/** 交给会话的授权：会话必须在 maxDurationMs 内结束（不超过预留分钟，也不晚于预留过期前的结算余量）。只在服务端流转 */
export interface VoiceGrant {
  usageId: string;
  idempotencyKey: string;
  reservedMinutes: number;
  maxDurationMs: number;
}

export type VoiceSessionOutcome =
  | { kind: 'completed'; durationMs: number }  // 会话正常结束，durationMs 为服务端计时（有限非负数；小数毫秒向上取整）
  | { kind: 'failed' }                          // 确定失败（会话没开起来、供应商拒绝等）：整笔释放
  | { kind: 'unknown' };                        // 不知道是否用了、用了多久：不结算不释放，留给过期任务

export type VoiceMeteringEvent =
  | 'overrun_unbilled'          // 实际时长超出预留：多出的分钟没扣
  | 'late_settle_not_charged'   // 结算时预留已过期或已释放：整笔没扣
  | 'session_result_unknown'    // 结果未知（见 VoiceUnknownCause）：不释放不结算，留给过期任务（不扣）
  | 'estimate_capped'           // 预计分钟超过 MAX_ESTIMATED_MINUTES，按封顶值预留
  | 'no_time_left';             // 预留到手时已来不及开会话（计时偏差）：释放

export interface VoiceMeterDeps {
  pool: Pool;
  now?: () => number;                              // 与数据库计量时钟同一时间轴（测试里注入）
  onEvent?: (event: VoiceMeteringEvent) => void;   // 少扣、未知这类情况不报错，但要被看见
}

/** 结果未知的来源：这几种都不释放、不结算，只发 session_result_unknown，并以 status 'unknown' 返回（不向调用方重新抛出） */
export type VoiceUnknownCause =
  | 'session_reported_unknown'  // 会话自己报未知
  | 'session_threw'             // 会话回调抛异常
  | 'invalid_duration'          // 会话报回的时长不是有限非负数
  | 'settle_threw';             // 结算调用抛异常（例如连接断开：不知道库里是否已结算）

export type VoiceSessionResult =
  // charged：本次调用确实扣了分钟（> 0 且不是幂等重放）
  | { status: 'completed'; usageId: string; charged: boolean; billableMinutes: number; chargedMinutes: number; unbilledMinutes: number }
  // 同 key 之前已结算（预留时发现，或本次结算是幂等重放 / 与已结算的分钟数冲突）：本次没有再扣
  | { status: 'completed_previously'; usageId: string; settledMinutes: number }
  // 同 key 的预留仍在进行中（另一个请求正在用，或上次结果未知）：不开新会话、不扣；等它结算或过期后再看
  | { status: 'in_progress'; usageId: string }
  | { status: 'failed'; usageId: string }
  | { status: 'failed_previously'; usageId: string }
  | { status: 'expired_retry_with_new_key'; usageId: string }
  | { status: 'unknown'; usageId: string; cause: VoiceUnknownCause }
  | { status: 'insufficient'; remaining: number }
  | { status: 'daily_cap_reached'; remainingToday: number }
  | { status: 'curfew_soon'; minutesUntilCurfew: number }
  | { status: 'conflict' | 'no_entitlement' | 'retained_no_ai' | 'curfew' };

export async function withVoiceMinutes(deps: VoiceMeterDeps, input: VoiceReserveInput,
                                       session: (grant: VoiceGrant) => Promise<VoiceSessionOutcome>): Promise<VoiceSessionResult> {
  const now = deps.now ?? Date.now;
  const emit = deps.onEvent ?? (() => {});
  const r = await reserveVoiceMinutes(deps.pool, input);
  switch (r.status) {
    case 'settled_previously':  // 同 key 已结算：不再开会话，不再扣
      return { status: 'completed_previously', usageId: r.usageId, settledMinutes: r.settledMinutes };
    case 'released_previously':
      return { status: 'failed_previously', usageId: r.usageId };
    case 'expired':
      return { status: 'expired_retry_with_new_key', usageId: r.usageId };
    case 'reserved':
      break;
    default:
      return r;
  }
  // 同 key 找回仍在预留中的那笔：不开新会话（并发重试只开一次；结果未知后重试不再开；旧预留开新会话可能跨进宵禁）
  if (r.resumed) return { status: 'in_progress', usageId: r.reservation.usageId };
  if (r.capped) emit('estimate_capped');
  const res = r.reservation;
  const maxDurationMs = Math.max(0, Math.min(res.reservedMinutes * MS_PER_MINUTE, Date.parse(res.expiresAt) - now() - SETTLE_MARGIN_MS));
  if (maxDurationMs <= 0) {
    emit('no_time_left');
    await releaseVoiceMinutes(deps.pool, input.accountId, res, 'voice_no_time_left');
    return { status: 'failed', usageId: res.usageId };
  }
  const unknown = async (cause: VoiceUnknownCause): Promise<VoiceSessionResult> => {  // 不释放、不结算
    emit('session_result_unknown');
    return { status: 'unknown', usageId: res.usageId, cause };
  };
  let outcome: VoiceSessionOutcome;
  try {
    outcome = await session({ usageId: res.usageId, idempotencyKey: res.idempotencyKey, reservedMinutes: res.reservedMinutes,
                              maxDurationMs });
  } catch {
    return unknown('session_threw');
  }
  if (outcome.kind === 'failed') {
    await releaseVoiceMinutes(deps.pool, input.accountId, res, 'voice_session_failed');
    return { status: 'failed', usageId: res.usageId };
  }
  if (outcome.kind !== 'completed') return unknown('session_reported_unknown');
  const raw = outcome.durationMs;
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0) return unknown('invalid_duration');
  const durationMs = Math.max(0, Math.ceil(raw));  // 小数毫秒向上取整（-0 归 0）
  if (!Number.isSafeInteger(durationMs)) return unknown('invalid_duration');
  let s: VoiceSettleResult;
  try {
    s = await settleVoiceMinutes(deps.pool, input.accountId, res, durationMs);
  } catch {
    return unknown('settle_threw');
  }
  if (s.status === 'settled') {
    // 结算是幂等重放：这笔之前已按同样分钟数结算，本次没有扣
    if (s.idempotent) return { status: 'completed_previously', usageId: res.usageId, settledMinutes: s.chargedMinutes };
    if (s.unbilledMinutes > 0) emit('overrun_unbilled');
    return { status: 'completed', usageId: res.usageId, charged: s.chargedMinutes > 0, billableMinutes: s.billableMinutes,
             chargedMinutes: s.chargedMinutes, unbilledMinutes: s.unbilledMinutes };
  }
  // 同 key 已按别的分钟数结算：以已结算的为准，不再扣
  if (s.status === 'conflict') return { status: 'completed_previously', usageId: res.usageId, settledMinutes: s.settledMinutes };
  emit('late_settle_not_charged');
  return { status: 'completed', usageId: res.usageId, charged: false, billableMinutes: s.billableMinutes, chargedMinutes: 0,
           unbilledMinutes: s.billableMinutes };
}
