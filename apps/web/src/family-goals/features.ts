// 家长诉求的前端开关（specs/family-goals-monthly-plan/spec.md 第 1 步，PLT-14；做法同 src/retest/features.ts）。
// 默认关闭：关闭时家长孩子页不渲染入口、不发任何请求，“我的诉求”页一律 404（页面在读会话之前判断）——与开关打开前零差异。
// BFF 侧是同名开关（services/bff/src/family/features.ts）：开关关时 BFF 不注册两条路由。前后端的开关由部署环境各自设置。
// 打开方式：STARBUDDY_FAMILY_GOALS=family_goals；只在 dev / test 生效，staging、pilot、生产一律关闭。只在服务端读，不进浏览器。
// 学生端不读这个开关，也不引用本目录的任何模块（tests/family-goals.test.ts 静态核对）：学生端永远没有诉求的字段与页面。

type Env = Record<string, string | undefined>;

export const FAMILY_GOALS_FEATURE = 'family_goals';

export function familyGoalsEnabled(env: Env = process.env): boolean {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') return false;
  return (env['STARBUDDY_FAMILY_GOALS'] ?? '').split(',').map((s) => s.trim()).includes(FAMILY_GOALS_FEATURE);
}
