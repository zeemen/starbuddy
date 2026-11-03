// 源码静态检查（spec 验收“星光金不作装饰”“不缓存、不外链”）：
//   - 星光金色值只出现在 src/star/tokens.ts，并且 tokens.ts 只被 Star.tsx 引用；
//   - 源码里没有 localStorage / sessionStorage / indexedDB；Service Worker 只在 src/pwa/client.ts 注册（LT-07），不做推送；
//   - 源码里没有外部 http(s) 资源地址，也没有 next/font/google 之类的在线字体；
//   - 不画装饰性背景（CSS 里没有星点、光斑类的背景图）。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';

const WEB = new URL('..', import.meta.url).pathname;
const SRC = join(WEB, 'src');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}
const sources = files(SRC).map((p) => ({ path: relative(WEB, p), text: readFileSync(p, 'utf8') }));
/** 去掉注释再查“用没用”：注释里说“不用 localStorage”不算使用 */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const configs = ['next.config.ts'].map((p) => ({ path: p, text: readFileSync(join(WEB, p), 'utf8') }));

test('星光金只在 tokens.ts；tokens.ts 只被 Star.tsx 引用', () => {
  const gold = /#?d9a441|217\s*,\s*164\s*,\s*65/i;
  const withGold = [...sources, ...configs].filter((f) => gold.test(f.text)).map((f) => f.path);
  assert.deepEqual(withGold, ['src/star/tokens.ts']);
  const importers = sources.filter((f) => /from ['"][^'"]*\/tokens(\.ts)?['"]/.test(f.text)).map((f) => f.path);
  assert.deepEqual(importers, ['src/star/Star.tsx']);
});

test('不用浏览器存储；Service Worker 接口只在 src/pwa/client.ts（LT-07：只注册与登出清缓存，不请求通知、不订阅推送）', () => {
  const CLIENT = 'src/pwa/client.ts';
  const SW_SOURCE = 'src/pwa/sw-source.ts';  // Service Worker 自己的源码（字符串，不在页面里运行）
  for (const f of sources) {
    const c = code(f.text);
    assert.ok(!/localStorage|sessionStorage|indexedDB|navigator\.storage/i.test(c), f.path);
    if (f.path !== CLIENT) assert.ok(!/serviceWorker/i.test(c), `${f.path} 不碰 Service Worker 接口`);
    if (f.path !== CLIENT && f.path !== SW_SOURCE) assert.ok(!/\bcaches\./.test(c), `${f.path} 不碰 Cache Storage`);
    assert.ok(!/\bNotification\b|PushManager|pushManager|showNotification|backgroundSync|periodicSync/.test(c), `${f.path} 不做推送通知`);
  }
  assert.ok(/serviceWorker/.test(code(sources.find((f) => f.path === CLIENT)!.text)));
});

test('源码与配置里没有外部资源地址、在线字体', () => {
  for (const f of [...sources, ...configs]) {
    const urls = f.text.match(/https?:\/\/[^\s'"`)]+/g) ?? [];
    const external = urls.filter((u) => !/^http:\/\/(127\.0\.0\.1|localhost|\[::1\]|bff\.invalid)(:|\/|$)/.test(u));
    assert.deepEqual(external, [], f.path);
    assert.ok(!/next\/font\/google|fonts\.googleapis|@import\s+url/.test(f.text), f.path);
  }
});

test('不画装饰性背景：CSS 里没有背景图与渐变', () => {
  for (const f of sources.filter((s) => s.path.endsWith('.css'))) {
    assert.ok(!/background(-image)?\s*:[^;]*(url\(|gradient\()/.test(f.text), f.path);
  }
});

test('所有调用 next 的脚本都关掉遥测（Next 默认向境外发匿名遥测，ADR 09）', () => {
  const pkg = JSON.parse(readFileSync(join(WEB, 'package.json'), 'utf8')) as { scripts: Record<string, string> };
  for (const [name, cmd] of Object.entries(pkg.scripts)) {
    if (/\bnext\b/.test(cmd)) assert.match(cmd, /NEXT_TELEMETRY_DISABLED=1/, name);
  }
  const root = join(WEB, '..', '..');
  const make = readFileSync(join(root, 'Makefile'), 'utf8').split('\n').filter((l) => /npx --no-install next /.test(l));
  assert.ok(make.length > 0);
  for (const l of make) assert.match(l, /NEXT_TELEMETRY_DISABLED=1/, l);
  assert.match(readFileSync(join(root, '.github/workflows/check.yml'), 'utf8'), /NEXT_TELEMETRY_DISABLED: "1"/);
  assert.match(readFileSync(join(WEB, 'playwright.config.ts'), 'utf8'), /NEXT_TELEMETRY_DISABLED: '1'/);
});

test('CSP 下不能有内联样式：组件里没有 style 属性与内联 <style>（H2，主题变量改为静态 theme.css）', () => {
  for (const f of sources.filter((s) => /\.tsx$/.test(s.path))) {
    assert.ok(!/\bstyle=\{/.test(f.text), `${f.path} 有 style 属性`);
    assert.ok(!/<style[\s>]/.test(code(f.text)), `${f.path} 有内联 <style>`);
    assert.ok(!/dangerouslySetInnerHTML/.test(f.text), `${f.path} 有 dangerouslySetInnerHTML`);
  }
});

test('开发身份桩已删除：源码、配置、端到端启动脚本里都没有它的痕迹（e2e 用例里只在“已删除”的负向断言中出现）', () => {
  const extra = ['next.config.ts', 'playwright.config.ts', 'scripts/e2e.sh', 'README.md', 'e2e/session.ts']
    .map((p) => ({ path: p, text: readFileSync(join(WEB, p), 'utf8') }));
  for (const f of [...sources, ...extra]) {
    assert.ok(!/x-dev-account-id|sb_dev_account|STARBUDDY_DEV_AUTH|\/dev\/login|\/dev\/session|dev-auth|SYNTHETIC_ACCOUNTS/.test(f.text), f.path);
  }
});
