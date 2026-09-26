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
