// LT-07 PWA（specs/launch-lt07-mobile-pwa 决定记录第 1–3 条）的单测与静态核对，全部合成值：
//   - Service Worker 源码在 node:vm 里按真实事件（install / activate / fetch / message）跑：只预缓存静态壳；构建产物（immutable）才进缓存；
//     页面导航、API、BFF 路径、RSC、带查询串、非 GET、跨源一律不接管、不进缓存；离线导航回固定离线页；登出消息清掉全部缓存后只重建静态壳；
//     激活时删除旧版本缓存；没有推送、通知、后台同步。
//   - 占位图标 PNG 的结构与颜色；manifest 字段；UA 识别；文案占位与禁语；离线页与 copy.ts 逐字一致、无脚本无外链；
//   - 构建号读取与 next.config.ts 的输出目录一致；登出表单带清缓存；镜像带上 public/。
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import vm from 'node:vm';
import { inflateSync } from 'node:zlib';
import { distDirFor } from '../next.config.ts';
import manifest from '../src/app/manifest.ts';
import { GET as iconRoute } from '../src/app/icons/[file]/route.ts';
import { GET as swRoute } from '../src/app/sw.js/route.ts';
import { XUANBAI, YAQING } from '../src/lib/theme.ts';
import { buildVersion, distDirOf } from '../src/pwa/build-version.ts';
import { CACHE_PREFIX, CLEAR_MESSAGE, CLEARED_MESSAGE, OFFLINE_PATH, STATIC_SHELL } from '../src/pwa/constants.ts';
import { ALL_PWA_COPY, INSTALL, OFFLINE } from '../src/pwa/copy.ts';
import { ICON_FILES, iconFor, iconPng } from '../src/pwa/icon.ts';
import { pwaKillSwitch } from '../src/pwa/features.ts';
import { KILL_SOURCE, renderSwSource } from '../src/pwa/sw-source.ts';
import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { installOs, isWeChat } from '../src/pwa/ua.ts';
import { loadForbidden } from './forbidden-lexicon.ts';

const WEB = new URL('..', import.meta.url).pathname;
const ROOT = join(WEB, '..', '..');
const ORIGIN = 'https://web.synthetic.invalid';
const V = 'synthetic-build-1';

// ---------- Service Worker 的 vm 模拟 ----------
type Req = { method: string; url: string; mode: string; headers: Headers };
type FetchEvent = { request: Req; responded?: Promise<Response> };
const req = (path: string, o: { method?: string; mode?: string; headers?: Record<string, string>; origin?: string } = {}): Req =>
  ({ method: o.method ?? 'GET', url: new URL(path, o.origin ?? ORIGIN).href, mode: o.mode ?? 'cors', headers: new Headers(o.headers ?? {}) });

function basic(body: string, headers: Record<string, string> = {}, status = 200): Response {
  const r = new Response(body, { status, headers });
  Object.defineProperty(r, 'type', { value: 'basic' });
  return r;
}

