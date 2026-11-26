'use client';
// 舞台样张页的客户端部分（specs/stage-whiteboard-step1、specs/stage-dsl-v0.2）：合成 DSL 经 packages/stage 的版本迁移入口与校验器、台词稿经配对核对后，由播放状态机驱动白板逐步播放。
// 只在内存里算，不连 BFF、不接 teach、不计量、不落库、不写任何浏览器存储。apps/web 只经 packages/stage 的接口使用舞台（ADR 07 决定 7）。
// 可提问模式（specs/stage-point-to-ask 第一阶段）：指着白板元素、槽位或这一步，从四种问题里选一个；“再讲一遍”在本地跳回那一步重放。
// 其余三种（specs/stage-point-to-ask-answer PR ②，预制答疑）：回答只取台词稿里审过的那句（answerFor），找不到就给兜底与“再讲一遍”；
// 不调模型、不发请求、不写存储、不作证据。提问对象经 validateStageQuestion 校验，台词稿经 migrateStageScript 读入。
import { createElement, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import {
  answerFor, captionAt, DSL_VERSION, initialPlayer, LAYOUT_NAMES, migrateStageDsl, migrateStageScript, originsAt, QUESTION_INTENTS, replayCursor, step,
  validateStageQuestion, type LayoutName, type QuestionIntent, type ScriptLine, type StageAnchor, type StageQuestion, type ValidatedStageDsl,
} from '../../../../packages/stage/src/index.ts';
import { createWhiteboard } from '../../../../packages/stage/src/Whiteboard.tsx';
import { STAGE_ASK as C } from '../shell/copy.ts';
import { STAGE_INVALID_SAMPLE, STAGE_SAMPLE, STAGE_SCRIPT } from './sample.ts';

const Whiteboard = createWhiteboard(createElement);

/** 播放中每一步停留的时间 */
const TICK_MS = 1200;

const LAYOUT_LABEL: Record<LayoutName, string> = { desktop: '桌面', tablet: '平板', mobile: '手机' };
const INTENT_LABEL: Record<QuestionIntent, string> = { why_step: C.why_step, what_is: C.what_is, where_from: C.where_from, again: C.again };
const anchorKey = (a: StageAnchor): string => `${a.kind}:${a.id}`;

/** 提问落在第几步（1 起算）：元素是它写上去的那一步，整一步是那一步，槽位是当前这一步 */
function anchorStep(dsl: ValidatedStageDsl, q: StageQuestion): number {
  if (q.anchor.kind === 'element') return (originsAt(dsl, q.cursor).get(q.anchor.id) ?? q.cursor - 1) + 1;
  if (q.anchor.kind === 'step') return dsl.steps.findIndex((s) => s.id === q.anchor.id) + 1;
  return q.cursor;
}
const STATUS_LABEL = { idle: '未开始', playing: '播放中', paused: '已暂停', ended: '已结束' } as const;

export function StageDemo() {
  // 经版本迁移入口读入（specs/stage-dsl-versioning）：当前版本直接校验，旧版本沿迁移阶梯升级后再校验
  const valid = useMemo(() => migrateStageDsl(STAGE_SAMPLE), []);
  const invalid = useMemo(() => migrateStageDsl(STAGE_INVALID_SAMPLE), []);
  // 台词稿与 DSL 配对核对（specs/stage-dsl-v0.2）：只有核对通过的稿才出字幕，字幕文字只取自稿，组件不拼接
  const pair = useMemo(() => (valid.ok ? migrateStageScript(valid.dsl, STAGE_SCRIPT) : null), [valid]);
  const total = valid.ok ? valid.dsl.steps.length : 1;
  const [player, dispatch] = useReducer(step, total, initialPlayer);
  const [layout, setLayout] = useState<LayoutName>('desktop');
  const [selected, setSelected] = useState<StageAnchor | null>(null);
  // 最近一次提问与它的预制答疑（没有答疑时 line 为 null，显示兜底与“再讲一遍”）
  const [answered, setAnswered] = useState<{ question: StageQuestion; line: ScriptLine | null } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const stepButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<StageAnchor | null>(null);

  // 选中后先暂停（第五节第 5 条），弹出菜单
  const select = useCallback((a: StageAnchor) => {
    if (player.status === 'playing') dispatch({ type: 'pause' });
    setAnswered(null);
    setSelected(a);
  }, [player.status]);
  const close = useCallback((refocus: boolean) => {
    restoreFocus.current = refocus ? selected : null;
    setSelected(null);
  }, [selected]);

  // 步数变了（前进、后退、跳转、重放）：收起菜单，原来选中的东西可能已不在白板上
  useEffect(() => { setSelected(null); }, [player.cursor]);
  // 菜单打开时焦点到第一个问题；关闭时按需回到原来的元素（或“问这一步”按钮）
  useEffect(() => {
    if (selected) { menuRef.current?.querySelector('button')?.focus(); return; }
    const back = restoreFocus.current;
    restoreFocus.current = null;
    if (!back) return;
    if (back.kind === 'step') stepButtonRef.current?.focus();
    else document.querySelector<SVGElement>(`[data-ask="${anchorKey(back)}"]`)?.focus();
  }, [selected]);
  // 点菜单与可提问元素以外的地方：取消
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

  if (!valid.ok || !pair?.ok) return <p role="alert" data-stage-error>合成样例没有通过校验</p>;
  const dsl = valid.dsl;
  const caption = captionAt(dsl, pair.script, player.cursor);
  const label = (a: StageAnchor, at: number | null, index: number): string => {
    if (a.kind === 'slot') return dsl.slots?.find((x) => x.id === a.id)?.kind === 'student_answer' ? C.slot_student_answer : C.slot_item_figure;
    return C.element_label.replace('{step}', String(at ?? '')).replace('{index}', String(index));
  };
  // 本地重放：跳回那一步的开头再播放（不出提问对象）
  const replay = (q: StageQuestion) => {
    dispatch({ type: 'seek', step: replayCursor(dsl, q) });
    dispatch({ type: 'play' });
  };
  const choose = (intent: QuestionIntent) => {
    if (!selected) return;
    const r = validateStageQuestion(dsl, { dsl_version: DSL_VERSION, cursor: player.cursor, anchor: selected, intent });
    if (!r.ok) { close(false); return; }
    if (intent === 'again') {
      replay(r.question);
      setSelected(null);
      return;
    }
    const stepId = dsl.steps[anchorStep(dsl, r.question) - 1]!.id;
    setAnswered({ question: r.question, line: answerFor(dsl, pair.script, stepId, intent) });
    close(true);
  };
  return (
    <>
      <section aria-labelledby="stage-play" data-stage-demo>
        <h2 id="stage-play">逐步播放</h2>
        <fieldset className="stage-layouts">
          <legend>布局</legend>
          {LAYOUT_NAMES.map((name) => (
            <label key={name}>
              <input type="radio" name="stage-layout" value={name} checked={layout === name} onChange={() => setLayout(name)} />
              {LAYOUT_LABEL[name]}
            </label>
          ))}
        </fieldset>
        <div className={`stage-frame stage-frame-${layout}`}>
          <Whiteboard dsl={dsl} cursor={player.cursor} layout={layout} className="stage-svg"
            askable={{ selected, onSelect: select, label }} />
        </div>
        <p aria-live="polite" className="stage-caption" data-stage-caption={caption?.id ?? ''} data-stage-caption-role={caption?.role ?? 'none'}>
          {caption?.text ?? ''}
        </p>
        <div className="stage-ask-row">
          <button type="button" ref={stepButtonRef} disabled={player.cursor === 0} data-stage-ask-step
            aria-pressed={selected?.kind === 'step'}
            onClick={() => select({ kind: 'step', id: dsl.steps[player.cursor - 1]!.id })}>{C.ask_step}</button>
        </div>
        {selected ? (
          <div ref={menuRef} role="group" aria-label={C.menu} className="stage-ask-menu" data-stage-ask-menu={anchorKey(selected)}
            onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); close(true); } }}>
            {QUESTION_INTENTS.map((intent) => (
              <button key={intent} type="button" data-stage-ask-intent={intent} onClick={() => choose(intent)}>{INTENT_LABEL[intent]}</button>
            ))}
            <button type="button" data-stage-ask-cancel onClick={() => close(true)}>{C.cancel}</button>
          </div>
        ) : null}
        <div aria-live="polite" data-stage-ask-received={answered?.question.intent ?? ''}
          data-stage-ask-anchor={answered ? anchorKey(answered.question.anchor) : ''}>
          {answered?.line ? (
            <p className="stage-caption" data-stage-ask-answer={answered.line.id} data-stage-ask-answer-role={answered.line.role}>{answered.line.text}</p>
          ) : answered ? (
            <p data-stage-ask-fallback>
              {C.no_answer}{' '}
              <button type="button" data-stage-ask-again onClick={() => replay(answered.question)}>{C.again}</button>
            </p>
          ) : null}
        </div>
        <p aria-live="polite" data-stage-status={player.status} data-stage-cursor={player.cursor}>
          {STATUS_LABEL[player.status]} · 第 {player.cursor} / {player.total} 步
        </p>
        <div className="stage-controls">
          {player.status === 'playing'
            ? <button type="button" onClick={() => dispatch({ type: 'pause' })} data-stage-action="pause">暂停</button>
            : <button type="button" onClick={() => dispatch({ type: 'play' })} disabled={player.status === 'ended'} data-stage-action="play">播放</button>}
          <button type="button" onClick={() => dispatch({ type: 'prev' })} disabled={player.cursor === 0} data-stage-action="prev">上一步</button>
          <button type="button" onClick={() => dispatch({ type: 'next' })} disabled={player.status === 'ended'} data-stage-action="next">下一步</button>
          <button type="button" onClick={() => dispatch({ type: 'reset' })} data-stage-action="reset">重来</button>
          <label>
            跳到第
            <select value={player.cursor} onChange={(e) => dispatch({ type: 'seek', step: Number(e.target.value) })} data-stage-action="seek">
              {Array.from({ length: player.total + 1 }, (_, k) => <option key={k} value={k}>{k}</option>)}
            </select>
            步
          </label>
        </div>
      </section>
      <section aria-labelledby="stage-invalid" data-stage-invalid>
        <h2 id="stage-invalid">非法样例被拒</h2>
        {invalid.ok
          ? <p role="alert">非法样例竟然通过了校验</p>
          : <ul>{invalid.errors.map((e) => <li key={`${e.path}-${e.code}`} data-error-code={e.code}><code>{e.path}</code>：{e.code}</li>)}</ul>}
      </section>
    </>
  );
}
