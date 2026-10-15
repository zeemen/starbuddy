// 学生端“今日”（spec 第 6 节）：按在读学科并列，每科按优先级；某科暂无安排不影响其他科。
// 推荐理由按判断显示（TaskList）；关于学生的句子只来自 RenderedStatement.text，带 data-claim-id。
// 跨科编排（PLT-11，开关 today_orch 默认关）：前端开关打开、且 BFF 返回了 ordered 时，在按科列表之前显示跨科有序列表，每条带“去做”；
// 开关关或 BFF 没返回 ordered 时与开关上线前零差异。
// 今日计划快照（PLT-15 PR2）：BFF 透出 snapshot = frozen 且条目带 task_id 时，学生本人的跨科列表每条另有“完成”“先跳过”。
// 本月计划入口（PLT-14 第 3 步，开关 monthly_plan 默认关）：打开时标题下多一条“本月计划”链接（只链到学习计划）；关时零差异。
import { notFound, redirect } from 'next/navigation';
import { fetchToday } from '../../../../lib/shell-api.ts';
import { subjectLabel, TODAY, TODAY_ORCH } from '../../../../shell/copy.ts';
import { requireStudent } from '../../../../shell/guard.ts';
import { TaskList } from '../../../../shell/TaskList.tsx';
import { todayOrchEnabled } from '../../../../shell/today-orch.ts';
import { OrderedTaskList } from '../../../../today/OrderedTaskList.tsx';
import { showPlanMarks } from '../../../../today/plan-mark.ts';
import { planMarkTicket } from '../../../../today/plan-mark-server.ts';
import { presenceKey } from '../../../../behavior/presence-server.ts';
import { MyPlanLink } from '../../../../monthly-plan/MyPlanLink.tsx';

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
  // “完成”“先跳过”（PLT-15 PR2）只在学生本人会话显示：会话里的学生就是路径里的学生（BFF 与库另外只认本人）
  const ownStudent = ps.me.account_kind === 'student' && ps.me.student_id === studentId;
  // 每个带任务 ID 的条目签一张一次性票据（幂等键的 nonce；绑定会话、学生、任务）。没配签名密钥（正式环境）时不签，也就不显示按钮
  const markKey = ownStudent && showOrch && ordered ? presenceKey() : null;
  const tickets: Record<string, string> = {};
  if (markKey && ordered) {
    for (const t of ordered.items) {
      if (showPlanMarks(ordered.snapshot, t.task_id)) tickets[t.task_id] = planMarkTicket({ key: markKey, token: ps.session.token, student: studentId }, t.task_id);
    }
  }
  return (
    <main data-testid="today">
      <h1>{TODAY.title}</h1>
      {/* 本月计划入口（PLT-14 第 3 步，开关 monthly_plan 默认关；关时不渲染任何元素）：只给学生本人 */}
      {ps.me.account_kind === 'student' && ps.me.student_id === studentId ? <MyPlanLink studentId={studentId} /> : null}
      {notInGrade ? <p className="muted" data-testid="today-not-in-grade">{TODAY.notInGrade}</p>
        : sections.length === 0 ? <p className="muted">{TODAY.noSubjects}</p> : null}
      {showOrch ? (
        <section className="today-orch" data-testid="today-orch" aria-labelledby="today-orch-heading">
          <h2 id="today-orch-heading">{TODAY_ORCH.heading}</h2>
          {ordered === null ? <p className="muted" data-testid="today-orch-unavailable">{TODAY_ORCH.unavailable}</p>
            : ordered.items.length === 0 ? <p className="muted" data-testid="today-orch-empty">{TODAY_ORCH.empty}</p>
            : <OrderedTaskList items={ordered.items} studentId={studentId} snapshot={ordered.snapshot}
                {...(ownStudent ? { csrf: ps.session.csrf, tickets } : {})} />}
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
