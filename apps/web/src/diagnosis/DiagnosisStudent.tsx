'use client';
// 学生端正式做题页的客户端部分（specs/diagnosis-student-entry）：把正式 server action 包成诊断组件要的 api，做法同开发演示页的
// 客户端部分（每个动作一个新的幂等键；同一步的作答、暂停以会话与步骤序号为幂等锚点，重复点击不会重复判分）。
import { useMemo } from 'react';
import { Diagnosis, type DiagnosisApi } from './Diagnosis.tsx';
import {
  abandonDiagnosisAction, answerDiagnosisAction, currentDiagnosisAction, pauseDiagnosisAction, probeDiagnosisAction, startDiagnosisAction,
} from './actions.ts';

export function DiagnosisStudent({ csrf, student }: { csrf: string; student: string }) {
  const api = useMemo<DiagnosisApi>(() => ({
    start: () => startDiagnosisAction(csrf, student, crypto.randomUUID()),
    current: (session) => currentDiagnosisAction(csrf, student, session),
    probe: (session, seq, prediction) => probeDiagnosisAction(csrf, student, session, seq, prediction, crypto.randomUUID()),
    answer: (session, seq, answer, work, elapsed) => answerDiagnosisAction(csrf, student, session, seq, answer, work, elapsed, crypto.randomUUID()),
    pause: (session, seq, elapsed) => pauseDiagnosisAction(csrf, student, session, seq, elapsed, crypto.randomUUID()),
    abandon: (session) => abandonDiagnosisAction(csrf, student, session, crypto.randomUUID()),
  }), [csrf, student]);
  return <Diagnosis api={api} />;
}
