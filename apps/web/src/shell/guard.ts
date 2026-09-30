// 壳页面的会话分流（只看会话类型，不判断授权；授权在 BFF 与数据库）：
// 没登录 → /login；学生会话打开家长地址 → 自己的今日；家长会话打开学生地址 → 孩子页。未授权与不存在由 BFF 的 404 决定。
import { notFound, redirect } from 'next/navigation';
import { pageSession, type PageSession } from '../lib/session.ts';
import { isUuid } from '../lib/star-map.ts';
import { trustEnabled, type TrustFeature } from './features.ts';

/** 信任界面页面的开关闸门：开关关时直接 404，不读会话、不发任何请求（4-H“信任界面预留”验收：无元素、无请求、无路由） */
export function requireTrust(feature: TrustFeature): void {
  if (!trustEnabled(feature)) notFound();
}

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

/** 信任界面的学生页只给学生本人：会话里的学生不是路径里的学生时 404（不按“家长那一侧”转走，也不发任何请求） */
export async function requireOwnStudent(studentId: string): Promise<PageSession> {
  const ps = await requireStudent(studentId);
  if (ps.me.account_kind !== 'student' || ps.me.student_id !== studentId) notFound();
  return ps;
}
