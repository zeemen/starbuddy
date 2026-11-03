// 家长端星图（spec 第 7.1 节，H2）：已绑定家长看孩子的星图。实现见 src/star/StarMapPage.tsx。
import { StarMapPage } from '../../../../star/StarMapPage.tsx';

export const dynamic = 'force-dynamic';

export default async function FamilyStarMap({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <StarMapPage studentId={studentId} audience="guardian" />;
}
