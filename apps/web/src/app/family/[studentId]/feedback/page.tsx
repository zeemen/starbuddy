// 家长端问题反馈页 /family/{studentId}/feedback（specs/launch-lt06-internal-test-ops 范围第 3 条）：固定分类 + 文本框 + 可选附上页面类型。
// 开关 internal_test 关时一律 404（在读会话之前判断，不发任何请求）。只给该学生的有效绑定家长（学生会话转回自己的今日）。
// 来源由 BFF 按会话记为家长观察，与学生自述分开存（规则 6）；文本只作反馈处理。文案全部是占位（src/internal-test/copy.ts）。
import { notFound } from 'next/navigation';
import { childLabel } from '../../../../auth/copy.ts';
import { FEEDBACK } from '../../../../internal-test/copy.ts';
import { internalTestEnabled } from '../../../../internal-test/features.ts';
import { FeedbackForm } from '../../../../internal-test/FeedbackForm.tsx';
import { isFeedbackRoute } from '../../../../internal-test/routes.ts';
import { FAMILY_SHELL } from '../../../../shell/copy.ts';
import { requireGuardian } from '../../../../shell/guard.ts';

export const dynamic = 'force-dynamic';

export default async function FamilyFeedbackPage({ params, searchParams }: {
  params: Promise<{ studentId: string }>; searchParams: Promise<{ from?: string }>;
}) {
  if (!internalTestEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireGuardian(studentId);
  const from = (await searchParams).from;
  const route = isFeedbackRoute(from) && !from.startsWith('/s/') && from !== '/student' ? from : null;  // 只认家长端模板
  return (
    <main data-testid="feedback-page">
      <p><a href={`/family/${studentId}`}>{FAMILY_SHELL.childPageFor(childLabel(ps.ordinal, ps.grade))}</a></p>
      <h1>{FEEDBACK.heading}</h1>
      <p className="muted">{FEEDBACK.intro}</p>
      <FeedbackForm studentId={studentId} csrf={ps.session.csrf} route={route} />
    </main>
  );
}
