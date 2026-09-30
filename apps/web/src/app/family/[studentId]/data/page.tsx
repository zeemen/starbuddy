// 家长数据权利页（冲刺 5-H；开关 data_rights，关时 404、不发请求）。实现在 src/trust/DataRightsPage.tsx。
import { DataRightsPage } from '../../../../trust/DataRightsPage.tsx';

export const dynamic = 'force-dynamic';

export default async function Page({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  return <DataRightsPage studentId={studentId} />;
}
