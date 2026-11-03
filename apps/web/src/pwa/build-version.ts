// Service Worker 的版本号（LT-07）：本次构建的 BUILD_ID（next build 写在输出目录里）。只在服务端（/sw.js 路由）读。
// 输出目录与 next.config.ts 的 distDirFor 同一口径：STARBUDDY_NEXT_DIST_DIR 恰好是 .next-e2e 时用端到端测试构建，其余一律 .next
// （tests/pwa.test.ts 核对两者一致）。读不到或格式不对返回 null，/sw.js 回 404（浏览器不安装，已安装的在下次更新检查时注销）。
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

type Env = Record<string, string | undefined>;

export const distDirOf = (env: Env = process.env): string =>
  env['STARBUDDY_NEXT_DIST_DIR'] === '.next-e2e' ? '.next-e2e' : '.next';

export function buildVersion(env: Env = process.env, cwd: string = process.cwd()): string | null {
  try {
    const id = readFileSync(join(cwd, distDirOf(env), 'BUILD_ID'), 'utf8').trim();
    return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}
