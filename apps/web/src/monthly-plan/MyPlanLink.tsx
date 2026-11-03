// 学生今日页的“本月计划”入口：开关关时返回 null——不渲染任何元素（与开关打开前零差异，今日页的视觉回归不变）。
// 只链到学习计划（学生端没有诉求、注意事项、回顾与分差），只引用学习计划一段的文案。
import { monthlyPlanEnabled } from './features.ts';
import { MY_PLAN } from './student-copy.ts';

export function MyPlanLink({ studentId }: { studentId: string }) {
  if (!monthlyPlanEnabled()) return null;
  return <p><a href={`/s/${studentId}/plan`} data-testid="today-my-plan">{MY_PLAN.entry}</a></p>;
}
