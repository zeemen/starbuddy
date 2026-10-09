'use client';
// 复测与验证题的作答组件（specs/retest-delivery-kge05）：取一道题 → 作答 → 只显示判分结论与条目新状态。写法同错题再练的 PracticeItem：
//   - 数据全部经 server action（服务端 → BFF）；组件不直连 BFF，不知道题目引用、任务原因、答案与错因；
//   - 票据是 BFF 封装的不透明串（sbr1），只放在组件内存里；结果未知或被限流时用同一张票据、原样的首次作答快照再提交一次（BFF 不再计次）；
//   - 结果只显示判分结论（验证器或 Rubric，规则 3）与条目新状态；待复核时只显示“等待复核”；不显示掌握、错因与任何评价；
//   - 原生表单控件，选项用 fieldset + legend，输入框有 label，状态提示走 role="status"，键盘可操作。
// 文案全部在 src/shell/copy.ts（占位 {{TODO_COPY:retest.*}}）。
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { Answer } from '../lib/diagnosis.ts';
import { RETEST as C, RETEST_AFTER, RETEST_ERROR, RETEST_VERDICT } from '../shell/copy.ts';
import { RichParagraphs, RichRuns } from '../shell/RichItem.tsx';
import { keepsTicket, needsNewItem, resubmitsSnapshot, type UiError } from '../practice/view.ts';
import { answerRetestAction, nextRetestAction } from './actions.ts';
import type { RetestAnswerView, RetestReadyView } from './view.ts';

type Phase = 'loading' | 'item' | 'result' | 'empty' | 'stopped';

export function RetestItem({ csrf, student, subject, entry, listHref }: { csrf: string; student: string; subject: string; entry: string; listHref: string }) {
  const ids = { heading: useId(), answer: useId() };
  const [phase, setPhase] = useState<Phase>('loading');
  const [ready, setReady] = useState<RetestReadyView | null>(null);
  const [result, setResult] = useState<RetestAnswerView | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('');
  const [choice, setChoice] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const started = useRef(false);
  const sent = useRef<Answer | null>(null);

  const load = useCallback(async () => {
    setPhase('loading');
    sent.current = null;
    setError(null); setMessage(''); setResult(null); setReady(null); setText(''); setChoice([]);
    const r = await nextRetestAction(csrf, student, subject, entry);
    if (r.kind === 'ready') {
      setReady(r.ready);
      setPhase('item');
    } else if (r.kind === 'empty') {
      setPhase('empty');
    } else {
      setError(r.error);
      setPhase('stopped');
    }
  }, [csrf, student, subject, entry]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void load();
  }, [load]);

  useEffect(() => {
    if (phase !== 'loading') heading.current?.focus();
  }, [phase, ready?.ticket]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    const answer: Answer | null = sent.current
      ?? (ready.item.format === 'fill' ? (text.trim() ? { text: text.trim() } : null) : (choice.length ? { choice: [...choice] } : null));
    if (!answer) return setMessage(C.needAnswer);
    sent.current = answer;
    setBusy(true);
    setError(null);
    setMessage(C.submitting);
    try {
      const r = await answerRetestAction(csrf, student, subject, entry, ready.ticket, answer);
      if (r.kind === 'result') {
        setError(null); setMessage('');
        setResult(r.result);
        setPhase('result');
        return;
      }
      setMessage('');
      setError(r.error);
      if (!resubmitsSnapshot(r.error)) sent.current = null;
      if (!keepsTicket(r.error) && !needsNewItem(r.error)) setPhase('stopped');
    } finally {
      setBusy(false);
    }
  };

  const item = ready?.item ?? null;
  const toggle = (key: string) => setChoice((c) => item?.format === 'single' ? [key] : c.includes(key) ? c.filter((k) => k !== key) : [...c, key]);
  const locked = error !== null && resubmitsSnapshot(error);
  const errorText = error && error !== 'not_found' && error !== 'login' ? RETEST_ERROR[error] : error === 'not_found' ? RETEST_ERROR['unavailable'] : '';

  return (
    <section className="practice-item" data-testid="retest-item" data-phase={phase} data-error={error ?? ''} aria-labelledby={ids.heading}>
      {phase === 'loading' ? <h2 id={ids.heading}>{C.loading}</h2> : null}
      {phase === 'item' && ready && item ? (
        <form onSubmit={submit} noValidate data-format={item.format}>
          <h2 id={ids.heading} ref={heading} tabIndex={-1}>{C.itemHeading}</h2>
          {item.rich ? (
            <div data-testid="retest-stem" data-item-text="stem" className="item-rich"><RichParagraphs stem={item.rich.stem} /></div>
          ) : (
            <p data-testid="retest-stem" data-item-text="stem">{item.stem}</p>
          )}
          {item.format === 'fill' ? (
            <p className="diagnosis-field">
              <label htmlFor={ids.answer}>{C.answerLabel}</label>
              <input id={ids.answer} type="text" value={text} maxLength={200} autoComplete="off" disabled={locked}
                     onChange={(e) => setText(e.target.value)} />
            </p>
          ) : (
            <fieldset className="diagnosis-options" disabled={locked}>
              <legend>{item.format === 'single' ? C.choiceLegend : C.choiceLegendMulti}</legend>
              {(item.options ?? []).map((o, i) => {
                const rich = item.rich?.options?.[i];
                return (
                  <label key={o.key}>
                    <input type={item.format === 'single' ? 'radio' : 'checkbox'} name="retest-choice" value={o.key}
                           checked={choice.includes(o.key)} onChange={() => toggle(o.key)} />
                    {' '}<span data-value="option-key">{o.key}</span>{' '}
                    <span data-item-text="option">{rich && rich.key === o.key ? <RichRuns runs={rich.content} /> : o.text}</span>
                  </label>
                );
              })}
            </fieldset>
          )}
          <p className="diagnosis-actions">
            {error && needsNewItem(error) ? (
              <button type="button" onClick={() => void load()} data-testid="retest-new-item">{C.newItem}</button>
            ) : (
              <button type="submit" disabled={busy} data-testid="retest-submit">{locked ? C.retry : C.submit}</button>
            )}
          </p>
        </form>
      ) : null}
      {phase === 'result' && result ? (
        <div data-testid="retest-result" data-verdict={result.verdict ?? ''} data-pending-review={result.pending_review ? 'true' : 'false'}
             data-entry-status={result.entry.status}>
          <h2 id={ids.heading} ref={heading} tabIndex={-1}>{C.resultHeading}</h2>
          <p data-testid="retest-verdict">{result.pending_review || !result.verdict ? C.pendingReview : RETEST_VERDICT[result.verdict]}</p>
          <p data-testid="retest-after"><span>{RETEST_AFTER[result.entry.status]}</span></p>
          {result.entry.status === 'due' ? (
            <p className="diagnosis-actions"><button type="button" onClick={() => void load()} data-testid="retest-another">{C.another}</button></p>
          ) : null}
        </div>
      ) : null}
      {phase === 'empty' ? <h2 id={ids.heading} ref={heading} tabIndex={-1} data-testid="retest-empty">{C.itemUnavailable}</h2> : null}
      {phase === 'stopped' ? <h2 id={ids.heading} ref={heading} tabIndex={-1} data-testid="retest-stopped">{errorText}</h2> : null}
      <p role="status" data-testid="retest-status">{phase === 'item' && error ? errorText : message}</p>
      <p><a href={listHref} data-testid="retest-back-to-list">{C.backToList}</a></p>
    </section>
  );
}
