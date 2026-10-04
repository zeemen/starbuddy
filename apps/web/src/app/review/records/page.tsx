// 版本记录（spec 设计要点 11，Gate 0 证据）：每行一次签字、发布或撤下，带签字人（staff 账号）与内容摘要。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import { ACTION_LABEL, DECISION_LABEL, SUBJECTS, getRecords, isSubject, signerTail, type SubjectCode } from '../../../lib/review.ts';

export const dynamic = 'force-dynamic';

// 考情政策线（冲刺 4）的对象类型
const POLICY_LABEL: Record<string, string> = { source_document: '来源文件', exam_scheme: '方案', admission_line: '招录行', school: '学校' };

export default async function RecordsPage({ searchParams }: { searchParams: Promise<{ subject?: string }> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const raw = (await searchParams).subject ?? 'aya';
  const subject: SubjectCode = isSubject(raw) ? raw : 'aya';
  const r = await getRecords(staff, subject);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>版本记录 · {SUBJECTS.find((s) => s.code === subject)?.name}</h1>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : (
        <table className="review-table" data-testid="records">
          <caption>每行一次签字、发布或撤下（地图版本），或一次编译页面签字（页面 ID 与提交号），或一次考情签字与发布（按城市与届别，各学科共用）；签字人是 staff 账号</caption>
          <thead>
            <tr>
              <th scope="col">版本号 / 页面</th><th scope="col">动作</th><th scope="col">结论</th><th scope="col">依据</th>
              <th scope="col">时间</th><th scope="col">签字人</th><th scope="col">摘要 / 提交号</th><th scope="col">校验（错 / 警）</th>
            </tr>
          </thead>
          <tbody>
            {r.value.items.map((x, i) => (
              <tr key={i} data-line={x.line} data-action={x.action}
                data-version={x.line === 'map' ? x.version ?? undefined : undefined} data-page={x.page_id ?? undefined}
                data-policy-object={x.line === 'policy' ? x.policy_object ?? undefined : undefined}
                data-policy-id={x.line === 'policy' ? x.policy_object_id ?? undefined : undefined}>
                <td>{x.line === 'page' ? <>页面 <code>{x.page_id}</code></>
                  : x.line === 'policy' ? <>考情 {POLICY_LABEL[x.policy_object ?? ''] ?? x.policy_object} <code>{x.city}</code>{x.cohort ? ` ${x.cohort}` : ''}{x.policy_version ? ` v${x.policy_version}` : ''}</>
                  : x.version}</td>
                <td>{ACTION_LABEL[x.action] ?? x.action}</td>
                <td>{DECISION_LABEL[x.decision] ?? x.decision}</td>
                <td>{x.basis_code ?? '—'}</td>
                <td>{new Date(x.decided_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</td>
                <td><code>{signerTail(x.signer)}</code></td>
                <td><code>{x.line === 'page' ? (x.page_commit ?? '').slice(0, 12) : x.digest ? x.digest.slice(0, 12) : '—'}</code></td>
                <td>{x.line === 'map' ? `${x.validation_errors} / ${x.validation_warnings}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p><a href={`/review?subject=${subject}`}>返回地图版本</a></p>
    </main>
  );
}
