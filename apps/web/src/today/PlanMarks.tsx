'use client';
// 今日计划跨科列表每条任务的“完成”“先跳过”（PLT-15 PR2）：提交给 server action markPlanAction（双重提交 CSRF、本人核对、
// 幂等键按任务与动作固定，都在服务端）。只由 OrderedTaskList 在当天快照已冻结、条目带任务 ID 时渲染（showPlanMarks）。
// 记下后只显示中性的“已记下”并标出按下的是哪一个；没记下时什么都不显示（对学生静默）。两个按钮都一直可点：同一任务以最后一次为准。
// 每次记下后换上服务端返回的新票据（幂等键随之换），同一页面内“完成 → 跳过 → 完成”三次都入库（审查阻断的处理）。
// 文案只来自 src/shell/copy.ts 的 TODAY_MARK（占位），不评价学生、不出现人设话术；不写 localStorage。
import { useActionState, useEffect, useState } from 'react';
import { markPlanAction, type PlanMarkResult } from '../app/s/[studentId]/actions.ts';
import { TODAY_MARK } from '../shell/copy.ts';

export function PlanMarks({ studentId, taskId, csrf, ticket }: { studentId: string; taskId: string; csrf: string; ticket: string }) {
  const [state, formAction, pending] = useActionState<PlanMarkResult | null, FormData>(markPlanAction, null);
  // 票据（服务端签，幂等键的 nonce 在里面）：每次记下后换成 action 返回的新票据；没记下时保留原票据（重试是同一个键）
  const [current, setCurrent] = useState(ticket);
  useEffect(() => { if (state?.ticket) setCurrent(state.ticket); }, [state]);
  // 水合完成标记：端到端测试先等它再点（与 ResultForm 同一做法）
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const marked = state?.code === 'recorded' ? state.action : null;
  return (
    <form action={formAction} className="inline-form plan-marks" data-testid="today-plan-marks" aria-busy={pending}
          data-hydrated={hydrated ? 'true' : undefined} data-marked={marked === 'plan_done' ? 'done' : marked === 'plan_skip' ? 'skip' : ''}>
      <input type="hidden" name="csrf" value={csrf} />
      <input type="hidden" name="student" value={studentId} />
      <input type="hidden" name="task" value={taskId} />
      <input type="hidden" name="ticket" value={state?.ticket ?? current} />
      <button type="submit" name="act" value="plan_done" data-testid="today-plan-done" aria-pressed={marked === 'plan_done'}>{TODAY_MARK.done}</button>{' '}
      <button type="submit" name="act" value="plan_skip" data-testid="today-plan-skip" aria-pressed={marked === 'plan_skip'}>{TODAY_MARK.skip}</button>{' '}
      <span role="status" className="muted" data-testid="today-plan-status">{marked ? TODAY_MARK.recorded : ''}</span>
    </form>
  );
}
