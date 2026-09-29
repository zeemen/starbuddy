// 数学自适应诊断的开发环境演示页（SIE-02a，spec 设计要点 5；做题页随冲刺 4 的统一外壳接入）。沿用 2-L 探针演示页的两道关：
//   - 构建时：文件名是 page.dev.tsx，只有 STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV 为 dev / test 时构建才把它当页面
//     （next.config.ts 的 pageExtensions）；生产构建里没有这个路由，tests/build-output.test.ts 核对；
//   - 运行时：开发开关与本机 Host 再核对一次（同 /dev/login），没登录跳开发登录页。
// 题面是学习服务在测试环境给的合成占位文字（契约 present_item 签字前不接真实题库）。
import { cookies, headers } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { DEV_COOKIE, devAccountFromCookie, devAuthEnabled, isLoopbackHost } from '../../../../lib/dev-auth.ts';
import { isUuid } from '../../../../lib/star-map.ts';
import { DIAGNOSIS_DEMO_COPY } from '../../../../diagnosis/copy.ts';
import { DiagnosisDemo } from './DiagnosisDemo.tsx';

export const dynamic = 'force-dynamic';

export default async function DiagnosisDemoPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!devAuthEnabled() || !isLoopbackHost((await headers()).get('host'))) notFound();
  const { studentId } = await params;
  if (!isUuid(studentId)) notFound();
  const account = devAccountFromCookie((await cookies()).get(DEV_COOKIE)?.value);
  if (!account) redirect(`/dev/login?next=${encodeURIComponent(`/dev/diagnosis/${studentId}`)}`);
  return (
    <main data-testid="diagnosis-page">
      <h1>{DIAGNOSIS_DEMO_COPY.title}</h1>
      <p className="muted">{DIAGNOSIS_DEMO_COPY.note}</p>
      <DiagnosisDemo student={studentId} />
    </main>
  );
}
