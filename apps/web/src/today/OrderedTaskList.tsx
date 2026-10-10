// 跨科有序列表（PLT-11，开关 today_orch 默认关，只给学生本人的今日页）：学科、类别、标题、预计分钟（数字在 data-value 里）、“去做”、推荐理由。
// 推荐理由用壳的同一个 Reason（src/shell/TaskList.tsx；BFF 只沿用按科任务已决定的那一条）：关于学生的句子仍只在 TaskList 里以 data-claim-id 渲染。
// 不渲染来源、条目引用（只进“去做”地址）、理由编码、掌握、置信度、证据。
import type { OrderedTask } from '../lib/shell-api.ts';
import { subjectLabel, TODAY_ORCH, TODAY_ORCH_KIND } from '../shell/copy.ts';
import { Reason } from '../shell/TaskList.tsx';
import { goHref } from './orch.ts';

export function OrderedTaskList({ items, studentId }: { items: OrderedTask[]; studentId: string }) {
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
            <Reason r={t.rationale} audience="student" studentId={studentId} />
          </li>
        );
      })}
    </ol>
  );
}
