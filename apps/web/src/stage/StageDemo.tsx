'use client';
// 舞台样张页的客户端部分（specs/stage-whiteboard-step1）：合成 DSL 经 packages/stage 的校验器后，由播放状态机驱动白板逐步播放。
// 只在内存里算，不连 BFF、不接 teach、不计量、不落库、不写任何浏览器存储。apps/web 只经 packages/stage 的接口使用舞台（ADR 07 决定 7）。
import { createElement, useEffect, useMemo, useReducer, useState } from 'react';
import { initialPlayer, LAYOUT_NAMES, step, validateStageDsl, type LayoutName } from '../../../../packages/stage/src/index.ts';
import { createWhiteboard } from '../../../../packages/stage/src/Whiteboard.tsx';
import { STAGE_INVALID_SAMPLE, STAGE_SAMPLE } from './sample.ts';

const Whiteboard = createWhiteboard(createElement);

/** 播放中每一步停留的时间 */
const TICK_MS = 1200;

const LAYOUT_LABEL: Record<LayoutName, string> = { desktop: '桌面', tablet: '平板', mobile: '手机' };
const STATUS_LABEL = { idle: '未开始', playing: '播放中', paused: '已暂停', ended: '已结束' } as const;

export function StageDemo() {
  const valid = useMemo(() => validateStageDsl(STAGE_SAMPLE), []);
  const invalid = useMemo(() => validateStageDsl(STAGE_INVALID_SAMPLE), []);
  const total = valid.ok ? valid.dsl.steps.length : 1;
  const [player, dispatch] = useReducer(step, total, initialPlayer);
  const [layout, setLayout] = useState<LayoutName>('desktop');

  useEffect(() => {
    if (player.status !== 'playing') return undefined;
    const t = setTimeout(() => dispatch({ type: 'tick' }), TICK_MS);
    return () => clearTimeout(t);
  }, [player]);

  if (!valid.ok) return <p role="alert" data-stage-error>合成样例没有通过校验</p>;
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
          <Whiteboard dsl={valid.dsl} cursor={player.cursor} layout={layout} className="stage-svg" />
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
