// 诊断写操作的公共实现（specs/diagnosis-student-entry 第二节第 4 条）：参数校验 + 经 BFF 读写，结果只回 ok、题面视图与分支标记。
// 不是 server action：由两处 'use server' 文件调用——正式做题页的 src/diagnosis/actions.ts（前端开关 + 会话）与
// 开发演示页的 action（开发环境 + 本机 Host + 会话）。身份（who）由调用方核对后传入；授权（只认学生本人）、
// 计量、闸门都在 BFF。客户端只传会话 ID、步骤序号、作答、用时与幂等键；学科、提问时间都由服务端定。不回任何评价。
// curfew：宵禁时段（BFF 402 reason = curfew，会话已置为暂停），组件显示“今天先到这里”
// restart：会话中途届别被改（BFF 409 SESSION_INCONSISTENT、reason = grade_changed），组件显示“重新开始”，点了先放弃这个会话再开新的
import type { BffSession } from '../lib/bff.ts';
import {
  abandonDiagnosis, answerDiagnosis, currentDiagnosis, isCurfew, isKey, pauseDiagnosis, probeDiagnosis, restartSession, startDiagnosis,
  type Answer, type AnswerOutcome, type DiagnosisView,
} from '../lib/diagnosis.ts';
import { retryableFailure } from '../lib/retry-after.ts';
import { isUuid } from '../lib/star-map.ts';
import { PREDICTION_OPTIONS, type Prediction } from '../probe/copy.ts';

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

export type StartOutcome = { ok: boolean; sessionId?: string; curfew?: boolean; restart?: string };
export type CurrentOutcome = { ok: boolean; view?: DiagnosisView; curfew?: boolean; restart?: string };
export type ProbeResult = { ok: boolean; curfew?: boolean; retryable?: boolean; retryAfterSec?: number | null };

export async function startOp(who: BffSession | null, student: string, key: string): Promise<StartOutcome> {
  if (!who || !isUuid(student) || !isKey(key)) return { ok: false };
  const r = await startDiagnosis(who, student, key);
  if (r.ok) return { ok: true, sessionId: r.value.session_id };
  const restart = restartSession(r);
  return { ok: false, curfew: isCurfew(r), ...(restart ? { restart } : {}) };
}

export async function currentOp(who: BffSession | null, student: string, session: string): Promise<CurrentOutcome> {
  if (!who || !isUuid(student) || !isUuid(session)) return { ok: false };
  const r = await currentDiagnosis(who, student, session);
  if (r.ok) return { ok: true, view: r.value };
  const restart = restartSession(r);
  return { ok: false, curfew: isCurfew(r), ...(restart ? { restart } : {}) };
}

export async function abandonOp(who: BffSession | null, student: string, session: string, key: string): Promise<{ ok: boolean }> {
  if (!who || !isUuid(student) || !isUuid(session) || !isKey(key)) return { ok: false };
  return { ok: (await abandonDiagnosis(who, student, session, key)).ok };
}

/** retryable / retryAfterSec：只在可重试的失败时带（src/lib/retry-after.ts 的 retryableFailure，specs/diagnosis-probe-retry） */
export async function probeOp(who: BffSession | null, student: string, session: string, seq: number, prediction: Prediction, key: string)
  : Promise<ProbeResult> {
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !PREDICTION_OPTIONS.some((o) => o.code === prediction)) {
    return { ok: false };
  }
  const r = await probeDiagnosis(who, student, session, seq, prediction, key);
  if (r.ok) return { ok: true };
  if (isCurfew(r)) return { ok: false, curfew: true };
  return retryableFailure(r) ? { ok: false, curfew: false, retryable: true, retryAfterSec: r.retryAfterSec ?? null } : { ok: false, curfew: false };
}

/** retryable / retryAfterSec：只在可重试的失败时带，组件据此冷却提交按钮（specs/diagnosis-answer-cooldown）；宵禁、届别变化照旧 */
export async function answerOp(who: BffSession | null, student: string, session: string, seq: number, answer: Answer, work: string | null,
  elapsedSec: number, key: string): Promise<AnswerOutcome> {
  if (!who || !isUuid(student) || !isUuid(session) || !isSeq(seq) || !isKey(key) || !isAnswer(answer) || !isSec(elapsedSec)
      || (work !== null && (typeof work !== 'string' || work.length === 0 || work.length > 20000))) {
    return { ok: false };
  }
  const r = await answerDiagnosis(who, student, session, seq, answer, work, elapsedSec, key);
  if (r.ok) return { ok: true };
  if (isCurfew(r)) return { ok: false, curfew: true };
  const restart = restartSession(r);
  if (restart) return { ok: false, curfew: false, restart };
  return retryableFailure(r) ? { ok: false, curfew: false, retryable: true, retryAfterSec: r.retryAfterSec ?? null } : { ok: false, curfew: false };
}

export async function pauseOp(who: BffSession | null, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string)
  : Promise<{ ok: boolean }> {
  if (!who || !isUuid(student) || !isUuid(session) || (seq !== null && !isSeq(seq)) || !isSec(stepElapsedSec) || !isKey(key)) return { ok: false };
  return { ok: (await pauseDiagnosis(who, student, session, seq, stepElapsedSec, key)).ok };
}
