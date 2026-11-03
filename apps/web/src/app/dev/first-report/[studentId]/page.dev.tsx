// 首份报告开发演示页（SIE-02b 第二步 PR 2；spec“第二步补充”第 4 条，待人确认 S2-3：开关默认关、只在 dev）。三道关：
//   - 构建时：文件名是 page.dev.tsx，只有 STARBUDDY_DEV_PAGES=1 且 STARBUDDY_ENV 为 dev / test 时构建才把它当页面
//     （next.config.ts 的 pageExtensions）；生产构建里没有这个路由，tests/build-output.test.ts 核对；
//   - 运行时：环境与本机 Host 再核对一次；
//   - 开关：STARBUDDY_TRUST_UI 含 report（只在 dev / test 生效，默认关），关时在读会话之前 404（src/trust/FirstReportDemo.tsx）。
// 身份只来自真实会话。只用合成数据（db/seeds/dev/first-report-synthetic.sql）。查询参数 verify=<判断编号>[,…] 列出要画虚线的待验证判断。
import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { devRuntimeAllowed } from '../../../../lib/dev-pages.ts';
import { FirstReportDemo } from '../../../../trust/FirstReportDemo.tsx';
import { parseVerifyIds } from '../../../../trust/report-view.ts';

export const dynamic = 'force-dynamic';

export default async function FirstReportDemoPage({ params, searchParams }: {
  params: Promise<{ studentId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!devRuntimeAllowed((await headers()).get('host'))) notFound();
  const { studentId } = await params;
  const verify = parseVerifyIds((await searchParams)['verify']);
  return <FirstReportDemo studentId={studentId} verify={verify} />;
}
