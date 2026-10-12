// 月度计划的前端开关（specs/family-goals-monthly-plan/step3-spec.md，PLT-14 第 3 步；做法同 src/family-goals/features.ts）。
// 默认关闭：关闭时家长孩子页与学生今日页不渲染入口、不发任何请求，两个页面一律 404（页面在读会话之前判断）——与开关打开前零差异。
// BFF 侧是同名开关（services/bff/src/family/monthly-plan.ts）：开关关时 BFF 不注册两条路由。前后端的开关由部署环境各自设置。
// 打开方式：STARBUDDY_MONTHLY_PLAN=monthly_plan；只在 dev / test 生效，staging、pilot、生产一律关闭。只在服务端读，不进浏览器。

type Env = Record<string, string | undefined>;

export const MONTHLY_PLAN_FEATURE = 'monthly_plan';

export function monthlyPlanEnabled(env: Env = process.env): boolean {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') return false;
  return (env['STARBUDDY_MONTHLY_PLAN'] ?? '').split(',').map((s) => s.trim()).includes(MONTHLY_PLAN_FEATURE);
}
