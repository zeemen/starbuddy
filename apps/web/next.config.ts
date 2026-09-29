// Next.js 配置（spec 范围第 1 条）：
//   - 关闭图片优化：不需要 sharp / libvips（LGPL，ADR 07 待人确认 #2）；package.json 用占位包顶掉 sharp；
//   - 星图页不缓存（学习者模型是 L2 数据，ADR 09）；
//   - 不注册 Service Worker、不加载境外字体或 CDN（字体用系统字体栈）。
//   - 开发专用页面（文件名 page.dev.tsx，例如探针演示页 /dev/probe）只在开发、测试构建里是页面：构建时
//     STARBUDDY_DEV_AUTH=1 且 STARBUDDY_ENV 为 dev / test（与 src/lib/dev-auth.ts 的开关相同）才把 .dev.tsx 算作页面扩展名；
//     生产构建里这些路由不存在（tests/build-output.test.ts 核对）。
//   - 端到端测试构建（scripts/e2e.sh）输出到单独的目录 .next-e2e（STARBUDDY_NEXT_DIST_DIR=.next-e2e，只认这一个取值），
//     正式的 .next 永远是生产构建；测试构建用 tsconfig.e2e.json 做类型检查，Next 生成的路由类型写在 .next-e2e/types 下，
//     不改 tsconfig.json、也不和 .next/types 的路由类型重复。
import type { NextConfig } from 'next';

type Env = Record<string, string | undefined>;

/** 构建时是否包含开发专用页面（与 devAuthEnabled 同一对开关） */
export const devPagesEnabled = (env: Env = process.env): boolean =>
  env['STARBUDDY_DEV_AUTH'] === '1' && ['dev', 'test'].includes(env['STARBUDDY_ENV'] ?? '');

/** 页面扩展名：生产构建不含 dev.tsx，所以 page.dev.tsx 不是页面 */
export const pageExtensionsFor = (env: Env = process.env): string[] =>
  devPagesEnabled(env) ? ['dev.tsx', 'tsx', 'ts', 'jsx', 'js'] : ['tsx', 'ts', 'jsx', 'js'];

/** 端到端测试构建的输出目录（scripts/e2e.sh 在构建与 next start 时设置） */
export const E2E_DIST_DIR = '.next-e2e';

/** 输出目录：只有 STARBUDDY_NEXT_DIST_DIR 恰好是 .next-e2e 时用测试目录，其余一律是默认的 .next */
export const distDirFor = (env: Env = process.env): string =>
  env['STARBUDDY_NEXT_DIST_DIR'] === E2E_DIST_DIR ? E2E_DIST_DIR : '.next';

const distDir = distDirFor();

const config: NextConfig = {
  distDir,
  // 测试构建换一份 tsconfig：它只包含 .next-e2e 的路由类型；Next 在构建时补写 include 也只会写到这份文件里
  ...(distDir === E2E_DIST_DIR ? { typescript: { tsconfigPath: 'tsconfig.e2e.json' } } : {}),
  pageExtensions: pageExtensionsFor(),
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: '/star-map/:path*',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
      {
        // 审核台：草稿内容与签字状态随时在变，不缓存
        source: '/review/:path*',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
      { source: '/review', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      {
        // 开发专用页面（探针演示等）：不缓存
        source: '/dev/:path*',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
      {
        // 建档：个人填写内容，不缓存（SIE-01）
        source: '/onboarding/:path*',
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      },
      {
        // 全站安全响应头。CSP 需要给 Next 的内联脚本配 nonce，留给 4-H（lessons 待人确认）
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
