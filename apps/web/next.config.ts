// Next.js 配置（spec 范围第 1 条）：
//   - 关闭图片优化：不需要 sharp / libvips（LGPL，ADR 07 待人确认 #2）；package.json 用占位包顶掉 sharp；
//   - 页面一律不缓存（学习者模型是 L2 数据，ADR 09；统一壳 spec 第 15、16 节）；
//   - 不加载境外字体或 CDN（字体用系统字体栈）。Service Worker（LT-07）只缓存静态壳与构建产物，页面、API、学生数据不缓存，
//     见 src/pwa/ 与 src/app/sw.js/route.ts；/sw.js、manifest、图标与 public/ 的离线页同样走下面的 no-store 与安全头。
//   - 开发专用页面（文件名 page.dev.tsx：探针演示页 /dev/probe、数学诊断演示页 /dev/diagnosis、符号样张页 /dev/star-glyphs）
//     只在开发、测试构建里是页面：构建时 STARBUDDY_DEV_PAGES=1 且 STARBUDDY_ENV 为 dev / test（构建期常量，4-H H2；
//     开发身份桩已删除，演示页走真实会话）才把 .dev.tsx 算作页面扩展名；生产构建里这些路由不存在（tests/build-output.test.ts 核对）。
//   - 安全头：CSP（每个请求一个 nonce）与 HSTS 在 src/proxy.ts；这里是固定的几个，对静态资源也生效。
//   - 端到端测试构建（scripts/e2e.sh）输出到单独的目录 .next-e2e（STARBUDDY_NEXT_DIST_DIR=.next-e2e，只认这一个取值），
//     正式的 .next 永远是生产构建；测试构建用 tsconfig.e2e.json 做类型检查，Next 生成的路由类型写在 .next-e2e/types 下，
//     不改 tsconfig.json、也不和 .next/types 的路由类型重复。
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import type { NextConfig } from 'next';

type Env = Record<string, string | undefined>;

/** 构建时是否包含开发专用页面（构建期常量：开发与测试构建才有，staging、pilot、生产构建里没有） */
export const devPagesEnabled = (env: Env = process.env): boolean =>
  env['STARBUDDY_DEV_PAGES'] === '1' && ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

/** 页面扩展名：生产构建不含 dev.tsx，所以 page.dev.tsx 不是页面 */
export const pageExtensionsFor = (env: Env = process.env): string[] =>
  devPagesEnabled(env) ? ['dev.tsx', 'tsx', 'ts', 'jsx', 'js'] : ['tsx', 'ts', 'jsx', 'js'];

/** 端到端测试构建的输出目录（scripts/e2e.sh 在构建与 next start 时设置） */
export const E2E_DIST_DIR = '.next-e2e';

/** 输出目录：只有 STARBUDDY_NEXT_DIST_DIR 恰好是 .next-e2e 时用测试目录，其余一律是默认的 .next */
export const distDirFor = (env: Env = process.env): string =>
  env['STARBUDDY_NEXT_DIST_DIR'] === E2E_DIST_DIR ? E2E_DIST_DIR : '.next';

/**
 * 舞台样张页（/dev/stage，specs/stage-whiteboard-step1）以相对路径引用仓库里的 packages/stage（不改 npm 工作区）。Turbopack 默认只解析
 * 项目根（apps/web）以内的文件，所以只在包含开发专用页面的构建里把 Turbopack 根目录放到仓库根；生产构建不引用 packages/stage，
 * 根目录保持默认。Next 以 CommonJS 方式加载本文件时有 __dirname；单测以 ESM 加载时退回当前目录（单测都在 apps/web 下运行）。
 */
export function repoRoot(webDir: string = typeof __dirname === 'string' ? __dirname : process.cwd()): string {
  const root = resolve(webDir, '..', '..');
  // 失败即停（fail closed）：算出的目录里必须真有 packages/stage，否则不放宽 Turbopack 的解析范围
  if (!existsSync(resolve(root, 'packages', 'stage', 'package.json'))) {
    throw new Error(`next.config.ts：${root} 下没有 packages/stage/package.json，拒绝把 Turbopack 根目录放到这里`);
  }
  return root;
}
export const turbopackRootFor = (env: Env = process.env): string | undefined => (devPagesEnabled(env) ? repoRoot() : undefined);

const distDir = distDirFor();
const turbopackRoot = turbopackRootFor();

const config: NextConfig = {
  distDir,
  ...(turbopackRoot ? { turbopack: { root: turbopackRoot } } : {}),
  // 测试构建换一份 tsconfig：它只包含 .next-e2e 的路由类型；Next 在构建时补写 include 也只会写到这份文件里
  ...(distDir === E2E_DIST_DIR ? { typescript: { tsconfigPath: 'tsconfig.e2e.json' } } : {}),
  pageExtensions: pageExtensionsFor(),
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        // 页面一律不缓存（统一壳、星图、建档、审核台、演示页都带会话或个人数据）；带哈希的静态资源除外
        source: '/((?!_next/static|_next/image).*)',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
      {
        // 全站固定的安全响应头；CSP（带 nonce）与 HSTS 在 src/proxy.ts
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), interest-cohort=()' },
        ],
      },
    ];
  },
};

export default config;
