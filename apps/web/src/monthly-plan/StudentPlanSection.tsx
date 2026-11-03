// 学习计划一段（学生“本月计划”页与家长“月度计划”页共用）：本月优先知识点（每条关于学生的句子带 data-claim-id 与“为什么”入口）、
// 每周节奏、本月复测、分科小复评。优先知识点只按顺序排列（无序列表，不编号，计划里的先后不是名次）。
// 句子文字只来自 BFF 按模板库渲染的结果；数字与日期另放在 data-value 里。只引用学习计划一段的文案与挑法（学生端可用）。
import { subjectName } from '../lib/labels.ts';
import { ClaimLinks } from '../trust/links.tsx';
import type { Audience } from '../trust/view.ts';
import { PLAN_SECTION } from './student-copy.ts';
import type { StudentPlan } from './student-view.ts';

export function StudentPlanSection({ plan, studentId, audience, headingLevel }: {
  plan: StudentPlan; studentId: string; audience: Audience; headingLevel: 2 | 3;
}) {
  const H = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <>
      <section aria-labelledby="plan-priority" data-testid="plan-priority">
        <H id="plan-priority">{PLAN_SECTION.priorityHeading}</H>
        {plan.priority.length === 0 ? <p className="muted">{PLAN_SECTION.priorityNone}</p> : (
          <ul className="shell-list">
            {plan.priority.map((x) => (
              <li key={x.claimId} data-subject={x.subject}>
                <span>{subjectName(x.subject)}</span>{' '}
                <span data-claim-id={x.claimId}>{x.text}</span>{' '}
                <ClaimLinks studentId={studentId} claimId={x.claimId} audience={audience} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="plan-rhythm" data-testid="plan-rhythm">
        <H id="plan-rhythm">{PLAN_SECTION.rhythmHeading}</H>
        {!plan.rhythm ? <p className="muted">{PLAN_SECTION.none}</p> : (
          <>
            <p>{plan.rhythm.text}</p>
            <p className="muted">
              <span>{PLAN_SECTION.sessions}</span>{' '}<span data-value="count">{plan.rhythm.sessions}</span>{' · '}
              <span>{PLAN_SECTION.minutes}</span>{' '}<span data-value="minutes">{plan.rhythm.minutes}</span>
            </p>
          </>
        )}
      </section>
      <section aria-labelledby="plan-retests" data-testid="plan-retests">
        <H id="plan-retests">{PLAN_SECTION.retestsHeading}</H>
        {!plan.retests ? <p className="muted">{PLAN_SECTION.none}</p> : (
          <>
            <p>{plan.retests.text}</p>
            <p className="muted">
              <span>{PLAN_SECTION.retestCount}</span>{' '}<span data-value="count">{plan.retests.count}</span>
              {plan.retests.dates.length > 0 ? (
                <>{' · '}<span>{PLAN_SECTION.retestDates}</span>{' '}
                  {plan.retests.dates.map((d, i) => <span key={d}>{i > 0 ? '、' : ''}<span data-value="date">{d}</span></span>)}</>
              ) : null}
            </p>
          </>
        )}
      </section>
      <section aria-labelledby="plan-mini" data-testid="plan-mini">
        <H id="plan-mini">{PLAN_SECTION.miniHeading}</H>
        {plan.miniReviews.length === 0 ? <p className="muted">{PLAN_SECTION.none}</p> : (
          <ul className="shell-list">
            {plan.miniReviews.map((m) => (
              <li key={m.subject} data-subject={m.subject}>
                <span>{subjectName(m.subject)}</span>{' '}<span data-value="date">{m.date}</span>{' '}<span>{m.text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
