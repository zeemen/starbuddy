// 内部成员测试运营的入口（LT-06）：开关关时一律返回 null——不渲染任何元素、不发请求（与开关打开前零差异）。
// - StudentFeedbackLink：学生壳顶栏的“问题反馈”，链接带上当前页面的路由模板（只是页面类型，不含标识）；
// - GuardianInternalTestLinks：家长孩子页的“问题反馈”与“退出内部测试”；
// - ExitedNotice：家长孩子页顶部的“已退出”说明（读 BFF 的退出状态；读不到不显示，不拖垮整页）。
import type { BffSession } from '../lib/bff.ts';
import { fetchInternalTestStatus } from './api.ts';
import { EXIT, FEEDBACK } from './copy.ts';
import { internalTestEnabled } from './features.ts';
import { routeTemplateOf } from './routes.ts';

const withFrom = (href: string, route: string | null): string => (route ? `${href}?from=${encodeURIComponent(route)}` : href);

export function StudentFeedbackLink({ studentId, path }: { studentId: string; path: string }) {
  if (!internalTestEnabled()) return null;
  return <a href={withFrom(`/s/${studentId}/feedback`, routeTemplateOf(path, studentId))} data-testid="nav-feedback">{FEEDBACK.entry}</a>;
}

export function GuardianInternalTestLinks({ studentId, exited }: { studentId: string; exited: boolean }) {
  if (!internalTestEnabled()) return null;
  return (
    <>
      {' · '}
      <a href={withFrom(`/family/${studentId}/feedback`, '/family/[studentId]')} data-testid="child-feedback">{FEEDBACK.entry}</a>
      {exited ? null : (
        <>
          {' · '}
          <a href={`/family/${studentId}/exit`} data-testid="child-internal-test-exit">{EXIT.entry}</a>
        </>
      )}
    </>
  );
}

/** 开关打开时读退出状态；关时、读不到时都按“未退出”（入口照常，BFF 仍会按统一判据拦） */
export async function internalTestExited(s: BffSession, studentId: string): Promise<boolean> {
  if (!internalTestEnabled()) return false;
  const r = await fetchInternalTestStatus(s, studentId);
  return r.ok && r.value.exited === true;
}

export function ExitedNotice({ exited }: { exited: boolean }) {
  if (!exited) return null;
  return <p role="status" className="rest-bar" data-testid="internal-test-exited">{EXIT.exitedNotice}</p>;
}
