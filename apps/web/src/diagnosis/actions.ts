'use server';
// 学生端数学诊断正式做题页的写操作（server action，specs/diagnosis-student-entry 第二节第 4 条）。
// 先过前端开关 STARBUDDY_DIAGNOSIS_UI（关时一律 { ok: false }，不读会话、不发请求），再做双重提交核对（CSRF = cookie，actionSession），
// 然后经 src/diagnosis/ops.ts 校验参数、连同 Origin 转交 BFF。只认学生本人、计量、闸门都在 BFF（家长会话由 BFF 拒绝）；
// 页面另用 requireOwnStudent 只给学生本人渲染。与开发演示页的 action 共用 ops，不引用演示页的任何文件。
import type { BffSession } from '../lib/bff.ts';
import type { Answer, AnswerOutcome } from '../lib/diagnosis.ts';
import { actionSession } from '../lib/session.ts';
import type { Prediction } from '../probe/copy.ts';
import { diagnosisEnabled } from './features.ts';
import { abandonOp, answerOp, currentOp, pauseOp, probeOp, startOp, type CurrentOutcome, type ProbeResult, type StartOutcome } from './ops.ts';

async function account(csrf: string): Promise<BffSession | null> {
  if (!diagnosisEnabled()) return null;
  return actionSession(csrf);
}

export async function startDiagnosisAction(csrf: string, student: string, key: string): Promise<StartOutcome> {
  return startOp(await account(csrf), student, key);
}

export async function currentDiagnosisAction(csrf: string, student: string, session: string): Promise<CurrentOutcome> {
  return currentOp(await account(csrf), student, session);
}

export async function abandonDiagnosisAction(csrf: string, student: string, session: string, key: string): Promise<{ ok: boolean }> {
  return abandonOp(await account(csrf), student, session, key);
}

export async function probeDiagnosisAction(csrf: string, student: string, session: string, seq: number, prediction: Prediction, key: string)
  : Promise<ProbeResult> {
  return probeOp(await account(csrf), student, session, seq, prediction, key);
}

export async function answerDiagnosisAction(csrf: string, student: string, session: string, seq: number, answer: Answer, work: string | null,
  elapsedSec: number, key: string): Promise<AnswerOutcome> {
  return answerOp(await account(csrf), student, session, seq, answer, work, elapsedSec, key);
}

export async function pauseDiagnosisAction(csrf: string, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string)
  : Promise<{ ok: boolean }> {
  return pauseOp(await account(csrf), student, session, seq, stepElapsedSec, key);
}
