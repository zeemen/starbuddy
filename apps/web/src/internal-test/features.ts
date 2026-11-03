// 内部成员测试运营的前端开关（specs/launch-lt06-internal-test-ops 范围第 3、4 条；做法同 src/retest/features.ts）。
// 默认关闭：关闭时学生壳、家长孩子页、审核台导航都不渲染入口、不发任何请求，反馈页、退出页、审核台的反馈与退出状态页一律 404
// （页面在读会话之前判断）——与开关打开前零差异。
// 后端是同名开关：BFF 的 STARBUDDY_INTERNAL_TEST（services/bff/src/internal-test/features.ts，关时三条路由不注册、不做退出判断）与
// 审核台后端的 REVIEW_CONSOLE_INTERNAL_TEST（services/learning/review_console/config.py，关时三个接口不注册）。各自由部署环境设置。
// 打开方式：STARBUDDY_INTERNAL_TEST=internal_test；dev / test 与 staging 可开（staging 依赖迁移 0059，执行之前不开；开关矩阵标 pending），
// pilot、生产一律关闭。只在服务端读，不进浏览器。
import { launchTier } from '../lib/launch-env.ts';

type Env = Record<string, string | undefined>;

export const INTERNAL_TEST_FEATURE = 'internal_test';

export function internalTestEnabled(env: Env = process.env): boolean {
  if (launchTier(env) === null) return false;
  return (env['STARBUDDY_INTERNAL_TEST'] ?? '').trim() === INTERNAL_TEST_FEATURE;
}
