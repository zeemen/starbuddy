// 错题再练的前端开关（specs/practice-loop-stage1/spec.md 第 5、6 节“功能开关 practice，默认关”；做法同 src/shell/features.ts）。
// 默认关闭：关闭时前端不渲染任何入口元素、不发任何请求，再练页面一律 404（页面在读会话之前判断）——与开关打开前零差异。
// BFF 侧是另一个同名开关（services/bff/src/practice/features.ts，STARBUDDY_PRACTICE=practice）：开关关时 BFF 不注册三条路由
// （与表外路径同一个 404）。前后端的开关由部署环境各自设置。
// 打开方式：STARBUDDY_PRACTICE=practice；只在 dev / test 生效，staging、pilot、生产一律关闭——封测开不开由人决定
// （前提是教研数值签字、present_item 题面契约与准入决定落地，spec 待人确认 #12、#13）。只在服务端读，不进浏览器。

type Env = Record<string, string | undefined>;

export const PRACTICE_FEATURE = 'practice';

export function practiceEnabled(env: Env = process.env): boolean {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') return false;
  return (env['STARBUDDY_PRACTICE'] ?? '').split(',').map((s) => s.trim()).includes(PRACTICE_FEATURE);
}

/**
 * 有再练入口的学科（前端学科码，与 BFF 路径里的 {subject} 同一套）：数学（阿鸦）、道法（知行）——spec 设计要点 0 的默认学科范围。
 * 语文（文砺）学习服务默认不开（BFF 回 404），这里也不放入口；英语不涉及。
 */
export const PRACTICE_SUBJECTS = ['math', 'politics'] as const;
export type PracticeSubject = (typeof PRACTICE_SUBJECTS)[number];
export const isPracticeSubject = (s: unknown): s is PracticeSubject => typeof s === 'string' && (PRACTICE_SUBJECTS as readonly string[]).includes(s);
