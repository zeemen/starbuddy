// 学生端“本月计划”页 /s/{studentId}/plan（specs/family-goals-monthly-plan/step3-spec.md 范围 2，PLT-14 第 3 步）：
// 只显示学生自己的学习计划——本月优先知识点、每周节奏、本月复测与分科小复评。没有家长诉求、家长注意事项、上月回顾与分差
// （BFF 的学生端接口本来就不返回；本页只引用学习计划一段的文案与挑法，tests/monthly-plan.test.ts 静态核对，e2e 另查 DOM）。
// 本月还没有生成时显示空状态（学生端不触发生成）。开关 monthly_plan 关时一律 404（在读会话之前判断，不发任何请求）。
// 只给学生本人（家长会话转到孩子页；别的学生 404）。
import { notFound, redirect } from 'next/navigation';
import { fetchMyPlan } from '../../../../monthly-plan/api.ts';
import { monthlyPlanEnabled } from '../../../../monthly-plan/features.ts';
import { MY_PLAN } from '../../../../monthly-plan/student-copy.ts';
import { pickMyPlan } from '../../../../monthly-plan/student-view.ts';
import { StudentPlanSection } from '../../../../monthly-plan/StudentPlanSection.tsx';
import { requireOwnStudent } from '../../../../shell/guard.ts';

export const dynamic = 'force-dynamic';

export default async function MyPlanPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!monthlyPlanEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireOwnStudent(studentId);
  const r = await fetchMyPlan(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const view = r.ok ? pickMyPlan(r.value) : null;
  return (
    <main data-testid="my-plan-page">
      <p><a href={`/s/${studentId}/today`}>{MY_PLAN.back}</a></p>
      <h1>{MY_PLAN.heading}</h1>
      {!view ? <p className="muted" data-unavailable="my-plan">{MY_PLAN.unavailable}</p> : (
        <>
          <p className="muted"><span>{MY_PLAN.month}</span>{' '}<span data-value="month">{view.month}</span></p>
          {!view.plan ? <p className="muted" data-testid="my-plan-empty">{MY_PLAN.empty}</p>
            : <StudentPlanSection plan={view.plan} studentId={studentId} audience="student" headingLevel={2} />}
        </>
      )}
    </main>
  );
}
