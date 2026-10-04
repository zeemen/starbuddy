// 考情政策线（冲刺 4，spec 第 8 节）：方案列表（链到差异视图），以及待签的来源文件、招录行、学校。
// 来源文件、招录行、学校在第二名不同 staff 对当前内容批准时由库函数发布；发布被拒时整笔回滚、签字不留痕，界面给出明确提示。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { POLICY_OBJECT_LABEL, RESULT_LABEL, STATUS_LABEL, formatTime, isCity, listPolicy } from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { signPolicyRecordAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

export default async function PolicyPage({ searchParams }: { searchParams: Promise<{ city?: string }> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const raw = (await searchParams).city ?? '';
  const city = isCity(raw) ? raw : undefined;
  const r = await listPolicy(staff, city);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>考情政策{city ? ` · ${city}` : ''}</h1>
      <p className="muted">考情政策线一律高风险，需两名不同教研对当前内容批准。方案签字后由签字人另行发布；来源文件、招录行、学校在第二名批准时自动发布。</p>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : (
        <>
          <h2>方案</h2>
          {r.value.schemes.length === 0 ? <p data-testid="no-schemes">没有方案</p> : (
            <table className="review-table" data-testid="schemes">
              <caption>城市 × 届别的中考方案（按版本号）</caption>
              <thead><tr><th scope="col">城市</th><th scope="col">届别</th><th scope="col">版本号</th><th scope="col">状态</th><th scope="col">发布时间</th><th scope="col">操作</th></tr></thead>
              <tbody>
                {r.value.schemes.map((s) => (
                  <tr key={s.id} data-scheme={s.version} data-status={s.status}>
                    <td><code>{s.city}</code></td><td>{s.cohort}</td><td>{s.version}</td><td>{STATUS_LABEL[s.status] ?? s.status}</td>
                    <td>{formatTime(s.published_at)}</td>
                    <td><a href={`/review/policy/${s.id}`}>{s.status === 'draft' ? '审核' : '查看'} {s.city} {s.cohort} v{s.version}</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h2>待签的来源文件、招录行、学校</h2>
          {r.value.drafts.length === 0 ? <p data-testid="no-policy-drafts">没有待签的记录</p> : (
            <ul className="review-list" data-testid="policy-drafts">
              {r.value.drafts.map((x) => (
                <li key={x.id} data-object={x.object_type} data-approvals={x.approvals}>
                  <p>{POLICY_OBJECT_LABEL[x.object_type] ?? x.object_type} · <code>{x.city}</code>{x.cohort ? ` ${x.cohort}` : ''} · {x.label}
                    {' '}· 已有 {x.approvals} / 2 名批准 · 摘要 <code>{x.digest.slice(0, 12)}</code></p>
                  <ActionForm action={signPolicyRecordAction} labels={RESULT_LABEL} testId={`policy-record-${x.id}`}>
                    <input type="hidden" name="object_id" value={x.id} />
                    <input type="hidden" name="object_type" value={x.object_type} />
                    <input type="hidden" name="expected_digest" value={x.digest} />
                    {x.approvals >= 1 ? <p className="muted">你若批准，就是第二名：库会在同一步发布；发布被拒时整笔回滚，你的签字不会保存。</p> : null}
                    <button type="submit" name="decision" value="approved">批准</button>{' '}
                    <button type="submit" name="decision" value="rejected">驳回</button>
                  </ActionForm>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </main>
  );
}
