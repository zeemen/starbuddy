'use client';
// 探针演示页的客户端部分：加载时记 session_start；先问“能做对吗”，选择后经 server action 写入，跳过不写；
// 然后显示题号占位。点“结束”或离开页面（pagehide）时记 session_end（同一个幂等键，只记一次）。
import { useEffect, useRef, useState } from 'react';
import { ConfidenceProbe } from '../../../../probe/ConfidenceProbe.tsx';
import { DEMO_COPY, type Prediction } from '../../../../probe/copy.ts';
import { probeAction, sessionEndAction, sessionStartAction } from '../actions.ts';

export function ProbeDemo({ student, itemId }: { student: string; itemId: string }) {
  // 每次打开页面一组幂等键：重试、React 严格模式重复执行副作用都不会重复记录
  const [keys] = useState(() => ({ start: crypto.randomUUID(), end: crypto.randomUUID(), probe: crypto.randomUUID() }));
  const openedAt = useRef(Date.now());
  const started = useRef(false);
  const ended = useRef(false);
  const itemHeading = useRef<HTMLHeadingElement>(null);
  const [phase, setPhase] = useState<'probe' | 'item' | 'ended'>('probe');
  const [notSaved, setNotSaved] = useState(false);

  const end = () => {
    if (ended.current) return;
    ended.current = true;
    const duration = Math.min(86400, Math.max(0, Math.round((Date.now() - openedAt.current) / 1000)));
    return sessionEndAction(student, duration, keys.end);
  };

  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void sessionStartAction(student, keys.start);
    }
    const onHide = () => void end();
    window.addEventListener('pagehide', onHide);
    return () => window.removeEventListener('pagehide', onHide);
    // 只在首次挂载时执行（student 与幂等键在本页生命周期内不变）
  }, []);

  useEffect(() => {
    if (phase === 'item') itemHeading.current?.focus();
  }, [phase]);

  const answer = async (p: Prediction) => {
    const r = await probeAction(student, p, keys.probe);
    setNotSaved(!r.ok);
    setPhase('item');
  };

  return (
    <div data-testid="probe-demo" data-phase={phase}>
      {phase === 'probe' ? <ConfidenceProbe onAnswer={answer} onSkip={() => setPhase('item')} /> : null}
      {phase !== 'probe' ? (
        <section aria-labelledby="demo-item">
          <h2 id="demo-item" ref={itemHeading} tabIndex={-1}>{DEMO_COPY.itemHeading}</h2>
          <p data-testid="item-placeholder">{DEMO_COPY.itemPlaceholder(itemId)}</p>
        </section>
      ) : null}
      <p role="status">{notSaved ? DEMO_COPY.notSaved : phase === 'ended' ? DEMO_COPY.ended : ''}</p>
      {phase !== 'ended' ? (
        <p>
          <button type="button" onClick={async () => { await end(); setPhase('ended'); }}>{DEMO_COPY.end}</button>
        </p>
      ) : null}
    </div>
  );
}
