// 学生壳的布局（spec 第 10、11、18 节）：/s/{studentId}/… 下的页面（今日、星图、科目）共用顶栏、额度与时长状态、
// 连续使用提醒、点点入口。身份只来自会话：只有学生会话、且是本人时才套壳并取状态；家长会话或没登录时不套壳、不发请求，
// 由各页自己转到登录页或家长那一侧（星图页的既有行为不变）。
import { headers } from 'next/headers';
import type { ReactNode } from 'react';
import { pageSession } from '../../../lib/session.ts';
import { fetchBalance, fetchRest } from '../../../lib/shell-api.ts';
import { isUuid } from '../../../lib/star-map.ts';
import { StudentShell } from '../../../shell/StudentShell.tsx';

export const dynamic = 'force-dynamic';

export default async function StudentLayout({ children, params }: { children: ReactNode; params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  if (!isUuid(studentId)) return <>{children}</>;
  const ps = await pageSession();
  if (!ps || ps.me.account_kind !== 'student' || ps.me.student_id !== studentId) return <>{children}</>;
  const [balance, rest] = await Promise.all([fetchBalance(ps.session, studentId), fetchRest(ps.session, studentId)]);
  const path = (await headers()).get('x-sb-path') ?? '';
  return (
    <StudentShell studentId={studentId} csrf={ps.session.csrf} balance={balance.ok ? balance.value : null} rest={rest.ok ? rest.value : null}
                  path={path}>
      {children}
    </StudentShell>
  );
}
