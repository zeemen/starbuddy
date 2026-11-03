// 学生端星图（spec 第 7.1 节，H2）：学生会话在本设备打开的学生本人。实现见 src/star/StarMapPage.tsx。
import { StarMapPage } from '../../../../star/StarMapPage.tsx';

export const dynamic = 'force-dynamic';

export default async function StudentStarMap({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <StarMapPage studentId={studentId} audience="student" />;
}
