'use client';
// 数学自适应诊断组件（SIE-02a，spec 设计要点 5）：计时、暂停与续做、每题前复用“能做对吗”探针（ConfidenceProbe，可跳过）、
// 过程题与讲解题的多行输入、结束页只说“完成了，老师会看你的过程”。
//   - 数据全部经 api（服务端 action → BFF）；组件不直连 BFF，不知道题号、节点、答案；
//   - 诊断中不显示任何评价，连对错也不显示；结束页不给结论（规则 11）；
//   - 计时只算看题作答的时间：探针、暂停都不计；暂停时把这道题已用的秒数交给服务端，续做时接着算；
//   - 原生表单控件，键盘可操作；选项用 fieldset + legend，输入框都有 label；状态提示走 role="status"。
// 文案全部在 copy.ts（过禁语检查）。
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { Answer, DiagnosisView } from '../lib/diagnosis.ts';
import { ConfidenceProbe } from '../probe/ConfidenceProbe.tsx';
import type { Prediction } from '../probe/copy.ts';
import { DIAGNOSIS_COPY as C, formatDuration } from './copy.ts';

export interface DiagnosisApi {
  start(): Promise<{ ok: boolean; sessionId?: string }>;
  current(sessionId: string): Promise<{ ok: boolean; view?: DiagnosisView }>;
  probe(sessionId: string, seq: number, prediction: Prediction): Promise<{ ok: boolean }>;
  answer(sessionId: string, seq: number, answer: Answer, work: string | null, elapsedSec: number): Promise<{ ok: boolean }>;
  pause(sessionId: string, seq: number | null, stepElapsedSec: number): Promise<{ ok: boolean }>;
}

type Phase = 'loading' | 'probe' | 'item' | 'paused' | 'finished' | 'unavailable';

