// 考试录入与对账卡（冲刺 5-H 设计要点 3）：学生本人或已绑定家长录入考试安排（范围节点、日期、满分）与成绩（各录各的，规则 6）；
// 考试页显示已冻结的预测区间（80%；学生只看区间，家长另附置信度说明）与对账卡。
// 判断先于展示（规则 11）：预测区间与对账卡的结构部分（区间内外、偏差节点、偏差方向、平台调整）都挂在预测所引的判断上
// （外层 data-claim-id），对账卡的句子每句带自己的 claim_id；前端在 view.ts 再断言一次，无 ID 的句子不渲染，
// 对账卡所依据的预测对不上时整张卡不显示。偏差方向只显示四个编码之一对应的占位，不附评价文字。
// 冻结后的预测只读（本页没有任何改预测的入口）。BFF 的预测与对账卡读取通道缺省 503：显示“暂时无法获取”。
// 考试安排没有列表接口（BFF 只返回新建的 ID）：录入后直接进这场考试的页面；以前录入的安排暂时找不回来（lessons 待人确认）。
import { notFound, redirect } from 'next/navigation';
import { ResultForm } from '../auth/ResultForm.tsx';
import { fetchPrediction, fetchReconciliation } from '../lib/trust-api.ts';
import {
  ADJUSTMENT, CONFIDENCE_BAND, DEVIATION_DIRECTION, EXAM, EXAM_RESULT, EXAM_SUBJECT, EXAM_TYPE, PREDICTION, RECONCILE, TRUST,
} from '../shell/copy.ts';
import { requireGuardian, requireOwnStudent, requireTrust } from '../shell/guard.ts';
import { examPlanAction, examScoreAction } from './actions.ts';
import { sideBase } from './links.tsx';
import {
  defaultFullScore, DEVIATION_DIRECTIONS, EXAM_SUBJECTS, EXAM_TYPES, pickPrediction, pickReconciliation, UUID, type Audience, type PredictionView, type ReconcileView,
} from './view.ts';

const side = (a: Audience): string => (a === 'student' ? 's' : 'family');
const todayShanghai = (): string => new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);

async function who(studentId: string, audience: Audience) {
  if (audience === 'student') return { ps: await requireOwnStudent(studentId), grade: null };
  const ps = await requireGuardian(studentId);
  return { ps, grade: ps.grade };
}

export async function ExamPlanPage({ studentId, audience }: { studentId: string; audience: Audience }) {
  requireTrust('reconcile');
  const { ps, grade } = await who(studentId, audience);
  const base = sideBase(audience, studentId);
  return (
    <main data-testid="exam-plan-page" data-trust-root="exam-plan">
      <p><a href={audience === 'student' ? `${base}/today` : base}>{TRUST.back}</a></p>
      <h1>{EXAM.heading}</h1>
      <h2 id="plan-heading">{EXAM.planHeading}</h2>
      <ResultForm action={examPlanAction} labels={EXAM_RESULT} testId="exam-plan-form" className="shell-form">
        <input type="hidden" name="student" value={studentId} />
        <input type="hidden" name="side" value={side(audience)} />
        <input type="hidden" name="csrf" value={ps.session.csrf} />
        <label htmlFor="exam-subject">{EXAM.subject}</label>
        <select id="exam-subject" name="subject" defaultValue="aya">
          {EXAM_SUBJECTS.map((s) => <option key={s} value={s}>{EXAM_SUBJECT[s]}</option>)}
        </select>
        <label htmlFor="exam-type">{EXAM.type}</label>
        <select id="exam-type" name="exam_type" defaultValue="monthly">
          {EXAM_TYPES.map((t) => <option key={t} value={t}>{EXAM_TYPE[t]}</option>)}
        </select>
        <label htmlFor="exam-date">{EXAM.date}</label>
        <input id="exam-date" name="exam_date" type="date" min={todayShanghai()} required />
        <label htmlFor="exam-full">{EXAM.fullScore}</label>
        <input id="exam-full" name="full_score" inputMode="numeric" defaultValue={String(defaultFullScore(grade))} required aria-describedby="exam-full-hint" />
        <span id="exam-full-hint" className="muted">{EXAM.fullScoreHint}</span>
        <label htmlFor="exam-scope">{EXAM.scope}</label>
        <textarea id="exam-scope" name="scope_node_ids" rows={3} required />
        <button type="submit">{EXAM.submitPlan}</button>
      </ResultForm>
    </main>
  );
}

