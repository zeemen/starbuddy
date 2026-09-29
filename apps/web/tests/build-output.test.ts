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
// 任何产物里都没有演示页的文案与标记。scripts/e2e.sh 会把 .next 覆盖成测试构建并留下标记文件，见到标记就跳过。
const TEST_BUILD = existsSync(join(NEXT, 'STARBUDDY_TEST_BUILD'));

test('生产构建里没有开发专用页面（探针演示页）', {
  skip: !BUILT ? '没有 .next 构建产物（先 next build）' : TEST_BUILD ? '.next 是 scripts/e2e.sh 的测试构建，不是生产构建' : false,
}, () => {
  const routes = JSON.parse(readFileSync(join(NEXT, 'app-path-routes-manifest.json'), 'utf8')) as Record<string, string>;
  assert.ok(Object.keys(routes).length > 0, '路由表为空，检查不到东西');
  assert.deepEqual(Object.values(routes).filter((r) => r.startsWith('/dev/probe')), []);
  assert.ok(!existsSync(join(NEXT, 'server', 'app', 'dev', 'probe')));
  const markers = ['探针演示', '\\u63a2\\u9488\\u6f14\\u793a', 'probe-demo', 'page.dev'];
  const hits = files(NEXT).filter((p) => /\.(css|html|js|json|rsc|txt)$/.test(p))  // 不查 .map：源码映射里带注释原文
    .filter((p) => { const t = readFileSync(p, 'utf8'); return markers.some((m) => t.includes(m)); })
    .map((p) => relative(NEXT, p));
  assert.deepEqual(hits, []);
});
