'use client';
// 数学诊断演示页的客户端部分：把演示页的 server action 包成诊断组件要的 api（每个动作一个新的幂等键；
// 同一步的作答、暂停以会话与步骤序号为幂等锚点，重复点击不会重复判分）。
import { useMemo } from 'react';
import { Diagnosis, type DiagnosisApi } from '../../../../diagnosis/Diagnosis.tsx';
import { answerAction, currentAction, pauseAction, probeAction, startAction } from '../actions.ts';

export function DiagnosisDemo({ csrf, student }: { csrf: string; student: string }) {
  const api = useMemo<DiagnosisApi>(() => ({
    start: () => startAction(csrf, student, crypto.randomUUID()),
    current: (session) => currentAction(csrf, student, session),
    probe: (session, seq, prediction) => probeAction(csrf, student, session, seq, prediction, crypto.randomUUID()),
    answer: (session, seq, answer, work, elapsed) => answerAction(csrf, student, session, seq, answer, work, elapsed, crypto.randomUUID()),
    pause: (session, seq, elapsed) => pauseAction(csrf, student, session, seq, elapsed, crypto.randomUUID()),
  }), [csrf, student]);
  return <Diagnosis api={api} />;
}
