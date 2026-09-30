// 家长的时长设置页 /family/{studentId}/time（spec 第 9 节）：单日上限只能在 0 到档位上限之间调低；休息时间（宵禁）只能收紧
// （必须覆盖默认时段；默认值由 BFF 从单处配置下发，这里不写死）。档位上限与默认时段显示为边界；结果 cap_above_plan、curfew_must_cover_default 各有提示。
// 规则由数据库函数 set_time_guard 判断，前端只做格式检查。学生端只读（学生会话打开这里转到自己的今日）。
import { notFound, redirect } from 'next/navigation';
import { childLabel } from '../../../../auth/copy.ts';
import { ResultForm } from '../../../../auth/ResultForm.tsx';
import { fetchTimeGuard } from '../../../../lib/shell-api.ts';
import { BALANCE, FAMILY_SHELL, TIME_GUARD, TIME_RESULT } from '../../../../shell/copy.ts';
import { requireGuardian } from '../../../../shell/guard.ts';
import { timeGuardAction } from '../../shell-actions.ts';

export const dynamic = 'force-dynamic';

export default async function TimePage({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const r = await fetchTimeGuard(ps.session, studentId);
  if (!r.ok) {
    if (r.status === 401) redirect('/login');
    notFound();
  }
  const g = r.value;
  return (
    <main data-testid="time-page">
      <p><a href={`/family/${studentId}`}>{FAMILY_SHELL.childPageFor(childLabel(ps.ordinal, ps.grade))}</a></p>
      <h1>{TIME_GUARD.heading}</h1>
      <p data-testid="time-current">{BALANCE.capAndCurfew(g.effective_daily_cap, g.curfew_start, g.curfew_end)}</p>
      <ResultForm action={timeGuardAction} labels={TIME_RESULT} testId="time-form">
        <input type="hidden" name="student" value={studentId} />
        <input type="hidden" name="csrf" value={ps.session.csrf} />
        <label htmlFor="cap">{TIME_GUARD.capLabel}</label>
        <input id="cap" name="daily_cap_minutes" type="number" min={0} step={1}
               defaultValue={g.daily_cap_minutes ?? ''} aria-describedby="cap-hint" />
        <p className="muted" id="cap-hint">{TIME_GUARD.capHint(g.plan_daily_cap)}</p>
        <label htmlFor="curfew-start">{TIME_GUARD.curfewStart}</label>
        <input id="curfew-start" name="curfew_start" type="time" defaultValue={g.custom_curfew ? g.curfew_start : ''} aria-describedby="curfew-hint" />
        <label htmlFor="curfew-end">{TIME_GUARD.curfewEnd}</label>
        <input id="curfew-end" name="curfew_end" type="time" defaultValue={g.custom_curfew ? g.curfew_end : ''} aria-describedby="curfew-hint" />
        <p className="muted" id="curfew-hint">{TIME_GUARD.curfewHint(g.default_curfew.start, g.default_curfew.end)}</p>
        <button type="submit">{TIME_GUARD.save}</button>
      </ResultForm>
    </main>
  );
}
