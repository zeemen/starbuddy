'use client';
// 登录表单：第一步手机号，第二步验证码（手机号随状态带回，放在隐藏字段里）。提示只来自 src/auth/copy.ts。
import { useActionState, useEffect, useState } from 'react';
import { LOGIN_COPY, LOGIN_RESULT } from '../../auth/copy.ts';
import { loginAction, type LoginState } from './actions.ts';

export function LoginForm() {
  const [state, formAction, pending] = useActionState<LoginState | null, FormData>(loginAction, null);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const step = state?.step ?? 'phone';
  const message = !state || !state.code ? '' : state.code === 'SENT' ? LOGIN_COPY.sent
    : Object.hasOwn(LOGIN_RESULT, state.code) ? LOGIN_RESULT[state.code] : LOGIN_RESULT['INTERNAL'];
  return (
    <form action={formAction} className="shell-form" data-testid="login" data-step={step} aria-busy={pending}
          data-hydrated={hydrated ? 'true' : undefined}>
      {step === 'phone' ? (
        <>
          <p>
            <label htmlFor="login-phone">{LOGIN_COPY.phoneLabel}</label>
            <input id="login-phone" name="phone" type="tel" inputMode="tel" autoComplete="tel" required maxLength={20}
                   aria-describedby="login-phone-hint" defaultValue={state?.phone ?? ''} />
            <span id="login-phone-hint" className="muted">{LOGIN_COPY.phoneHint}</span>
          </p>
          <button type="submit" name="op" value="send">{LOGIN_COPY.sendCode}</button>
        </>
      ) : (
        <>
          <input type="hidden" name="phone" value={state?.phone ?? ''} />
          <p>
            <label htmlFor="login-code">{LOGIN_COPY.codeLabel}</label>
            <input id="login-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6}
                   aria-describedby="login-code-hint" />
            <span id="login-code-hint" className="muted">{LOGIN_COPY.codeHint}</span>
          </p>
          <button type="submit" name="op" value="verify">{LOGIN_COPY.submit}</button>{' '}
          <button type="submit" name="op" value="change" formNoValidate>{LOGIN_COPY.changePhone}</button>
        </>
      )}
      <p role="status" className="shell-result" data-code={state?.code ?? ''}>{message}</p>
    </form>
  );
}
