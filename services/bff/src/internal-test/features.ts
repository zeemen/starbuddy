// 内部成员测试运营的功能开关（specs/launch-lt06-internal-test-ops 范围第 3、4 条；做法同 src/practice/features.ts）。
// 打开时注册问题反馈、退出测试与退出状态三条路由（src/http/internal-test-routes.ts），并启用“已退出档案”的统一判据
// （server.ts：已退出的档案拒绝一切新写入与触发模型处理的请求，数据权利与退出状态除外）；关闭时三条路由不注册（与表外路径同一个 404）、
// 不做退出判断——与开关上线前逐字节一致。
// 依赖迁移 0059（草稿，待人在副本演练后执行）：执行之前不能打开（开关矩阵标 pending: migration_0059）。
// 打开方式：STARBUDDY_INTERNAL_TEST=internal_test，dev / test 与 staging 可开；pilot、生产设了就拒绝启动（调用方负责）。
import { launchFeatureEnv, type RunEnv } from '../http/config.ts';

export const INTERNAL_TEST_FEATURE = 'internal_test';

/** 解析开关；pilot、生产设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseInternalTestFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!launchFeatureEnv(env)) throw new Error('STARBUDDY_INTERNAL_TEST 只在 dev / test / staging 可用（内部成员测试；pilot、生产一律关闭）');
  if (raw.trim() !== INTERNAL_TEST_FEATURE) throw new Error(`STARBUDDY_INTERNAL_TEST 只认 ${INTERNAL_TEST_FEATURE}`);
  return true;
}
