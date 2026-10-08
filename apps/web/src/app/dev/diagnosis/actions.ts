'use server';
// 数学诊断演示页的写操作（server action，SIE-02a）：身份只来自真实会话（4-H H2），经 BFF 读写；浏览器不直连 BFF。
// 只在开发、测试构建里存在（本文件只被 page.dev.tsx 引用的演示客户端使用，见 next.config.ts 的 pageExtensions）；运行时再核对一次环境与 Host。
// CSRF：客户端组件把页面渲染时拿到的 CSRF 令牌原样带回，这里核对它等于 CSRF cookie，再连同 Origin 转交 BFF。
// 客户端只传会话 ID、步骤序号、作答、用时与幂等键；学科、提问时间都由服务端定。结果只回 ok 与题面视图，不回任何评价。
import { headers } from 'next/headers';
import type { BffSession } from '../../../lib/bff.ts';
import { devRuntimeAllowed } from '../../../lib/dev-pages.ts';
import { actionSession } from '../../../lib/session.ts';
import {
  abandonDiagnosis, answerDiagnosis, currentDiagnosis, isCurfew, isKey, pauseDiagnosis, probeDiagnosis, restartSession, startDiagnosis,
  type Answer, type DiagnosisView,
} from '../../../lib/diagnosis.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { PREDICTION_OPTIONS, type Prediction } from '../../../probe/copy.ts';

async function account(csrf: string): Promise<BffSession | null> {
  if (!devRuntimeAllowed((await headers()).get('host'))) return null;
  return actionSession(csrf);
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

// curfew：宵禁时段（BFF 402 reason = curfew，会话已置为暂停），组件显示“今天先到这里”
// restart：会话中途届别被改（BFF 409 SESSION_INCONSISTENT、reason = grade_changed），组件显示“重新开始”，点了先放弃这个会话再开新的
export async function startAction(csrf: string, student: string, key: string): Promise<{ ok: boolean; sessionId?: string; curfew?: boolean; restart?: string }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isKey(key)) return { ok: false };
  const r = await startDiagnosis(who, student, key);
  if (r.ok) return { ok: true, sessionId: r.value.session_id };
  const restart = restartSession(r);
  return { ok: false, curfew: isCurfew(r), ...(restart ? { restart } : {}) };
}

export async function currentAction(csrf: string, student: string, session: string): Promise<{ ok: boolean; view?: DiagnosisView; curfew?: boolean; restart?: string }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isUuid(session)) return { ok: false };
  const r = await currentDiagnosis(who, student, session);
  if (r.ok) return { ok: true, view: r.value };
  const restart = restartSession(r);
  return { ok: false, curfew: isCurfew(r), ...(restart ? { restart } : {}) };
}

export async function abandonAction(csrf: string, student: string, session: string, key: string): Promise<{ ok: boolean }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isUuid(session) || !isKey(key)) return { ok: false };
  return { ok: (await abandonDiagnosis(who, student, session, key)).ok };
}

export async function probeAction(csrf: string, student: string, session: string, seq: number, prediction: Prediction, key: string): Promise<{ ok: boolean; curfew?: boolean }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !PREDICTION_OPTIONS.some((o) => o.code === prediction)) {
    return { ok: false };
  }
  const r = await probeDiagnosis(who, student, session, seq, prediction, key);
  return r.ok ? { ok: true } : { ok: false, curfew: isCurfew(r) };
}

export async function answerAction(csrf: string, student: string, session: string, seq: number, answer: Answer, work: string | null, elapsedSec: number,
  key: string): Promise<{ ok: boolean; curfew?: boolean; restart?: string }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !isAnswer(answer) || !isSec(elapsedSec)
      || (work !== null && (typeof work !== 'string' || work.length === 0 || work.length > 20000))) {
    return { ok: false };
  }
  const r = await answerDiagnosis(who, student, session, seq, answer, work, elapsedSec, key);
  if (r.ok) return { ok: true };
  const restart = restartSession(r);
  return { ok: false, curfew: isCurfew(r), ...(restart ? { restart } : {}) };
}

export async function pauseAction(csrf: string, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string): Promise<{ ok: boolean }> {
  const who = await account(csrf);
  if (!who || !isUuid(student) || !isUuid(session) || (seq !== null && !isSeq(seq)) || !isSec(stepElapsedSec) || !isKey(key)) return { ok: false };
  return { ok: (await pauseDiagnosis(who, student, session, seq, stepElapsedSec, key)).ok };
}
