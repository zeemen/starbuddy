// 上线测试开关矩阵（LT-01 决定记录第 1、5 条，deploy/flags/staging.yaml）的前端口径：与 BFF 的 launchFeatureEnv 一致。
// dev / test 照旧；staging 只能打开上线测试范围内的功能（各开关文件自己列出 staging 可开的子集）；pilot、生产与没设一律关闭。
// 只在服务端读，不进浏览器。
type Env = Record<string, string | undefined>;

export type LaunchTier = 'local' | 'staging' | null;

export function launchTier(env: Env = process.env): LaunchTier {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run === 'dev' || run === 'test') return 'local';
  if (run === 'staging') return 'staging';
  return null;
}
