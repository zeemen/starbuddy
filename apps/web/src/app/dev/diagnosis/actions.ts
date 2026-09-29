'use server';
// 数学诊断演示页的写操作（server action，SIE-02a）：服务端读开发身份（4-H 换真实会话），经 BFF 读写；浏览器不直连 BFF。
// 只在开发、测试构建里存在（本文件只被 page.dev.tsx 引用的演示客户端使用，见 next.config.ts 的 pageExtensions）；运行时再核对一次开关与 Host。
// 客户端只传会话 ID、步骤序号、作答、用时与幂等键；学科、提问时间都由服务端定。结果只回 ok 与题面视图，不回任何评价。
import { cookies, headers } from 'next/headers';
import { DEV_COOKIE, devAccountFromCookie, devAuthEnabled, isLoopbackHost } from '../../../lib/dev-auth.ts';
import {
  answerDiagnosis, currentDiagnosis, isKey, pauseDiagnosis, probeDiagnosis, startDiagnosis, type Answer, type DiagnosisView,
} from '../../../lib/diagnosis.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { PREDICTION_OPTIONS, type Prediction } from '../../../probe/copy.ts';

async function account(): Promise<string | null> {
  if (!devAuthEnabled() || !isLoopbackHost((await headers()).get('host'))) return null;
  return devAccountFromCookie((await cookies()).get(DEV_COOKIE)?.value);
}

const isSeq = (n: unknown): n is number => typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 100;
const isSec = (n: unknown): n is number => typeof n === 'number' && Number.isInteger(n) && n >= 0 && n <= 7200;
function isAnswer(a: unknown): a is Answer {
  if (!a || typeof a !== 'object' || Array.isArray(a)) return false;
  const o = a as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== 1) return false;
  if (keys[0] === 'text') return typeof o['text'] === 'string' && o['text'].length > 0 && o['text'].length <= 200;
  return keys[0] === 'choice' && Array.isArray(o['choice']) && o['choice'].length > 0 && o['choice'].length <= 8
    && o['choice'].every((k) => typeof k === 'string' && /^[A-Z]$/.test(k));
}

export async function startAction(student: string, key: string): Promise<{ ok: boolean; sessionId?: string }> {
  const who = await account();
  if (!who || !isUuid(student) || !isKey(key)) return { ok: false };
  const r = await startDiagnosis(who, student, key);
  return r.ok ? { ok: true, sessionId: r.value.session_id } : { ok: false };
}

export async function currentAction(student: string, session: string): Promise<{ ok: boolean; view?: DiagnosisView }> {
  const who = await account();
  if (!who || !isUuid(student) || !isUuid(session)) return { ok: false };
  const r = await currentDiagnosis(who, student, session);
  return r.ok ? { ok: true, view: r.value } : { ok: false };
}

export async function probeAction(student: string, session: string, seq: number, prediction: Prediction, key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !PREDICTION_OPTIONS.some((o) => o.code === prediction)) {
    return { ok: false };
  }
  return { ok: (await probeDiagnosis(who, student, session, seq, prediction, key)).ok };
}

export async function answerAction(student: string, session: string, seq: number, answer: Answer, work: string | null, elapsedSec: number,
  key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !isAnswer(answer) || !isSec(elapsedSec)
      || (work !== null && (typeof work !== 'string' || work.length === 0 || work.length > 20000))) {
    return { ok: false };
  }
  return { ok: (await answerDiagnosis(who, student, session, seq, answer, work, elapsedSec, key)).ok };
}

export async function pauseAction(student: string, session: string, seq: number | null, stepElapsedSec: number, key: string): Promise<{ ok: boolean }> {
  const who = await account();
  if (!who || !isUuid(student) || !isUuid(session) || (seq !== null && !isSeq(seq)) || !isSec(stepElapsedSec) || !isKey(key)) return { ok: false };
  return { ok: (await pauseDiagnosis(who, student, session, seq, stepElapsedSec, key)).ok };
}
