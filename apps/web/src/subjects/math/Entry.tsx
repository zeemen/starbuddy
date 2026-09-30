// 数学科目页（live）：4-H 只显示今天的数学任务与数学星空入口；作答、诊断、批改等消耗类入口在冲刺 5 联调。
// 数据来自 BFF 的今日计划（不调原产品、不读适配器内容包，规则 1）。
import type { TodaySection } from '../../lib/shell-api.ts';
import { SUBJECTS, TODAY } from '../../shell/copy.ts';
import { TaskList } from '../../shell/TaskList.tsx';

export function MathEntry({ section, audience, studentId }: { section: TodaySection | null; audience: 'student' | 'guardian'; studentId?: string }) {
  return (
    <section aria-labelledby="subject-today">
      <h2 id="subject-today">{SUBJECTS.todayTasks}</h2>
      {!section ? <p className="muted">{SUBJECTS.notEnrolled}</p>
        : section.status !== 'ok' || section.tasks.length === 0 ? <p className="muted" data-testid="nothing-today">{TODAY.nothingToday}</p>
          : <TaskList section={section} audience={audience} studentId={studentId} />}
    </section>
  );
}
