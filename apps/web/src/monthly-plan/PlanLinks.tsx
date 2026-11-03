// 家长孩子页入口行里的“月度计划”：开关关时返回 null——不渲染任何元素（与开关打开前零差异）。只在家长端使用
// （学生今日页的入口在 ./MyPlanLink.tsx，只引用学习计划一段的文案）。
import { MONTHLY_PLAN } from './guardian-copy.ts';
import { monthlyPlanEnabled } from './features.ts';

export function GuardianPlanLink({ studentId }: { studentId: string }) {
  if (!monthlyPlanEnabled()) return null;
  return (
    <>
      {' · '}
      <a href={`/family/${studentId}/monthly-plan`} data-testid="child-monthly-plan">{MONTHLY_PLAN.entry}</a>
    </>
  );
}
