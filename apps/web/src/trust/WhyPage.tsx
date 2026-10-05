// “为什么”页（冲刺 5-H 设计要点 1）：一条判断的陈述、状态、依据（证据链）与依据版本；下面是“我不同意”的三条路（设计要点 2）。
// 学生看状态（初步判断 / 待验证）；家长另附置信度说明（SIE 第八章：家长看不到无置信度的推断）。
// 证据链只有时间、证据种类、证据等级、知识点编号：不展示逐句对话与作答原文（view.ts 只挑这四个字段）。
// 看不到的判断连存在都不暴露：读不到、不可见、不存在一律同一个“暂时无法获取”，也不显示异议按钮。
// 待验证的判断是虚线，不作为陈述：只显示状态，不显示结论文字（BFF 已去掉待验证的文字，学生受众另去掉置信度分档；view.ts 再断言）。
import { randomUUID } from 'node:crypto';
import { notFound, redirect } from 'next/navigation';
import { ResultForm } from '../auth/ResultForm.tsx';
import { fetchWhy } from '../lib/trust-api.ts';
import { CONFIDENCE_BAND, DISPUTE, DISPUTE_KIND, DISPUTE_RESULT, EVIDENCE_LEVEL, EVIDENCE_SOURCE, TRUST, WHY, WHY_STATUS } from '../shell/copy.ts';
import { trustEnabled } from '../shell/features.ts';
import { requireGuardian, requireOwnStudent, requireTrust } from '../shell/guard.ts';
import { raiseDisputeAction } from './actions.ts';
import { sideBase } from './links.tsx';
import { disputeKindsFor, newIdempotencyKey, pickWhy, shanghaiDay, UUID, type Audience, type WhyView } from './view.ts';

export async function WhyPage({ studentId, claimId, audience }: { studentId: string; claimId: string; audience: Audience }) {
  requireTrust('why');
  if (!UUID.test(claimId)) notFound();
  const ps = audience === 'student' ? await requireOwnStudent(studentId) : await requireGuardian(studentId);
  const r = await fetchWhy(ps.session, studentId, claimId);
  if (!r.ok && r.status === 401) redirect('/login');
  const view = r.ok ? pickWhy(r.value, claimId, audience) : null;
  const base = sideBase(audience, studentId);
  return (
    <main data-testid="why-page" data-trust-root="why">
      <p><a href={audience === 'student' ? `${base}/today` : base}>{TRUST.back}</a></p>
      <h1>{WHY.heading}</h1>
      {view ? <ClaimExplain view={view} audience={audience} /> : <p className="muted" data-unavailable="why">{TRUST.unavailable}</p>}
      {view && trustEnabled('dispute')
        ? <DisputePanel studentId={studentId} claimId={view.claim_id} audience={audience} csrf={ps.session.csrf} /> : null}
    </main>
  );
}

function ClaimExplain({ view, audience }: { view: WhyView; audience: Audience }) {
  const st = view.statement;
  return (
    <section className="why" data-claim-id={view.claim_id} data-status={st.status} aria-labelledby="why-evidence">
      {st.display === 'solid' && st.text !== null
        ? <p className="claim-text"><span data-claim-id={st.claim_id}>{st.text}</span></p>
        : <p className="muted">{WHY.dashedNote}</p>}
      <p>
        <span className="muted">{WHY.statusLabel}</span>{' '}
        <span data-testid="why-status">{WHY_STATUS[st.status]}</span>
        {audience === 'guardian' && st.confidence_band
          ? <>{' '}<span className="muted confidence" data-confidence={st.confidence_band}>{CONFIDENCE_BAND[st.confidence_band]}</span></> : null}
      </p>
      <h2 id="why-evidence">{WHY.evidenceHeading}</h2>
      {view.evidence.length === 0 ? <p className="muted">{WHY.evidenceEmpty}</p> : (
        <table className="trust-table" data-testid="why-evidence">
          <thead>
            <tr><th scope="col">{WHY.colTime}</th><th scope="col">{WHY.colKind}</th><th scope="col">{WHY.colLevel}</th><th scope="col">{WHY.colNode}</th></tr>
          </thead>
          <tbody>
            {view.evidence.map((e, i) => (
              <tr key={i}>
                <td><span data-value="date">{shanghaiDay(e.occurred_at)}</span></td>
                <td>{EVIDENCE_SOURCE[e.source_type]}</td>
                <td>{e.evidence_level ? EVIDENCE_LEVEL[e.evidence_level] : null}</td>
                <td>{e.node_id ? <span data-value="node">{e.node_id}</span> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {view.source_version ? <p className="muted"><span>{WHY.version}</span>{' '}<span data-value="version">{view.source_version}</span></p> : null}
    </section>
  );
}

/** “我不同意”的三条路：每条一张表单（学生本人三条，家长两条）；结果只显示下一步编码映射的占位文案 */
function DisputePanel({ studentId, claimId, audience, csrf }: { studentId: string; claimId: string; audience: Audience; csrf: string }) {
  const base = sideBase(audience, studentId);
  return (
    <section aria-labelledby="dispute-heading" data-testid="dispute-panel">
      <h2 id="dispute-heading">{DISPUTE.heading}</h2>
      {disputeKindsFor(audience).map((kind) => (
        <ResultForm key={kind} action={raiseDisputeAction} labels={DISPUTE_RESULT} testId={`dispute-${kind}`} className="shell-form">
          <input type="hidden" name="student" value={studentId} />
          <input type="hidden" name="claim" value={claimId} />
          <input type="hidden" name="kind" value={kind} />
          <input type="hidden" name="idempotency_key" value={newIdempotencyKey(randomUUID())} />
          <input type="hidden" name="side" value={audience === 'student' ? 's' : 'family'} />
          <input type="hidden" name="csrf" value={csrf} />
          <button type="submit">{DISPUTE_KIND[kind]}</button>
        </ResultForm>
      ))}
      <p><a href={`${base}/disputes`}>{DISPUTE.toList}</a></p>
    </section>
  );
}
