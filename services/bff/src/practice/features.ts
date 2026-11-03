// 错题再练的功能开关（specs/practice-loop-stage1/spec.md 第 5 节“功能开关 practice，默认关”；做法同 src/star-map/features.ts）。
// 打开时注册三条学生端路由（src/http/practice-routes.ts）；关闭时不注册，与表外路径同一个 404。
// 打开方式：STARBUDDY_PRACTICE=practice，只在 dev / test 生效；其余环境设了就拒绝启动（main.ts）。
// 封测环境开不开由人决定：前提是教研数值签字、present_item 题面契约与准入决定落地（spec 待人确认 #12、#13），由人改这里的规则。
// LT-01 决定记录第 1、7 条：staging 可开（学习服务在教研签字前回 501，开关开、待签字内容）；pilot、生产仍拒绝。
import { launchFeatureEnv, type RunEnv } from '../http/config.ts';

export const PRACTICE_FEATURE = 'practice';

/** 解析开关；dev / test / staging 以外设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parsePracticeFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!launchFeatureEnv(env)) throw new Error('STARBUDDY_PRACTICE 只在 dev / test / staging 可用（教研签字与题面契约落地之前 pilot 以上一律关闭）');
  if (raw.trim() !== PRACTICE_FEATURE) throw new Error(`STARBUDDY_PRACTICE 只认 ${PRACTICE_FEATURE}`);
  return true;
}
