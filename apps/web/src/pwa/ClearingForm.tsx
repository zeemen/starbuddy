'use client';
// 提交时顺带清 Service Worker 缓存的普通表单（LT-07）：审核台“退出”用（审核台的表单不走 ResultForm）。
import type { ReactNode } from 'react';
import { clearPwaCaches } from './client.ts';

export function ClearingForm({ action, className, children }: {
  action: (f: FormData) => void | Promise<void>; className?: string; children: ReactNode;
}) {
  return <form action={action} className={className} onSubmit={() => { void clearPwaCaches(); }}>{children}</form>;
}
