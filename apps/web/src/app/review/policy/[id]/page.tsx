// 考情方案审核页（冲刺 4，spec 第 8 节）：与同城市同届别的上一版本、与上一届已发布方案比较（科目、满分、计分、合场、批次），
// policy.validate_scheme 的结果逐条列出；两名不同 staff 签字，签字身份发布。表单带当前摘要：看到的就是签的。
import { notFound, redirect } from 'next/navigation';
import { currentSession } from '../../../../lib/review-session.ts';
import {
  ACTION_LABEL, DECISION_LABEL, RESULT_LABEL, STATUS_LABEL, formatTime, getPolicyView, isVersionId, signerTail,
} from '../../../../lib/review.ts';
import { ActionForm } from '../../ActionForm.tsx';
import { DiffTable } from '../../DiffTable.tsx';
import { publishSchemeAction, signSchemeAction } from '../../actions.ts';

export const dynamic = 'force-dynamic';

export default async function SchemePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  if (!isVersionId(id)) notFound();
  const r = await getPolicyView(staff, id);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  if (!r.ok) {
    if (r.problem.status === 404 || r.problem.status === 422) notFound();
    return <main><h1>考情方案</h1><p className="review-result error">读取失败（{r.problem.code}）</p></main>;
  }
  const d = r.value;
  const s = d.scheme;
  const isDraft = s.status === 'draft';
  const errors = d.validation.filter((i) => i.severity === 'error');
  return (
    <main>
      <h1>考情方案 {s.city} {s.cohort} 届 v{s.version}（{STATUS_LABEL[s.status] ?? s.status}）</h1>
      <p className="muted">计分总分 {d.total ?? '—'} · 内容摘要 <code data-testid="scheme-digest">{d.digest.slice(0, 12)}</code>
        {' '}· 需 {d.summary.required_signers} 名不同教研对当前内容批准，已有 {d.summary.approvals} 名</p>

      <section aria-labelledby="h-prev">
        <h2 id="h-prev">与同城市同届别的上一版本比较{d.previous_version ? `（v${d.previous_version.version}，${STATUS_LABEL[d.previous_version.status] ?? d.previous_version.status}）` : ''}</h2>
        {d.previous_version ? <DiffTable changes={d.diff_previous_version} caption="与上一版本相比的变化" testId="diff-prev-version" labels="policy" />
          : <p data-testid="no-prev-version">没有发布过的上一版本，全部按新增列出：</p>}
        {!d.previous_version ? <DiffTable changes={d.diff_previous_version} caption="全部内容" testId="diff-prev-version" labels="policy" /> : null}
      </section>
      <section aria-labelledby="h-cohort">
        <h2 id="h-cohort">与上一届已发布方案比较{d.previous_cohort ? `（${d.previous_cohort.cohort} 届 v${d.previous_cohort.version}）` : ''}</h2>
        {d.previous_cohort ? <DiffTable changes={d.diff_previous_cohort} caption="与上一届相比的变化" testId="diff-prev-cohort" labels="policy" />
          : <p data-testid="no-prev-cohort">上一届没有已发布方案</p>}
      </section>
      <section aria-labelledby="h-validate">
        <h2 id="h-validate">发布前校验（policy.validate_scheme）</h2>
        {d.validation.length === 0 ? <p>没有问题</p> : (
          <ul data-testid="scheme-validation">
            {d.validation.map((i, k) => <li key={k} data-check={i.check_name}>{i.severity === 'error' ? '阻断' : '提示'}：{i.detail}（<code>{i.check_name}</code>）</li>)}
          </ul>
        )}
      </section>
      <section aria-labelledby="h-scheme-sign">
        <h2 id="h-scheme-sign">签字与发布</h2>
        {isDraft ? (
          <>
            <ActionForm action={signSchemeAction} labels={RESULT_LABEL} testId="scheme-sign">
              <input type="hidden" name="scheme" value={s.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <p>我已核对上面的两组差异与校验结果（摘要 <code>{d.digest.slice(0, 12)}</code>）。同一账号对同一内容只能签一次。</p>
              <button type="submit" name="decision" value="approved">批准</button>{' '}
              <button type="submit" name="decision" value="rejected">驳回</button>
            </ActionForm>
            <ActionForm action={publishSchemeAction} labels={RESULT_LABEL} testId="scheme-publish">
              <input type="hidden" name="scheme" value={s.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <p>{errors.length > 0 ? `还有 ${errors.length} 条阻断，发布会被拒绝。` : '发布后，同城市同届别原已发布的方案自动撤下。'}</p>
              <button type="submit">发布这个方案</button>
            </ActionForm>
          </>
        ) : <p data-testid="scheme-status-note">本方案{STATUS_LABEL[s.status] ?? s.status}（发布于 {formatTime(s.published_at)}），不能再签字。</p>}
      </section>
      <section aria-labelledby="h-scheme-records">
        <h2 id="h-scheme-records">签字记录</h2>
        {d.records.length === 0 ? <p>还没有签字</p> : (
          <table className="review-table" data-testid="scheme-records">
            <caption>这个方案的签字与发布（签字人是 staff 账号）</caption>
            <thead><tr><th scope="col">动作</th><th scope="col">结论</th><th scope="col">签字人</th><th scope="col">时间</th><th scope="col">是否对应当前内容</th></tr></thead>
            <tbody>
              {d.records.map((x, k) => (
                <tr key={k} data-action={x.action}>
                  <td>{ACTION_LABEL[x.action] ?? x.action}</td><td>{DECISION_LABEL[x.decision] ?? x.decision}</td>
                  <td><code>{signerTail(x.signer)}</code></td><td>{formatTime(x.signed_at)}</td>
                  <td>{x.digest === d.digest ? '是' : '否（签字后内容改过）'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <p><a href={`/review/policy?city=${s.city}`}>返回考情政策</a></p>
    </main>
  );
}
