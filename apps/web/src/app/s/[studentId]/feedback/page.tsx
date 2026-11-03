// 学生端问题反馈页 /s/{studentId}/feedback（specs/launch-lt06-internal-test-ops 范围第 3 条）：固定分类 + 文本框 + 可选附上刚才所在的页面类型。
// 开关 internal_test 关时一律 404（在读会话之前判断，不发任何请求）。只给学生本人（家长会话转到孩子页；别的学生 404）。
// 来源由 BFF 按会话记为学生自述；文本只作反馈处理，不进画像与判断账本。文案全部是占位（src/internal-test/copy.ts）。
import { notFound } from 'next/navigation';
import { FEEDBACK } from '../../../../internal-test/copy.ts';
import { internalTestEnabled } from '../../../../internal-test/features.ts';
import { FeedbackForm } from '../../../../internal-test/FeedbackForm.tsx';
import { isFeedbackRoute } from '../../../../internal-test/routes.ts';
import { requireOwnStudent } from '../../../../shell/guard.ts';

export const dynamic = 'force-dynamic';

export default async function StudentFeedbackPage({ params, searchParams }: {
  params: Promise<{ studentId: string }>; searchParams: Promise<{ from?: string }>;
}) {
  if (!internalTestEnabled()) notFound();
  const { studentId } = await params;
  const ps = await requireOwnStudent(studentId);
  const from = (await searchParams).from;
  const route = isFeedbackRoute(from) && from.startsWith('/s/') ? from : null;  // 地址栏参数只认登记表里的学生端模板
  return (
    <main data-testid="feedback-page">
      <p><a href={`/s/${studentId}/today`}>{FEEDBACK.back}</a></p>
      <h1>{FEEDBACK.heading}</h1>
      <p className="muted">{FEEDBACK.intro}</p>
      <FeedbackForm studentId={studentId} csrf={ps.session.csrf} route={route} />
    </main>
  );
}
