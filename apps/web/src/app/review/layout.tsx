// 审核台的布局（spec 冲刺 1 · PR 3）：staff 专用，不与学生、家长页面共用导航。
// 只处理公共知识层内容（地图版本、节点、边、投影、原子）与 staff 账号，不显示任何学生数据（规则 13）。
import type { Metadata } from 'next';
import type { ReactNode } from 'react';

export const metadata: Metadata = {
  title: '魁星点斗 · 审核台',
  robots: { index: false, follow: false },
};

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return (
    <div className="review">
      <header className="review-header">
        <p>
          <strong>审核台 v0</strong> <span className="watermark">开发原型 · 合成数据</span>
        </p>
        <nav aria-label="审核台">
          <a href="/review">地图版本</a>
        </nav>
      </header>
      {children}
    </div>
  );
}
