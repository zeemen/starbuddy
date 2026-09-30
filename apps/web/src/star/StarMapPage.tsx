// 星图页（3-H 原型，spec 第 7.1 节 H2）：服务端组件，经 BFF 读取；浏览器只拿到渲染后的页面。
// 学生在 /s/{studentId}/star-map，家长在 /family/{studentId}/star-map；身份只来自会话（没有或失效去登录页），
// 学生会话打开家长地址（或反过来）转到自己那一侧的地址；未授权与不存在同样 404（BFF 与数据库判断）。
// 7.2 的前提满足之前数据源、三态映射与“原型 · 合成数据”水印都不动（ADR 11 过渡条款），不新增任何关于学生的文字。
import { notFound, redirect } from 'next/navigation';
import { pageSession } from '../lib/session.ts';
import { fetchStarMap, isUuid } from '../lib/star-map.ts';
import { StarDefs } from './Star.tsx';
import { StarSky } from './StarSky.tsx';

export const starMapPath = (audience: 'student' | 'guardian', studentId: string): string =>
  audience === 'student' ? `/s/${studentId}/star-map` : `/family/${studentId}/star-map`;

export async function StarMapPage({ studentId, audience }: { studentId: string; audience: 'student' | 'guardian' }) {
  if (!isUuid(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  const kind = ps.me.account_kind === 'student' ? 'student' : 'guardian';
  if (kind !== audience) redirect(starMapPath(kind, studentId));
  const result = await fetchStarMap(studentId, ps.session);
  if (result.kind === 'unauthenticated') redirect('/login');
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
