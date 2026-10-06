// 学生端错题再练列表 /s/{studentId}/learn/{subject}/practice（specs/practice-loop-stage1 PR 4）：只挂组件，组件里先过开关闸门。
import { PracticeListPage } from '../../../../../../practice/PracticePages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; subject: string }> }) {
  const { studentId, subject } = await params;
  return <PracticeListPage studentId={studentId} subject={subject} />;
}
