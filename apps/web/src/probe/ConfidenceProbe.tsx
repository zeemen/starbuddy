'use client';
// “能做对吗”探针（冲刺 2-L，spec 设计要点 3）：题目展示前问一句，三个按钮（能 / 不能 / 不确定）加“跳过”。
//   - 选择后只回调 onAnswer，由页面经 server action 调 BFF 写入；跳过只回调 onSkip，不提交任何东西；
//   - 不打分、不评价、不显示历史，也不显示别人怎么选；
//   - 原生按钮，Tab / 回车 / 空格都能操作；按钮的可访问名称就是按钮上的字，整组由问题句标注。
// 文案全部在 copy.ts（过禁语检查）。
import { useId, useState } from 'react';
import { PREDICTION_OPTIONS, PROBE_COPY, type Prediction } from './copy.ts';

export function ConfidenceProbe({ onAnswer, onSkip }: {
  onAnswer: (prediction: Prediction) => void | Promise<void>;
  onSkip: () => void;
}) {
  const questionId = useId();
  const [busy, setBusy] = useState(false);
  const answer = async (p: Prediction) => {
    if (busy) return;
    setBusy(true);
    try {
      await onAnswer(p);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="group" aria-labelledby={questionId} className="probe" data-testid="confidence-probe" aria-busy={busy}>
      <p id={questionId} className="probe-question">{PROBE_COPY.question}</p>
      <p className="probe-options">
        {PREDICTION_OPTIONS.map((o) => (
          <button key={o.code} type="button" data-prediction={o.code} disabled={busy} onClick={() => void answer(o.code)}>
            {o.label}
          </button>
        ))}
        <button type="button" className="probe-skip" disabled={busy} onClick={onSkip}>{PROBE_COPY.skip}</button>
      </p>
    </div>
  );
}
