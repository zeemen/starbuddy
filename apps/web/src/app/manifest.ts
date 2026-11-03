// Web App Manifest（LT-07）：/manifest.webmanifest。名称、占位图标（src/pwa/icon.ts 生成）、standalone、主题色（占位，取页面主题的
// 鸦青与宣白，src/lib/theme.ts；设计提供前不改，决定记录第 3 条）。start_url 是登录页：已登录时它按会话转到家长首页或学生的“今日”。
// 不声明 share_target、protocol_handlers、推送相关字段（不做推送）。
import type { MetadataRoute } from 'next';
import { XUANBAI } from '../lib/theme.ts';
import { APP_NAME, APP_SHORT_NAME } from '../pwa/copy.ts';

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: '/',
    name: APP_NAME,
    short_name: APP_SHORT_NAME,
    lang: 'zh-CN',
    dir: 'ltr',
    start_url: '/login',
    scope: '/',
    display: 'standalone',
    background_color: XUANBAI,
    theme_color: XUANBAI,  // 与浅色主题下的 <meta name="theme-color"> 一致（src/app/layout.tsx）；深色由 meta 的媒体查询给 YAQING
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
