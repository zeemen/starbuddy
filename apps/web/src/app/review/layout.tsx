// 审核台的布局（spec 冲刺 1 · PR 3）：staff 专用，不与学生、家长页面共用导航。
// 地图版本部分只处理公共知识层内容（地图版本、节点、边、投影、原子）与 staff 账号（规则 13）；
// 复核队列（冲刺 2-G）只显示不透明学生指针（student_ref）与结构化判分（维度分、置信度、证据 ID），不显示作答原文与任何可识别信息。
// 页头显示当前登录名与“退出”（PR 4）；未登录时只显示标题（登录页也用这个布局）。
import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { currentSession } from '../../lib/review-session.ts';
import { whoAmI } from '../../lib/review.ts';
import { logoutAction } from './actions.ts';

export const metadata: Metadata = {
  title: '魁星点斗 · 审核台',
  robots: { index: false, follow: false },
};

export default async function ReviewLayout({ children }: { children: ReactNode }) {
  const token = await currentSession();
  const me = token ? await whoAmI(token) : null;
  const login = me && me.ok ? me.value.login_name : null;
  return (
    <div className="review">
      <header className="review-header">
        <p>
          <strong>审核台 v0</strong> <span className="watermark">开发原型</span>
        </p>
        {login ? (
          <nav aria-label="审核台">
            <a href="/review">地图版本</a>
            <a href="/review/queue">复核队列</a>
            <a href="/review/content">内容版本</a>
            <a href="/review/policy">考情政策</a>
            <a href="/review/pages">编译页面</a>
            <a href="/review/spot-checks">事后抽检</a>
            <span data-testid="whoami">{login}</span>{' '}
            <form action={logoutAction} className="inline-form">
              <button type="submit">退出</button>
            </form>
          </nav>
        ) : null}
      </header>
      {children}
    </div>
  );
}
