'use server';
// 探针演示页的写操作（server action，冲刺 2-L）：服务端读开发身份（4-H 换真实会话），经 BFF 写入；浏览器不直连 BFF。
// 只在开发、测试构建里存在（本文件只被 page.dev.tsx 引用，见 next.config.ts 的 pageExtensions）；运行时再核对一次开关与 Host。
// 客户端只传预测、幂等键与时长；学科、题目、时间都由服务端定。结果只回 ok，不回显任何内容。
import { cookies, headers } from 'next/headers';
import { DEV_COOKIE, devAccountFromCookie, devAuthEnabled, isLoopbackHost } from '../../../lib/dev-auth.ts';
import { DEMO_PROBE_ITEM, isKey, postProbe, postSessionEvent } from '../../../lib/events.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { PREDICTION_OPTIONS, type Prediction } from '../../../probe/copy.ts';

async function account(): Promise<string | null> {
  if (!devAuthEnabled() || !isLoopbackHost((await headers()).get('host'))) return null;
  return devAccountFromCookie((await cookies()).get(DEV_COOKIE)?.value);
}

export async function probeAction(student: string, prediction: Prediction, key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isKey(key) || !PREDICTION_OPTIONS.some((o) => o.code === prediction)) return { ok: false };
  return { ok: (await postProbe(who, student, DEMO_PROBE_ITEM, prediction, key)).ok };
}

export async function sessionStartAction(student: string, key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isKey(key)) return { ok: false };
  return { ok: (await postSessionEvent(who, student, { kind: 'session_start', payload: { entry: 'other' } }, key)).ok };
}

export async function sessionEndAction(student: string, durationSec: number, key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isKey(key) || !Number.isInteger(durationSec) || durationSec < 0 || durationSec > 86400) return { ok: false };
  return { ok: (await postSessionEvent(who, student, { kind: 'session_end', payload: { duration_sec: durationSec } }, key)).ok };
}
