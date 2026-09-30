// 家长首页（H1 的最小范围）：已绑定孩子的列表（“孩子 N · 某年级”，不收姓名）、在本设备打开学生模式、退出登录。
// 建档、同意、选科、星图、额度与时长等随 H4。身份只来自会话：没登录回登录页，学生会话去学生页。
import { redirect } from 'next/navigation';
import { childLabel, FAMILY_COPY, FAMILY_RESULT, openStudentModeFor } from '../../auth/copy.ts';
import { ResultForm } from '../../auth/ResultForm.tsx';
import { currentSession, whoAmI } from '../../lib/shell-auth.ts';
import { logoutAction, openStudentModeAction } from './actions.ts';

export const dynamic = 'force-dynamic';

export default async function FamilyPage() {
  const s = await currentSession();
  if (!s) redirect('/login');
  const me = await whoAmI(s.token);
  if (!me.ok) redirect('/login');
  if (me.value.account_kind === 'student') redirect('/student');
  const children = me.value.children;
  const csrf = s.csrf ?? '';
  return (
    <main>
      <h1>{FAMILY_COPY.title}</h1>
      <h2>{FAMILY_COPY.childrenHeading}</h2>
      {children.length === 0 ? <p className="muted">{FAMILY_COPY.noChildren}</p> : null}
      {children.length > 0 ? (
        <section aria-labelledby="student-mode-heading">
          <h3 id="student-mode-heading">{FAMILY_COPY.studentModeHeading}</h3>
          <p className="muted">{FAMILY_COPY.studentModeNote}</p>
          <ul className="shell-list">
            {children.map((c, i) => {
              const label = childLabel(i + 1, c.grade);
              return (
                <li key={c.student_id} data-testid="child">
                  <ResultForm action={openStudentModeAction} labels={FAMILY_RESULT} testId={`open-student-${i + 1}`}>
                    <span>{label}</span>{' '}
                    <input type="hidden" name="student" value={c.student_id} />
                    <input type="hidden" name="csrf" value={csrf} />
                    <button type="submit" aria-label={openStudentModeFor(label)}>{FAMILY_COPY.openStudentMode}</button>
                  </ResultForm>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      <ResultForm action={logoutAction} labels={FAMILY_RESULT} testId="logout">
        <input type="hidden" name="csrf" value={csrf} />
        <button type="submit">{FAMILY_COPY.logout}</button>
      </ResultForm>
    </main>
  );
}
