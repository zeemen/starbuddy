'use client';
// 根布局里注册 Service Worker（LT-07）：只在生产构建（含端到端测试构建）里注册；next dev 不注册（开发构建的静态资源没有哈希）。
// enabled 由根布局按停用开关给（src/pwa/features.ts）：停用时不注册，只请已有的注册取一次更新（换成自毁版）。不渲染任何元素。
// 浏览器接口只在 src/pwa/client.ts 里调用（tests/static.test.ts）。
import { useEffect } from 'react';
import { registerSw, updateExistingSw } from './client.ts';

export function PwaRegister({ enabled }: { enabled: boolean }) {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') return;
    if (enabled) registerSw();
    else updateExistingSw();
  }, [enabled]);
  return null;
}
