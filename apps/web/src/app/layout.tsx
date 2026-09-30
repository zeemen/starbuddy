// 统一壳的根布局（原型）：中文页面、系统字体栈（不加载境外字体，ADR 09）、主题随系统。
// 不注册 Service Worker、不写 localStorage / IndexedDB（spec 第 4 节）。
// CSP（spec 第 16 节，H2）：src/proxy.ts 为每个请求生成 nonce，Next 给自己的脚本加上；主题变量改成静态文件 theme.css
// （不再输出内联 <style>）。每个页面都要按请求渲染才拿得到 nonce，所以这里读一次请求头，让所有页面都是动态渲染。
import type { Metadata } from 'next';
import { headers } from 'next/headers';
import type { ReactNode } from 'react';
import './theme.css';
import './globals.css';

export const metadata: Metadata = {
  title: '魁星点斗 · 星图原型',
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  await headers();  // 动态渲染：静态预渲染的页面没有 nonce，CSP 下脚本会被拦
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
