// 学生端“今日”（spec 第 6 节）：按在读学科并列，每科按优先级；某科暂无安排不影响其他科。
// 推荐理由按判断显示（TaskList）；关于学生的句子只来自 RenderedStatement.text，带 data-claim-id。
import { notFound, redirect } from 'next/navigation';
import { fetchToday } from '../../../../lib/shell-api.ts';
import { subjectLabel, TODAY } from '../../../../shell/copy.ts';
import { requireStudent } from '../../../../shell/guard.ts';
import { TaskList } from '../../../../shell/TaskList.tsx';

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
  return (
    <main data-testid="today">
      <h1>{TODAY.title}</h1>
      {notInGrade ? <p className="muted" data-testid="today-not-in-grade">{TODAY.notInGrade}</p>
        : sections.length === 0 ? <p className="muted">{TODAY.noSubjects}</p> : null}
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
