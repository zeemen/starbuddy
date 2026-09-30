// 异议状态页（冲刺 5-H 设计要点 2）：自己提出的异议与状态（BFF 按 0032 RLS 只给自己提出的，家长须有效绑定）。
// 类型、状态只按编码映射到占位；结论码不显示（没有文案，不能把编码直接给人看）；不显示验证任务、复核任务与原始事实引用。
// 撤回未处理的异议：BFF 与契约都还没有撤回接口（SIE-03 lessons PR③ 待人确认 6），本页不放撤回按钮。
import { notFound, redirect } from 'next/navigation';
import { fetchDisputes } from '../lib/trust-api.ts';
import { DISPUTE, DISPUTE_KIND, DISPUTE_STATUS, TRUST } from '../shell/copy.ts';
import { trustEnabled } from '../shell/features.ts';
import { requireGuardian, requireOwnStudent, requireTrust } from '../shell/guard.ts';
import { sideBase } from './links.tsx';
import { pickDisputes, shanghaiDay, type Audience } from './view.ts';

export async function DisputesPage({ studentId, audience }: { studentId: string; audience: Audience }) {
  requireTrust('dispute');
  const ps = audience === 'student' ? await requireOwnStudent(studentId) : await requireGuardian(studentId);
  const r = await fetchDisputes(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const rows = r.ok ? pickDisputes(r.value) : null;
  const base = sideBase(audience, studentId);
  return (
    <main data-testid="disputes-page" data-trust-root="disputes">
      <p><a href={audience === 'student' ? `${base}/today` : base}>{TRUST.back}</a></p>
      <h1>{DISPUTE.listHeading}</h1>
      {!rows ? <p className="muted" data-unavailable="disputes">{TRUST.unavailable}</p>
        : rows.length === 0 ? <p className="muted" data-testid="disputes-empty">{DISPUTE.empty}</p> : (
          <table className="trust-table" data-testid="disputes">
            <thead>
              <tr>
                <th scope="col">{DISPUTE.colKind}</th><th scope="col">{DISPUTE.colStatus}</th>
                <th scope="col">{DISPUTE.colCreated}</th><th scope="col">{DISPUTE.colResolved}</th><th scope="col">{DISPUTE.colClaim}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.dispute_id} data-dispute-status={d.status}>
                  <td>{DISPUTE_KIND[d.kind]}</td>
                  <td>{DISPUTE_STATUS[d.status]}</td>
                  <td><span data-value="date">{shanghaiDay(d.created_at)}</span></td>
                  <td>{d.resolved_at ? <span data-value="date">{shanghaiDay(d.resolved_at)}</span> : null}</td>
                  <td>{trustEnabled('why') ? <a href={`${base}/why/${d.claim_id}`}>{DISPUTE.openClaim}</a> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </main>
  );
}
