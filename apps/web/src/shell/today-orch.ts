// 今日计划跨科编排（PLT-11，specs/today-plan-orchestration-plt11 范围第 3 条）的前端开关。
// 默认关闭：关闭时今日页与开关上线前零差异（不渲染跨科列表；BFF 侧开关关时也不返回 ordered）。
// BFF 侧是同名开关（services/bff/src/shell/today-orch.ts，STARBUDDY_TODAY_ORCH=today_orch）；前后端的开关由部署环境各自设置，
// 前端开着而 BFF 没返回 ordered 时同样不渲染。打开方式：STARBUDDY_TODAY_ORCH=today_orch；只在 dev / test 生效。只在服务端读，不进浏览器。
// 只给学生本人：家长端没有跨科列表（可见范围待人确认，spec“不做”）。跨科列表与“去做”地址在 src/today/（壳目录不链再练、复测页）。

type Env = Record<string, string | undefined>;

export const TODAY_ORCH_FEATURE = 'today_orch';

export function todayOrchEnabled(env: Env = process.env): boolean {
  const run = env['STARBUDDY_ENV'] ?? '';
  if (run !== 'dev' && run !== 'test') return false;
  return (env['STARBUDDY_TODAY_ORCH'] ?? '').split(',').map((s) => s.trim()).includes(TODAY_ORCH_FEATURE);
}
