// 家长登录页（spec 第 2 节，H1）：手机号 + 短信验证码。已登录（会话仍有效）就直接进对应的首页。
import { redirect } from 'next/navigation';
import { LOGIN_COPY } from '../../auth/copy.ts';
import { currentSession, whoAmI } from '../../lib/shell-auth.ts';
import { LoginForm } from './LoginForm.tsx';

export const dynamic = 'force-dynamic';

export default async function LoginPage() {
  const s = await currentSession();
  if (s) {
    const me = await whoAmI(s.token);
    if (me.ok) redirect(me.value.account_kind === 'student' ? '/student' : '/family');
  }
  return (
    <main>
      <h1>{LOGIN_COPY.title}</h1>
      <p className="muted">{LOGIN_COPY.intro}</p>
      <LoginForm />
    </main>
  );
}
