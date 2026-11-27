// 讲评课页面的前端开关（specs/stage-lesson-session ③b-5；做法同 src/practice/features.ts）。
// 默认关闭：关闭时前端不渲染任何入口、不发任何请求，讲评课页面一律 404（页面在读会话之前判断）——与开关打开前零差异。
// BFF 侧是另一个同名开关（services/bff/src/lesson/features.ts，STARBUDDY_LESSON=lesson）：关时 BFF 不注册两条路由。
// 打开方式：STARBUDDY_LESSON=lesson；只在 dev / test 生效，staging、pilot、生产一律关闭（插件契约 1.1.0 签字、真实讲评课内容落地前，
// 学习服务那一侧只在候选契约开关打开时可用）。只在服务端读，不进浏览器。
import { launchTier } from '../lib/launch-env.ts';

type Env = Record<string, string | undefined>;

export const LESSON_FEATURE = 'lesson';

export function lessonEnabled(env: Env = process.env): boolean {
  if (launchTier(env) !== 'local') return false;
  return (env['STARBUDDY_LESSON'] ?? '').split(',').map((s) => s.trim()).includes(LESSON_FEATURE);
}

/** 有讲评课的学科（前端学科码）→ BFF 的学科码：只有数学（阿鸦）。道法、语文的讲评课另起（规格第三节） */
export const LESSON_SUBJECTS = { math: 'aya' } as const;
export type LessonSubject = keyof typeof LESSON_SUBJECTS;
export const isLessonSubject = (s: unknown): s is LessonSubject => s === 'math';
