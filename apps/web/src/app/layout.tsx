// 统一壳的根布局（原型）：中文页面、系统字体栈（不加载境外字体，ADR 09）、主题随系统。
// 不注册 Service Worker、不写 localStorage / IndexedDB（spec 第 4 节）。
import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { THEME_CSS } from '../lib/theme.ts';
import './globals.css';

export const metadata: Metadata = {
  title: '魁星点斗 · 星图原型',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <style>{THEME_CSS}</style>
      </head>
      <body>{children}</body>
    </html>
  );
}
