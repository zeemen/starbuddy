// 统一复核队列（specs/review-queue-sprint2 设计要点 2）：按学科、状态、原因筛选，按（创建时间, id）游标翻页，链到详情。
// 可见范围：只有不透明学生指针（student_ref）、学科、类型、原因、状态与领取情况；不显示作答原文与任何可识别信息。
import { redirect } from 'next/navigation';
import { currentSession } from '../../../lib/review-session.ts';
import {
  QUEUE_REASONS, QUEUE_STATUSES, QUEUE_STATUS_LABEL, SOURCE_KIND_LABEL, SUBJECTS, assignmentLabel, formatTime, isCursor, isQueueReason,
  isQueueStatus, isSubject, isTaskId, queue, queueQuery, reasonLabel, type QueueFilter, type QueueReason, type QueueStatus, type SubjectCode,
} from '../../../lib/review.ts';

export const dynamic = 'force-dynamic';

type Search = { subject?: string; status?: string; reason?: string; before?: string; before_id?: string };

export default async function QueuePage({ searchParams }: { searchParams: Promise<Search> }) {
  const staff = await currentSession();
  if (!staff) redirect('/review/login');
  const sp = await searchParams;
  // 地址栏参数只认白名单里的值，其余回到默认（不把任意文字带进查询）
  const subject: SubjectCode = sp.subject && isSubject(sp.subject) ? sp.subject : 'aya';
  const status: QueueStatus = sp.status && isQueueStatus(sp.status) ? sp.status : 'open';
  const reason: QueueReason | undefined = sp.reason && isQueueReason(sp.reason) ? sp.reason : undefined;
  // 游标两个参数都合格式才用，否则从第一页开始
  const paged = !!sp.before && isCursor(sp.before) && !!sp.before_id && isTaskId(sp.before_id);
  const before = paged ? sp.before : undefined;
  const before_id = paged ? sp.before_id : undefined;
  const filter: QueueFilter = { subject, status, reason, before, before_id };
  const r = await queue(staff, filter);
  if (!r.ok && r.problem.status === 401) redirect('/review/login');
  const subjectName = SUBJECTS.find((s) => s.code === subject)?.name;
  const link = (f: Partial<QueueFilter>) => `/review/queue?${queueQuery({ ...filter, ...f })}`;
  return (
    <main>
      <h1>复核队列</h1>
      <p className="muted">只显示不透明学生指针与结构化判分；不显示作答原文与任何可识别信息。</p>
      <nav aria-label="学科" className="review-tabs">
        {SUBJECTS.map((s) => (
          <a key={s.code} href={link({ subject: s.code, before: undefined, before_id: undefined })} aria-current={s.code === subject ? 'page' : undefined}>
            {s.name}
          </a>
        ))}
      </nav>
      <form method="get" action="/review/queue" className="review-form" data-testid="queue-filter">
        <input type="hidden" name="subject" value={subject} />
        <label htmlFor="q-status">状态</label>{' '}
        <select id="q-status" name="status" defaultValue={status}>
          {QUEUE_STATUSES.map((s) => <option key={s} value={s}>{QUEUE_STATUS_LABEL[s]}</option>)}
        </select>{' '}
        <label htmlFor="q-reason">原因</label>{' '}
        <select id="q-reason" name="reason" defaultValue={reason ?? ''}>
          <option value="">全部</option>
          {QUEUE_REASONS.map((x) => <option key={x} value={x}>{reasonLabel(x)}</option>)}
        </select>{' '}
        <button type="submit">筛选</button>
      </form>
      {!r.ok ? (
        <p className="review-result error">读取失败（{r.problem.code}）</p>
      ) : r.value.items.length === 0 ? (
        <p data-testid="queue-empty">没有符合条件的复核任务</p>
      ) : (
        <div className="table-scroll">
          <table className="review-table" data-testid="queue">
            <caption>
              {subjectName}的复核任务 · {QUEUE_STATUS_LABEL[status]}{reason ? ` · ${reasonLabel(reason)}` : ''}（按创建时间，新的在前）
            </caption>
            <thead>
              <tr>
                <th scope="col">创建时间</th><th scope="col">学科</th><th scope="col">学生指针</th><th scope="col">类型</th>
                <th scope="col">原因</th><th scope="col">状态</th><th scope="col">领取</th><th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {r.value.items.map((t) => (
                <tr key={t.id} data-task={t.id} data-status={t.status} data-assigned-to-me={String(t.assigned_to_me)}>
                  <td>{formatTime(t.created_at)}</td>
                  <td>{SUBJECTS.find((s) => s.code === t.subject)?.name ?? t.subject}</td>
                  <td><code>{t.student_ref}</code></td>
                  <td>{SOURCE_KIND_LABEL[t.source_kind] ?? t.source_kind}</td>
                  <td>{t.reasons.map(reasonLabel).join('、')}</td>
                  <td>{QUEUE_STATUS_LABEL[t.status] ?? t.status}</td>
                  <td>{t.status === 'resolved' ? '—' : assignmentLabel(t)}</td>
                  <td><a href={`/review/queue/${t.id}`}>查看任务 {t.student_ref}</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p>
        {before ? <><a href={link({ before: undefined, before_id: undefined })}>回到第一页</a>{' '}</> : null}
        {r.ok && r.value.next ? <a href={link({ before: r.value.next.before, before_id: r.value.next.before_id })} data-testid="queue-next">下一页</a> : null}
      </p>
    </main>
  );
}
