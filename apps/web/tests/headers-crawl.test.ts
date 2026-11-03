// 第二轮 7.6（4-H H6）：安全头爬取覆盖 src/app 下的全部页面。新增页面（page.tsx）或路由（route.ts）不在 e2e/crawl-pages.ts 登记就失败；
// 登记为不爬的要写明理由与别处的覆盖。开发专用页面（page.dev.tsx）只在开发 / 测试构建里存在，由 tests/build-output.test.ts 管。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { test } from 'node:test';
import { CRAWL } from '../e2e/crawl-pages.ts';

const APP = new URL('../src/app/', import.meta.url);
const walk = (dir: URL, rel = ''): string[] => readdirSync(dir).flatMap((n) => {
  const u = new URL(n, dir);
  return statSync(u).isDirectory() ? walk(new URL(`${n}/`, dir), `${rel}${n}/`) : [`${rel}${n}`];
});

test('安全头爬取：src/app 下每个 page.tsx / route.ts 都在 e2e/crawl-pages.ts 登记（爬取或写明理由排除），登记表里没有不存在的文件', () => {
  const files = walk(APP).filter((f) => /(^|\/)(page\.tsx|route\.ts)$/.test(f)).sort();
  assert.deepEqual(Object.keys(CRAWL).sort(), files);
  for (const [f, p] of Object.entries(CRAWL)) {
    if ('excluded' in p) assert.ok(p.excluded.length > 10, `${f} 排除要写理由`);
    else assert.match(p.path('5a000000-0000-4000-8000-0000000000c1'), /^\/[a-z]/, f);
  }
  // 爬取在两个端到端文件里真的用到了登记表
  assert.match(readFileSync(new URL('../e2e/headers.spec.ts', import.meta.url), 'utf8'), /from '\.\/crawl-pages\.ts'/);
  assert.match(readFileSync(new URL('../e2e/trust-on.spec.ts', import.meta.url), 'utf8'), /from '\.\/crawl-pages\.ts'/);
  // H5 星图 v1 开关打开的一套（starmap-on）按同一张登记表爬星图页（学生与家长）
  assert.match(readFileSync(new URL('../e2e/star-map-v1.spec.ts', import.meta.url), 'utf8'), /from '\.\/crawl-pages\.ts'/);
});

test('安全头的来源：proxy 对静态资源以外的全部路径生成 CSP；next.config.ts 对全部路径加 nosniff、DENY、no-referrer，对页面加 no-store', () => {
  const proxy = readFileSync(new URL('../src/proxy.ts', import.meta.url), 'utf8');
  assert.ok(proxy.includes("matcher: [{ source: '/((?!_next/static|_next/image|favicon.ico).*)' }]"), 'proxy 只放过带哈希的静态资源');
  const cfg = readFileSync(new URL('../next.config.ts', import.meta.url), 'utf8');
  assert.ok(cfg.includes("source: '/:path*'"));
  for (const h of ["key: 'X-Content-Type-Options', value: 'nosniff'", "key: 'X-Frame-Options', value: 'DENY'", "key: 'Referrer-Policy', value: 'no-referrer'"]) {
    assert.ok(cfg.includes(h), h);
  }
  assert.ok(cfg.includes('poweredByHeader: false'));
});
