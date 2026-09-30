'use client';
// 统一壳登录与学生模式入口的表单外壳：提交给 server action，把结果码对应的提示显示在表单内的状态区域（屏幕阅读器会读出）。
// 结果不经地址栏传递；提示文字只来自 src/auth/copy.ts。
import { useActionState, useEffect, useState, type ReactNode } from 'react';

export interface FormResult { code: string }

export function ResultForm({
  action, labels, children, testId, className,
}: {
  action: (prev: FormResult | null, f: FormData) => Promise<FormResult>;
  labels: Record<string, string>;
  children: ReactNode;
  testId: string;
  className?: string;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  // 水合完成标记：端到端测试先等它再提交（与审核台 ActionForm 同一做法）
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const label = state ? (Object.hasOwn(labels, state.code) ? labels[state.code] : labels['INTERNAL']) : '';
  return (
    <form action={formAction} className={className ?? 'shell-form'} data-testid={testId} aria-busy={pending}
          data-hydrated={hydrated ? 'true' : undefined}>
      {children}
      <p role="status" className="shell-result" data-code={state?.code ?? ''}>{label}</p>
    </form>
  );
}
