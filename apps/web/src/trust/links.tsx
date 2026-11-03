// 信任界面的入口（冲刺 5-H）：开关关时一律返回 null——不渲染任何元素（4-H“信任界面预留”验收）。
//   ClaimLinks：一条判断旁的“为什么”入口（今日计划理由旁、星的详情位）；只在有 claim_id 时出现（没有判断就没有“为什么”，规则 11）。
//     “我不同意”在“为什么”页里：先看到判断与依据，再选三条路之一。
//   TrustNav：学生壳顶栏下、家长孩子页的入口行（考试与对账卡、我提出的异议、周报——学生“成长手记”、家长“本周周报”，
//     家长的数据查看导出与删除）。
import { anyTrustEnabled, trustEnabled } from '../shell/features.ts';
import { TRUST, WEEKLY } from '../shell/copy.ts';
import type { Audience } from './view.ts';

export const sideBase = (audience: Audience, studentId: string): string => (audience === 'student' ? `/s/${studentId}` : `/family/${studentId}`);

export function ClaimLinks({ studentId, claimId, audience }: { studentId: string; claimId: string; audience: Audience }) {
  if (!trustEnabled('why')) return null;
  return (
    <a className="trust-link" href={`${sideBase(audience, studentId)}/why/${claimId}`} data-trust-link="why" data-for-claim={claimId}>
      {TRUST.entryWhy}
    </a>
  );
}

export function TrustNav({ studentId, audience }: { studentId: string; audience: Audience }) {
  const features = audience === 'guardian' ? (['reconcile', 'dispute', 'weekly', 'data_rights'] as const) : (['reconcile', 'dispute', 'weekly'] as const);
  if (!anyTrustEnabled(features)) return null;
  const b = sideBase(audience, studentId);
  return (
    <nav className="trust-nav" aria-label={TRUST.navLabel} data-trust-nav={audience}>
      {trustEnabled('reconcile') ? <a href={`${b}/exams`} data-trust-link="reconcile">{TRUST.entryExams}</a> : null}
      {trustEnabled('dispute') ? <a href={`${b}/disputes`} data-trust-link="dispute">{TRUST.entryDisputes}</a> : null}
      {trustEnabled('weekly') ? (
        <a href={`${b}/weekly`} data-trust-link="weekly">{audience === 'student' ? WEEKLY.entryStudent : WEEKLY.entryGuardian}</a>
      ) : null}
      {audience === 'guardian' && trustEnabled('data_rights') ? <a href={`${b}/data`} data-trust-link="data_rights">{TRUST.entryData}</a> : null}
    </nav>
  );
}
