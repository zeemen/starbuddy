'use server';
// 数学诊断演示页的写操作（server action，SIE-02a）：身份只来自真实会话（4-H H2），经 BFF 读写；浏览器不直连 BFF。
// 只在开发、测试构建里存在（本文件只被 page.dev.tsx 引用的演示客户端使用，见 next.config.ts 的 pageExtensions）；运行时再核对一次环境与 Host。
// CSRF：客户端组件把页面渲染时拿到的 CSRF 令牌原样带回，这里核对它等于 CSRF cookie，再连同 Origin 转交 BFF。
// 参数校验与 BFF 调用在 src/diagnosis/ops.ts（与学生端正式做题页的 src/diagnosis/actions.ts 共用，specs/diagnosis-student-entry）；
// 这里只多一道开发环境关卡，不受前端开关 STARBUDDY_DIAGNOSIS_UI 影响。
import { headers } from 'next/headers';
import type { BffSession } from '../../../lib/bff.ts';
import { devRuntimeAllowed } from '../../../lib/dev-pages.ts';
import type { Answer, AnswerOutcome } from '../../../lib/diagnosis.ts';
import { actionSession } from '../../../lib/session.ts';
import {
  abandonOp, answerOp, currentOp, pauseOp, probeOp, startOp, type CurrentOutcome, type ProbeResult, type StartOutcome,
} from '../../../diagnosis/ops.ts';
import type { Prediction } from '../../../probe/copy.ts';

async function account(csrf: string): Promise<BffSession | null> {
  if (!devRuntimeAllowed((await headers()).get('host'))) return null;
  return actionSession(csrf);
}

export async function startAction(csrf: string, student: string, key: string): Promise<StartOutcome> {
  return startOp(await account(csrf), student, key);
}

export async function currentAction(csrf: string, student: string, session: string): Promise<CurrentOutcome> {
  return currentOp(await account(csrf), student, session);
}

export async function abandonAction(csrf: string, student: string, session: string, key: string): Promise<{ ok: boolean }> {
  return abandonOp(await account(csrf), student, session, key);
}

export async function probeAction(csrf: string, student: string, session: string, seq: number, prediction: Prediction, key: string)
  : Promise<ProbeResult> {
  return probeOp(await account(csrf), student, session, seq, prediction, key);
}

export async function answerAction(csrf: string, student: string, session: string, seq: number, answer: Answer, work: string | null, elapsedSec: number,
  key: string): Promise<AnswerOutcome> {
  return answerOp(await account(csrf), student, session, seq, answer, work, elapsedSec, key);
}

export async function pauseAction(csrf: string, student: string, session: string, seq: number | null, stepElapsedSec: number, key: string): Promise<{ ok: boolean }> {
  return pauseOp(await account(csrf), student, session, seq, stepElapsedSec, key);
}
