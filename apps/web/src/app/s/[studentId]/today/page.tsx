// 学生端“今日”（spec 第 6 节）：按在读学科并列，每科按优先级；某科暂无安排不影响其他科。
// 推荐理由按判断显示（TaskList）；关于学生的句子只来自 RenderedStatement.text，带 data-claim-id。
// 跨科编排（PLT-11，开关 today_orch 默认关）：前端开关打开、且 BFF 返回了 ordered 时，在按科列表之前显示跨科有序列表，每条带“去做”；
// 开关关或 BFF 没返回 ordered 时与开关上线前零差异。
import { notFound, redirect } from 'next/navigation';
import { fetchToday } from '../../../../lib/shell-api.ts';
import { subjectLabel, TODAY, TODAY_ORCH } from '../../../../shell/copy.ts';
import { requireStudent } from '../../../../shell/guard.ts';
import { TaskList } from '../../../../shell/TaskList.tsx';
import { todayOrchEnabled } from '../../../../shell/today-orch.ts';
import { OrderedTaskList } from '../../../../today/OrderedTaskList.tsx';

export const dynamic = 'force-dynamic';

export default async function TodayPage({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  const ps = await requireStudent(studentId);
  const r = await fetchToday(ps.session, studentId);
  if (!r.ok) {
    if (r.status === 401) redirect('/login');
    notFound();
  }
  const sections = r.value.subjects;
  // 当前年级（/me，BFF 按届别推算）不在 7–9：今日计划不排学科，说明原因而不是“还没有在读的科目”（PR3）
  const notInGrade = ps.me.account_kind === 'student' && ps.me.grade === null;
  const ordered = r.value.ordered;
  const showOrch = todayOrchEnabled() && ps.me.account_kind === 'student' && ordered !== undefined && !notInGrade && sections.length > 0;
  return (
    <main data-testid="today">
      <h1>{TODAY.title}</h1>
      {notInGrade ? <p className="muted" data-testid="today-not-in-grade">{TODAY.notInGrade}</p>
        : sections.length === 0 ? <p className="muted">{TODAY.noSubjects}</p> : null}
      {showOrch ? (
        <section className="today-orch" data-testid="today-orch" aria-labelledby="today-orch-heading">
          <h2 id="today-orch-heading">{TODAY_ORCH.heading}</h2>
          {ordered === null ? <p className="muted" data-testid="today-orch-unavailable">{TODAY_ORCH.unavailable}</p>
            : ordered.items.length === 0 ? <p className="muted" data-testid="today-orch-empty">{TODAY_ORCH.empty}</p>
            : <OrderedTaskList items={ordered.items} studentId={studentId} />}
          {ordered && ordered.truncated_count > 0 ? (
            <p className="muted" data-testid="today-orch-truncated">{TODAY_ORCH.truncated} <span data-value="count">{ordered.truncated_count}</span></p>
          ) : null}
          {ordered?.partial ? <p className="muted" data-testid="today-orch-partial">{TODAY_ORCH.partial}</p> : null}
        </section>
      ) : null}
      {sections.map((s) => (
        <section key={s.subject} className="today-subject" data-subject={s.subject} aria-labelledby={`today-${s.subject}`}>
          <h2 id={`today-${s.subject}`}>{subjectLabel(s.subject)}</h2>
          {s.status !== 'ok' || s.tasks.length === 0
            ? <p className="muted" data-testid="nothing-today">{TODAY.nothingToday}</p>
            : <TaskList section={s} audience="student" studentId={studentId} />}
        </section>
      ))}
    </main>
  );
}
