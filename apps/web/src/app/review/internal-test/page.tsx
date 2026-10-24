// 审核台 · 内部测试退出状态（specs/launch-lt06-internal-test-ops 范围第 4 条）：每个退出测试的档案一行——退出时间、撤回的同意类型、
// 是否仍有有效同意、作废的会话数、删除请求状态、是否已删除。学生只有不透明指针。测试结束统一删除时用它核对（docs/ops/internal-test-runbook.md）。
// 开关 STARBUDDY_INTERNAL_TEST=internal_test（dev / test / staging）：关着时 404，在读会话之前判断，不发任何请求。staff 登录。
import { notFound, redirect } from 'next/navigation';
import { internalTestEnabled } from '../../../internal-test/features.ts';
import { currentSession } from '../../../lib/review-session.ts';
import { formatTime, listInternalTestExits } from '../../../lib/review.ts';

export const dynamic = 'force-dynamic';

const CONSENT_NAME: Record<string, string> = {
  guardian_consent_under14: '监护人同意', privacy_policy: '隐私政策', voice_processing: '语音处理', pilot_participation: '试点参与',
};
const ERASURE_NAME: Record<string, string> = { pending: '冷静期中', executed: '已执行', cancelled: '已撤回' };  // 与 0035 erasure_request.status 一致

export default async function InternalTestExitsPage() {
  if (!internalTestEnabled()) notFound();
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const r = await listInternalTestExits(staff);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  return (
    <main>
      <h1>内部测试退出状态</h1>
      <p className="muted">由家长发起退出的档案。已有数据保留到测试结束，按运维手册走删除流程统一删除。</p>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="exits-empty">没有退出的档案</p>
      ) : (
        <div className="table-scroll">
          <table className="review-table" data-testid="exits-list">
            <caption>退出内部测试的档案</caption>
            <thead>
              <tr>
                <th scope="col">学生指针</th><th scope="col">退出时间</th><th scope="col">撤回的同意</th><th scope="col">仍有有效同意</th>
                <th scope="col">作废的会话</th><th scope="col">删除请求</th><th scope="col">已删除</th>
              </tr>
            </thead>
            <tbody>
              {r.value.items.map((x) => (
                <tr key={x.student_ref} data-ref={x.student_ref}>
                  <td><code>{x.student_ref}</code></td>
                  <td>{formatTime(x.exited_at)}</td>
                  <td>{x.consents_revoked.length ? x.consents_revoked.map((c) => CONSENT_NAME[c] ?? c).join('、') : '—'}</td>
                  <td>{x.consent_active ? '是' : '否'}</td>
                  <td>{x.sessions_revoked}</td>
                  <td>{x.erasure_status ? ERASURE_NAME[x.erasure_status] ?? x.erasure_status : '—'}</td>
                  <td>{x.erased ? '是' : '否'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