export async function ExamPage({ studentId, examId, audience }: { studentId: string; examId: string; audience: Audience }) {
  requireTrust('reconcile');
  if (!UUID.test(examId)) notFound();
  const { ps } = await who(studentId, audience);
  const [pr, rr] = await Promise.all([fetchPrediction(ps.session, studentId, examId), fetchReconciliation(ps.session, studentId, examId)]);
  for (const r of [pr, rr]) {
    if (!r.ok && r.status === 401) redirect('/login');
    if (!r.ok && r.status === 404) notFound();
  }
  const prediction = pr.ok ? pickPrediction(pr.value) : null;
  const picked = rr.ok ? pickReconciliation(rr.value, prediction) : null;
  const base = sideBase(audience, studentId);
  return (
    <main data-testid="exam-page" data-trust-root="exam">
      <p><a href={`${base}/exams`}>{EXAM.toPlan}</a></p>
      <h1>{EXAM.heading}</h1>

      <section aria-labelledby="score-heading">
        <h2 id="score-heading">{EXAM.scoreHeading}</h2>
        <ResultForm action={examScoreAction} labels={EXAM_RESULT} testId="exam-score-form" className="shell-form">
          <input type="hidden" name="student" value={studentId} />
          <input type="hidden" name="exam" value={examId} />
          <input type="hidden" name="side" value={side(audience)} />
          <input type="hidden" name="csrf" value={ps.session.csrf} />
          <label htmlFor="exam-raw">{EXAM.rawScore}</label>
          <input id="exam-raw" name="raw_score" inputMode="decimal" required />
          <button type="submit">{EXAM.submitScore}</button>
        </ResultForm>
      </section>

      <section aria-labelledby="prediction-heading" data-testid="prediction">
        <h2 id="prediction-heading">{PREDICTION.heading}</h2>
        {!pr.ok ? <p className="muted" data-unavailable="prediction">{TRUST.unavailable}</p>
          : prediction ? <Prediction p={prediction} audience={audience} /> : <p className="muted">{PREDICTION.none}</p>}
      </section>

      <section aria-labelledby="reconcile-heading" data-testid="reconciliation">
        <h2 id="reconcile-heading">{RECONCILE.heading}</h2>
        {!rr.ok || !pr.ok ? <p className="muted" data-unavailable="reconciliation">{TRUST.unavailable}</p>
          : picked && prediction ? <ReconcileCard card={picked.card} predictionClaim={prediction.claim_id} audience={audience} />
            : <p className="muted">{RECONCILE.none}</p>}
      </section>
    </main>
  );
}

function Prediction({ p, audience }: { p: PredictionView; audience: Audience }) {
  return (
    <div data-claim-id={p.claim_id} data-frozen={p.frozen ? 'true' : 'false'}>
      <p>
        <span>{PREDICTION.interval}</span>{' '}
        <span data-value="low">{p.low}</span>{' '}<span>{PREDICTION.to}</span>{' '}<span data-value="high">{p.high}</span>
      </p>
      <p className="muted">{p.frozen ? PREDICTION.frozen : PREDICTION.notFrozen}</p>
      {audience === 'guardian' ? <p className="muted confidence"><span>{PREDICTION.levelNote}</span>{' '}<span data-value="level">{Math.round(p.interval_level * 100)}%</span></p> : null}
    </div>
  );
}

/** 对账卡：结构部分挂在预测所引的判断上；句子只渲染带 claim_id 的（view.ts 已过滤，这里再挡一次） */
function ReconcileCard({ card, predictionClaim, audience }: { card: ReconcileView; predictionClaim: string; audience: Audience }) {
  return (
    <div className="reconcile-card" data-claim-id={predictionClaim} data-within={card.within_interval ? 'true' : 'false'} data-testid="reconcile-card">
      <p><span>{RECONCILE.actual}</span>{' '}<span data-value="score">{card.actual_score}</span></p>
      <p>{card.within_interval ? RECONCILE.within : RECONCILE.outside}</p>
      {card.deviation_nodes.length > 0 ? (
        <>
          <h3>{RECONCILE.nodes}</h3>
          <ul className="shell-list">{card.deviation_nodes.map((n) => <li key={n}><span data-value="node">{n}</span></li>)}</ul>
        </>
      ) : null}
      {card.deviation_directions.length > 0 ? (
        <>
          <h3>{RECONCILE.directions}</h3>
          <ul className="shell-list" data-testid="directions">
            {card.deviation_directions.map((d) => <li key={d} data-direction-index={DEVIATION_DIRECTIONS.indexOf(d) + 1}>{DEVIATION_DIRECTION[d]}</li>)}
          </ul>
        </>
      ) : null}
      {card.adjustments.length > 0 ? (
        <>
          <h3>{RECONCILE.adjustments}</h3>
          <ul className="shell-list">{card.adjustments.map((a) => <li key={a}>{ADJUSTMENT[a]}</li>)}</ul>
        </>
      ) : null}
      {card.statements.length > 0 ? (
        <>
          <h3>{RECONCILE.statements}</h3>
          <ul className="shell-list" data-testid="reconcile-statements">
            {card.statements.map((s, i) => (s.claim_id ? (
              <li key={i}>
                <span data-claim-id={s.claim_id}>{s.text}</span>
                {audience === 'guardian' ? <>{' '}<span className="muted confidence" data-confidence={s.confidence_band}>{CONFIDENCE_BAND[s.confidence_band]}</span></> : null}
              </li>
            ) : null))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
