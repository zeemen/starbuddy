// 统一壳的根布局（原型）：中文页面、系统字体栈（不加载境外字体，ADR 09）、主题随系统。
// 不写 localStorage / IndexedDB（spec 第 4 节）。Service Worker（LT-07）：src/pwa/PwaRegister.tsx 注册 /sw.js，只缓存静态壳与构建产物，
// 页面 HTML、API 与学生数据一律不缓存（specs/launch-lt07-mobile-pwa 决定记录第 1 条）；停用开关 STARBUDDY_PWA_KILL_SWITCH=1 时不注册
// （src/pwa/features.ts，/sw.js 改发自毁版）。
// 移动端（LT-07）：viewport-fit=cover（刘海与底部横条用 CSS 的 safe-area 留白）、软键盘弹出时缩小布局视口（interactive-widget），
// 不禁止缩放；Manifest 与占位图标、主题色（占位）。
// CSP（spec 第 16 节，H2）：src/proxy.ts 为每个请求生成 nonce，Next 给自己的脚本加上；主题变量改成静态文件 theme.css
// （不再输出内联 <style>）。每个页面都要按请求渲染才拿得到 nonce，所以这里读一次请求头，让所有页面都是动态渲染。
import type { Metadata, Viewport } from 'next';
import { headers } from 'next/headers';
import type { ReactNode } from 'react';
import { XUANBAI, YAQING } from '../lib/theme.ts';
import { APP_SHORT_NAME } from '../pwa/copy.ts';
import { pwaKillSwitch } from '../pwa/features.ts';
import { PwaRegister } from '../pwa/PwaRegister.tsx';
import './theme.css';
import './globals.css';

export const metadata: Metadata = {
  title: '魁星点斗 · 星图原型',
  robots: { index: false, follow: false },
  manifest: '/manifest.webmanifest',
  icons: { icon: [{ url: '/icons/icon-192.png', type: 'image/png', sizes: '192x192' }], apple: [{ url: '/icons/apple-touch-icon.png', sizes: '180x180' }] },
  appleWebApp: { capable: true, title: APP_SHORT_NAME, statusBarStyle: 'default' },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  interactiveWidget: 'resizes-content',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: XUANBAI },
    { media: '(prefers-color-scheme: dark)', color: YAQING },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  await headers();  // 动态渲染：静态预渲染的页面没有 nonce，CSP 下脚本会被拦
  return (
    <html lang="zh-CN">
      <body>
        {children}
        <PwaRegister enabled={!pwaKillSwitch()} />
      </body>
    </html>
  );
}
