// 学生壳的公共布局（spec 第 10、11、18 节）：顶栏“今日”“星图”“科目”、额度与时长状态、连续使用提醒条、点点入口、退出学生模式。
// 任何科目页一步回到星图（顶栏常驻“星图”）。点点入口只是占位面板（系统口吻“点点正在准备中”，没有台词、对话、模型调用、形象）。
// 休息提醒系统口吻、只提醒不锁定；计时以 BFF 为准，这里只显示。不写 localStorage。
// 会话起止（SIE-05 PR2）由 src/behavior/SessionBeacon.tsx 经本站 /s/{id}/presence 写入，壳本身不显示任何事件。
import type { ReactNode } from 'react';
import { FAMILY_RESULT } from '../auth/copy.ts';
import { ResultForm } from '../auth/ResultForm.tsx';
import { logoutAction } from '../app/family/actions.ts';
import { ackRestAction } from '../app/s/[studentId]/actions.ts';
import type { Balance, RestView } from '../lib/shell-api.ts';
import { entryOf } from '../behavior/presence.ts';
import { SessionBeacon } from '../behavior/SessionBeacon.tsx';
import { DIANDIAN, NAV, REST } from './copy.ts';
import { StatusBar } from './StatusBar.tsx';
import { TrustNav } from '../trust/links.tsx';

export type ShellTab = 'today' | 'star-map' | 'learn';

export function StudentShell({ studentId, csrf, balance, rest, path, children }: {
  studentId: string; csrf: string; balance: Balance | null; rest: RestView | null; path: string; children: ReactNode;
}) {
  const base = `/s/${studentId}`;
  return (
    <div className="shell" data-testid="student-mode">
      {/* 会话起止（SIE-05 PR2）：不渲染元素；只按入口类别记 session_start / session_end，不记哪一页、停了多久 */}
      <SessionBeacon studentId={studentId} csrf={csrf} entry={entryOf(path, studentId)} />
      <header className="shell-top">
        <nav aria-label={NAV.label} className="shell-nav">
          <a href={`${base}/today`} data-testid="nav-today">{NAV.today}</a>
          <a href={`${base}/star-map`} data-testid="nav-star-map">{NAV.starMap}</a>
          <a href={`${base}/learn`} data-testid="nav-learn">{NAV.subjects}</a>
        </nav>
        <ResultForm action={logoutAction} labels={FAMILY_RESULT} testId="exit-student-mode" className="shell-form shell-exit">
          <input type="hidden" name="csrf" value={csrf} />
          <span className="visually-hidden" id="exit-note">{NAV.exitNote}</span>
          <button type="submit" aria-describedby="exit-note">{NAV.exit}</button>
        </ResultForm>
      </header>
      <TrustNav studentId={studentId} audience="student" />
      {balance ? <StatusBar balance={balance} audience="student" /> : null}
      {rest?.show ? (
        <div className="rest-bar" role="status" data-testid="rest-reminder">
          <span>{REST.reminder(rest.continuous_minutes, rest.suggest_break_minutes)}</span>
          <ResultForm action={ackRestAction} labels={FAMILY_RESULT} testId="rest-ack" className="shell-form inline-form">
            <input type="hidden" name="csrf" value={csrf} />
            <input type="hidden" name="student" value={studentId} />
            <input type="hidden" name="back" value={path} />
            <button type="submit">{REST.ack}</button>
          </ResultForm>
        </div>
      ) : null}
      {children}
      <details className="diandian" data-testid="diandian">
        <summary>{DIANDIAN.entry}</summary>
        <p>{DIANDIAN.placeholder}</p>
      </details>
    </div>
  );
}
