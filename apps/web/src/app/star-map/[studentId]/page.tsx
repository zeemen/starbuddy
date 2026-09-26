// 星图原型页（spec 第 1、3、4、5 节）：服务端组件，经 BFF 读取；浏览器只拿到渲染后的页面。
// 每个在读学科一片星空；没有选科时显示空状态。未登录去开发登录页；未授权与不存在同样 404。
import { cookies } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { DEV_COOKIE, devAccountFromCookie, devAuthEnabled } from '../../../lib/dev-auth.ts';
import { fetchStarMap } from '../../../lib/star-map.ts';
import { StarDefs } from '../../../star/Star.tsx';
import { StarSky } from '../../../star/StarSky.tsx';

export const dynamic = 'force-dynamic';

export default async function StarMapPage({ params }: { params: Promise<{ studentId: string }> }) {
  const { studentId } = await params;
  const account = devAccountFromCookie((await cookies()).get(DEV_COOKIE)?.value);
  const result = await fetchStarMap(studentId, account);
  if (result.kind === 'unauthenticated') {
    if (devAuthEnabled()) redirect(`/dev/login?next=${encodeURIComponent(`/star-map/${studentId}`)}`);
    return (
      <main>
        <h1>星图</h1>
        <p>请先登录。</p>
      </main>
    );
  }
  if (result.kind === 'not-found') notFound();
  const { map } = result;
  return (
    <main>
      <StarDefs />
      <h1>星图</h1>
      <p><span className="watermark" data-testid="watermark">原型 · 合成数据</span></p>
      {map.skies.length === 0 ? (
        <p data-testid="empty">还没有选科</p>
      ) : (
        map.skies.map((sky) => <StarSky key={sky.subject} sky={sky} />)
      )}
    </main>
  );
}