function harness(opts: { offline?: boolean } = {}) {
  const handlers: Record<string, (e: unknown) => void> = {};
  const store = new Map<string, Map<string, Response>>();
  const fetched: string[] = [];
  const replies: unknown[] = [];
  const state = { offline: opts.offline ?? false, skipped: 0, claimed: 0 };
  const keyOf = (r: string | { url: string }) => (typeof r === 'string' ? r : r.url);
  const caches = {
    open: async (n: string) => {
      if (!store.has(n)) store.set(n, new Map());
      const m = store.get(n)!;
      return {
        match: async (r: string | { url: string }) => m.get(keyOf(r))?.clone(),
        put: async (r: string | { url: string }, res: Response) => { m.set(keyOf(r), res); },
      };
    },
    keys: async () => [...store.keys()],
    delete: async (n: string) => store.delete(n),
  };
  const fakeFetch = async (r: string | { url: string }) => {
    const url = keyOf(r);
    fetched.push(new URL(url).pathname + new URL(url).search);
    if (state.offline) throw new TypeError('network down (synthetic)');
    const p = new URL(url).pathname;
    if (p.startsWith('/_next/static/immutable-redirected')) {
      const r = basic('/* synthetic */', { 'cache-control': 'public, max-age=31536000, immutable' });
      Object.defineProperty(r, 'redirected', { value: true });
      return r;
    }
    if (p.startsWith('/_next/static/immutable-')) return basic('/* synthetic */', { 'cache-control': 'public, max-age=31536000, immutable' });
    if (p.startsWith('/_next/static/')) return basic('/* synthetic */', { 'cache-control': 'no-store' });
    return basic(`synthetic ${p}`, { 'cache-control': 'no-store' });
  };
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (t: string, fn: (e: unknown) => void) => { handlers[t] = fn; },
    skipWaiting: async () => { state.skipped++; },
    clients: { claim: async () => { state.claimed++; } },
  };
  vm.runInContext(renderSwSource(V), vm.createContext({ self, caches, fetch: fakeFetch, Request, Response, URL, Headers, Promise, JSON, Error, TypeError }));
  const until = async (e: string, extra: Record<string, unknown> = {}) => {
    let p: Promise<unknown> = Promise.resolve();
    handlers[e]!({ ...extra, waitUntil: (x: Promise<unknown>) => { p = x; } });
    await p;
  };
  const dispatchFetch = (r: Req): Promise<Response> | undefined => {
    const ev: FetchEvent & { respondWith: (p: Promise<Response>) => void } = { request: r, respondWith(p) { this.responded = p; } };
    handlers['fetch']!(ev);
    return ev.responded;
  };
  const entries = () => [...store.entries()].flatMap(([n, m]) => [...m.keys()].map((k) => `${n} ${new URL(k).pathname}${new URL(k).search}`));
  return { handlers, store, fetched, replies, state, until, dispatchFetch, entries,
    message: (data: unknown) => until('message', { data, source: { postMessage: (m: unknown) => replies.push(m) } }) };
}

const SHELL_CACHE = `${CACHE_PREFIX}shell-${V}`;
const STATIC_CACHE = `${CACHE_PREFIX}static-${V}`;

