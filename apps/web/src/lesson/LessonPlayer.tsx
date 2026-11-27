'use client';
// 讲评课播放组件（specs/stage-lesson-session ③b-5）：开始 → 打开讲评课（server action → BFF，计一次讲评课）→ 逐步播放 → 指着元素提问。
//   - 讲解 DSL 与台词稿由学习服务下发；这里再用 packages/stage 的 migrateStageDsl / migrateStageScript 读一次（与样张页同一套入口），
//     不合格就不播放（规格第二节第 1 条；学习服务那一侧的校验见 specs/stage-lesson-session lessons ③b-4a L5）；
//   - 可提问模式沿用第一阶段（specs/stage-point-to-ask）：指着白板元素、槽位或这一步，从菜单选问题；“再讲一遍”在本地跳回那一步重放，不发请求；
//     其余三种经 server action 转 BFF（不扣次），回答显示在字幕区下方；没有答疑、到了上限显示兜底与“再讲一遍”；
//   - 等待用过程状态组件（ProcessStatus）；结果未知时用同一个键重试（打开不重扣、提问不重计）；
//   - 票据只在组件内存里（不进 DOM、不进地址栏、不写浏览器存储）；提问与回答不落库（规格第二节第 5 条）。
// 文案全部在 src/shell/copy.ts（STAGE_ASK、LESSON、LESSON_ERROR、PROCESS_STATUS，都是 {{TODO_COPY:…}} 占位）。
import { createElement, useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  captionAt, DSL_VERSION, initialPlayer, migrateStageDsl, migrateStageScript, originsAt, QUESTION_INTENTS, replayCursor, step, validateStageQuestion,
  type LayoutName, type QuestionIntent, type StageAnchor, type StageQuestion, type ValidatedStageDsl, type ValidatedStageScript,
} from '../../../../packages/stage/src/index.ts';
import { createWhiteboard } from '../../../../packages/stage/src/Whiteboard.tsx';
import { LESSON as L, LESSON_ERROR as E, STAGE_ASK as C } from '../shell/copy.ts';
import { ProcessStatus } from '../shell/ProcessStatus.tsx';
import { askLessonAction, openLessonAction, type AskInput } from './actions.ts';
import type { AskView, LessonRef, UiError } from './view.ts';

const Whiteboard = createWhiteboard(createElement);
const TICK_MS = 1600;
const INTENT_LABEL: Record<QuestionIntent, string> = { why_step: C.why_step, what_is: C.what_is, where_from: C.where_from, again: C.again };
const anchorKey = (a: StageAnchor): string => `${a.kind}:${a.id}`;

type Phase = 'idle' | 'opening' | 'play' | 'error';
interface Loaded { dsl: ValidatedStageDsl; script: ValidatedStageScript; ticket: string }
interface Asked { question: StageQuestion; key: string; view: AskView | null; error: UiError | null }

function useLayout(): LayoutName {
  const [layout, setLayout] = useState<LayoutName>('desktop');
  useEffect(() => {
    const m = window.matchMedia('(max-width: 640px)');
    const t = window.matchMedia('(max-width: 1024px)');
    const update = () => setLayout(m.matches ? 'mobile' : t.matches ? 'tablet' : 'desktop');
    update();
    m.addEventListener('change', update);
    t.addEventListener('change', update);
    return () => { m.removeEventListener('change', update); t.removeEventListener('change', update); };
  }, []);
  return layout;
}

export function LessonPlayer({ csrf, student, subject, lesson }: { csrf: string; student: string; subject: string; lesson: LessonRef }) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<UiError | 'content' | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [left, setLeft] = useState(0);
  // 打开的幂等键：结果未知（retry）时原样再发；其余失败后重新开始换一个新键
  const openKey = useRef<string | null>(null);
  const layout = useLayout();

  const open = useCallback(async (retry: boolean) => {
    if (!retry || !openKey.current) openKey.current = crypto.randomUUID();
    setPhase('opening');
    setError(null);
    const r = await openLessonAction(csrf, student, subject, lesson, openKey.current);
    if (r.kind === 'error') { setError(r.error); setPhase('error'); return; }
    const d = migrateStageDsl(r.view.dsl);
    const sc = d.ok ? migrateStageScript(d.dsl, r.view.script) : null;
    if (!d.ok || !sc?.ok) { setError('content'); setPhase('error'); return; }
    setLoaded({ dsl: d.dsl, script: sc.script, ticket: r.view.ticket });
    setLeft(r.view.questionsLeft);
    setPhase('play');
  }, [csrf, student, subject, lesson]);

  if (phase === 'play' && loaded) {
    return <Player csrf={csrf} student={student} subject={subject} loaded={loaded} left={left} setLeft={setLeft} layout={layout}
      onClosed={(e) => { setLoaded(null); setError(e); setPhase('error'); }} />;
  }
  return (
    <section data-lesson-root="start" aria-live="polite">
      {phase === 'opening' ? (
        <p role="status"><ProcessStatus stage="preparing" /></p>
      ) : phase === 'error' && error ? (
        <div>
          <p role="alert" data-lesson-error={error}>{E[error]}</p>
          {error === 'retry'
            ? <button type="button" data-lesson-retry onClick={() => void open(true)}>{E.retryButton}</button>
            : error !== 'not_found' && error !== 'curfew' && error !== 'quota'
              ? <button type="button" data-lesson-start onClick={() => void open(false)}>{L.start}</button> : null}
        </div>
      ) : (
        <p>
          <button type="button" data-lesson-start onClick={() => void open(false)}>{L.start}</button>{' '}
          <span className="muted">{L.startHint}</span>
        </p>
      )}
    </section>
  );
}

