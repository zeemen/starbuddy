'use client';
// 错题再练的作答组件（specs/practice-loop-stage1 PR 4）：取一道题 → 作答 → 只显示判分结论与条目新状态。
//   - 数据全部经 server action（服务端 → BFF）；组件不直连 BFF，不知道题目引用、节点答案与错因；
//   - 票据是 BFF 封装的不透明串，只放在组件内存里（不进 DOM、不进地址栏、不写浏览器存储），作答时原样带回；
//     结果未知（retry）或被限流（rate_limited）时用同一张票据、原样的首次作答快照再提交一次（BFF 不再计次；作答控件禁用，
//     避免改了作答之后同一张票据被判“已用”）；票据已用或过期时换一道题；
//   - 结果只显示判分结论（验证器或 Rubric，规则 3）与条目新状态；待复核时只显示“等待复核”；不显示掌握、错因与任何评价；
//   - 题面与作答控件的写法同诊断组件（src/diagnosis/Diagnosis.tsx）：原生表单控件，选项用 fieldset + legend，输入框有 label，
//     状态提示走 role="status"，键盘可操作。
// 文案全部在 src/shell/copy.ts（占位 {{TODO_COPY:practice.*}}，过禁语与禁用推断词表检查）。
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { Answer } from '../lib/diagnosis.ts';
import { PRACTICE as C, PRACTICE_AFTER, PRACTICE_ERROR, PRACTICE_VERDICT } from '../shell/copy.ts';
import { RichParagraphs, RichRuns } from '../shell/RichItem.tsx';
import { answerPracticeAction, nextPracticeAction } from './actions.ts';
import { keepsTicket, needsNewItem, resubmitsSnapshot, shanghaiMinute, type AnswerView, type ReadyView, type UiError } from './view.ts';

type Phase = 'loading' | 'item' | 'result' | 'empty' | 'stopped';

export function PracticeItem({ csrf, student, subject, entry, listHref }: { csrf: string; student: string; subject: string; entry: string; listHref: string }) {
  const ids = { heading: useId(), answer: useId() };
  const [phase, setPhase] = useState<Phase>('loading');
  const [ready, setReady] = useState<ReadyView | null>(null);
  const [result, setResult] = useState<AnswerView | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('');
  const [choice, setChoice] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const started = useRef(false);
  /** 这张票据首次提交时的作答快照（只在组件内存）；结果未知或限流后再提交时原样发它 */
  const sent = useRef<Answer | null>(null);

  const load = useCallback(async () => {
    setPhase('loading');
    sent.current = null;
    setError(null); setMessage(''); setResult(null); setReady(null); setText(''); setChoice([]);
    const r = await nextPracticeAction(csrf, student, subject, entry);
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
      const r = await answerPracticeAction(csrf, student, subject, entry, ready.ticket, answer);
      if (r.kind === 'result') {
        setError(null); setMessage('');
        setResult(r.result);
        setPhase('result');
        return;
      }
      setMessage('');
      setError(r.error);
      // 只有结果未知与限流要原样再发；作答形状不对时放开控件，让学生改了再交
      if (!resubmitsSnapshot(r.error)) sent.current = null;
      if (!keepsTicket(r.error) && !needsNewItem(r.error)) setPhase('stopped');
    } finally {
      setBusy(false);
    }
  };

  const item = ready?.item ?? null;
  const toggle = (key: string) => setChoice((c) => item?.format === 'single' ? [key] : c.includes(key) ? c.filter((k) => k !== key) : [...c, key]);
  const locked = error !== null && resubmitsSnapshot(error);
  const errorText = error && error !== 'not_found' && error !== 'login' ? PRACTICE_ERROR[error] : error === 'not_found' ? PRACTICE_ERROR['unavailable'] : '';

  return (
    <section className="practice-item" data-testid="practice-item" data-phase={phase} data-error={error ?? ''} aria-labelledby={ids.heading}>
      {phase === 'loading' ? <h2 id={ids.heading}>{C.loading}</h2> : null}
      {phase === 'item' && ready && item ? (
        <form onSubmit={submit} noValidate data-format={item.format}>
          <h2 id={ids.heading} ref={heading} tabIndex={-1}>{C.itemHeading}</h2>
          <p className="muted"><span>{C.colRound}</span>{' '}<span data-value="round">{ready.round}</span></p>
          {item.rich ? (
            <div data-testid="practice-stem" data-item-text="stem" className="item-rich"><RichParagraphs stem={item.rich.stem} /></div>
          ) : (
            <p data-testid="practice-stem" data-item-text="stem">{item.stem}</p>
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
                    <input type={item.format === 'single' ? 'radio' : 'checkbox'} name="practice-choice" value={o.key}
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
              <button type="button" onClick={() => void load()} data-testid="practice-new-item">{C.newItem}</button>
            ) : (
              <button type="submit" disabled={busy} data-testid="practice-submit">{locked ? C.retry : C.submit}</button>
            )}
          </p>
        </form>
      ) : null}
      {phase === 'result' && result ? (
        <div data-testid="practice-result" data-verdict={result.verdict ?? ''} data-pending-review={result.pending_review ? 'true' : 'false'}
             data-entry-status={result.entry.status}>
          <h2 id={ids.heading} ref={heading} tabIndex={-1}>{C.resultHeading}</h2>
          <p data-testid="practice-verdict">{result.pending_review || !result.verdict ? C.pendingReview : PRACTICE_VERDICT[result.verdict]}</p>
          <p data-testid="practice-after">
            <span>{PRACTICE_AFTER[result.entry.status]}</span>
            {result.entry.status === 'waiting' && result.entry.next_round_at ? (
              <>{' '}<span data-value="next-round-at">{shanghaiMinute(result.entry.next_round_at)}</span></>
            ) : null}
          </p>
          {result.entry.status === 'due' ? (
            <p className="diagnosis-actions"><button type="button" onClick={() => void load()} data-testid="practice-another">{C.another}</button></p>
          ) : null}
        </div>
      ) : null}
      {phase === 'empty' ? <h2 id={ids.heading} ref={heading} tabIndex={-1} data-testid="practice-empty">{C.itemUnavailable}</h2> : null}
      {phase === 'stopped' ? <h2 id={ids.heading} ref={heading} tabIndex={-1} data-testid="practice-stopped">{errorText}</h2> : null}
      <p role="status" data-testid="practice-status">{phase === 'item' && error ? errorText : message}</p>
      <p><a href={listHref} data-testid="practice-back-to-list">{C.backToList}</a></p>
    </section>
  );
}
