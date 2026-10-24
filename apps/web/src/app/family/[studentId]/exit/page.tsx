// 家长端退出内部测试确认页 /family/{studentId}/exit（specs/launch-lt06-internal-test-ops 范围第 4 条）：孩子页入口是第一次确认，
// 本页列出退出会发生什么、勾选“我已了解”再提交是第二次确认（没勾选不发请求；BFF 另要求 confirm: true）。
// 已退出时只显示已退出说明。开关 internal_test 关时一律 404（在读会话之前判断，不发任何请求）。只给有效绑定家长。
// 不含同意书正文（文案全部是占位，src/internal-test/copy.ts）。
import { notFound, redirect } from 'next/navigation';
import { childLabel } from '../../../../auth/copy.ts';
import { ResultForm } from '../../../../auth/ResultForm.tsx';
import { exitInternalTestAction } from '../../../../internal-test/actions.ts';
import { fetchInternalTestStatus } from '../../../../internal-test/api.ts';
import { EXIT, EXIT_RESULT } from '../../../../internal-test/copy.ts';
import { internalTestEnabled } from '../../../../internal-test/features.ts';
import { ExitedNotice } from '../../../../internal-test/links.tsx';
import { FAMILY_SHELL } from '../../../../shell/copy.ts';
import { requireGuardian } from '../../../../shell/guard.ts';

export const dynamic = 'force-dynamic';

export default async function InternalTestExitPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!internalTestEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const r = await fetchInternalTestStatus(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const exited = r.ok && r.value.exited === true;
  return (
    <main data-testid="internal-test-exit-page">
      <p><a href={`/family/${studentId}`}>{FAMILY_SHELL.childPageFor(childLabel(ps.ordinal, ps.grade))}</a></p>
      <h1>{EXIT.heading}</h1>
      <ExitedNotice exited={exited} />
      {exited ? null : (
        <>
          <ul className="shell-list" data-testid="internal-test-exit-effects">
            {EXIT.effects.map((e) => <li key={e}>{e}</li>)}
          </ul>
          <ResultForm action={exitInternalTestAction} labels={EXIT_RESULT} testId="internal-test-exit-form">
            <input type="hidden" name="student" value={studentId} />
            <input type="hidden" name="csrf" value={ps.session.csrf} />
            <p>
              <label className="inline-label">
                <input type="checkbox" name="confirm" required />{EXIT.confirmLabel}
              </label>
            </p>
            <button type="submit">{EXIT.submit}</button>
          </ResultForm>
        </>
      )}
    </main>
  );
}
