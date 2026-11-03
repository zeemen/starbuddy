// “能做对吗”探针的开发环境演示页（冲刺 2-L，spec 待人确认 #6 默认：做题页随冲刺 4 统一外壳落地，先做组件与演示页）。
// 两道关：
//   - 构建时：文件名是 page.dev.tsx，只有 STARBUDDY_DEV_PAGES=1 且 STARBUDDY_ENV 为 dev / test 时构建才把它当页面
//     （next.config.ts 的 pageExtensions）；生产构建里没有这个路由，tests/build-output.test.ts 核对；
//   - 运行时：环境与本机 Host 再核对一次；身份只来自真实会话（4-H H2，开发身份桩已删除），没登录去 /login，
//     家长会话也能打开（提交时由 BFF 按“只认学生本人”拒绝，页面只提示没记下）。
// 只显示合成题的题号占位，不显示题干；探针结果、行为事件都不在页面上显示（只给 Agent 用）。
import { headers } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { devRuntimeAllowed } from '../../../../lib/dev-pages.ts';
import { pageSession } from '../../../../lib/session.ts';
import { DEMO_PROBE_ITEM } from '../../../../lib/events.ts';
import { isUuid } from '../../../../lib/star-map.ts';
import { DEMO_COPY } from '../../../../probe/copy.ts';
import { ProbeDemo } from './ProbeDemo.tsx';

export const dynamic = 'force-dynamic';

export default async function ProbeDemoPage({ params }: { params: Promise<{ studentId: string }> }) {
  if (!devRuntimeAllowed((await headers()).get('host'))) notFound();
  const { studentId } = await params;
  if (!isUuid(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  return (
    <main data-testid="probe-page">
      <h1>{DEMO_COPY.title}</h1>
      <p className="muted">{DEMO_COPY.note}</p>
      <ProbeDemo csrf={ps.session.csrf} student={studentId} itemId={DEMO_PROBE_ITEM.item_id} />
    </main>
  );
}
