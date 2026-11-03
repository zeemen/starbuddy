// 跨科有序列表（PLT-11，开关 today_orch 默认关，只给学生本人的今日页）：学科、类别、标题、预计分钟（数字在 data-value 里）、“去做”、推荐理由。
// 推荐理由用壳的同一个 Reason（src/shell/TaskList.tsx；BFF 只沿用按科任务已决定的那一条）：关于学生的句子仍只在 TaskList 里以 data-claim-id 渲染。
// 不渲染来源、条目引用（只进“去做”地址）、理由编码、掌握、置信度、证据。
// “完成”“先跳过”（PLT-15 PR2）：只在调用方给了 csrf 与该任务的票据（学生本人会话）、当天快照已冻结、条目带任务 ID 时显示（showPlanMarks）；
// 任务 ID 与票据只进表单隐藏字段。
import type { OrderedTask, OrderedToday } from '../lib/shell-api.ts';
import { subjectLabel, TODAY_ORCH, TODAY_ORCH_KIND } from '../shell/copy.ts';
import { Reason } from '../shell/TaskList.tsx';
import { goHref } from './orch.ts';
import { showPlanMarks } from './plan-mark.ts';
import { PlanMarks } from './PlanMarks.tsx';

export function OrderedTaskList({ items, studentId, snapshot, csrf, tickets }: {
  items: OrderedTask[]; studentId: string; snapshot?: OrderedToday['snapshot']; csrf?: string;
  /** 任务 ID → 服务端签的票据（只在学生本人会话、配了签名密钥时由今日页给） */
  tickets?: Readonly<Record<string, string>>;
}) {
  return (
    <ol className="task-list" data-testid="today-orch-list">
      {items.map((t, i) => {
        const href = goHref(studentId, t);
        return (
          <li key={i} className="task" data-testid="today-orch-item">
            <span className="muted">{subjectLabel(t.subject)}</span>{' '}
            <span className="task-kind">{TODAY_ORCH_KIND[t.kind] ?? TODAY_ORCH.untitled}</span>{' '}
            {t.node_name ? <span className="task-title" data-value="node">{t.node_name}</span>
              : <span className="task-title">{TODAY_ORCH.untitled}</span>}{' '}
            <span className="muted">{TODAY_ORCH.minutes} <span data-value="minutes">{t.estimated_minutes}</span></span>{' '}
            {href ? <a href={href} data-testid="today-orch-go">{TODAY_ORCH.go}</a> : null}
            {csrf && showPlanMarks(snapshot, t.task_id) && tickets?.[t.task_id]
              ? <PlanMarks studentId={studentId} taskId={t.task_id} csrf={csrf} ticket={tickets[t.task_id]!} /> : null}
            <Reason r={t.rationale} audience="student" studentId={studentId} />
          </li>
        );
      })}
    </ol>
  );
}
