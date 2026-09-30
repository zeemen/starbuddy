// 学生模式的落地页（H1 的过渡页；今日计划、星图等壳页面随 H4）：只确认本设备是学生会话，并给出退出入口。
// 退出后回登录页，家长要重新短信验证（孩子拿不到家长会话）。
import { redirect } from 'next/navigation';
import { FAMILY_RESULT, STUDENT_COPY } from '../../auth/copy.ts';
import { ResultForm } from '../../auth/ResultForm.tsx';
import { currentSession, whoAmI } from '../../lib/shell-auth.ts';
import { logoutAction } from '../family/actions.ts';

export const dynamic = 'force-dynamic';

export default async function StudentPage() {
  const s = await currentSession();
  if (!s) redirect('/login');
  const me = await whoAmI(s.token);
  if (!me.ok) redirect('/login');
  if (me.value.account_kind !== 'student') redirect('/family');
  return (
    <main data-testid="student-mode">
      <h1>{STUDENT_COPY.title}</h1>
      <p>{STUDENT_COPY.intro}</p>
      <ResultForm action={logoutAction} labels={FAMILY_RESULT} testId="exit-student-mode">
        <input type="hidden" name="csrf" value={s.csrf ?? ''} />
        <p className="muted" id="exit-note">{STUDENT_COPY.exitNote}</p>
        <button type="submit" aria-describedby="exit-note">{STUDENT_COPY.exit}</button>
      </ResultForm>
    </main>
  );
}
