// 家长端周报（冲刺 5-H 跨科周报 v0；开关 weekly，关时 404、不发请求）。实现在 src/trust/WeeklyPage.tsx。
import { WeeklyPage } from '../../../../trust/WeeklyPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <WeeklyPage studentId={studentId} audience="guardian" />;
}
