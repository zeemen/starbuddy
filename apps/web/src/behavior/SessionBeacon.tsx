'use client';
// 学生壳的会话起止（SIE-05 PR2）：壳布局只在学生本人的会话下渲染它（家长端不挂）。不渲染任何元素，不显示任何内容。
//   - 进入页面：报 ping（服务端按 cookie 判断是接着上一页的会话，还是开始新会话并写 session_start）；
//   - 有操作（点按、按键、滚动）：重置无活动计时；距上次报告超过 HEARTBEAT_SEC 才再报一次 ping（只更新 cookie 里的最近活动时间，不写事件）；
//   - 切到后台、离开页面（visibilitychange → hidden）：报 touch（只更新还在的会话）；
//   - 无活动满 IDLE_SEC：报 end（服务端写 session_end）；之后再有操作就重新 ping，开始新会话。
// 请求失败一律忽略：不提示、不重试（下一次进入页面或有操作时自然再报）。只发到本站的 /s/{id}/presence，浏览器不直连 BFF。
import { useEffect, useRef } from 'react';
import { HEARTBEAT_SEC, IDLE_SEC, type Entry } from './presence.ts';

type Op = 'ping' | 'touch' | 'end';

export function SessionBeacon({ studentId, csrf, entry }: { studentId: string; csrf: string; entry: Entry }) {
  // React 严格模式会把副作用多跑一遍：首次 ping 只发一次
  const pinged = useRef(false);
  useEffect(() => {
    const send = (op: Op) => {
      const body = JSON.stringify(op === 'ping' ? { csrf, op, entry } : { csrf, op });
      fetch(`/s/${studentId}/presence`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body, cache: 'no-store', credentials: 'same-origin', keepalive: true,
      }).catch(() => undefined);
    };
    let lastSent = Date.now();
    let ended = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { ended = true; send('end'); }, IDLE_SEC * 1000);
    };
    const activity = () => {
      if (ended || Date.now() - lastSent >= HEARTBEAT_SEC * 1000) {
        ended = false;
        lastSent = Date.now();
        send('ping');
      }
      arm();
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        if (!ended) send('touch');
      } else {
        activity();
      }
    };
    if (!pinged.current) {
      pinged.current = true;
      send('ping');
    }
    arm();
    const opts = { passive: true } as const;
    window.addEventListener('pointerdown', activity, opts);
    window.addEventListener('keydown', activity, opts);
    window.addEventListener('scroll', activity, opts);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('pointerdown', activity);
      window.removeEventListener('keydown', activity);
      window.removeEventListener('scroll', activity);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [studentId, csrf, entry]);
  return null;
}
