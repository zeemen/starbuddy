// 家长端“月度计划”页 /family/{studentId}/monthly-plan（specs/family-goals-monthly-plan/step3-spec.md 范围 2，PLT-14 第 3 步）：
// 四块——本月诉求摘要（只读，链接到“我的诉求”）、上月回顾（含目标分与最近一次成绩的分差区间，与下一步同屏）、孩子的学习计划、家长注意事项。
// 打开即由 BFF 在本月没有快照时请洞察服务生成（同月只生成一次）；没有权益时显示升级说明占位。开关 monthly_plan 关时一律 404
// （在读会话之前判断，不发任何请求）。只给家长（学生会话转回自己的今日；BFF 另对学生会话一律 404）。
// 关于学生的句子只来自 BFF 按模板库渲染、引用判断的结果（data-claim-id，带“为什么”入口）；分差只有区间，不显示成绩与目标分的差值；
// 不显示名次、录取概率、提分承诺（spec 硬约束）。文案全部是占位（monthly-plan/guardian-copy.ts、student-copy.ts）。
import { notFound, redirect } from 'next/navigation';
import { childLabel } from '../../../../auth/copy.ts';
import { fetchFamilyGoals } from '../../../../family-goals/api.ts';
import { familyGoalsEnabled } from '../../../../family-goals/features.ts';
import { pickGoalsView, type GoalsView } from '../../../../family-goals/view.ts';
import { subjectName } from '../../../../lib/labels.ts';
import { fetchGuardianPlan } from '../../../../monthly-plan/api.ts';
import { monthlyPlanEnabled } from '../../../../monthly-plan/features.ts';
import { MONTHLY_PLAN } from '../../../../monthly-plan/guardian-copy.ts';
import { pickGuardianPlan, type GuardianPlan } from '../../../../monthly-plan/guardian-view.ts';
import { StudentPlanSection } from '../../../../monthly-plan/StudentPlanSection.tsx';
import { EXAM_TYPE, FAMILY_SHELL } from '../../../../shell/copy.ts';
import { requireGuardian } from '../../../../shell/guard.ts';
import { ClaimLinks } from '../../../../trust/links.tsx';

export const dynamic = 'force-dynamic';

export default async function MonthlyPlanPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!monthlyPlanEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const [r, goals] = await Promise.all([fetchGuardianPlan(ps.session, studentId),
                                        familyGoalsEnabled() ? fetchFamilyGoals(ps.session, studentId) : Promise.resolve(null)]);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const view = r.ok ? pickGuardianPlan(r.value) : null;
  const goalsView = goals && goals.ok ? pickGoalsView(goals.value) : null;
  const label = childLabel(ps.ordinal, ps.grade);
  return (
    <main data-testid="monthly-plan-page">
      <p><a href={`/family/${studentId}`}>{FAMILY_SHELL.childPageFor(label)}</a></p>
      <h1>{MONTHLY_PLAN.heading}</h1>
      {view ? <p className="muted"><span>{MONTHLY_PLAN.month}</span>{' '}<span data-value="month">{view.month}</span></p> : null}

      <section aria-labelledby="mp-goals" data-testid="monthly-plan-goals">
        <h2 id="mp-goals">{MONTHLY_PLAN.goalsHeading}</h2>
        <p className="muted">{MONTHLY_PLAN.goalsIntro}</p>
        {goals === null ? <p className="muted">{MONTHLY_PLAN.goalsOff}</p> : <GoalsSummary v={goalsView} studentId={studentId} />}
      </section>

      {!view ? <p className="muted" data-unavailable="monthly-plan">{MONTHLY_PLAN.unavailable}</p>
        : !view.entitled || !view.plan ? <p className="muted" data-testid="monthly-plan-not-entitled">{MONTHLY_PLAN.notEntitled}</p>
        : <PlanBody plan={view.plan} studentId={studentId} />}
    </main>
  );
}

