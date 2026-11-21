// 掌握状态 → 星图三态（specs/star-map-sprint3/spec.md 第 2 节；4-C 之前的默认规则，待人确认 #1）。
// 映射只在这里做一处，学生端、家长端、周报共用；前端只认 display 与 retained，不自己推算（规则 2）。
// 只按 state 映射，不看 p_mastery：门槛判定属于学习服务（4-C，由教研配置），BFF 与前端不重算。

export type MasteryState = 'unseen' | 'understand' | 'execute' | 'stable' | 'transfer' | 'retain';
export type Display = 'unlit' | 'half' | 'lit';

export interface StarDisplay {
  display: Display;
  retained: boolean;
  needs_verification: boolean;
}

/** confidence 低于它时标“待验证”（只在列表与详情显示文字，星的三态不变） */
export const VERIFY_BELOW = 0.5;

export const DISPLAY: Record<MasteryState, Display> = {
  unseen: 'unlit',
  understand: 'unlit',
  execute: 'half',
  stable: 'half',
  transfer: 'lit',
  retain: 'lit',
};

/** state 为 null 表示没有当前掌握记录：按 unseen 处理，不标待验证 */
export function toDisplay(state: MasteryState | null, confidence: number | null): StarDisplay {
  if (state === null) return { display: 'unlit', retained: false, needs_verification: false };
  const display = DISPLAY[state];
  if (display === undefined) throw new Error(`unknown mastery state: ${String(state)}`);
  return {
    display,
    retained: state === 'retain',
    needs_verification: confidence !== null && confidence < VERIFY_BELOW,
  };
}
