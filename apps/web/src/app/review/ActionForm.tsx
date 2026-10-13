'use client';
// 审核台的表单外壳：提交给 server action，把结果（提示码与规则说明）显示在表单下方的状态区域（屏幕阅读器会读出）。
// 结果不经地址栏传递，别人不能用链接伪造提示文字。
import { useActionState, useEffect, useState, type ReactNode } from 'react';
import type { ActionState } from './actions.ts';

const OK = new Set(['saved', 'signed', 'published', 'retired', 'change_signed', 'taken', 'released', 'resolved', 'applied', 'recorded',
  'source_added', 'source_retired', 'written']);

export function ActionForm({
  action, labels, children, testId,
}: {
  action: (prev: ActionState | null, f: FormData) => Promise<ActionState>;
  labels: Record<string, string>;
  children: ReactNode;
  testId: string;
}) {
  const [state, formAction, pending] = useActionState(action, null);
  // 水合完成标记：页面刷新后、React 接管之前点击按钮，提交可能丢失或走无 JS 的整页提交，结果区显示不出来。
  // 端到端测试先等这个标记再点（审核台签字用例在负载下偶发超时的根因）。
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const label = state ? (Object.hasOwn(labels, state.code) ? labels[state.code] : labels['INTERNAL']) : '';
  return (
    <form action={formAction} className="review-form" data-testid={testId} aria-busy={pending} data-hydrated={hydrated ? 'true' : undefined}>
      {children}
      <p role="status" className={state && !OK.has(state.code) ? 'review-result error' : 'review-result'} data-code={state?.code ?? ''}>
        {label}
        {state?.detail ? `：${state.detail}` : ''}
      </p>
    </form>
  );
}
