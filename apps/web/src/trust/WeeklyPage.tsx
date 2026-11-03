// 跨科周报 v0（冲刺 5-H 设计要点 4）：学生版“成长手记”、家长版周报（开关 weekly，关时 404、不发请求）。
// 只在壳内展示（待人确认 #5），不发短信、推送。关于学生的句子只来自判断：每句一个 data-claim-id（view.ts 再断言，无 ID 的不渲染）；
// 段落标题、栏目标签全是占位文案（人写人审）；学生端不附置信度说明，家长端附（SIE 第八章）。每句旁是“为什么”入口（开关 why）；
// 判断来源不是模型推断时（客观记录、学生自述、家长观察）另加来源标注（规则 6，契约审查 L1）。
// 客观栏是客观记录（使用时长、各科练习），标明来源、不是判断；计划完成 / 跳过是学生自己标记的，单独一栏、标来源 self（迁移 0057，规则 6）。
// 数字放在 data-value 里。
import { notFound, redirect } from 'next/navigation';
import { fetchWeekly } from '../lib/trust-api.ts';
import { CONFIDENCE_BAND, EXAM_SUBJECT, TRUST, WEEKLY, WEEKLY_SECTION, WEEKLY_SOURCE } from '../shell/copy.ts';
import { requireGuardian, requireOwnStudent, requireTrust } from '../shell/guard.ts';
import { ClaimLinks, sideBase } from './links.tsx';
import { pickWeekly, type Audience, type WeeklyObjective } from './view.ts';

export async function WeeklyPage({ studentId, audience }: { studentId: string; audience: Audience }) {
  requireTrust('weekly');
  const ps = audience === 'student' ? await requireOwnStudent(studentId) : await requireGuardian(studentId);
  const r = await fetchWeekly(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const view = r.ok ? pickWeekly(r.value, audience) : null;
  const base = sideBase(audience, studentId);
  return (
    <main data-testid="weekly-page" data-trust-root="weekly" data-audience={audience}>
      <p><a href={audience === 'student' ? `${base}/today` : base}>{TRUST.back}</a></p>
      <h1>{audience === 'student' ? WEEKLY.studentHeading : WEEKLY.guardianHeading}</h1>
      {!r.ok ? <p className="muted" data-unavailable="weekly">{TRUST.unavailable}</p>
        : !view ? <p className="muted" data-testid="weekly-empty">{WEEKLY.empty}</p> : (
          <>
            <p className="muted" data-testid="weekly-week">
              <span>{WEEKLY.weekStart}</span>{' '}<span data-value="date">{view.week_start}</span>{' '}
              <span>{WEEKLY.weekEnd}</span>{' '}<span data-value="date">{view.week_end}</span>
            </p>
            {view.sections.map((sec) => (
              <section key={sec.key} aria-labelledby={`weekly-${sec.key}`} data-weekly-section={sec.key}>
                <h2 id={`weekly-${sec.key}`}>{WEEKLY_SECTION[sec.key]}</h2>
                <ul>
                  {sec.sentences.map((s) => s.claim_id ? (
                    <li key={s.claim_id}>
                      <span data-claim-id={s.claim_id}>{s.text}</span>
                      {s.preliminary ? <>{' '}<span className="muted">{WEEKLY.preliminary}</span></> : null}
                      {s.source_type && s.source_type !== 'inferred' ? (
                        <>{' '}<span className="muted" data-claim-source={s.source_type}>{WEEKLY_SOURCE[s.source_type]}</span></>
                      ) : null}
                      {audience === 'guardian' && s.confidence_band ? <>{' '}<span className="muted">{CONFIDENCE_BAND[s.confidence_band]}</span></> : null}
                      {' '}<ClaimLinks studentId={studentId} claimId={s.claim_id} audience={audience} />
                    </li>
                  ) : null)}
                </ul>
              </section>
            ))}
            {view.objective ? <ObjectiveColumn o={view.objective} /> : null}
          </>
        )}
    </main>
  );
}

/** 客观栏：使用时长、各科练习时长与作答题数（来源 objective，不是判断）；其后是单独的“学生自己标记”栏 */
function ObjectiveColumn({ o }: { o: WeeklyObjective }) {
  return (
    <>
    <section aria-labelledby="weekly-objective" data-weekly-objective="" data-source-type="objective">
      <h2 id="weekly-objective">{WEEKLY.objectiveHeading}</h2>
      <p className="muted">{WEEKLY.objectiveSource}</p>
      <dl>
        <dt>{WEEKLY.usage}</dt><dd><span data-value="minutes">{o.usage_minutes}</span></dd>
      </dl>
      {o.subjects.length > 0 ? (
        <table className="trust-table" data-testid="weekly-subjects">
          <thead>
            <tr><th scope="col">{WEEKLY.colSubject}</th><th scope="col">{WEEKLY.colPractice}</th><th scope="col">{WEEKLY.colAttempts}</th></tr>
          </thead>
          <tbody>
            {o.subjects.map((s) => (
              <tr key={s.subject} data-subject={s.subject}>
                <td>{EXAM_SUBJECT[s.subject]}</td>
                <td><span data-value="minutes">{s.practice_minutes}</span></td>
                <td><span data-value="count">{s.attempts}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
    <SelfMarkedColumn m={o.self_marked} />
    </>
  );
}

/** 学生自己标记的计划完成 / 跳过（来源 self，迁移 0057）：单独一栏、单独标来源；不是核实过的完成，也不是判断（规则 6、11） */
function SelfMarkedColumn({ m }: { m: WeeklyObjective['self_marked'] }) {
  return (
    <section aria-labelledby="weekly-self-marked" data-weekly-self-marked="" data-source-type="self">
      <h2 id="weekly-self-marked">{WEEKLY.selfMarkedHeading}</h2>
      <p className="muted">{WEEKLY.selfMarkedSource}</p>
      <dl>
        <dt>{WEEKLY.planDone}</dt><dd><span data-value="count">{m.plan_done}</span></dd>
        <dt>{WEEKLY.planSkipped}</dt><dd><span data-value="count">{m.plan_skipped}</span></dd>
      </dl>
    </section>
  );
}
