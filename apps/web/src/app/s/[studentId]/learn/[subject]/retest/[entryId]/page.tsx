// 学生端复测与验证题作答页 /s/{studentId}/learn/{subject}/retest/{entryId}（specs/retest-delivery-kge05）：只挂组件，组件里先过开关闸门。
import { RetestItemPage } from '../../../../../../../retest/RetestPages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; subject: string; entryId: string }> }) {
  const { studentId, subject, entryId } = await params;
  return <RetestItemPage studentId={studentId} subject={subject} entryId={entryId} />;
}
