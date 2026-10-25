// Service Worker 源码（LT-07 PWA，specs/launch-lt07-mobile-pwa 决定记录第 1 条）。手写，不引入 workbox 等依赖。
// 缓存范围（决定记录）：只缓存静态壳（离线页、离线页样式、manifest、占位图标）与构建产物（/_next/static 下带哈希、
// 响应头 immutable 的文件）；任何 API 响应、学生数据、页面 HTML 都不缓存——
//   - 页面导航一律走网络（network-only），网络失败时回固定离线页 /offline.html（静态文件，不含任何个人数据）；
//   - 非 GET、跨源、带查询串（含 RSC 的 ?_rsc=）、带 RSC 请求头、/api、/bff 以及其余一切路径：不调用 respondWith，交给浏览器（network-only）；
//   - 登出时页面发 {type: 'sb:clear-caches'}：删掉全部缓存，再只重新预缓存静态壳（不含个人数据）；构建产物下次访问时再缓存。
// 版本化：VERSION 取本次构建的 BUILD_ID（src/pwa/build-version.ts），/sw.js 内容随构建变化，浏览器据此更新；新版本安装后
// skipWaiting + clients.claim，激活时删除旧版本的缓存。
// 停用（安全审查 1）：开关 STARBUDDY_PWA_KILL_SWITCH=1（src/pwa/features.ts）时 /sw.js 改发下面的 KILL_SOURCE——浏览器更新检查取到它后
// 安装、激活，删掉本站全部缓存、注销注册、让已打开的页面重新导航（之后页面不受任何 Service Worker 控制）。
// 不做推送通知：没有 push、notificationclick、sync 监听。
// 源码是普通 JS 字符串（Service Worker 不经打包），tests/pwa.test.ts 在 node:vm 里按真实事件跑一遍。只在服务端（/sw.js 路由）用。
import { CACHE_PREFIX, CLEAR_MESSAGE, CLEARED_MESSAGE, OFFLINE_PATH, STATIC_SHELL } from './constants.ts';

export { CACHE_PREFIX, CLEAR_MESSAGE, CLEARED_MESSAGE, OFFLINE_PATH, STATIC_SHELL };

const TEMPLATE = String.raw`'use strict';
// 魁星点斗 Service Worker（LT-07）：只缓存静态壳与构建产物；页面、API、学生数据一律走网络，不进缓存。
var VERSION = '__SB_VERSION__';
var PREFIX = '__SB_PREFIX__';
var SHELL_CACHE = PREFIX + 'shell-' + VERSION;
var STATIC_CACHE = PREFIX + 'static-' + VERSION;
var SHELL = __SB_SHELL__;
var OFFLINE = '__SB_OFFLINE__';
var CLEAR = '__SB_CLEAR__';
var CLEARED = '__SB_CLEARED__';
var STATIC_PATH = /^\/_next\/static\/[A-Za-z0-9._~\/-]+\.(?:js|css|woff2?|svg|png)$/;

function abs(path) { return new URL(path, self.location.origin).href; }

function precache() {
  return caches.open(SHELL_CACHE).then(function (cache) {
    return Promise.all(SHELL.map(function (path) {
      return fetch(new Request(abs(path), { cache: 'no-store', credentials: 'omit' })).then(function (res) {
        if (!res.ok) throw new Error('precache ' + path + ' ' + res.status);
        return cache.put(abs(path), res);
      });
    }));
  });
}

function dropCaches(keep) {
  return caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return keep.indexOf(k) === -1; }).map(function (k) { return caches.delete(k); }));
  });
}

/** 这个请求怎么处理：navigate（走网络，失败回离线页）、shell（静态壳）、static（构建产物）、network（不接管） */
function policy(req) {
  if (req.method !== 'GET') return 'network';
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return 'network';
  if (req.mode === 'navigate') return 'navigate';
  if (url.search !== '' || url.hash !== '') return 'network';
  if (req.headers.get('RSC') !== null || req.headers.get('Next-Router-State-Tree') !== null || req.headers.get('Next-Action') !== null) return 'network';
  if (SHELL.indexOf(url.pathname) !== -1) return 'shell';
  if (STATIC_PATH.test(url.pathname)) return 'static';
  return 'network';
}

function offline() {
  return caches.open(SHELL_CACHE).then(function (cache) { return cache.match(abs(OFFLINE)); }).then(function (hit) {
    return hit || new Response('offline', { status: 503, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' } });
  });
}

function fromShell(req) {
  return caches.open(SHELL_CACHE).then(function (cache) { return cache.match(abs(new URL(req.url).pathname)); }).then(function (hit) {
    return hit || fetch(req);
  });
}

function cacheable(res) {
  return res.ok && res.type === 'basic' && !res.redirected && /\bimmutable\b/.test(res.headers.get('cache-control') || '') && !/\bno-store\b/.test(res.headers.get('cache-control') || '');
}

function fromStatic(req) {
  return caches.open(STATIC_CACHE).then(function (cache) {
    return cache.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (res) {
        if (cacheable(res)) return cache.put(req, res.clone()).then(function () { return res; }, function () { return res; });
        return res;
      });
    });
  });
}

self.addEventListener('install', function (event) {
  event.waitUntil(precache().then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(dropCaches([SHELL_CACHE, STATIC_CACHE]).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
  var req = event.request;
  var how = policy(req);
  if (how === 'navigate') event.respondWith(fetch(req).catch(offline));
  else if (how === 'shell') event.respondWith(fromShell(req));
  else if (how === 'static') event.respondWith(fromStatic(req));
  // network：不接管，浏览器照常走网络，响应不进任何缓存
});

self.addEventListener('message', function (event) {
  var data = event.data;
  if (!data || data.type !== CLEAR) return;
  var reply = function (ok) { if (event.source && event.source.postMessage) event.source.postMessage({ type: CLEARED, ok: ok }); };
  event.waitUntil(dropCaches([]).then(precache).then(function () { reply(true); }, function () { reply(false); }));
});
`;

/** 自毁版 Service Worker（停用开关打开时 /sw.js 的内容）：没有 fetch 监听（一切走网络），激活即清缓存、注销、重新导航已打开的页面 */
export const KILL_SOURCE = String.raw`'use strict';
// 魁星点斗 Service Worker 停用版（LT-07）：删掉本站全部缓存，注销自己，让已打开的页面重新导航；不接管任何请求。
self.addEventListener('install', function (event) {
  event.waitUntil(self.skipWaiting());
});
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) { return Promise.all(keys.map(function (k) { return caches.delete(k); })); })
      .then(function () { return self.clients.claim().catch(function () { return undefined; }); })
      .then(function () { return self.registration.unregister(); })
      .then(function () { return self.clients.matchAll({ type: 'window' }); })
      .then(function (list) {
        return Promise.all(list.map(function (c) { return c.navigate(c.url).catch(function () { return undefined; }); }));
      })
  );
});
`;

/** 本次构建的 Service Worker 源码；version 只收 BUILD_ID 的字符集 */
export function renderSwSource(version: string): string {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(version)) throw new Error('service worker version must match [A-Za-z0-9_-]{1,64}');
  return TEMPLATE
    .replace('__SB_VERSION__', version)
    .replace('__SB_PREFIX__', CACHE_PREFIX)
    .replace('__SB_SHELL__', JSON.stringify(STATIC_SHELL))
    .replace('__SB_OFFLINE__', OFFLINE_PATH)
    .replace('__SB_CLEAR__', CLEAR_MESSAGE)
    .replace('__SB_CLEARED__', CLEARED_MESSAGE);
}