test('Service Worker 源码：版本只收构建号字符集；没有推送、通知、后台同步、importScripts，没有外部地址', () => {
  assert.throws(() => renderSwSource('x\';alert(1)//'));
  assert.throws(() => renderSwSource(''));
  const src = renderSwSource(V);
  assert.ok(src.includes(`var VERSION = '${V}'`));
  assert.ok(!/__SB_[A-Z]+__/.test(src), '占位都替换了');
  for (const bad of [/addEventListener\('(push|sync|periodicsync|notificationclick|notificationclose)'/, /showNotification|pushManager|importScripts|localStorage|indexedDB/, /https?:\/\//]) {
    assert.ok(!bad.test(src), String(bad));
  }
  assert.equal(new Set(handlersOf(src)).size, 4);
  assert.deepEqual(handlersOf(src).sort(), ['activate', 'fetch', 'install', 'message']);
});
function handlersOf(src: string): string[] { return [...src.matchAll(/addEventListener\('([a-z]+)'/g)].map((m) => m[1]!); }

test('install：只预缓存静态壳（不带 cookie），然后 skipWaiting', async () => {
  const h = harness();
  await h.until('install');
  assert.deepEqual(h.fetched, [...STATIC_SHELL]);
  assert.deepEqual(h.entries().sort(), STATIC_SHELL.map((p) => `${SHELL_CACHE} ${p}`).sort());
  assert.equal(h.state.skipped, 1);
});

test('fetch：页面导航走网络、不进缓存；离线时回固定离线页', async () => {
  const h = harness();
  await h.until('install');
  const before = h.entries();
  for (const path of ['/family', '/s/5a000000-0000-4000-8000-0000000000c1/today', '/login', '/review/queue', '/offline.html?x=1']) {
    const res = await h.dispatchFetch(req(path, { mode: 'navigate' }));
    assert.ok(res, `${path} 导航由 SW 转交网络`);
    assert.equal(await res!.text(), `synthetic ${new URL(path, ORIGIN).pathname}`, path);
  }
  assert.deepEqual(h.entries(), before, '导航响应一条都没进缓存');
  h.state.offline = true;
  const off = await h.dispatchFetch(req('/s/5a000000-0000-4000-8000-0000000000c1/today', { mode: 'navigate' }));
  assert.equal(await off!.text(), `synthetic ${OFFLINE_PATH}`, '离线回离线页（预缓存的那一份）');
  assert.deepEqual(h.entries(), before);
});

test('fetch：API、BFF、RSC、server action、带查询串、非 GET、跨源与其余路径一律不接管（network-only，不进缓存）', async () => {
  const h = harness();
  await h.until('install');
  const before = h.entries();
  const cases: Req[] = [
    req('/api/anything'), req('/bff/v0/me'), req('/bff/v0/students/x/consents', { method: 'POST' }),
    req('/s/5a000000-0000-4000-8000-0000000000c1/today', { headers: { RSC: '1' } }),
    req('/family?_rsc=abc'), req('/family', { method: 'POST', headers: { 'Next-Action': 'synthetic' } }),
    req('/s/5a000000-0000-4000-8000-0000000000c1/presence', { method: 'POST' }),
    req('/family/5a000000-0000-4000-8000-0000000000c1/data/download', { method: 'POST' }),
    req('/family'), req('/healthz'), req('/readyz'), req('/sw.js'), req('/_next/image?url=x'),
    req('/_next/static/immutable-a.js?dpl=1'), req('/_next/static/immutable-a.js', { method: 'POST' }),
    req('/_next/static/immutable-a.js', { origin: 'https://other.synthetic.invalid' }),
    req('/icons/unknown.png'), req('/manifest.webmanifest', { method: 'HEAD' }),
  ];
  for (const r of cases) assert.equal(h.dispatchFetch(r), undefined, `${r.method} ${r.url}`);
  assert.deepEqual(h.entries(), before);
});

test('fetch：静态壳从缓存回；构建产物只有 immutable 的才进缓存，命中后不再走网络', async () => {
  const h = harness();
  await h.until('install');
  h.fetched.length = 0;
  const shell = await h.dispatchFetch(req('/offline.css'));
  assert.equal(await shell!.text(), 'synthetic /offline.css');
  assert.deepEqual(h.fetched, [], '静态壳命中缓存');
  const a = await h.dispatchFetch(req('/_next/static/immutable-a.js'));
  assert.equal(a!.status, 200);
  const b = await h.dispatchFetch(req('/_next/static/chunks/plain.js'));  // 响应头不是 immutable
  assert.equal(b!.status, 200);
  const c = await h.dispatchFetch(req('/_next/static/immutable-redirected.js'));  // 经过重定向的响应不进缓存
  assert.equal(c!.status, 200);
  assert.deepEqual(h.entries().filter((e) => e.startsWith(STATIC_CACHE)), [`${STATIC_CACHE} /_next/static/immutable-a.js`]);
  h.fetched.length = 0;
  await h.dispatchFetch(req('/_next/static/immutable-a.js'));
  assert.deepEqual(h.fetched, [], '第二次从缓存回');
  // 缓存里只有静态壳与构建产物
  for (const e of h.entries()) {
    const p = e.split(' ')[1]!;
    assert.ok((STATIC_SHELL as readonly string[]).includes(p) || /^\/_next\/static\/[^?]+$/.test(p), e);
  }
});

test('activate：删除旧版本与别的缓存，clients.claim', async () => {
  const h = harness();
  h.store.set(`${CACHE_PREFIX}shell-old`, new Map());
  h.store.set('other-cache', new Map());
  await h.until('install');
  await h.until('activate');
  assert.deepEqual([...h.store.keys()].sort(), [SHELL_CACHE]);
  assert.equal(h.state.claimed, 1);
});

test('message：登出清缓存——删掉全部缓存（含构建产物），只重建静态壳并回执；别的消息不理', async () => {
  const h = harness();
  await h.until('install');
  await h.dispatchFetch(req('/_next/static/immutable-a.js'));
  assert.ok(h.entries().some((e) => e.startsWith(STATIC_CACHE)));
  await h.message({ type: 'something-else' });
  assert.ok(h.entries().some((e) => e.startsWith(STATIC_CACHE)), '别的消息不清');
  await h.message({ type: CLEAR_MESSAGE });
  assert.deepEqual(h.entries().sort(), STATIC_SHELL.map((p) => `${SHELL_CACHE} ${p}`).sort());
  assert.equal(JSON.stringify(h.replies), JSON.stringify([{ type: CLEARED_MESSAGE, ok: true }]));  // vm 里建的对象跨 realm，按 JSON 比
});

// ---------- 路由、构建号、manifest、图标 ----------
test('构建号：输出目录与 next.config.ts 的 distDirFor 一致；只收构建号字符集，读不到返回 null', () => {
  for (const env of [{}, { STARBUDDY_NEXT_DIST_DIR: '.next-e2e' }, { STARBUDDY_NEXT_DIST_DIR: '../etc' }]) assert.equal(distDirOf(env), distDirFor(env));
  const dir = mkdtempSync(join(tmpdir(), 'sb-pwa-'));
  assert.equal(buildVersion({}, dir), null);
  mkdirSync(join(dir, '.next'));
  writeFileSync(join(dir, '.next', 'BUILD_ID'), 'Syn_build-01\n');
  assert.equal(buildVersion({}, dir), 'Syn_build-01');
  writeFileSync(join(dir, '.next', 'BUILD_ID'), "x';evil");
  assert.equal(buildVersion({}, dir), null);
});

test('/sw.js：有构建号时回脚本（text/javascript、no-store、作用域 /），没有时 404', async () => {
  const r = swRoute();
  assert.equal(r.headers.get('cache-control'), 'no-store');
  const id = buildVersion();
  if (id === null) { assert.equal(r.status, 404); return; }
  assert.equal(r.status, 200);
  assert.match(r.headers.get('content-type') ?? '', /^text\/javascript/);
  assert.equal(r.headers.get('service-worker-allowed'), null, '作用域就是脚本所在的 /，不放宽');
  assert.equal(await r.text(), renderSwSource(id));
});

test('停用开关：只认 1；打开时 /sw.js 发自毁版（不依赖构建号），页面不再注册', async () => {
  for (const [v, on] of [['1', true], ['', false], ['0', false], ['true', false], [undefined, false]] as const) {
    assert.equal(pwaKillSwitch(v === undefined ? {} : { STARBUDDY_PWA_KILL_SWITCH: v }), on, String(v));
  }
  const saved = process.env['STARBUDDY_PWA_KILL_SWITCH'];
  process.env['STARBUDDY_PWA_KILL_SWITCH'] = '1';
  try {
    const r = swRoute();
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    assert.equal(await r.text(), KILL_SOURCE);
  } finally {
    if (saved === undefined) delete process.env['STARBUDDY_PWA_KILL_SWITCH']; else process.env['STARBUDDY_PWA_KILL_SWITCH'] = saved;
  }
  const layout = readFileSync(join(WEB, 'src/app/layout.tsx'), 'utf8');
  assert.match(layout, /<PwaRegister enabled=\{!pwaKillSwitch\(\)\} \/>/);
  const reg = readFileSync(join(WEB, 'src/pwa/PwaRegister.tsx'), 'utf8');
  assert.match(reg, /if \(enabled\) registerSw\(\);\s*else updateExistingSw\(\);/);
});

test('自毁版 Service Worker：不接管请求；激活时删掉全部缓存、注销注册、让已打开的页面重新导航', async () => {
  assert.deepEqual(handlersOf(KILL_SOURCE).sort(), ['activate', 'install']);
  assert.ok(!/https?:\/\/|importScripts|respondWith/.test(KILL_SOURCE));
  const store = new Map<string, unknown>([['sb-shell-old', 1], ['sb-static-old', 1], ['other', 1]]);
  const calls: string[] = [];
  const handlers: Record<string, (e: unknown) => void> = {};
  const self = {
    addEventListener: (t: string, fn: (e: unknown) => void) => { handlers[t] = fn; },
    skipWaiting: async () => { calls.push('skipWaiting'); },
    registration: { unregister: async () => { calls.push('unregister'); return true; } },
    clients: {
      claim: async () => { calls.push('claim'); },
      matchAll: async () => [{ url: `${ORIGIN}/family`, navigate: async (u: string) => { calls.push(`navigate ${u}`); } },
                             { url: `${ORIGIN}/login`, navigate: async () => { throw new TypeError('synthetic'); } }],
    },
  };
  const caches = { keys: async () => [...store.keys()], delete: async (k: string) => store.delete(k) };
  vm.runInContext(KILL_SOURCE, vm.createContext({ self, caches, Promise, TypeError }));
  const run = async (e: string) => { let p: Promise<unknown> = Promise.resolve(); handlers[e]!({ waitUntil: (x: Promise<unknown>) => { p = x; } }); await p; };
  await run('install');
  await run('activate');
  assert.equal(store.size, 0, '全部缓存都删了');
  assert.deepEqual(calls, ['skipWaiting', 'claim', 'unregister', `navigate ${ORIGIN}/family`]);
});

test('manifest：standalone、同源 start_url 与 scope、占位图标都是登记的文件、主题色取页面主题；没有推送相关字段', () => {
  const m = manifest();
  assert.equal(m.display, 'standalone');
  assert.equal(m.start_url, '/login');
  assert.equal(m.scope, '/');
  assert.equal(m.lang, 'zh-CN');
  assert.equal(m.theme_color, XUANBAI);
  assert.equal(m.background_color, XUANBAI);
  const icons = m.icons ?? [];
  assert.deepEqual(icons.map((i) => i.sizes), ['192x192', '512x512', '512x512']);
  for (const i of icons) {
    const file = i.src.replace('/icons/', '');
    assert.ok(Object.hasOwn(ICON_FILES, file), i.src);
    assert.equal(i.sizes, `${ICON_FILES[file]}x${ICON_FILES[file]}`);
    assert.ok((STATIC_SHELL as readonly string[]).includes(i.src), `${i.src} 在预缓存的静态壳里`);
  }
  assert.ok(icons.some((i) => i.purpose === 'maskable'));
  for (const k of ['gcm_sender_id', 'share_target', 'protocol_handlers', 'related_applications']) assert.ok(!(k in m), k);
});

function pngInfo(png: Buffer) {
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const chunks: { type: string; data: Buffer }[] = [];
  for (let o = 8; o < png.length;) {
    const len = png.readUInt32BE(o);
    chunks.push({ type: png.toString('ascii', o + 4, o + 8), data: png.subarray(o + 8, o + 8 + len) });
    o += 12 + len;
  }
  return chunks;
}
const hex = (r: number, g: number, b: number) => `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`.toUpperCase();

test('占位图标：合法 PNG（IHDR、IDAT、IEND），尺寸对，四角是鸦青、中心是宣白；未登记的文件名 404', async () => {
  for (const [file, size] of Object.entries(ICON_FILES)) {
    const png = iconFor(file)!;
    const chunks = pngInfo(png);
    assert.deepEqual(chunks.map((c) => c.type), ['IHDR', 'IDAT', 'IEND'], file);
    assert.equal(chunks[0]!.data.readUInt32BE(0), size);
    assert.equal(chunks[0]!.data.readUInt32BE(4), size);
    const raw = inflateSync(chunks[1]!.data);
    assert.equal(raw.length, (size * 4 + 1) * size);
    const px = (x: number, y: number) => { const o = y * (size * 4 + 1) + 1 + x * 4; return hex(raw[o]!, raw[o + 1]!, raw[o + 2]!); };
    assert.equal(px(0, 0), YAQING.toUpperCase());
    assert.equal(px(size - 1, size - 1), YAQING.toUpperCase());
    assert.equal(px(Math.floor(size / 2), Math.floor(size / 2)), XUANBAI.toUpperCase());
  }
  assert.equal(iconFor('../secret.png'), null);
  assert.throws(() => iconPng(8));
  const ok = await iconRoute(new Request(`${ORIGIN}/icons/icon-192.png`), { params: Promise.resolve({ file: 'icon-192.png' }) });
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get('content-type'), 'image/png');
  const no = await iconRoute(new Request(`${ORIGIN}/icons/x.png`), { params: Promise.resolve({ file: 'x.png' }) });
  assert.equal(no.status, 404);
});

// ---------- 微信与文案 ----------
const UA = {
  wechatIos: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47(0x18002f2c) NetType/WIFI Language/zh_CN',
  wechatAndroid: 'Mozilla/5.0 (Linux; Android 14; SYN-AL00) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 XWEB/1160065 MMWEBSDK/20231202 MicroMessenger/8.0.47.2560(0x28002F30) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64',
  wecom: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 wxwork/4.1.20 MicroMessenger/7.0.1 Language/zh',
  safari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  chromeAndroid: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
  desktop: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
};

test('UA 识别：微信（含企业微信）另提示在浏览器打开；iOS / 安卓 / 其他各取一段说明', () => {
  assert.equal(isWeChat(UA.wechatIos), true);
  assert.equal(isWeChat(UA.wechatAndroid), true);
  assert.equal(isWeChat(UA.wecom), true);
  for (const u of [UA.safari, UA.chromeAndroid, UA.desktop, '', null, undefined]) assert.equal(isWeChat(u), false, String(u));
  assert.equal(installOs(UA.wechatIos), 'ios');
  assert.equal(installOs(UA.safari), 'ios');
  assert.equal(installOs(UA.wechatAndroid), 'android');
  assert.equal(installOs(UA.chromeAndroid), 'android');
  assert.equal(installOs(UA.desktop), 'other');
  assert.equal(installOs(null), 'other');
});

test('PWA 文案：引导与离线页都是 {{TODO_COPY:pwa.*}} 占位；不含禁语 A–E 类；不对学生作陈述', () => {
  for (const s of [...Object.values(INSTALL), ...Object.values(OFFLINE)]) assert.match(s, /^\{\{TODO_COPY:pwa\.[a-z_]+\}\} \S/, s);
  const rules = loadForbidden();
  for (const s of ALL_PWA_COPY) for (const r of rules) assert.ok(!r.rx.test(s), `${r.id}: ${s}`);
  for (const s of ALL_PWA_COPY) assert.ok(!/孩子|学生|你(的)?(成绩|水平|掌握)/.test(s), s);
});

test('离线页：静态文件，文字与 copy.ts 逐字一致；没有脚本、内联样式、外部资源；样式只引同源 offline.css', () => {
  const html = readFileSync(join(WEB, 'public', 'offline.html'), 'utf8');
  const css = readFileSync(join(WEB, 'public', 'offline.css'), 'utf8');
  assert.equal(OFFLINE_PATH, '/offline.html');
  for (const s of Object.values(OFFLINE)) assert.ok(html.includes(s), s);
  assert.ok(html.includes(`<title>${OFFLINE.title}</title>`));
  assert.ok(!/<script\b/i.test(html), '没有脚本');
  assert.ok(!/<style\b|\sstyle=/i.test(html), '没有内联样式（CSP）');
  assert.ok(!/(src|href)="(https?:)?\/\//i.test(html), '没有外部资源');
  assert.deepEqual([...html.matchAll(/<link\b[^>]*href="([^"]+)"/g)].map((m) => m[1]), ['/offline.css']);
  assert.ok((STATIC_SHELL as readonly string[]).includes('/offline.css'));
  assert.ok(!/url\(|@import/.test(css), '样式不引资源');
  assert.ok(!/#d9a441/i.test(html + css), '不用星光金');
});

test('登出清缓存：家长退出登录、退出学生模式、审核台退出的表单都会请 Service Worker 清缓存', () => {
  const family = readFileSync(join(WEB, 'src/app/family/page.tsx'), 'utf8');
  assert.match(family, /testId="logout" clearCachesOnSubmit/);
  const shell = readFileSync(join(WEB, 'src/shell/StudentShell.tsx'), 'utf8');
  assert.match(shell, /testId="exit-student-mode"[^>]*clearCachesOnSubmit/);
  const review = readFileSync(join(WEB, 'src/app/review/layout.tsx'), 'utf8');
  assert.match(review, /<ClearingForm action=\{logoutAction\}/);
  // 结束会话的其余前端路径：内测退出表单、数据删除确认成功（不改 BFF）
  assert.match(readFileSync(join(WEB, 'src/app/family/[studentId]/exit/page.tsx'), 'utf8'), /testId="internal-test-exit-form" clearCachesOnSubmit/);
  assert.match(readFileSync(join(WEB, 'src/trust/DataPanels.tsx'), 'utf8'), /if \(r\.code === 'confirmed'\) void clearPwaCaches\(\);/);
  const form = readFileSync(join(WEB, 'src/auth/ResultForm.tsx'), 'utf8');
  assert.match(form, /onSubmit=\{clearCachesOnSubmit \? \(\) => \{ void clearPwaCaches\(\); \} : undefined\}/);
});

test('部署：public/ 下只有离线页与样式两个文件；镜像只拷这两个，dockerignore 只放行这两个', () => {
  assert.deepEqual(readdirSync(join(WEB, 'public')).sort(), ['offline.css', 'offline.html']);
  const docker = readFileSync(join(ROOT, 'deploy/docker/web.Dockerfile'), 'utf8');
  assert.match(docker, /^COPY apps\/web\/public\/offline\.html apps\/web\/public\/offline\.css \.\/public\/$/m);
  assert.ok(!/^COPY apps\/web\/public(\/)?\s/m.test(docker), '不整目录拷 public/');
  const ign = readFileSync(join(ROOT, 'deploy/docker/web.Dockerfile.dockerignore'), 'utf8');
  assert.deepEqual(ign.split('\n').filter((l) => l.startsWith('!apps/web/public')), ['!apps/web/public/offline.html', '!apps/web/public/offline.css']);
});

test('PWA 文案过禁语扫描（personas.guard，platform 口径）与禁用推断词表（sie_lexicon），与壳文案同口径', () => {
  const g = spawnSync('python3', ['-m', 'personas.guard', 'scan', '--persona', 'platform', '--stdin-json'], {
    cwd: ROOT, input: JSON.stringify(ALL_PWA_COPY), encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'packages') },
  });
  assert.equal(g.status, 0, g.stdout + g.stderr);
  assert.deepEqual((JSON.parse(g.stdout) as { results: { hits: unknown[] }[] }).results.filter((x) => x.hits.length > 0), []);
  const script = `import json, sys\nsys.path.insert(0, 'docs/design/sie')\nimport sie_lexicon\nlex = sie_lexicon.load()\n`
    + `xs = json.load(sys.stdin)\nprint(json.dumps([[x, [h.category for h in lex.find(x)]] for x in xs], ensure_ascii=False))`;
  const r = spawnSync('python3', ['-c', script], { cwd: ROOT, input: JSON.stringify([...ALL_PWA_COPY, '（合成）这孩子天赋一般']), encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout) as [string, string[]][];
  assert.ok(out.at(-1)![1].length > 0, '探针应命中');
  assert.deepEqual(out.slice(0, -1).filter(([, hits]) => hits.length > 0), []);
});
