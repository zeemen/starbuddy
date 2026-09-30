// 家长端孩子页（spec 第 9、13、14 节）：额度与时长（分来源明细）、选科、今日计划（判断陈述带置信度说明）、星图、时长设置、
// 完善学习档案入口（SIE-01）、信任界面入口（考试与对账卡、异议、数据查看 / 导出 / 删除；冲刺 5-H，开关默认关，关时不渲染、不发请求）。
// 不显示对话内容、同伴比较、排名、无置信度的推断；没有点点入口，没有角色形象。
import { notFound, redirect } from 'next/navigation';
import { childLabel, FAMILY_COPY } from '../../../auth/copy.ts';
import { ResultForm } from '../../../auth/ResultForm.tsx';
import { fetchBalance, fetchEnrollments, fetchToday } from '../../../lib/shell-api.ts';
import { BALANCE, ENROLL_RESULT, ENROLLMENT, FAMILY_SHELL, resourceLabel, slotText, SOURCE_LABEL, subjectLabel, TODAY } from '../../../shell/copy.ts';
import { requireGuardian } from '../../../shell/guard.ts';
import { StatusBar } from '../../../shell/StatusBar.tsx';
import { TaskList } from '../../../shell/TaskList.tsx';
import { TrustSlot } from '../../../shell/TrustSlot.tsx';
import { TrustNav } from '../../../trust/links.tsx';
import { enrollAction, pauseAction } from '../shell-actions.ts';

export const dynamic = 'force-dynamic';

const day = (iso: string): string => new Date(Date.parse(iso) + 8 * 3600_000).toISOString().slice(0, 10);

export default async function ChildPage({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const [balance, enrollments, today] = await Promise.all([
    fetchBalance(ps.session, studentId), fetchEnrollments(ps.session, studentId), fetchToday(ps.session, studentId)]);
  // 只有 404（无权或不存在）走“没有找到”；401 回登录页；其他错误只让对应区块显示“暂时无法获取”，不拖垮整页
  for (const r of [balance, enrollments, today]) {
    if (!r.ok && r.status === 401) redirect('/login');
    if (!r.ok && r.status === 404) notFound();
  }
  const b = balance.ok ? balance.value : null;
  const e = enrollments.ok ? enrollments.value : null;
  const t = today.ok ? today.value : null;
  const label = childLabel(ps.ordinal, ps.grade);
  const csrf = ps.session.csrf;
  const timeHref = `/family/${studentId}/time`;
  return (
    <main data-testid="child-page">
      <p><a href="/family">{FAMILY_SHELL.backToFamily}</a></p>
      <h1>{label}</h1>
      <p>
        <a href={`/family/${studentId}/star-map`} data-testid="child-star-map">{FAMILY_COPY.starMap}</a>{' · '}
        <a href={timeHref} data-testid="child-time">{FAMILY_SHELL.timeSettings}</a>{' · '}
        <a href={`/onboarding/${studentId}`} data-testid="child-onboarding">{FAMILY_SHELL.onboarding}</a>
      </p>

      <section aria-labelledby="balance-heading">
        <h2 id="balance-heading">{BALANCE.heading}</h2>
        {b ? <StatusBar balance={b} audience="guardian" timeHref={timeHref} /> : <p className="muted" data-unavailable="balance">{BALANCE.unavailable}</p>}
        {b && b.balances.length > 0 ? (
          <ul className="shell-list" data-testid="balances">
            {b.balances.map((x) => (
              <li key={x.resource_type} data-resource={x.resource_type}>
                {resourceLabel(x.resource_type)}：{BALANCE.remaining(x.remaining, x.unit)}（{BALANCE.periodEnd(day(x.period_end))}）
                {x.sources && x.sources.length > 1 ? (
                  <ul>{x.sources.map((src, i) => <li key={i}>{SOURCE_LABEL[src.source] ?? src.source}：{BALANCE.remaining(src.remaining, x.unit)}</li>)}</ul>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section aria-labelledby="enroll-heading">
        <h2 id="enroll-heading">{ENROLLMENT.heading}</h2>
        {e?.rules ? (
          <p className="muted" data-testid="enroll-rules">
            {e.rules.core_subjects === 'pick_one' ? ENROLLMENT.rulePickOne : e.rules.core_subjects === 'all_launched' ? ENROLLMENT.ruleAllLaunched : null}
            {e.rules.elective_slots ? `；${ENROLLMENT.electiveSlots(e.rules.elective_slots)}` : null}
            {`；${ENROLLMENT.changeRule}`}
          </p>
        ) : null}
        {!e ? <p className="muted" data-unavailable="enrollments">{BALANCE.unavailable}</p> : null}
        <ul className="shell-list" data-testid="enrollments">
          {(e?.subjects ?? []).map((s) => {
            const name = subjectLabel(s.subject);
            return (
              <li key={s.subject} data-subject={s.subject} data-enrollment={s.enrollment ?? 'none'}>
                <span>{name}</span> <span className="muted">{slotText(s.is_core ? 'core' : 'elective', undefined)}</span>{' '}
                <span className="muted">{!s.open && s.enrollment !== 'active' ? ENROLLMENT.notOpen
                  : s.enrollment === 'active' ? ENROLLMENT.active : s.enrollment === 'paused' ? ENROLLMENT.paused : ENROLLMENT.none}</span>
                {s.enrollment === 'active' ? (
                  <ResultForm action={pauseAction} labels={ENROLL_RESULT} testId={`pause-${s.subject}`} className="shell-form inline-form">
                    <input type="hidden" name="student" value={studentId} />
                    <input type="hidden" name="subject" value={s.subject} />
                    <input type="hidden" name="csrf" value={csrf} />
                    <button type="submit">{ENROLLMENT.pause(name)}</button>
                  </ResultForm>
                ) : (
                  <ResultForm action={enrollAction} labels={ENROLL_RESULT} testId={`enroll-${s.subject}`} className="shell-form inline-form">
                    <input type="hidden" name="student" value={studentId} />
                    <input type="hidden" name="subject" value={s.subject} />
                    <input type="hidden" name="csrf" value={csrf} />
                    <button type="submit">{ENROLLMENT.enroll(name)}</button>
                  </ResultForm>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-labelledby="today-heading">
        <h2 id="today-heading">{TODAY.title}</h2>
        {!t ? <p className="muted" data-unavailable="today">{BALANCE.unavailable}</p>
          : t.subjects.length === 0 ? <p className="muted">{TODAY.noSubjects}</p> : null}
        {(t?.subjects ?? []).map((s) => (
          <section key={s.subject} data-subject={s.subject} aria-labelledby={`g-today-${s.subject}`}>
            <h3 id={`g-today-${s.subject}`}>{subjectLabel(s.subject)}</h3>
            {s.status !== 'ok' || s.tasks.length === 0 ? <p className="muted">{TODAY.nothingToday}</p> : <TaskList section={s} audience="guardian" studentId={studentId} />}
          </section>
        ))}
      </section>

      {/* 信任界面入口（冲刺 5-H：考试与对账卡、我提出的异议、数据查看导出与删除；开关关时不渲染）；周报本次不做，仍是预留位 */}
      <TrustNav studentId={studentId} audience="guardian" />
      <TrustSlot feature="weekly" />
    </main>
  );
}
