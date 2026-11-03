// 星图页（3-H 原型，spec 第 7.1 节 H2）：服务端组件，经 BFF 读取；浏览器只拿到渲染后的页面。
// 学生在 /s/{studentId}/star-map，家长在 /family/{studentId}/star-map；身份只来自会话（没有或失效去登录页），
// 学生会话打开家长地址（或反过来）转到自己那一侧的地址；未授权与不存在同样 404（BFF 与数据库判断）。
// 7.2 的前提满足之前数据源、三态映射与“原型 · 合成数据”水印都不动（ADR 11 过渡条款），不新增任何关于学生的文字。
// H5 星图 v1 挂在前端开关 starmap_v1 后（src/star/features.ts，默认关）：关时下面 v0 的路径与输出一字不变；
// 开时改读 BFF 的 v1 接口，由 StarMapV1.tsx 渲染（会话、跳转与 404 的处理与 v0 相同）。
import { notFound, redirect } from 'next/navigation';
import { pageSession } from '../lib/session.ts';
import { fetchStarMap, isUuid } from '../lib/star-map.ts';
import { fetchStarMapV1 } from '../lib/star-map-v1.ts';
import { STAR_MAP_V1 } from '../shell/copy.ts';
import { starMapFeatures } from './features.ts';
import { StarDefs } from './Star.tsx';
import { StarMapV1View } from './StarMapV1.tsx';
import { StarSky } from './StarSky.tsx';
import { viewMap } from './v1-view.ts';

export const starMapPath = (audience: 'student' | 'guardian', studentId: string): string =>
  audience === 'student' ? `/s/${studentId}/star-map` : `/family/${studentId}/star-map`;

export async function StarMapPage({ studentId, audience }: { studentId: string; audience: 'student' | 'guardian' }) {
  if (!isUuid(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  const kind = ps.me.account_kind === 'student' ? 'student' : 'guardian';
  if (kind !== audience) redirect(starMapPath(kind, studentId));
  const flags = starMapFeatures();
  if (flags.has('starmap_v1')) {
    // H5 星图 v1（开关 starmap_v1，默认关）：星 = 知识节点，判断先于展示；水印照旧保留（spec #6）
    const v1 = await fetchStarMapV1(studentId, ps.session);
    if (v1.kind === 'unauthenticated') redirect('/login');
    if (v1.kind === 'not-found') notFound();
    return (
      <main>
        <StarDefs />
        <h1>{STAR_MAP_V1.title}</h1>
        <p><span className="watermark" data-testid="watermark">{STAR_MAP_V1.watermark}</span></p>
        <StarMapV1View view={viewMap(v1.map, { dashed: flags.has('starmap_dashed') })} audience={audience} studentId={studentId} />
      </main>
    );
  }
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
      {/* 星的详情位的“为什么”（冲刺 5-H）：入口挂在判断上（src/trust/links.tsx 的 ClaimLinks，“我不同意”在“为什么”页里）。
          现在的星图仍是 3-H 的 v0 形状，星上没有 claim_id（7.2 的 v1 才带），按规则 11 这里不渲染任何入口；
          v1 切换时每颗非中性的星旁放 <ClaimLinks claimId={star.claim_id} />。 */}
    </main>
  );
}
