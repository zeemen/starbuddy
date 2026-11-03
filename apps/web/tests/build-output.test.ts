// 构建产物检查（spec 验收“不缓存、不外链”）：.next/ 里没有加载外部资源的引用（白名单为空）。
// 只查“会被浏览器自动加载”的写法：CSS 的 url(http…) 与 @import、HTML 的 src / srcset / poster 与 <link href> 指向外部、
// 以及脚本里的 importScripts / new Worker / fetch("http…")。命名空间字符串（http://www.w3.org/2000/svg）
// 与 React 报错信息里的文档链接不会被加载，不算外链。运行时的真实请求由 e2e 的“所有请求都是同源”兜底。
// 需要先 next build；make web-test 先构建再跑本测试。
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';

const NEXT = new URL('../.next', import.meta.url).pathname;
const BUILT = existsSync(join(NEXT, 'BUILD_ID'));
/** 打包器把非 ASCII 字符串写成反斜杠 u 加四位小写十六进制的写法 */
const escapeUnicode = (s: string): string =>
  [...s].map((c) => (c.charCodeAt(0) < 128 ? c : `${String.fromCharCode(92)}u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)).join('');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? (name === 'cache' ? [] : files(p)) : [p];
  });
}

test('构建产物里没有外部资源引用', { skip: BUILT ? false : '没有 .next 构建产物（先 next build）' }, () => {
  const offenders: string[] = [];
  const LOCAL = /^(https?:)?\/\/(127\.0\.0\.1|localhost)(:|\/)/;
  for (const p of files(NEXT)) {
    if (!/\.(css|html|js|json|rsc|txt)$/.test(p)) continue;
    const text = readFileSync(p, 'utf8');
    const refs = [
      ...text.matchAll(/url\(\s*['"]?((?:https?:)?\/\/[^)'"\s]+)/g),
      ...text.matchAll(/@import\s+(?:url\()?['"]?((?:https?:)?\/\/[^)'"\s;]+)/g),
      ...text.matchAll(/\b(?:src|srcset|poster)=\\?["']((?:https?:)?\/\/[^"'\\\s]+)/g),
      // href 只看 <link>（样式、预加载、图标会被自动加载）；<a href> 是用户点击的链接，不是资源
      ...text.matchAll(/<link\b[^>]*?\bhref=\\?["']((?:https?:)?\/\/[^"'\\\s]+)/g),
      ...text.matchAll(/(?:importScripts|new Worker|fetch)\(\s*["'`]((?:https?:)?\/\/[^"'`]+)/g),
    ].map((m) => m[1]!).filter((u) => !LOCAL.test(u));
    for (const u of refs) offenders.push(`${relative(NEXT, p)}: ${u}`);
  }
  assert.deepEqual(offenders, []);
});

// 开发专用页面（冲刺 2-L 探针演示页 /dev/probe，文件名 page.dev.tsx）不在生产构建里：路由表里没有、服务端产物里没有、
// 任何产物里都没有演示页的文案与标记。端到端的测试构建输出到单独的 .next-e2e（scripts/e2e.sh），.next 永远是生产构建，
// 这项检查不再有“测试构建就跳过”的出口。
test('生产构建里没有开发专用页面（探针演示页、数学诊断演示页、符号样张页）', {
  skip: BUILT ? false : '没有 .next 构建产物（先 next build）',
}, () => {
  const routes = JSON.parse(readFileSync(join(NEXT, 'app-path-routes-manifest.json'), 'utf8')) as Record<string, string>;
  assert.ok(Object.keys(routes).length > 0, '路由表为空，检查不到东西');
  assert.deepEqual(Object.values(routes).filter((r) => r.startsWith('/dev/')), []);
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'probe')));
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'diagnosis')));  // 数学诊断演示页（SIE-02a）
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'star-glyphs')));  // 符号样张页（4-H H2 起只在开发构建里）
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'first-report')));  // 首份报告演示页（SIE-02b 第二步 PR 2）
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'stage')));  // 舞台样张页（specs/stage-whiteboard-step1）
  const markers = ['探针演示', '\\u63a2\\u9488\\u6f14\\u793a', 'probe-demo', 'page.dev', 'first-report-page', 'data-report-verify',
    '数学诊断演示', '\\u6570\\u5b66\\u8bca\\u65ad\\u6f14\\u793a', 'diagnosis-page', '老师会看你的过程', '星形符号样张',
    '\\u8001\\u5e08\\u4f1a\\u770b\\u4f60\\u7684\\u8fc7\\u7a0b',
    // 舞台样张页与 packages/stage（只有样张页引用它）：页面标记、白板的属性名、合成样例标题（原文与 \u 转义两种写法）
    'data-stage-page', 'data-stage-demo', 'data-stage-part', ...['舞台样张', '（合成样例）一元一次方程'].flatMap((s) => [s, escapeUnicode(s)])];
  const hits = files(NEXT).filter((p) => /\.(css|html|js|json|rsc|txt)$/.test(p))  // 不查 .map：源码映射里带注释原文
    .filter((p) => { const t = readFileSync(p, 'utf8'); return markers.some((m) => t.includes(m)); })
    .map((p) => relative(NEXT, p));
  assert.deepEqual(hits, []);
});

// 开发身份桩（4-H H2 删除）：生产构建里找不到它的任何痕迹——请求头名、cookie 名、开关名、开发登录与写 cookie 的路由、
// 合成账号列表里的账号 ID。
test('生产构建里没有开发身份桩的痕迹', { skip: BUILT ? false : '没有 .next 构建产物（先 next build）' }, () => {
  const routes = JSON.parse(readFileSync(join(NEXT, 'app-path-routes-manifest.json'), 'utf8')) as Record<string, string>;
  assert.deepEqual(Object.values(routes).filter((r) => r.startsWith('/dev') || r.startsWith('/star-map')), []);
  const markers = ['x-dev-account-id', 'sb_dev_account', 'STARBUDDY_DEV_AUTH', '/dev/login', '/dev/session', 'SYNTHETIC_ACCOUNTS',
    '5a000000-0000-4000-8000-0000000000b1', '5a000000-0000-4000-8000-0000000000a1', 'devAccountFromCookie'];
  const hits = files(NEXT).filter((p) => !p.endsWith('.map'))
    .filter((p) => { const t = readFileSync(p, 'utf8'); return markers.some((m) => t.includes(m)); })
    .map((p) => relative(NEXT, p));
  assert.deepEqual(hits, []);
});
