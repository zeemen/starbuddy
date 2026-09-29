// 审核台登录（PR 4）：登录名、口令、验证器 App 的 6 位码。表单提交给 server action（同源校验），浏览器不直连后端。
// 已登录（会话仍有效）就直接进审核台首页。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { RESULT_LABEL, whoAmI } from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { loginAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

export default async function ReviewLogin() {
  const token = await currentSession();
  if (token && (await whoAmI(token)).ok) redirect('/review');
  return (
    <main>
      <h1>审核台登录</h1>
      <p className="muted">账号由工程负责人建立；6 位码来自绑定的验证器 App。连错 5 次锁定 15 分钟。</p>
      <ActionForm action={loginAction} labels={RESULT_LABEL} testId="login">
        <p>
          <label htmlFor="login-name">登录名</label>{' '}
          <input id="login-name" name="login_name" autoComplete="username" required pattern="[a-z][a-z0-9_.\-]{2,31}" />
        </p>
        <p>
          <label htmlFor="login-password">口令</label>{' '}
          <input id="login-password" name="password" type="password" autoComplete="current-password" required maxLength={128} />
        </p>
        <p>
          <label htmlFor="login-code">6 位码</label>{' '}
          <input id="login-code" name="code" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6} />
        </p>
        <button type="submit">登录</button>
      </ActionForm>
    </main>
  );
}