export function Diagnosis({ api }: { api: DiagnosisApi }) {
  const ids = { stem: useId(), work: useId(), answer: useId(), timer: useId() };
  const [phase, setPhase] = useState<Phase>('loading');
  const [session, setSession] = useState<string | null>(null);
  const [view, setView] = useState<DiagnosisView | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('');
  const [work, setWork] = useState('');
  const [choice, setChoice] = useState<string[]>([]);
  const [, tick] = useState(0);
  const stepStartedAt = useRef<number | null>(null);   // 这道题开始计时的时刻（看题时）
  const skippedProbe = useRef<number | null>(null);    // 这一步已跳过探针
  const heading = useRef<HTMLHeadingElement>(null);
  const started = useRef(false);

  const stepElapsed = (): number => {
    const base = view?.step?.step_elapsed_sec ?? 0;
    return stepStartedAt.current === null ? base : base + Math.floor((Date.now() - stepStartedAt.current) / 1000);
  };

  const show = useCallback((v: DiagnosisView) => {
    setView(v);
    setText(''); setWork(''); setChoice([]);
    stepStartedAt.current = null;
    if (v.status === 'finished' || v.status === 'abandoned') return setPhase('finished');
    if (v.status === 'paused') return setPhase('paused');
    if (!v.step) return setPhase('unavailable');
    if (!v.step.probe_recorded && skippedProbe.current !== v.step.seq) return setPhase('probe');
    stepStartedAt.current = Date.now();
    setPhase('item');
  }, []);

  const load = useCallback(async (sid: string) => {
    const r = await api.current(sid);
    if (!r.ok || !r.view) {
      setMessage(C.notSaved);
      return;
    }
    setMessage('');
    show(r.view);
  }, [api, show]);

  const begin = useCallback(async () => {
    setPhase('loading');
    const s = await api.start();
    if (!s.ok || !s.sessionId) return setPhase('unavailable');
    setSession(s.sessionId);
    await load(s.sessionId);
  }, [api, load]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void begin();
  }, [begin]);

  // 计时：看题作答时每秒刷新一次显示
  useEffect(() => {
    if (phase !== 'item') return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  useEffect(() => {
    if (phase === 'item' || phase === 'finished' || phase === 'paused') heading.current?.focus();
  }, [phase, view?.step?.seq]);

  const onPredict = async (p: Prediction) => {
    if (!session || !view?.step) return;
    const r = await api.probe(session, view.step.seq, p);
    if (!r.ok) skippedProbe.current = view.step.seq;  // 没记下也不挡做题
    await load(session);
  };
  const onSkip = () => {
    if (!view?.step) return;
    skippedProbe.current = view.step.seq;
    show(view);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!session || !view?.step || busy) return;
    const step = view.step;
    const answer: Answer | null = step.item.format === 'fill' ? (text.trim() ? { text: text.trim() } : null)
      : (choice.length ? { choice } : null);
    if (!answer) return setMessage(C.needAnswer);
    const needsWork = step.kind !== 'adaptive';
    if (needsWork && !work.trim()) return setMessage(C.needWork);
    setBusy(true);
    setMessage(C.submitting);
    try {
      const r = await api.answer(session, step.seq, answer, needsWork ? work : null, Math.min(7200, stepElapsed()));
      if (!r.ok) return setMessage(C.notSaved);
      setMessage('');
      await load(session);
    } finally {
      setBusy(false);
    }
  };

  const pause = async () => {
    if (!session || busy) return;
    setBusy(true);
    try {
      const seq = phase === 'item' ? view?.step?.seq ?? null : null;
      const r = await api.pause(session, seq, Math.min(7200, stepElapsed()));
      if (!r.ok) return setMessage(C.notSaved);
      stepStartedAt.current = null;
      setMessage('');
      setPhase('paused');
    } finally {
      setBusy(false);
    }
  };

  const total = (view?.elapsed_sec ?? 0) + (phase === 'item' ? stepElapsed() : 0);
  const step = view?.step ?? null;
  const toggle = (key: string) => setChoice((c) => step?.item.format === 'single' ? [key]
    : c.includes(key) ? c.filter((k) => k !== key) : [...c, key]);

  return (
    <section className="diagnosis" data-testid="diagnosis" data-phase={phase} aria-labelledby={ids.stem + '-h'}>
      <h2 id={ids.stem + '-h'}>{C.heading}</h2>
      {phase === 'loading' || phase === 'probe' ? <p className="muted">{C.intro}</p> : null}
      {view && phase !== 'finished' && phase !== 'unavailable' ? (
        <p className="diagnosis-timer">
          <span id={ids.timer}>{C.timerLabel}</span>{' '}
          <span role="timer" aria-labelledby={ids.timer} data-testid="diagnosis-timer">
            {C.timerOf(formatDuration(total), formatDuration(view.time_budget_sec))}
          </span>
        </p>
      ) : null}
      {phase === 'loading' ? <p>{C.starting}</p> : null}
      {phase === 'probe' && step ? (
        <>
          <p className="muted">{C.stepLabel(step.seq)}</p>
          <ConfidenceProbe onAnswer={onPredict} onSkip={onSkip} />
        </>
      ) : null}
      {phase === 'item' && step ? (
        <form onSubmit={submit} className="diagnosis-item" data-kind={step.kind} noValidate>
          <h3 ref={heading} tabIndex={-1}>{C.stemHeading}（{C.stepLabel(step.seq)}）</h3>
          <p data-testid="diagnosis-stem">{step.item.stem}</p>
          {step.kind !== 'adaptive' ? (
            <>
              <p>{step.kind === 'process' ? C.processHint : C.explainHint}</p>
              <p className="diagnosis-field">
                <label htmlFor={ids.work}>{step.kind === 'process' ? C.processLabel : C.explainLabel}</label>
                <textarea id={ids.work} rows={8} value={work} maxLength={20000} onChange={(e) => setWork(e.target.value)} />
              </p>
            </>
          ) : null}
          {step.item.format === 'fill' ? (
            <p className="diagnosis-field">
              <label htmlFor={ids.answer}>{C.answerLabel}</label>
              <input id={ids.answer} type="text" value={text} maxLength={200} autoComplete="off" onChange={(e) => setText(e.target.value)} />
            </p>
          ) : (
            <fieldset className="diagnosis-options">
              <legend>{step.item.format === 'single' ? C.choiceLegend : C.choiceLegendMulti}</legend>
              {(step.item.options ?? []).map((o) => (
                <label key={o.key}>
                  <input type={step.item.format === 'single' ? 'radio' : 'checkbox'} name={`choice-${step.seq}`} value={o.key}
                         checked={choice.includes(o.key)} onChange={() => toggle(o.key)} />
                  {' '}{o.key}. {o.text}
                </label>
              ))}
            </fieldset>
          )}
          <p className="diagnosis-actions">
            <button type="submit" disabled={busy}>{C.submit}</button>
          </p>
        </form>
      ) : null}
      {phase === 'paused' ? (
        <>
          <h3 ref={heading} tabIndex={-1}>{C.paused}</h3>
          <p className="diagnosis-actions"><button type="button" onClick={() => void begin()}>{C.resume}</button></p>
        </>
      ) : null}
      {phase === 'finished' ? <h3 ref={heading} tabIndex={-1} data-testid="diagnosis-finished">{C.finished}</h3> : null}
      {phase === 'unavailable' ? <p>{C.unavailable}</p> : null}
      {phase === 'probe' || phase === 'item' ? (
        <p className="diagnosis-actions"><button type="button" className="diagnosis-pause" disabled={busy} onClick={() => void pause()}>{C.pause}</button></p>
      ) : null}
      <p role="status">{message}</p>
    </section>
  );
}
