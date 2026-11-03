// 家长孩子页上“我的诉求”的入口：开关关时返回 null——不渲染任何元素（与开关打开前零差异）。只在家长端使用。
import { FAMILY_GOALS } from './copy.ts';
import { familyGoalsEnabled } from './features.ts';

export function GoalsLink({ studentId }: { studentId: string }) {
  if (!familyGoalsEnabled()) return null;
  return (
    <>
      {' · '}
      <a href={`/family/${studentId}/goals`} data-testid="child-family-goals">{FAMILY_GOALS.entry}</a>
    </>
  );
}
