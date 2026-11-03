// PWA 的公共常量（LT-07）：Service Worker 源码（sw-source.ts，服务端）与浏览器端（client.ts）共用；单独成文件，浏览器包里不带 SW 源码。

/** 预缓存的静态壳：只有这些路径与 /_next/static 构建产物会进缓存（决定记录第 1 条） */
export const STATIC_SHELL = [
  '/offline.html',
  '/offline.css',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/maskable-512.png',
  '/icons/apple-touch-icon.png',
] as const;

/** 离线时导航回的固定页面 */
export const OFFLINE_PATH = '/offline.html';
/** 缓存名前缀：sb-shell-<版本>、sb-static-<版本> */
export const CACHE_PREFIX = 'sb-';
/** 登出清缓存的消息类型（页面 → Service Worker）与回执 */
export const CLEAR_MESSAGE = 'sb:clear-caches';
export const CLEARED_MESSAGE = 'sb:caches-cleared';
