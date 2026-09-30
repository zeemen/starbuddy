// 学生端一场考试：成绩录入、预测区间、对账卡（冲刺 5-H；开关 reconcile，关时 404、不发请求）。实现在 src/trust/ExamPages.tsx。
import { ExamPage } from '../../../../../trust/ExamPages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; examId: string }> }) {
  const { studentId, examId } = await params;
  return <ExamPage studentId={studentId} examId={examId} audience="student" />;
}
