// 复测与验证题的功能开关（specs/retest-delivery-kge05/spec.md 范围第 4 条“开关 STARBUDDY_RETEST_UI，默认关”；做法同 src/practice/features.ts）。
// 打开时注册三条学生端路由（src/http/retest-routes.ts）；关闭时不注册，与表外路径同一个 404。
// 打开方式：STARBUDDY_RETEST_UI=retest，只在 dev / test 生效；其余环境设了就拒绝启动（main.ts）。
// 前端是同名开关（apps/web/src/retest/features.ts）；前后端的开关由部署环境各自设置。
// 封测开不开由人决定：前提与错题再练相同（教研数值签字、present_item 题面契约与准入决定落地），另加复测计量口径的确认（spec 范围第 2 条）。
// LT-01 决定记录第 1、7 条：staging 可开（学习服务在教研签字前回 501，开关开、待签字内容）；pilot、生产仍拒绝。
import { launchFeatureEnv, type RunEnv } from '../http/config.ts';

export const RETEST_FEATURE = 'retest';

/** 解析开关；dev / test / staging 以外设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseRetestFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!launchFeatureEnv(env)) throw new Error('STARBUDDY_RETEST_UI 只在 dev / test / staging 可用（教研签字与题面契约落地之前 pilot 以上一律关闭）');
  if (raw.trim() !== RETEST_FEATURE) throw new Error(`STARBUDDY_RETEST_UI 只认 ${RETEST_FEATURE}`);
  return true;
}
