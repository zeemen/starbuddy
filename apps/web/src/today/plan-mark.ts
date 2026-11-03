// 今日计划“完成”“先跳过”（PLT-15 PR2，specs/today-plan-snapshot-plt15 范围第 3 条）的纯函数：什么时候显示按钮、收哪两种动作。
// 只在学生本人的今日页、跨科列表里带任务 ID（BFF 透出的 task_id，学习服务冻结了当天快照）的条目上显示；
// snapshot 为 not_frozen、没有 snapshot、或条目没有 task_id 时不显示。客户端组件也用它，所以这里不引用 node:crypto。

export const PLAN_ACTIONS = ['plan_done', 'plan_skip'] as const;
export type PlanAction = (typeof PLAN_ACTIONS)[number];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export const isPlanAction = (v: unknown): v is PlanAction => typeof v === 'string' && (PLAN_ACTIONS as readonly string[]).includes(v);
export const isTaskId = (v: unknown): v is string => typeof v === 'string' && UUID.test(v);

/** 这一条显示“完成”“先跳过”吗：当天快照已冻结、且条目带合格的任务 ID */
export function showPlanMarks(snapshot: string | undefined, taskId: unknown): taskId is string {
  return snapshot === 'frozen' && isTaskId(taskId);
}
