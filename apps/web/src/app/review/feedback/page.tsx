// 审核台 · 内部测试问题反馈（specs/launch-lt06-internal-test-ops 范围第 3 条）：只读列表（新到旧、按状态筛、游标翻页）+ 每条改状态。
// 可见范围：分类、来源（学生自述 / 家长观察分开）、文本、页面类型、状态与时间；学生只有不透明指针，没有 student_id 与提交者账号。
// 反馈文本只作反馈处理，不进画像与判断账本；页面只显示，不复制到别处。staff 登录。
// 开关 STARBUDDY_INTERNAL_TEST=internal_test（dev / test / staging）：关着时 404，在读会话之前判断，不发任何请求。
import { notFound, redirect } from 'next/navigation';
import { internalTestEnabled } from '../../../internal-test/features.ts';
import { currentSession } from '../../../lib/review-session.ts';
import {
  FEEDBACK_CATEGORY_NAME, FEEDBACK_RESULT_LABEL, FEEDBACK_SOURCE_LABEL, FEEDBACK_STATUSES, FEEDBACK_STATUS_LABEL, feedbackQuery, formatTime,
  isCursor, isFeedbackStatus, isVersionId, listFeedback, type FeedbackFilter,
} from '../../../lib/review.ts';
import { ActionForm } from '../ActionForm.tsx';
import { feedbackStatusAction } from '../actions.ts';

export const dynamic = 'force-dynamic';

type Search = { status?: string; before?: string; before_id?: string };

export default async function FeedbackPage({ searchParams }: { searchParams: Promise<Search> }) {
  if (!internalTestEnabled()) notFound();
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const sp = await searchParams;
  const status = isFeedbackStatus(sp.status) ? sp.status : undefined;
  const paged = !!sp.before && isCursor(sp.before) && !!sp.before_id && isVersionId(sp.before_id);
  const filter: FeedbackFilter = { status, before: paged ? sp.before : undefined, before_id: paged ? sp.before_id : undefined };
  const r = await listFeedback(staff, filter);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  const link = (f: FeedbackFilter) => `/review/feedback?${feedbackQuery(f)}`;
  return (
    <main>
      <h1>内部测试问题反馈</h1>
      <p className="muted">学生与家长在内部测试里提交的问题反馈，新到的在前。学生只显示不透明指针。反馈文本只用于处理问题，不进画像。</p>
      <nav aria-label="状态" className="review-tabs">
        <a href={link({})} aria-current={status === undefined ? 'page' : undefined}>全部</a>
        {FEEDBACK_STATUSES.map((s) => (
          <a key={s} href={link({ status: s })} aria-current={s === status ? 'page' : undefined}>{FEEDBACK_STATUS_LABEL[s]}</a>
        ))}
      </nav>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="feedback-empty">没有反馈</p>
      ) : (
        <div className="table-scroll">
          <table className="review-table" data-testid="feedback-list">
            <caption>问题反馈（{status ? FEEDBACK_STATUS_LABEL[status] : '全部'}）</caption>
            <thead>
              <tr>
                <th scope="col">提交时间</th><th scope="col">来源</th><th scope="col">分类</th><th scope="col">内容</th>
                <th scope="col">页面类型</th><th scope="col">学生指针</th><th scope="col">状态</th>
              </tr>
            </thead>
            <tbody>
              {r.value.items.map((x) => (
                <tr key={x.id} data-feedback={x.id} data-status={x.status}>
                  <td>{formatTime(x.created_at)}</td>
                  <td>{FEEDBACK_SOURCE_LABEL[x.source] ?? x.source}</td>
                  <td>{FEEDBACK_CATEGORY_NAME[x.category] ?? x.category}</td>
                  <td className="review-work">{x.body}</td>
                  <td>{x.route_template ? <code>{x.route_template}</code> : '—'}</td>
                  <td>{x.student_ref ? <code>{x.student_ref}</code> : '—'}</td>
                  <td>
                    <ActionForm action={feedbackStatusAction} labels={FEEDBACK_RESULT_LABEL} testId={`feedback-status-${x.id}`}>
                      <input type="hidden" name="feedback" value={x.id} />
                      <label htmlFor={`fs-${x.id}`}>状态</label>{' '}
                      <select id={`fs-${x.id}`} name="status" defaultValue={x.status}>
                        {FEEDBACK_STATUSES.map((s) => <option key={s} value={s}>{FEEDBACK_STATUS_LABEL[s]}</option>)}
                      </select>{' '}
                      <button type="submit">改状态</button>
                      {x.status_changed_at ? <span className="muted">（{formatTime(x.status_changed_at)} 改过）</span> : null}
                    </ActionForm>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p>
        {paged ? <><a href={link({ status })}>回到第一页</a>{' '}</> : null}
        {r.ok && r.value.next ? (
          <a href={link({ status, before: r.value.next.before, before_id: r.value.next.before_id })} data-testid="feedback-next">下一页</a>
        ) : null}
      </p>
    </main>
  );
}
