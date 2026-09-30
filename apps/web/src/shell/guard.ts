// 壳页面的会话分流（只看会话类型，不判断授权；授权在 BFF 与数据库）：
// 没登录 → /login；学生会话打开家长地址 → 自己的今日；家长会话打开学生地址 → 孩子页。未授权与不存在由 BFF 的 404 决定。
import { notFound, redirect } from 'next/navigation';
import { pageSession, type PageSession } from '../lib/session.ts';
import { isUuid } from '../lib/star-map.ts';

export async function requireStudent(studentId: string): Promise<PageSession> {
  if (!isUuid(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  if (ps.me.account_kind !== 'student') redirect(`/family/${studentId}`);
  return ps;
}

export async function requireGuardian(studentId: string): Promise<PageSession & { ordinal: number; grade: number }> {
  if (!isUuid(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  if (ps.me.account_kind !== 'guardian') {
    redirect(ps.me.student_id ? `/s/${ps.me.student_id}/today` : '/student');
  }
  const i = ps.me.children.findIndex((c) => c.student_id === studentId);
  if (i < 0) notFound();  // 没有有效绑定：与不存在相同（BFF 另外按 RLS 再判断一次）
  return { ...ps, ordinal: i + 1, grade: ps.me.children[i]!.grade };
}
