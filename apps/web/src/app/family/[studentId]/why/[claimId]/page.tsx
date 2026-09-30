// 家长端“为什么”与“我不同意”（冲刺 5-H；开关 why / dispute，关时 404、不发请求）。实现在 src/trust/WhyPage.tsx。
import { WhyPage } from '../../../../../trust/WhyPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string; claimId: string }> }) {
  const { studentId, claimId } = await params;
  return <WhyPage studentId={studentId} claimId={claimId} audience="guardian" />;
}
