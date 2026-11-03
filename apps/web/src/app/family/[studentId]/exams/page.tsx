// 家长端考试安排录入（冲刺 5-H；开关 reconcile，关时 404、不发请求）。实现在 src/trust/ExamPages.tsx。
import { ExamPlanPage } from '../../../../trust/ExamPages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <ExamPlanPage studentId={studentId} audience="guardian" />;
}
