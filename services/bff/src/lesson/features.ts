// 讲评课会话的功能开关（specs/stage-lesson-session ③b-4b；做法同 src/practice/features.ts）。
// 打开时注册两条学生端路由（src/http/lesson-routes.ts）；关闭时不注册，与表外路径同一个 404。
// 打开方式：STARBUDDY_LESSON=lesson，只在 dev / test 生效；其余环境（含 staging）设了就拒绝启动（main.ts）：
// 学习服务的讲评课接口走插件契约 1.1.0 候选版（签字前只在 dev / test），讲评课页面（③b-5）也还没有。
import { isLocalEnv, type RunEnv } from '../http/config.ts';

export const LESSON_FEATURE = 'lesson';

/** 解析开关；dev / test 以外设了直接抛错（调用方拒绝启动），没设则关闭 */
export function parseLessonFeature(env: RunEnv, raw: string | undefined): boolean {
  if (raw === undefined || raw.trim() === '') return false;
  if (!isLocalEnv(env)) throw new Error('STARBUDDY_LESSON 只在 dev / test 可用（插件契约 1.1.0 签字、讲评课内容与页面落地之前 staging 以上一律关闭）');
  if (raw.trim() !== LESSON_FEATURE) throw new Error(`STARBUDDY_LESSON 只认 ${LESSON_FEATURE}`);
  return true;
}
