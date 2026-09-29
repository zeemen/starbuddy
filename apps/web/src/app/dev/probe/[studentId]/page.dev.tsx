// “能做对吗”探针的开发环境演示页（冲刺 2-L，spec 待人确认 #6 默认：做题页随冲刺 4 统一外壳落地，先做组件与演示页）。
// 两道关：
//   - 构建时：文件名是 page.dev.tsx，只有 STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV 为 dev / test 时构建才把它当页面
//     （next.config.ts 的 pageExtensions）；生产构建里没有这个路由，tests/build-output.test.ts 核对；
//   - 运行时：开发开关与本机 Host 再核对一次（同 /dev/login），没登录跳开发登录页。
// 只显示合成题的题号占位，不显示题干；探针结果、行为事件都不在页面上显示（只给 Agent 用）。
import { cookies, headers } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { DEV_COOKIE, devAccountFromCookie, devAuthEnabled, isLoopbackHost } from '../../../../lib/dev-auth.ts';
import { DEMO_PROBE_ITEM } from '../../../../lib/events.ts';
import { isUuid } from '../../../../lib/star-map.ts';
import { DEMO_COPY } from '../../../../probe/copy.ts';
import { ProbeDemo } from './ProbeDemo.tsx';

export const dynamic = 'force-dynamic';

export default async function ProbeDemoPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!devAuthEnabled() || !isLoopbackHost((await headers()).get('host'))) notFound();
  const { studentId } = await params;
  if (!isUuid(studentId)) notFound();
  const account = devAccountFromCookie((await cookies()).get(DEV_COOKIE)?.value);
  if (!account) redirect(`/dev/login?next=${encodeURIComponent(`/dev/probe/${studentId}`)}`);
  return (
    <main data-testid="probe-page">
      <h1>{DEMO_COPY.title}</h1>
      <p className="muted">{DEMO_COPY.note}</p>
      <ProbeDemo student={studentId} itemId={DEMO_PROBE_ITEM.item_id} />
    </main>
  );
}
