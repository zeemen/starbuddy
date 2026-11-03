// 占位图标（LT-07）：manifest 与 apple-touch-icon 引用的 PNG，由 src/pwa/icon.ts 用代码生成（不含第三方素材）。
// 只认登记的几个文件名，其余 404。静态壳的一部分：Service Worker 预缓存（src/pwa/sw-source.ts 的 STATIC_SHELL）。
import { iconFor } from '../../../pwa/icon.ts';

export async function GET(_req: Request, { params }: { params: Promise<{ file: string }> }): Promise<Response> {
  const png = iconFor((await params).file);
  if (!png) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } });
  return new Response(new Uint8Array(png), { headers: { 'content-type': 'image/png', 'cache-control': 'no-store' } });
}
