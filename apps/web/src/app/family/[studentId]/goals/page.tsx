// 家长端“我的诉求”页 /family/{studentId}/goals（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14）：
// 关注学科（最多 2 科，只列在读学科）、学校考试分科目标分（可不填）、关注学校（初三才有，名单来自招录库已发布的学校）、补充说明。
// 开关 family_goals 关时一律 404（在读会话之前判断，不发任何请求）。只给家长（学生会话转回自己的今日；BFF 另对学生会话一律 404）。
// 页面只回显家长本人写的内容，不对孩子作任何陈述（规则 11），不显示名次、录取概率、提分承诺（spec 硬约束）。文案全部是占位（./copy.ts）。
import { notFound, redirect } from 'next/navigation';
import { childLabel } from '../../../../auth/copy.ts';
import { ResultForm } from '../../../../auth/ResultForm.tsx';
import { familyGoalsAction } from '../../../../family-goals/actions.ts';
import { fetchFamilyGoals } from '../../../../family-goals/api.ts';
import { FAMILY_GOALS, FAMILY_GOALS_RESULT } from '../../../../family-goals/copy.ts';
import { familyGoalsEnabled } from '../../../../family-goals/features.ts';
import { pickGoalsView } from '../../../../family-goals/view.ts';
import { subjectName } from '../../../../lib/labels.ts';
import { FAMILY_SHELL } from '../../../../shell/copy.ts';
import { requireGuardian } from '../../../../shell/guard.ts';

export const dynamic = 'force-dynamic';

const day = (iso: string): string => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t + 8 * 3600_000).toISOString().slice(0, 10) : '';
};

export default async function FamilyGoalsPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!familyGoalsEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const r = await fetchFamilyGoals(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const v = r.ok ? pickGoalsView(r.value) : null;
  const label = childLabel(ps.ordinal, ps.grade);
  const target = (s: string) => v?.current?.targets.find((t) => t.subject === s);
  return (
    <main data-testid="family-goals-page">
      <p><a href={`/family/${studentId}`}>{FAMILY_SHELL.childPageFor(label)}</a></p>
      <h1>{FAMILY_GOALS.heading}</h1>
      <p className="muted">{FAMILY_GOALS.intro}</p>
      {!v ? <p className="muted" data-unavailable="family-goals">{FAMILY_GOALS.unavailable}</p> : (
        <>
          <p data-testid="family-goals-current">{v.current ? FAMILY_GOALS.current(v.current.version) : FAMILY_GOALS.none}</p>
          <ResultForm action={familyGoalsAction} labels={FAMILY_GOALS_RESULT} testId="family-goals-form">
            <input type="hidden" name="student" value={studentId} />
            <input type="hidden" name="csrf" value={ps.session.csrf} />
            <fieldset>
              <legend>{FAMILY_GOALS.focusLegend}</legend>
              {v.enrolled.length === 0 ? <p className="muted">{FAMILY_GOALS.focusNone}</p> : v.enrolled.map((s) => (
                <label key={s} className="inline-label">
                  <input type="checkbox" name="focus" value={s} defaultChecked={v.current?.focus.includes(s) ?? false} />
                  {subjectName(s)}
                </label>
              ))}
            </fieldset>
            <fieldset>
              <legend>{FAMILY_GOALS.targetLegend}</legend>
              <p className="muted" id="target-hint">{FAMILY_GOALS.targetHint}</p>
              {v.enrolled.map((s) => (
                <p key={s}>
                  <span>{subjectName(s)}</span>{' '}
                  <label htmlFor={`full-${s}`}>{FAMILY_GOALS.fullScore}</label>
                  <input id={`full-${s}`} name={`full_${s}`} type="number" min={1} max={300} step={0.1} inputMode="decimal"
                         defaultValue={target(s)?.full_score ?? ''} aria-describedby="target-hint" />{' '}
                  <label htmlFor={`target-${s}`}>{FAMILY_GOALS.targetScore}</label>
                  <input id={`target-${s}`} name={`target_${s}`} type="number" min={0} max={300} step={0.1} inputMode="decimal"
                         defaultValue={target(s)?.target_score ?? ''} aria-describedby="target-hint" />
                </p>
              ))}
            </fieldset>
            {v.canSetSchool ? (
              v.schools.length === 0 ? <p className="muted" data-testid="family-goals-school-empty">{FAMILY_GOALS.schoolEmpty}</p> : (
                <p>
                  <label htmlFor="school">{FAMILY_GOALS.schoolLabel}</label>
                  <select id="school" name="school" defaultValue={v.current?.schoolId ?? ''}>
                    <option value="">{FAMILY_GOALS.schoolNone}</option>
                    {v.schools.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
                  </select>
                </p>
              )
            ) : <p className="muted" data-testid="family-goals-school-not-yet">{FAMILY_GOALS.schoolNotYet}</p>}
            <p>
              <label htmlFor="note">{FAMILY_GOALS.noteLabel}</label>
              <textarea id="note" name="note" maxLength={200} rows={3} defaultValue={v.current?.note ?? ''} aria-describedby="note-hint" />
            </p>
            <p className="muted" id="note-hint">{FAMILY_GOALS.noteHint}</p>
            <button type="submit">{FAMILY_GOALS.save}</button>
          </ResultForm>
          {v.history.length > 0 ? (
            <section aria-labelledby="goals-history">
              <h2 id="goals-history">{FAMILY_GOALS.historyHeading}</h2>
              <ul className="shell-list" data-testid="family-goals-history">
                {v.history.map((h) => <li key={h.version}>{FAMILY_GOALS.historyItem(h.version, day(h.createdAt))}</li>)}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </main>
  );
}
