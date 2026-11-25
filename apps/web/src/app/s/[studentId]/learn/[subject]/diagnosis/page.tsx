// 学生端数学诊断做题页 /s/{studentId}/learn/{subject}/diagnosis（specs/diagnosis-student-entry）：只挂组件，组件里先过开关闸门。
import { DiagnosisStudentPage } from '../../../../../../diagnosis/DiagnosisPages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; subject: string }> }) {
  const { studentId, subject } = await params;
  return <DiagnosisStudentPage studentId={studentId} subject={subject} />;
}
