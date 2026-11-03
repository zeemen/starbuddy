// 学生模式的入口（H1 的过渡页，H4 起转到壳的“今日”）：只确认本设备是学生会话。家长会话回家长首页，没登录回登录页。
// 退出学生模式在壳的顶栏（src/shell/StudentShell.tsx）。
import { redirect } from 'next/navigation';
import { currentSession, whoAmI } from '../../lib/shell-auth.ts';

export const dynamic = 'force-dynamic';

export default async function StudentPage() {
  const s = await currentSession();
  if (!s) redirect('/login');
  const me = await whoAmI(s.token);
  if (!me.ok) redirect('/login');
  if (me.value.account_kind !== 'student') redirect('/family');
  if (!me.value.student_id) redirect('/login');
  redirect(`/s/${me.value.student_id}/today`);
}