/** 提问落在第几步（1 起算）：元素是它写上去的那一步，整一步是那一步，槽位是当前这一步（与样张页同一口径） */
function anchorStep(dsl: ValidatedStageDsl, q: StageQuestion): number {
  if (q.anchor.kind === 'element') return (originsAt(dsl, q.cursor).get(q.anchor.id) ?? q.cursor - 1) + 1;
  if (q.anchor.kind === 'step') return dsl.steps.findIndex((s) => s.id === q.anchor.id) + 1;
  return q.cursor;
}

function Player({ csrf, student, subject, loaded, left, setLeft, layout, onClosed }: {
  csrf: string; student: string; subject: string; loaded: Loaded; left: number; setLeft: (n: number) => void; layout: LayoutName;
  onClosed: (e: UiError) => void;
}) {
  const { dsl, script, ticket } = loaded;
  const [player, dispatch] = useReducer(step, dsl.steps.length, initialPlayer);
  const [selected, setSelected] = useState<StageAnchor | null>(null);
  const [asked, setAsked] = useState<Asked | null>(null);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const stepButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<StageAnchor | null>(null);

  const select = useCallback((a: StageAnchor) => {
    if (busy) return;
    if (player.status === 'playing') dispatch({ type: 'pause' });
    setSelected(a);
  }, [player.status, busy]);
  const close = useCallback((refocus: boolean) => {
    restoreFocus.current = refocus ? selected : null;
    setSelected(null);
  }, [selected]);

  useEffect(() => { setSelected(null); }, [player.cursor]);
  useEffect(() => {
    if (selected) { menuRef.current?.querySelector('button')?.focus(); return; }
    const back = restoreFocus.current;
    restoreFocus.current = null;
    if (!back) return;
    if (back.kind === 'step') stepButtonRef.current?.focus();
    else document.querySelector<SVGElement>(`[data-ask="${anchorKey(back)}"]`)?.focus();
  }, [selected]);
  useEffect(() => {
    if (!selected) return undefined;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (t && (menuRef.current?.contains(t) || t.closest('[data-ask]') || t === stepButtonRef.current)) return;
      setSelected(null);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [selected]);
  useEffect(() => {
    if (player.status !== 'playing') return undefined;
    const t = setTimeout(() => dispatch({ type: 'tick' }), TICK_MS);
    return () => clearTimeout(t);
  }, [player]);

  const replay = (q: StageQuestion) => {
    dispatch({ type: 'seek', step: replayCursor(dsl, q) });
    dispatch({ type: 'play' });
  };
  const send = async (a: Asked) => {
    setBusy(true);
    setAsked({ ...a, view: null, error: null });
    const q = a.question;
    const input: AskInput = { cursor: q.cursor, anchor: { kind: q.anchor.kind, id: q.anchor.id }, intent: q.intent as AskInput['intent'] };
    const r = await askLessonAction(csrf, student, subject, ticket, input, a.key);
    setBusy(false);
    if (r.kind === 'error') {
      if (r.error === 'expired' || r.error === 'closed' || r.error === 'curfew' || r.error === 'not_found') { onClosed(r.error); return; }
      setAsked({ ...a, view: null, error: r.error });
      return;
    }
    setLeft(r.view.questionsLeft);
    setAsked({ ...a, view: r.view, error: null });
  };
  const choose = (intent: QuestionIntent) => {
    if (!selected) return;
    const r = validateStageQuestion(dsl, { dsl_version: DSL_VERSION, cursor: player.cursor, anchor: selected, intent });
    if (!r.ok) { close(false); return; }
    if (intent === 'again') { replay(r.question); setSelected(null); return; }
    close(true);
    // 每一问一个新键；结果未知时“再试一次”带同一个键（学习服务重放同一个回答，不再计次）
    void send({ question: r.question, key: crypto.randomUUID(), view: null, error: null });
  };

  const caption = captionAt(dsl, script, player.cursor);
  const label = (a: StageAnchor, at: number | null, index: number): string => {
    if (a.kind === 'slot') return dsl.slots?.find((x) => x.id === a.id)?.kind === 'student_answer' ? C.slot_student_answer : C.slot_item_figure;
    return C.element_label.replace('{step}', String(at ?? '')).replace('{index}', String(index));
  };
  // 到了上限：菜单只剩“再讲一遍”（规格第四节验收）
  const intents = left > 0 ? QUESTION_INTENTS : QUESTION_INTENTS.filter((i) => i === 'again');
  const answeredStep = asked ? anchorStep(dsl, asked.question) : null;
  return (
    <section data-lesson-root="play" aria-labelledby="lesson-play">
      <h2 id="lesson-play" className="visually-hidden">{L.heading}</h2>
      <div className={`stage-frame stage-frame-${layout}`}>
        <Whiteboard dsl={dsl} cursor={player.cursor} layout={layout} className="stage-svg" askable={{ selected, onSelect: select, label }} />
      </div>
      <p aria-live="polite" className="stage-caption" data-stage-caption={caption?.id ?? ''} data-stage-caption-role={caption?.role ?? 'none'}>
        {caption?.text ?? ''}
      </p>
      <div aria-live="polite" data-lesson-answer-for={asked ? anchorKey(asked.question.anchor) : ''} data-lesson-answer-step={answeredStep ?? ''}>
        {busy ? <p role="status"><ProcessStatus stage="submitting" /></p>
          : asked?.view?.kind === 'answered' ? <p className="stage-caption" data-lesson-answer>{asked.view.text}</p>
            : asked?.view?.kind === 'no_answer' ? (
              <p data-stage-ask-fallback data-lesson-no-answer={asked.view.reason}>
                {asked.view.reason === 'limit' ? L.limit : C.no_answer}{' '}
                <button type="button" data-stage-ask-again onClick={() => replay(asked.question)}>{C.again}</button>
              </p>
            ) : asked?.error ? (
              <p role="alert" data-lesson-ask-error={asked.error}>
                {E[asked.error]}{' '}
                {asked.error === 'retry'
                  ? <button type="button" data-lesson-ask-retry onClick={() => void send(asked)}>{E.retryButton}</button>
                  : <button type="button" data-stage-ask-again onClick={() => replay(asked.question)}>{C.again}</button>}
              </p>
            ) : null}
      </div>
      <div className="stage-ask-row">
        <button type="button" ref={stepButtonRef} disabled={player.cursor === 0 || busy} data-stage-ask-step aria-pressed={selected?.kind === 'step'}
          onClick={() => select({ kind: 'step', id: dsl.steps[player.cursor - 1]!.id })}>{C.ask_step}</button>{' '}
        <span className="muted">{L.questionsLeft}</span>{' '}<span data-value="questions-left">{left}</span>{' '}<span className="muted">{L.questionsUnit}</span>
      </div>
      {selected ? (
        <div ref={menuRef} role="group" aria-label={C.menu} className="stage-ask-menu" data-stage-ask-menu={anchorKey(selected)}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); close(true); } }}>
          {intents.map((intent) => (
            <button key={intent} type="button" data-stage-ask-intent={intent} onClick={() => choose(intent)}>{INTENT_LABEL[intent]}</button>
          ))}
          <button type="button" data-stage-ask-cancel onClick={() => close(true)}>{C.cancel}</button>
        </div>
      ) : null}
      <p data-stage-status={player.status} data-stage-cursor={player.cursor}>
        <span data-value="cursor">{player.cursor}</span> / <span data-value="total">{player.total}</span>
      </p>
      <div className="stage-controls">
        {player.status === 'playing'
          ? <button type="button" onClick={() => dispatch({ type: 'pause' })} data-stage-action="pause">{L.pause}</button>
          : <button type="button" onClick={() => dispatch({ type: 'play' })} disabled={player.status === 'ended'} data-stage-action="play">{L.play}</button>}
        <button type="button" onClick={() => dispatch({ type: 'prev' })} disabled={player.cursor === 0} data-stage-action="prev">{L.prev}</button>
        <button type="button" onClick={() => dispatch({ type: 'next' })} disabled={player.status === 'ended'} data-stage-action="next">{L.next}</button>
        <button type="button" onClick={() => dispatch({ type: 'reset' })} data-stage-action="reset">{L.restart}</button>
      </div>
    </section>
  );
}
