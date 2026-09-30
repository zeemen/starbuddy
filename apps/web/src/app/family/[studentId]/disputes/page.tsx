// 家长端异议状态页（冲刺 5-H；开关 dispute，关时 404、不发请求）。实现在 src/trust/DisputesPage.tsx。
import { DisputesPage } from '../../../../trust/DisputesPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <DisputesPage studentId={studentId} audience="guardian" />;
}
