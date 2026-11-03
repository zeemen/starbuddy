// 学生端错题再练作答页 /s/{studentId}/learn/{subject}/practice/{entryId}（specs/practice-loop-stage1 PR 4）：只挂组件，组件里先过开关闸门。
import { PracticeItemPage } from '../../../../../../../practice/PracticePages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; subject: string; entryId: string }> }) {
  const { studentId, subject, entryId } = await params;
  return <PracticeItemPage studentId={studentId} subject={subject} entryId={entryId} />;
}
