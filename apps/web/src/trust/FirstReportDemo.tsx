// 首份报告开发演示页（SIE-02b 第二步 PR 2；spec“第二步补充”第 4 条）：只在开发、测试构建里（page.dev.tsx），开关 report 默认关。
// 关时在读会话之前 404、不发任何请求；身份只来自真实会话：学生本人看学生版、有效绑定家长看家长版（受众由 BFF 按会话决定）。
// 判断先于展示（规则 11）：关于学生的句子只来自 BFF 转来的报告句（每句一个 data-claim-id，report-view.ts 再断言）；
// 顶部提示语、标题与衔接短语是洞察服务登记的固定文字（占位，data-fixed-text），页面标签全是占位文案（copy.ts）。
// 假设标“初步判断”；家长版每句附置信度分档，学生版不附；查询参数 verify 列出的待验证判断经单条解释读取，画虚线、不显示结论文字。
import { notFound, redirect } from 'next/navigation';
import { fetchFirstReport, fetchReportExplain } from '../lib/trust-api.ts';
import { pageSession } from '../lib/session.ts';
import { CONFIDENCE_BAND, FIRST_REPORT, FIRST_REPORT_SECTION, TRUST } from '../shell/copy.ts';
import { requireTrust } from '../shell/guard.ts';
import { pickFirstReport, pickVerify, type FirstReportView } from './report-view.ts';
import { UUID, type Audience } from './view.ts';

export async function FirstReportDemo({ studentId, verify }: { studentId: string; verify: string[] }) {
  requireTrust('report');
  if (!UUID.test(studentId)) notFound();
  const ps = await pageSession();
  if (!ps) redirect('/login');
  // 只认学生本人或有效绑定家长（授权仍以 BFF 与数据库为准）；别的孩子、兄妹的学生会话一律 404，不发请求
  const audience: Audience | null = ps.me.account_kind === 'student'
    ? (ps.me.student_id === studentId ? 'student' : null)
    : (ps.me.children.some((c) => c.student_id === studentId) ? 'guardian' : null);
  if (!audience) notFound();
  const r = await fetchFirstReport(ps.session, studentId);
  if (!r.ok && r.status === 401) redirect('/login');
  if (!r.ok && r.status === 404) notFound();
  const view = r.ok ? pickFirstReport(r.value, audience) : null;
  const dashed = (await Promise.all(verify.map(async (id) => {
    const x = await fetchReportExplain(ps.session, studentId, id);
    return x.ok ? pickVerify(x.value, id, audience) : null;
  }))).filter((x): x is { claim_id: string } => x !== null);
  return (
    <main data-testid="first-report-page" data-trust-root="first-report" data-audience={audience}>
      <h1>{FIRST_REPORT.heading}</h1>
      <p className="muted">{FIRST_REPORT.devNote}</p>
      {view === 'none' ? <p className="muted" data-testid="first-report-empty">{FIRST_REPORT.empty}</p>
        : view ? <Report view={view} audience={audience} />
        : <p className="muted" data-unavailable="first-report">{TRUST.unavailable}</p>}
      {dashed.length > 0 ? (
        <section aria-labelledby="report-verify" data-report-verify="">
          <h2 id="report-verify">{FIRST_REPORT.verifyHeading}</h2>
          <ul className="report-sentences">
            {dashed.map((d) => (
              <li key={d.claim_id} className="claim-dashed" data-claim-id={d.claim_id} data-display="dashed">{FIRST_REPORT.verifyNote}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}

function Report({ view, audience }: { view: FirstReportView; audience: Audience }) {
  return (
    <article className="first-report" aria-labelledby="first-report-title">
      <p className="report-notice" role="note" data-fixed-text="notice">{view.notice}</p>
      <h2 id="first-report-title" data-fixed-text="title">{view.title}</h2>
      {view.sections.map((sec) => (
        <section key={sec.key} aria-labelledby={`report-${sec.key}`} data-report-section={sec.key}>
          <h3 id={`report-${sec.key}`}>{FIRST_REPORT_SECTION[sec.key]}</h3>
          <p data-fixed-text="connective">{sec.connective}</p>
          <ul className="report-sentences">
            {sec.sentences.map((s) => (
              <li key={s.claim_id} data-claim-id={s.claim_id} data-status={s.status}>
                <span>{s.text}</span>
                {s.preliminary ? <>{' '}<span className="report-label">{FIRST_REPORT.preliminary}</span></> : null}
                {audience === 'guardian' && s.confidence_band
                  ? <>{' '}<span className="muted" data-confidence={s.confidence_band}>{CONFIDENCE_BAND[s.confidence_band]}</span></> : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </article>
  );
}
