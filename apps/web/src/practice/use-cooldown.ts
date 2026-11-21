// 提交按钮的冷却（specs/retry-after-cooldown）：再练与复测的作答组件共用。只在组件内存里计时，组件卸载或换题时清掉；
// 冷却结束只恢复按钮，不替学生自动提交。
import { useCallback, useEffect, useRef, useState } from 'react';

export function useCooldown(): { cooling: boolean; start(ms: number): void; clear(): void } {
  const [cooling, setCooling] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clear = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    setCooling(false);
  }, []);
  const start = useCallback((ms: number) => {
    if (timer.current !== null) clearTimeout(timer.current);
    setCooling(true);
    timer.current = setTimeout(() => { timer.current = null; setCooling(false); }, ms);
  }, []);
  useEffect(() => () => { if (timer.current !== null) clearTimeout(timer.current); }, []);
  return { cooling, start, clear };
}
