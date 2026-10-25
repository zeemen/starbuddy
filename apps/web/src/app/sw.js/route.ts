// Service Worker 脚本（LT-07）：内容见 src/pwa/sw-source.ts，版本为本次构建的 BUILD_ID；作用域是整站（/sw.js 在根路径，默认作用域 /）。
// no-store（注册时另设 updateViaCache: 'none'），每次更新检查都取最新脚本。
// 停用开关 STARBUDDY_PWA_KILL_SWITCH=1（src/pwa/features.ts）打开时返回自毁版：已安装的在下次更新检查时换成它，清缓存并注销。
// 读不到构建号时回 404（浏览器不安装）。
import { buildVersion } from '../../pwa/build-version.ts';
import { pwaKillSwitch } from '../../pwa/features.ts';
import { KILL_SOURCE, renderSwSource } from '../../pwa/sw-source.ts';

export const dynamic = 'force-dynamic';

const JS = { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' };

export function GET(): Response {
  if (pwaKillSwitch()) return new Response(KILL_SOURCE, { headers: JS });
  const version = buildVersion();
  if (!version) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } });
  return new Response(renderSwSource(version), { headers: JS });
}