/** 本月诉求摘要：家长本人写的当前版（只读）；修改在“我的诉求”页 */
function GoalsSummary({ v, studentId }: { v: GoalsView | null; studentId: string }) {
  const edit = <p><a href={`/family/${studentId}/goals`} data-testid="monthly-plan-goals-edit">{MONTHLY_PLAN.goalsEdit}</a></p>;
  if (!v?.current) return <><p className="muted">{MONTHLY_PLAN.goalsNone}</p>{edit}</>;
  const c = v.current;
  const school = c.schoolId ? v.schools.find((s) => s.id === c.schoolId)?.name ?? null : null;
  return (
    <>
      <dl>
        <dt>{MONTHLY_PLAN.focus}</dt>
        <dd>{c.focus.length === 0 ? '—' : c.focus.map(subjectName).join('、')}</dd>
        <dt>{MONTHLY_PLAN.target}</dt>
        <dd>
          {c.targets.length === 0 ? '—' : c.targets.map((t, i) => (
            <span key={t.subject}>{i > 0 ? '、' : ''}{subjectName(t.subject)}{' '}
              <span data-value="score">{t.target_score}</span>{' / '}<span data-value="score">{t.full_score}</span></span>
          ))}
        </dd>
        {school ? <><dt>{MONTHLY_PLAN.school}</dt><dd>{school}</dd></> : null}
      </dl>
      {edit}
    </>
  );
}

function PlanBody({ plan, studentId }: { plan: GuardianPlan; studentId: string }) {
  return (
    <>
      <p className="muted"><span>{MONTHLY_PLAN.generated}</span>{' '}<span data-value="date">{plan.generatedDay}</span></p>

      <section aria-labelledby="mp-review" data-testid="monthly-plan-review">
        <h2 id="mp-review">{MONTHLY_PLAN.reviewHeading}</h2>
        {!plan.lastMonth ? <p className="muted" data-testid="monthly-plan-review-none">{MONTHLY_PLAN.reviewNone}</p> : (
          <>
            <p><span data-value="month">{plan.lastMonth.month}</span>{' '}<span>{plan.lastMonth.text}</span></p>
            {plan.lastMonth.lit.length > 0 ? (
              <>
                <h3>{MONTHLY_PLAN.litHeading}</h3>
                <ul className="shell-list">
                  {plan.lastMonth.lit.map((x) => (
                    <li key={x.claimId} data-subject={x.subject}>
                      <span>{subjectName(x.subject)}</span>{' '}<span data-claim-id={x.claimId}>{x.text}</span>{' '}
                      <ClaimLinks studentId={studentId} claimId={x.claimId} audience="guardian" />
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        )}
        <h3>{MONTHLY_PLAN.gapsHeading}</h3>
        {plan.gaps.length === 0 ? <p className="muted" data-testid="monthly-plan-gaps-none">{MONTHLY_PLAN.gapsNone}</p> : (
          <>
            <ul className="shell-list" data-testid="monthly-plan-gaps">
              {plan.gaps.map((g) => (
                <li key={`${g.claimId}-${g.subject}`} data-subject={g.subject}>
                  <span>{subjectName(g.subject)}</span>{' '}<span>{EXAM_TYPE[g.examType]}</span>{' '}
                  <span>{MONTHLY_PLAN.examDate}</span>{' '}<span data-value="date">{g.examDate}</span>{' · '}
                  {/* 分差区间与它所引的判断在同一个 data-claim-id 元素里：陈述与判断一一对应 */}
                  <span data-claim-id={g.claimId}>
                    {g.band.reached ? <span>{MONTHLY_PLAN.gapReached}</span> : (
                      <><span>{MONTHLY_PLAN.gapBand}</span>{' '}<span data-value="score-band">{`${g.band.low}–${g.band.high}`}</span></>
                    )}{' '}
                    <span>{g.text}</span>
                  </span>{' '}
                  <ClaimLinks studentId={studentId} claimId={g.claimId} audience="guardian" />
                </li>
              ))}
            </ul>
            <p className="muted">{MONTHLY_PLAN.gapNext}</p>
          </>
        )}
      </section>

      <section aria-labelledby="mp-plan" data-testid="monthly-plan-student">
        <h2 id="mp-plan">{MONTHLY_PLAN.planHeading}</h2>
        <StudentPlanSection plan={plan.student} studentId={studentId} audience="guardian" headingLevel={3} />
      </section>

      <section aria-labelledby="mp-notes" data-testid="monthly-plan-notes">
        <h2 id="mp-notes">{MONTHLY_PLAN.notesHeading}</h2>
        {plan.notes.length === 0 ? <p className="muted">{MONTHLY_PLAN.notesNone}</p> : (
          <ul className="shell-list">{plan.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        )}
      </section>
    </>
  );
}
