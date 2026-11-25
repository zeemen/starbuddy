// 做题等待时的动作级过程状态（specs/process-status）：阶段编码与等待阈值（纯函数，组件 ProcessStatus.tsx 与单测共用）。
// 阶段只用前端真实经历的步骤（一次 server action 请求进行中）；前端看不到服务端内部进行到哪一步（判分、写证据、重算掌握都在一次调用里），
// 所以没有“正在核对答案”“正在判分”一类阶段——写出来就是猜，还会让学生把等待和对错联系起来（规则 3）。

/** preparing：开始、取题、读题的请求进行中；submitting：作答请求进行中；next：作答已成功、正在读下一题（诊断自动连读） */
export const PROCESS_STAGES = ['preparing', 'submitting', 'next'] as const;
export type ProcessStage = (typeof PROCESS_STAGES)[number];

/** 等待超过这么多秒，在动作标签后显示已等待的秒数 */
export const WAITED_AFTER_SEC = 3;
/** 等待超过这么多秒，再加一句“还在处理”（不给预计时间，不说快慢原因） */
export const STILL_AFTER_SEC = 15;

export function waitView(elapsedSec: number): { seconds: number | null; still: boolean } {
  const s = Number.isFinite(elapsedSec) && elapsedSec > 0 ? Math.floor(elapsedSec) : 0;
  return { seconds: s >= WAITED_AFTER_SEC ? s : null, still: s >= STILL_AFTER_SEC };
}
