// 计量拒绝原因的共用提示（spec 第 9 节“拒绝提示组件”）：冲刺 5 的诊断、批改、讲评入口用；4-H 没有消耗类入口。
// 穷举检查：services/bff/tests/denial-copy.test.ts 用 services/bff/src/metering/meter.ts 的 ReserveResult / MeteredCallResult 类型核对
// 这里的键，计量新增拒绝原因而这里没有文案时 BFF 的类型检查（make check）失败。本文件不能有 import（BFF 的 tsc 也编译它）。
// 系统口吻；学生端不出现购买、升级或营销类文字（规格书 V1.2 U9）；不评价、不催促。

export type DenialCode =
  | 'retained_no_ai' | 'daily_cap_reached' | 'curfew' | 'curfew_soon' | 'no_entitlement' | 'insufficient'
  | 'conflict' | 'dedup_in_progress' | 'daily_limit_reached';

export const DENIAL_COPY: Record<DenialCode, string> = {
  retained_no_ai: '现在可以查看星图和历史记录，也可以做题库自练；这项功能暂时不可用。',
  daily_cap_reached: '今天的陪练到这里，可以继续做题库自练。',
  curfew: '现在是休息时间，明天再继续。',
  curfew_soon: '快到休息时间了，这项功能需要的时间比剩下的时间长。',
  no_entitlement: '这项功能暂时不可用，需要家长开通。',
  insufficient: '这项功能本周期的次数已经用完。',
  conflict: '同一个请求正在处理，请稍后再看。',
  dedup_in_progress: '同一个请求正在处理，请稍后再看。',
  daily_limit_reached: '今天的题库自练次数已经用完，明天可以继续。',
};

/** 带数值的提示（今日剩余分钟、距休息时间分钟） */
export const denialWithMinutes = (code: 'daily_cap_reached' | 'curfew_soon', minutes: number): string =>
  code === 'curfew_soon' ? `还有 ${minutes} 分钟到休息时间，这项功能需要的时间比剩下的时间长。` : `今天还剩 ${minutes} 分钟。`;
