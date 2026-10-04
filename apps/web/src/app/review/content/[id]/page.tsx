// 内容版本审核页（冲刺 4，spec 第 7 节）：与上一已发布版本的差异（能力视图、题目登记、准入）、发布前提（所指地图版本、考情方案）、
// 签字、发布、撤下；题目准入的签字与执行（执行后的掌握重算由学习服务按执行记录做）。所有表单带当前摘要：看到的就是签的。
import { notFound, redirect } from 'next/navigation';
import { currentSession } from '../../../../lib/review-session.ts';
import {
  ACTION_LABEL, ADMISSIONS, ADMISSION_LABEL, DECISION_LABEL, LICENSE_STATUSES, RESULT_LABEL, RETIRE_BASIS, SOURCE_TYPES, STATUS_LABEL,
  formatTime, getContentView, isVersionId, signerTail, type ContentItem,
} from '../../../../lib/review.ts';
import { ActionForm } from '../../ActionForm.tsx';
import { DiffTable } from '../../DiffTable.tsx';
import {
  applyAdmissionAction, publishContentAction, retireContentAction, signAdmissionAction, signContentAction,
} from '../../actions.ts';

export const dynamic = 'force-dynamic';

function AdmissionForm({ cv, subject, item }: { cv: string; subject: string; item: ContentItem }) {
  const fid = (k: string) => `adm-${item.item_id}-${k}`.replace(/[^A-Za-z0-9_-]/g, '_');
  return (
    <details className="node-editor" data-item={item.item_id}>
      <summary><code>{item.item_id}</code> · {ADMISSION_LABEL[item.admission] ?? item.admission} · 来源 {item.source_ref ?? '—'}（{item.source_type}，授权 {item.license_status}）</summary>
      <ActionForm action={signAdmissionAction} labels={RESULT_LABEL} testId={`admission-${item.item_id}`}>
        <input type="hidden" name="cv" value={cv} />
        <input type="hidden" name="subject" value={subject} />
        <input type="hidden" name="item_id" value={item.item_id} />
        <input type="hidden" name="expected_digest" value={item.admission_digest} />
        <p>
          <label htmlFor={fid('to')}>目标准入</label>{' '}
          <select id={fid('to')} name="to_admission" defaultValue="" required>
            <option value="" disabled>请选择</option>
            {ADMISSIONS.filter((a) => a !== item.admission).map((a) => <option key={a} value={a}>{ADMISSION_LABEL[a]}</option>)}
          </select>{' '}
          <label htmlFor={fid('level')}>学科审核等级</label>{' '}
          <input id={fid('level')} name="review_level" pattern="Q[0-5]" size={3} placeholder="Q2" />
        </p>
        <p>
          <label htmlFor={fid('ref')}>来源记录编号</label>{' '}
          <input id={fid('ref')} name="source_ref" defaultValue={item.source_ref ?? ''} pattern="[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}" />{' '}
          <label htmlFor={fid('type')}>来源类型</label>{' '}
          <select id={fid('type')} name="source_type" defaultValue={item.source_type}>
            {SOURCE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>{' '}
          <label htmlFor={fid('license')}>授权</label>{' '}
          <select id={fid('license')} name="license_status" defaultValue="">
            <option value="">不改（升为已准入时必须写明）</option>
            {LICENSE_STATUSES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </p>
        <p className="muted">升为已准入须来源已核（编号、已知类型、授权 cleared）与审核等级不低于 Q2；方向只允许 来源待核 → 待学科审核 → 已准入，任意 → 已拒绝。签字后还要“执行”才生效。</p>
        <button type="submit" name="decision" value="approved">批准这项准入变化</button>{' '}
        <button type="submit" name="decision" value="rejected">驳回</button>
      </ActionForm>
    </details>
  );
}

export default async function ContentVersionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  if (!isVersionId(id)) notFound();
  const r = await getContentView(staff, id);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  if (!r.ok) {
    if (r.problem.status === 404 || r.problem.status === 422) notFound();
    return <main><h1>内容版本</h1><p className="review-result error">读取失败（{r.problem.code}）</p></main>;
  }
  const d = r.value;
  const v = d.version;
  const isDraft = v.status === 'draft';
  const blocking = d.checks.filter((c) => c.severity === 'error');
  return (
    <main>
      <h1>内容版本 {v.content_version}（{STATUS_LABEL[v.status] ?? v.status}）</h1>
      <p className="muted">
        六段标识 <code data-testid="six-segment">{v.six_segment}</code> · 所指地图版本 {d.map_version ? `${d.map_version.version}（${STATUS_LABEL[d.map_version.status] ?? d.map_version.status}）` : '—'}
        {' '}· 对比基准：{d.base_version ? `上一已发布版本 ${d.base_version.content_version}` : '没有发布过的同前五段版本，全部按新增列出'}
        {' '}· 内容摘要 <code data-testid="cv-digest">{d.digest.slice(0, 12)}</code>
      </p>

      <section aria-labelledby="h-cv-summary" data-testid="cv-summary" data-ready={String(d.summary.ready)}>
        <h2 id="h-cv-summary">摘要与发布前提</h2>
        <ul>
          <li>风险：<span className="risk risk-high">高风险</span>（内容版本一律高风险，需 {d.summary.required_signers} 名教研签字）</li>
          <li>差异：版本 {d.summary.counts['version'] ?? 0} 条，能力视图 {d.summary.counts['skill_node'] ?? 0} 条，题目登记 {d.summary.counts['item'] ?? 0} 条（其中准入变化 {d.summary.admission_changes} 条）</li>
          <li>最近一条审核结论：{d.summary.latest_decision ? DECISION_LABEL[d.summary.latest_decision] : '还没有'}
            {d.summary.latest_decision && !d.summary.latest_matches ? '（针对的是修改前的内容，需要重签）' : ''}</li>
          {d.checks.length === 0 ? <li>发布前提：都满足</li> : d.checks.map((c) => (
            <li key={c.check_name} data-check={c.check_name}>{c.severity === 'error' ? '阻断' : '提示'}：{c.detail}</li>
          ))}
        </ul>
        <p className="muted">能力点与题目的编号在平台上全局唯一（不带版本），新版本的登记在差异里显示为新增，上一版本的登记显示为删除。</p>
      </section>

      <section aria-labelledby="h-cv-diff">
        <h2 id="h-cv-diff">差异</h2>
        <DiffTable changes={d.changes} caption="与上一已发布版本相比的变化（旧值 → 新值）" testId="cv-changes" />
      </section>

      <section aria-labelledby="h-cv-sign">
        <h2 id="h-cv-sign">签字与发布</h2>
        {isDraft ? (
          <>
            <ActionForm action={signContentAction} labels={RESULT_LABEL} testId="cv-sign">
              <input type="hidden" name="cv" value={v.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <p><label htmlFor="cv-basis">依据编码（可不填）</label>{' '}<input id="cv-basis" name="basis_code" pattern="[a-z0-9_]{1,48}" /></p>
              <p>我已核对上面列出的全部差异与发布前提（摘要 <code>{d.digest.slice(0, 12)}</code>）。</p>
              <button type="submit" name="decision" value="approved">批准</button>{' '}
              <button type="submit" name="decision" value="rejected">驳回</button>
            </ActionForm>
            <ActionForm action={publishContentAction} labels={RESULT_LABEL} testId="cv-publish">
              <input type="hidden" name="cv" value={v.id} />
              <input type="hidden" name="expected_digest" value={d.digest} />
              <p>{blocking.length > 0 ? `还有 ${blocking.length} 条阻断，发布会被拒绝。` : '发布不会自动撤下同前五段的旧版本（要停用旧版本请另行撤下）。'}</p>
              <button type="submit">发布这个内容版本</button>
            </ActionForm>
          </>
        ) : (
          <p data-testid="cv-status-note">{v.status === 'published' ? '本版本已发布' : '本版本已撤下'}（发布于 {formatTime(v.published_at)}），不能再签字。</p>
        )}
        {v.status !== 'retired' ? (
          <ActionForm action={retireContentAction} labels={RESULT_LABEL} testId="cv-retire">
            <input type="hidden" name="cv" value={v.id} />
            <p>
              <label htmlFor="cv-retire-basis">撤下依据</label>{' '}
              <select id="cv-retire-basis" name="basis_code" defaultValue="" required>
                <option value="" disabled>请选择</option>
                {RETIRE_BASIS.map((b) => <option key={b.code} value={b.code}>{b.label}</option>)}
              </select>
            </p>
            <p>
              <input type="checkbox" id="cv-retire-confirm" name="confirm" value="yes" required />{' '}
              <label htmlFor="cv-retire-confirm">{v.status === 'published' ? '我知道撤下后家庭端立即看不到这个版本；该学科没有其他已发布版本时，新选科会返回未上线' : '我确认撤下这个草稿'}</label>
            </p>
            <p><button type="submit">撤下这个内容版本</button></p>
          </ActionForm>
        ) : null}
      </section>

      <section aria-labelledby="h-cv-admission">
        <h2 id="h-cv-admission">题目准入</h2>
        {d.pending_admissions.length > 0 ? (
          <ActionForm action={applyAdmissionAction} labels={RESULT_LABEL} testId="admission-apply">
            <input type="hidden" name="cv" value={v.id} />
            <p>已批准、待执行的准入变化 {d.pending_admissions.length} 条（一批里有一条不合格，整批回滚）：</p>
            <ul>
              {d.pending_admissions.map((p) => (
                <li key={p.id} data-review={p.id}>
                  <input type="hidden" name="review_id" value={p.id} />
                  <code>{p.item_id}</code>：{ADMISSION_LABEL[p.from_admission ?? ''] ?? p.from_admission} → {ADMISSION_LABEL[p.to_admission ?? ''] ?? p.to_admission}
                  {p.review_level ? `（${p.review_level}）` : ''}，签字人 <code>{signerTail(p.signer)}</code>
                </li>
              ))}
            </ul>
            <p className="muted">执行后由学习服务按执行记录重算受影响学生的掌握（python -m learning.jobs admission-recompute）；准入降级会在执行记录上留受影响的评分版本清单。</p>
            <button type="submit">执行这些准入变化</button>
          </ActionForm>
        ) : <p>没有待执行的准入变化</p>}
        {v.status !== 'retired' ? d.items.map((it) => <AdmissionForm key={it.item_id} cv={v.id} subject={v.subject} item={it} />) : null}
      </section>

      <section aria-labelledby="h-cv-records">
        <h2 id="h-cv-records">签字与执行记录</h2>
        {d.records.length === 0 ? <p>还没有记录</p> : (
          <table className="review-table" data-testid="cv-records">
            <caption>这个内容版本的审核、发布、撤下，以及其下题目的准入签字与执行</caption>
            <thead><tr><th scope="col">线</th><th scope="col">动作</th><th scope="col">题目</th><th scope="col">结论</th><th scope="col">签字人</th>
              <th scope="col">时间</th><th scope="col">摘要</th></tr></thead>
            <tbody>
              {d.records.map((x) => (
                <tr key={x.id} data-line={x.line} data-action={x.action}>
                  <td>{x.line === 'admission' ? '准入' : '内容版本'}</td>
                  <td>{ACTION_LABEL[x.action] ?? x.action}</td>
                  <td>{x.item_id ? <><code>{x.item_id}</code> {ADMISSION_LABEL[x.from_admission ?? ''] ?? ''} → {ADMISSION_LABEL[x.to_admission ?? ''] ?? ''}</> : '—'}</td>
                  <td>{DECISION_LABEL[x.decision] ?? x.decision}</td>
                  <td><code>{signerTail(x.signer)}</code></td>
                  <td>{formatTime(x.decided_at)}</td>
                  <td><code>{x.digest ? x.digest.slice(0, 12) : '—'}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <p><a href={`/review/content?subject=${v.subject}`}>返回内容版本</a> · <a href={`/review/records?subject=${v.subject}`}>版本记录</a></p>
    </main>
  );
}
