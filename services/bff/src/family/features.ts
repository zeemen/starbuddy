// 家长诉求的功能开关（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14；做法同 src/practice/features.ts）。
// 打开时注册两条家长端路由（src/http/family-goals-routes.ts）；关闭时不注册，与表外路径同一个 404。
// 打开方式：STARBUDDY_FAMILY_GOALS=family_goals，只在 dev / test 生效；其余环境设了就拒绝启动（调用方负责）。
// 封测环境开不开由人决定：前提是迁移 0053 演练、文案定稿（{{TODO_COPY:family_goals.*}}）与可见范围确认。
import { isLocalEnv, type RunEnv } from '../http/config.ts';

export const FAMILY_GOALS_FEATURE = 'family_goals';

/** 解析开关；非 dev / test 设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseFamilyGoalsFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!isLocalEnv(env)) throw new Error('STARBUDDY_FAMILY_GOALS 只在 dev / test 可用（迁移演练与文案定稿之前 staging 以上一律关闭）');
  if (raw.trim() !== FAMILY_GOALS_FEATURE) throw new Error(`STARBUDDY_FAMILY_GOALS 只认 ${FAMILY_GOALS_FEATURE}`);
  return true;
}
