// 浏览器端的 Service Worker 调用（LT-07）：注册与登出清缓存。整个前端只有这个文件（与 PwaRegister.tsx）碰 Service Worker 接口
// （tests/static.test.ts 核对）。不用 localStorage、IndexedDB；不请求通知权限、不订阅推送。
import { CACHE_PREFIX, CLEAR_MESSAGE, CLEARED_MESSAGE } from './constants.ts';

/** 注册 /sw.js（作用域整站；脚本不走 HTTP 缓存）。不支持、非安全上下文或注册失败都静默跳过：页面不依赖 Service Worker */
export function registerSw(): void {
  if (typeof window === 'undefined' || !window.isSecureContext || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).catch(() => undefined);
}

/**
 * 停用开关打开时（src/pwa/features.ts）：不注册；已有注册的请它立刻取一次更新——取到的是自毁版，它清缓存、注销并重新导航本页。
 */
export function updateExistingSw(): void {
  if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.getRegistrations()
    .then((list) => Promise.all(list.map((r) => r.update().catch(() => undefined))))
    .catch(() => undefined);
}

/**
 * 登出时清缓存：有正在控制本页的 Service Worker 就请它删掉全部缓存（它随后只重新预缓存不含个人数据的静态壳）；
 * 没有就由页面直接删掉本站以 sb- 开头的缓存。不等结果、不阻塞登出（返回的 Promise 供测试等待）。
 */
export function clearPwaCaches(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  const sw = 'serviceWorker' in navigator ? navigator.serviceWorker.controller : null;
  if (sw) {
    return new Promise<void>((resolve) => {
      const done = (e: MessageEvent) => {
        if ((e.data as { type?: string } | null)?.type !== CLEARED_MESSAGE) return;
        navigator.serviceWorker.removeEventListener('message', done);
        resolve();
      };
      navigator.serviceWorker.addEventListener('message', done);
      sw.postMessage({ type: CLEAR_MESSAGE });
      setTimeout(() => { navigator.serviceWorker.removeEventListener('message', done); resolve(); }, 5000);
    });
  }
  if (!('caches' in window)) return Promise.resolve();
  return caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith(CACHE_PREFIX)).map((k) => caches.delete(k))))
    .then(() => undefined, () => undefined);
}
