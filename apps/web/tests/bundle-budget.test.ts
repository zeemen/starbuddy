// 包体积预算（LT-07 范围第 4 条；make web-test 先 next build 再跑）。按生产构建 .next 计算 gzip 后的字节数：
//   - 核心页面的首屏 JS：框架公共入口（build-manifest.json 的 rootMainFiles + polyfillFiles）加该页面 client reference manifest 里登记的
//     entryJSFiles（根布局、页面与其组件的分块）；首屏 CSS：entryCSSFiles；
//   - 全部 /_next/static 下 JS 的总量与最大单个分块。
// 阈值是占位（BUDGET，2026-10-24 按当时的构建实测值约 1.3 倍取整），待性能 SLO 签字后替换（spec 范围第 4 条“待签字”）。
// 3G 节流下首屏可交互的目标同样待签字，这里不测。超预算时打印各页实测值，便于评审判断是该瘦身还是该调预算。
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { gzipSync } from 'node:zlib';

const NEXT = new URL('../.next', import.meta.url).pathname;
const BUILT = existsSync(join(NEXT, 'BUILD_ID'));

/** 预算（字节，gzip 后）。占位，待签字 */
// 2026-10-24 实测：核心页面首屏 JS 173.6–187.2 KB、CSS 3.1 KB；static JS 总量 222.2 KB（21 个），最大分块 71.6 KB
export const BUDGET = {
  routeFirstLoadJs: 245_000,
  routeFirstLoadCss: 5_000,
  totalStaticJs: 290_000,
  largestChunk: 95_000,
} as const;

/** 核心页面（spec 范围：学生端与家长端核心流程）：src/app 下的路由目录 */
export const CORE_ROUTES = ['login', 'family', 'family/[studentId]', 'family/[studentId]/star-map', 's/[studentId]/today',
  's/[studentId]/star-map', 's/[studentId]/learn', 's/[studentId]/learn/[subject]'];

const gz = (rel: string): number => gzipSync(readFileSync(join(NEXT, rel.replace(/^\/?_next\//, '')))).length;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => { const p = join(dir, n); return statSync(p).isDirectory() ? walk(p) : [p]; });
}

function manifestOf(route: string): { entryJSFiles: Record<string, string[]>; entryCSSFiles: Record<string, { path: string }[]> } {
  const text = readFileSync(join(NEXT, 'server', 'app', route, 'page_client-reference-manifest.js'), 'utf8');
  const start = text.indexOf('= {');
  assert.ok(start > 0, `${route} 的 client reference manifest 格式变了`);
  return JSON.parse(text.slice(start + 2).trim().replace(/;$/, '')) as ReturnType<typeof manifestOf>;
}

test('包体积预算（占位阈值，待签字）：核心页面首屏 JS / CSS、静态 JS 总量与最大分块', { skip: BUILT ? false : '没有 .next 构建产物（先 next build）' }, () => {
  const build = JSON.parse(readFileSync(join(NEXT, 'build-manifest.json'), 'utf8')) as { rootMainFiles: string[]; polyfillFiles: string[] };
  const shared = [...build.rootMainFiles, ...build.polyfillFiles];
  assert.ok(shared.length > 0, 'build-manifest.json 里没有公共入口');
  const report: string[] = [];
  const over: string[] = [];
  for (const route of CORE_ROUTES) {
    const m = manifestOf(route);
    const js = new Set([...shared, ...Object.values(m.entryJSFiles).flat()]);
    const css = new Set(Object.values(m.entryCSSFiles).flat().map((c) => c.path));
    const jsBytes = [...js].reduce((s, f) => s + gz(f), 0);
    const cssBytes = [...css].reduce((s, f) => s + gz(f), 0);
    report.push(`${route}: JS ${jsBytes} B（${js.size} 个文件），CSS ${cssBytes} B`);
    if (jsBytes > BUDGET.routeFirstLoadJs) over.push(`${route} 首屏 JS ${jsBytes} > ${BUDGET.routeFirstLoadJs}`);
    if (cssBytes > BUDGET.routeFirstLoadCss) over.push(`${route} 首屏 CSS ${cssBytes} > ${BUDGET.routeFirstLoadCss}`);
  }
  const chunks = walk(join(NEXT, 'static')).filter((p) => p.endsWith('.js'));
  const sizes = chunks.map((p) => gzipSync(readFileSync(p)).length);
  const total = sizes.reduce((a, b) => a + b, 0);
  const largest = Math.max(...sizes);
  report.push(`static JS 总量 ${total} B（${chunks.length} 个），最大分块 ${largest} B`);
  if (total > BUDGET.totalStaticJs) over.push(`static JS 总量 ${total} > ${BUDGET.totalStaticJs}`);
  if (largest > BUDGET.largestChunk) over.push(`最大分块 ${largest} > ${BUDGET.largestChunk}`);
  console.log(`[bundle-budget] ${report.join('；')}`);
  assert.deepEqual(over, [], report.join('\n'));
});
