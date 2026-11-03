// 舞台样张页（specs/stage-whiteboard-step1）：packages/stage 的讲解 DSL v0.1 与最小 SVG 白板，用合成 DSL 逐步播放、展示非法样例被拒。
// 开发页（文件名 page.dev.tsx）：只在开发、测试构建里存在（构建期常量，next.config.ts）；运行时再核对环境与本机 Host，否则 404。
// 不读会话、不连 BFF、不接 teach、不计量、不落库。
import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { devRuntimeAllowed } from '../../../lib/dev-pages.ts';
import { StageDemo } from '../../../stage/StageDemo.tsx';

export const dynamic = 'force-dynamic';

export default async function StagePage() {
  if (!devRuntimeAllowed((await headers()).get('host'))) notFound();
  return (
    <main data-stage-page>
      <h1>舞台样张</h1>
      <p><span className="watermark">原型 · 合成 DSL · 角色只放占位框，正式母版交付后替换</span></p>
      <StageDemo />
    </main>
  );
}
