// 学生端复测与验证题列表 /s/{studentId}/learn/{subject}/retest（specs/retest-delivery-kge05）：只挂组件，组件里先过开关闸门。
import { RetestListPage } from '../../../../../../retest/RetestPages.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; subject: string }> }) {
  const { studentId, subject } = await params;
  return <RetestListPage studentId={studentId} subject={subject} />;
}
