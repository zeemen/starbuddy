'use client';
// 做题等待时的动作级过程状态（specs/process-status；借鉴 DeepTutor 的“一行状态 + 用时”，只取动作级）：
//   - 一行：形状标记（进行中是缓慢转动的空心圆环，reduce-motion 时不转；状态不靠颜色表达）+ 动作标签；
//   - 等待超过 3 秒显示已等待的秒数，超过 15 秒多一句“还在处理”（阈值在 process-status.ts）；阶段变化时重新计时；
//   - 不展示模型思考、工具调用、用量与服务端返回的任何文字；组件里没有中文，文案按阶段编码从 copy 取（{{TODO_COPY:status.*}} 占位，人写人审）；
//   - 读屏：组件放在宿主已有的 role="status" 行里，阶段变化时由那一行播报；秒数在 aria-hidden 里，不每秒打扰；“还在处理”出现时播报一次。
import { useEffect, useState } from 'react';
import { PROCESS_STATUS as C } from './copy.ts';
import { waitView, type ProcessStage } from './process-status.ts';

export function ProcessStatus({ stage }: { stage: ProcessStage }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [stage]);
  const w = waitView(elapsed);
  return (
    <span className="process-status" data-testid="process-status" data-stage={stage}>
      <span className="process-mark" aria-hidden="true" />
      <span>{C[stage]}</span>
      {w.seconds !== null ? (
        <span className="process-waited" aria-hidden="true" data-testid="process-waited">
          <span data-value="waited-sec">{w.seconds}</span>
          <span>{C.seconds}</span>
        </span>
      ) : null}
      {w.still ? <span className="process-still" data-testid="process-still">{C.still}</span> : null}
    </span>
  );
}
